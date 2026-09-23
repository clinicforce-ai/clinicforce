"""Staff-only Stedi commercial eligibility and estimate workflow.

This route is deliberately separate from the existing Availity workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store
from app.insurance import commercial, commercial_store, ecw_backend, golden, store


router = APIRouter(prefix="/api/doctor/insurance/commercial", tags=["doctor-commercial-estimator"])
STEDI_URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=160)
    stedi_payer_id: str = Field(min_length=1, max_length=80)
    category: str = "commercial_medical"
    status: Literal["draft", "enrollment", "fixture_validated", "shadow", "staff_guidance_enabled", "suspended"] = "draft"
    aliases: list[str] = Field(default_factory=list, max_length=100)
    rules: dict[str, Any] = Field(default_factory=dict)


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    billing_npi: str = Field(pattern=r"^\d{10}$")
    rendering_npi: str = Field(pattern=r"^\d{10}$")
    organization_name: str = Field(min_length=1, max_length=160)
    tin: str = Field(min_length=4, max_length=20)
    location_id: str = Field(min_length=1, max_length=100)
    place_of_service: str = Field(min_length=1, max_length=20)
    network_name: str = Field(min_length=1, max_length=120)
    plan_matcher: str = Field(default="", max_length=200)
    effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["draft", "verified", "suspended"] = "draft"
    evidence_reference: str = Field(min_length=3, max_length=500)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    service_category: Literal["office", "emg_ncs"]
    plan_matcher: str = Field(default="", max_length=200)
    status: Literal["review_only", "staff_guidance_enabled", "suspended"] = "review_only"
    rules: dict[str, Any]


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    profile_id: str
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class EcwStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,200}$")


class ServiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^(?:\d{5}|[A-Za-z]\d{4}|\d{4}[A-Za-z])$")
    units: int = Field(ge=1, le=10000)


class ServicesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[ServiceLine] = Field(min_length=1, max_length=20)


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["staff_export", "ecw_document", "fixture", "stedi"]
    response_reference: str = Field(min_length=3, max_length=500)
    requested_dos: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    payer_checked_at: str
    expires_at: str | None = None
    benefits: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _session(request: Request) -> dict:
    session = await insurance_session(request)
    if not session.get("practice_id"):
        raise HTTPException(403, "Open the commercial estimator from the staff workspace.")
    return session


def _admin(session: dict) -> None:
    if session["staff"].get("role") != "admin":
        raise HTTPException(403, "Only a clinic administrator can change commercial-estimator configuration.")


def _network(practice_id: str, network_id: str) -> dict:
    value = next((row for row in commercial_store.list_networks(practice_id) if row["id"] == network_id), None)
    if not value:
        raise HTTPException(422, "The selected provider network is not configured for this practice.")
    return value


def _public(case: dict) -> dict:
    result = {key: case.get(key) for key in ("id", "created_at", "revision", "source_mode", "commercial", "estimate", "review_tasks") if key in case}
    # Never expose the staffing intake's patient/card fields or raw payer evidence.
    return result


def _load(case_id: str, session: dict) -> dict:
    case = store.get_case(case_id, session["patient_id"], session["share_manifest_id"], **_scope(session))
    if not case or case.get("source_mode") != "commercial":
        raise HTTPException(404, "Commercial estimate case not found.")
    return case


def _save(case: dict, session: dict, event: str) -> dict:
    try:
        saved = store.save_case(case, session["patient_id"], session["share_manifest_id"], event, **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not saved:
        raise HTTPException(404, "Commercial estimate case not found.")
    return saved


def _config(practice_id: str) -> dict:
    return {"connections": commercial_store.list_connections(practice_id), "networks": commercial_store.list_networks(practice_id),
            "profiles": commercial_store.list_profiles(practice_id), "rate_versions": commercial_store.list_rate_versions(practice_id),
            "stedi_execution_mode": os.getenv("STEDI_EXECUTION_MODE", "off"),
            "live_eligibility_enabled": os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() == "true"}


def _ecw_practice(practice_id: str, payer_connection_id: str, provider_network_id: str) -> dict[str, Any]:
    """Build only the eCW mappings needed by this commercial case.

    It intentionally does not use AVAILITY_PRACTICE_CONFIG: both integrations
    remain independently configurable.
    """
    payer = commercial_store.connection(practice_id, payer_connection_id)
    network = _network(practice_id, provider_network_id)
    if not payer:
        raise HTTPException(422, "The commercial payer configuration no longer exists.")
    aliases = {payer["display_name"]: payer["id"]}
    aliases.update({value: payer["id"] for value in payer.get("aliases", []) if isinstance(value, str) and value.strip()})
    return {
        "timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"),
        "payer_aliases": aliases,
        "providers": {network["billing_npi"]: {}, network["rendering_npi"]: {}},
        "locations": {network["location_id"]: {"place_of_service": network["place_of_service"],
                                               "billing_provider_npi": network["billing_npi"]}},
    }


def _ecw_practice_for_nmct(practice_id: str) -> dict[str, Any]:
    """Expose all approved NMCT mappings to the eCW reader, not to clinic staff."""
    aliases: dict[str, str] = {}
    for payer in commercial_store.list_connections(practice_id):
        if payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
            continue
        for value in [payer["display_name"], *payer.get("aliases", [])]:
            if isinstance(value, str) and value.strip():
                aliases[value] = payer["id"]
    providers: dict[str, dict] = {}
    locations: dict[str, dict] = {}
    for network in commercial_store.list_networks(practice_id):
        if network["status"] != "verified":
            continue
        providers[network["billing_npi"]] = {}
        providers[network["rendering_npi"]] = {}
        locations[network["location_id"]] = {"place_of_service": network["place_of_service"], "billing_provider_npi": network["billing_npi"]}
    return {"timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"), "payer_aliases": aliases, "providers": providers, "locations": locations}


def _nmct_path(practice_id: str, inputs: dict, source: dict) -> tuple[dict, dict, dict]:
    payer = commercial_store.connection(practice_id, str(inputs.get("payer_id") or ""))
    if not payer or payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
        raise HTTPException(409, "NMCT has no approved Stedi payer mapping for this appointment's eCW coverage.")
    service_date = str(inputs.get("service_date") or "")
    rendering_npi = str((inputs.get("rendering_provider") or {}).get("npi") or "")
    networks = [row for row in commercial_store.list_networks(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["status"] == "verified"
                and row["rendering_npi"] == rendering_npi and row["location_id"] == str(source.get("location_id") or "")
                and row["place_of_service"] == str(inputs.get("place_of_service") or "")
                and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)]
    if len(networks) != 1:
        raise HTTPException(409, "NMCT has no single verified provider-network record for this eCW provider and location.")
    profiles = [row for row in commercial_store.list_profiles(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["provider_network_id"] == networks[0]["id"]
                and row["status"] == "staff_guidance_enabled"]
    if len(profiles) != 1:
        raise HTTPException(409, "NMCT has no single enabled estimate profile for this provider and payer.")
    return payer, networks[0], profiles[0]


def _ecw_case_data(inputs: dict, source: dict, conflicts: list[str]) -> dict[str, Any]:
    return {"appointment_id": source.get("appointment_id"), "appointment_date": source.get("appointment_date"),
            "retrieved_at": source.get("retrieved_at"), "appointment_status": source.get("appointment_status"),
            "suggested_lines": [{"code": row.get("code"), "units": row.get("units"), "description": row.get("description")}
                                for row in inputs.get("lines", []) if row.get("code")],
            "reason": inputs.get("reason"), "conflicts": conflicts}


def _past_visit_with_saved_estimate(visit: dict[str, Any], case: dict | None) -> dict[str, Any]:
    """Attach only an estimate already saved for this exact eCW encounter."""
    ecw = ((case or {}).get("commercial") or {}).get("ecw") or {}
    estimate = (case or {}).get("estimate")
    if (case or {}).get("source_mode") != "commercial" or ecw.get("appointment_id") != visit.get("id") or not isinstance(estimate, dict):
        return {**visit, "saved_estimate": None}
    return {**visit, "saved_estimate": {
        "created_at": case.get("created_at"),
        "allowed_cents": estimate.get("allowed_cents"),
        "patient_cents": estimate.get("patient_cents"),
        "plan_cents": estimate.get("plan_cents"),
        "disclaimer": estimate.get("disclaimer"),
    }}


def _commercial_slot(appointment_id: str) -> str:
    return "commercial:" + hashlib.sha256(appointment_id.encode()).hexdigest()


def _ecw_coded_lines(practice_id: str, profile: dict, network: dict, service_date: str,
                     inputs: dict) -> tuple[list[dict[str, Any]], list[str]]:
    """Use only explicit eCW codes and units; never turn visit text into a charge."""
    try:
        selected = [ServiceLine(code=str(row.get("code") or "").upper(), units=int(str(row.get("units") or "")))
                    for row in inputs.get("lines", [])]
    except (TypeError, ValueError, ValidationError):
        return [], ["eClinicalWorks returned a coded service without a whole-number unit. Update the appointment coding before estimating."]
    if not selected:
        return [], ["eClinicalWorks did not return a CPT/HCPCS-coded service for this appointment. Add the planned service in eClinicalWorks before estimating."]
    try:
        matched = commercial_store.matching_rates(practice_id, profile, network, service_date,
                                                  [line.model_dump() for line in selected])
    except commercial.CommercialError as exc:
        return [], [str(exc)]
    by_code = {row["code"]: row for row in matched}
    lines = [{"code": line.code, "units": line.units, "rate_id": by_code.get(line.code, {}).get("id"),
              "allowed_cents": by_code.get(line.code, {}).get("allowed_cents"),
              "cash_cents": by_code.get(line.code, {}).get("cash_cents")} for line in selected]
    missing = [line["code"] for line in lines if line["allowed_cents"] is None]
    return lines, ([f"No active contracted rate matches: {', '.join(missing)}."] if missing else [])


def _ecw_identity(session: dict, service_date: str) -> dict[str, str]:
    patient = session["intake"]["patient"]
    values = {"patient_first_name": patient.get("given_name"), "patient_last_name": patient.get("family_name"),
              "patient_birth_date": str(patient.get("birth_date") or ""), "ecw_appointment_date": service_date}
    if not all(values[key] for key in ("patient_first_name", "patient_last_name", "patient_birth_date")):
        raise HTTPException(422, "Confirm patient name and birth date before reading eClinicalWorks.")
    return {key: str(value) for key, value in values.items()}


def _ecw_conflicts(case: dict, session: dict, inputs: dict, source: dict) -> list[str]:
    data, fields = case["commercial"], session["intake"]["fields"]
    network = _network(session["practice_id"], data["provider_network_id"])
    relationship = {"self": "18", "spouse": "01", "child": "19", "other": "G8"}.get(str(fields.get("relationship") or ""), fields.get("relationship"))
    checks = (("service date", inputs.get("service_date"), data["service_date"]),
              ("payer", inputs.get("payer_id"), data["payer_connection_id"]),
              ("billing provider NPI", source.get("billing_provider", {}).get("npi"), network["billing_npi"]),
              ("rendering provider NPI", inputs.get("rendering_provider", {}).get("npi"), network["rendering_npi"]),
              ("place of service", inputs.get("place_of_service"), network["place_of_service"]),
              ("member ID", inputs.get("member_id"), fields.get("member_id")),
              ("subscriber relationship", inputs.get("relationship"), relationship))
    conflicts = [f"eClinicalWorks {label} does not match the confirmed intake." for label, observed, expected
                 in checks if not observed or not expected or str(observed).strip().casefold() != str(expected).strip().casefold()]
    # eCW may omit charge items, phone, or historical visits. Those inputs are
    # needed by other integrations, but are not prerequisites for this Stedi
    # estimate.  Only the explicit appointment/coverage comparison can block it.
    return list(dict.fromkeys(conflicts))


def _record_evidence(case: dict, session: dict, data: dict[str, Any], raw_object_key: str | None = None) -> dict:
    commercial_case = case["commercial"]
    if data["requested_dos"] != commercial_case["service_date"]:
        raise commercial.CommercialError("Eligibility evidence must use the case service date.")
    network = _network(session["practice_id"], commercial_case["provider_network_id"])
    context = {"practice_id": session["practice_id"], "patient_id": session["patient_id"], "coverage_id": session["intake"]["id"], "subscriber_relationship": session["intake"]["fields"].get("relationship", ""), "payer_connection_id": commercial_case["payer_connection_id"], "service_date": commercial_case["service_date"], "rendering_npi": network["rendering_npi"], "place_of_service": network["place_of_service"], "service_category": commercial_case["service_category"]}
    if raw_object_key:
        data["raw_object_key"] = raw_object_key
    evidence = commercial_store.save_evidence(session["practice_id"], case["id"], commercial.context_fingerprint(os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), context), data)
    commercial_case["evidence_id"] = evidence["id"]; commercial_case["request_state"] = "complete"
    benefits = data["benefits"]
    case["review_tasks"] = (["Prior authorization is required for the returned benefit. Confirm approval before estimating."] if benefits.get("authorization_signal") == "required" else
                            ["The payer could not determine prior-authorization requirements. Verify before estimating."] if benefits.get("authorization_signal") == "unknown" else
                            ["A referral requirement was returned for the selected benefit. Confirm it before estimating."] if benefits.get("referral_signal") == "required" else [])
    return evidence


def _subscriber_identity(fields: dict[str, Any], patient: dict[str, Any]) -> tuple[str, str, str, bool]:
    relationship = str(fields.get("relationship") or "self")
    is_self = relationship in {"self", "18"}
    name = str(fields.get("subscriber_name") or "").strip() or f"{patient.get('given_name') or ''} {patient.get('family_name') or ''}".strip()
    parts = name.split()
    birth_date = str(patient.get("birth_date") if is_self else fields.get("subscriber_birth_date") or "")
    if len(parts) < 2 or not birth_date:
        raise HTTPException(422, "Confirm the card member ID, policyholder first and last name, and policyholder birth date before sending Stedi eligibility.")
    return parts[0], " ".join(parts[1:]), birth_date, is_self


@router.get("/configuration")
async def configuration(request: Request) -> dict:
    session = await _session(request)
    return _config(session["practice_id"])


@router.get("/appointments")
async def ecw_appointments(request: Request) -> dict:
    """List the confirmed patient's scheduled visits without creating an eligibility check."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        values = await ecw_backend.list_appointments(_ecw_identity(session, date.today().isoformat()), _ecw_practice_for_nmct(session["practice_id"]))
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"appointments": values}


@router.get("/history")
async def ecw_completed_appointments(request: Request) -> dict:
    """List prior eCW visits and a matching saved estimate without recalculating."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        visits = await ecw_backend.list_completed_appointments(
            _ecw_identity(session, date.today().isoformat()), _ecw_practice_for_nmct(session["practice_id"]),
        )
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    workflows = session["intake"].get("workflows", {})
    case_ids = [workflows.get("commercial"), *(workflows.get(_commercial_slot(visit["id"])) for visit in visits)]
    saved_cases = await run_in_threadpool(store.get_cases, case_ids, session["patient_id"], session["share_manifest_id"], **_scope(session))
    by_appointment = {str(((case.get("commercial") or {}).get("ecw") or {}).get("appointment_id") or ""): case
                      for case in saved_cases if case.get("source_mode") == "commercial"}
    return {"visits": [_past_visit_with_saved_estimate(visit, by_appointment.get(visit["id"])) for visit in visits]}


@router.post("/cases/from-ecw")
async def start_from_ecw(value: EcwStartInput, request: Request) -> Any:
    """Create an NMCT estimate case from the selected eCW appointment only."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(_ecw_identity(session, date.today().isoformat()), _ecw_practice_for_nmct(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    payer, network, profile = _nmct_path(session["practice_id"], inputs, source)
    lines, line_tasks = _ecw_coded_lines(session["practice_id"], profile, network, inputs["service_date"], inputs)
    commercial_data = {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"],
                       "service_date": inputs["service_date"], "service_category": profile["service_category"], "lines": lines,
                       "evidence_id": None, "request_state": "not_requested"}
    conflicts = _ecw_conflicts({"commercial": commercial_data}, session, inputs, source)
    workflow = {"source_mode": "commercial", "commercial": {**commercial_data, "ecw": _ecw_case_data(inputs, source, conflicts)},
                "review_tasks": conflicts or line_tasks or ["Ready to check Stedi eligibility using the eClinicalWorks-coded services."]}
    binding = hashlib.sha256(f"commercial-v1|{payer['id']}|{network['id']}|{profile['id']}|{inputs['service_date']}|{value.appointment_id}".encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], workflow_slot=_commercial_slot(value.appointment_id), **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _response(_public(case))


@router.post("/cases/{case_id}/ecw-intake")
async def read_ecw_appointment(case_id: str, request: Request) -> Any:
    """Read the scheduled appointment and fail closed on any card/eCW mismatch."""
    session = await _session(request)
    case = await run_in_threadpool(_load, case_id, session)
    data = case["commercial"]
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, data["service_date"]),
            _ecw_practice(session["practice_id"], data["payer_connection_id"], data["provider_network_id"]),
        )
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    conflicts = _ecw_conflicts(case, session, inputs, source)
    data["ecw"] = _ecw_case_data(inputs, source, conflicts)
    case["review_tasks"] = (conflicts or [
        "Confirm eClinicalWorks-suggested CPT/HCPCS codes and units; they are not automatically selected.",
        "Run Stedi eligibility or record a staff-reviewed saved payer response.",
    ])
    return _response(_public(await run_in_threadpool(_save, case, session, "ecw-appointment-read")))


@router.post("/ecw/saved-eligibility-probe")
async def saved_eligibility_probe(request: Request) -> dict:
    """Administrator-only, read-only proof of saved TriZetto/eCW evidence access."""
    session = await _session(request)
    _admin(session)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        # This only searches saved resources. It cannot create an eligibility transaction.
        return await ecw_backend.probe_saved_eligibility(_ecw_identity(session, date.today().isoformat()))
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/connections")
async def create_connection(value: ConnectionInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_connection(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/networks")
async def create_network(value: NetworkInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_network(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/profiles")
async def create_profile(value: ProfileInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_profile(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/rates")
async def import_rates(request: Request, payer_connection_id: str, source_type: str, source_reference: str, effective_from: str, effective_to: str | None = None, change_ceiling_bps: int = 0, file: UploadFile = File(...)) -> dict:
    session = await _session(request); _admin(session)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024: raise HTTPException(413, "Rate imports must be 20 MB or smaller.")
    try:
        rows = commercial.parse_rate_csv(content, file.filename or "rates.csv")
        return commercial_store.import_rates(session["practice_id"], payer_connection_id, source_type, source_reference, hashlib.sha256(content).hexdigest(), effective_from, effective_to, change_ceiling_bps, rows)
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/rates/{version_id}/activate")
async def activate_rates(version_id: str, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.activate_rates(session["practice_id"], version_id)
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/cases")
async def start_case(value: StartInput, request: Request) -> Any:
    session = await _session(request)
    try: date.fromisoformat(value.service_date)
    except ValueError: raise HTTPException(422, "Service date is invalid.") from None
    practice_id = session["practice_id"]
    payer = commercial_store.connection(practice_id, value.payer_connection_id)
    network, profile = _network(practice_id, value.provider_network_id), commercial_store.profile(practice_id, value.profile_id)
    if not payer or not profile or profile["payer_connection_id"] != payer["id"] or profile["provider_network_id"] != network["id"]:
        raise HTTPException(422, "Payer, provider network, and estimation profile must be a matching practice configuration.")
    if payer["category"] != "commercial_medical" or payer["status"] == "suspended" or network["status"] != "verified":
        raise HTTPException(409, "This payer/provider configuration is not eligible for commercial estimation.")
    workflow = {"source_mode": "commercial", "commercial": {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"], "service_date": value.service_date, "service_category": profile["service_category"], "lines": [], "evidence_id": None, "request_state": "not_requested"}, "review_tasks": ["Confirm expected CPT/HCPCS codes and units before checking eligibility."]}
    binding = hashlib.sha256(f"commercial-v1|{payer['id']}|{network['id']}|{profile['id']}|{value.service_date}".encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], **_scope(session))
    except store.StaffCaseConflict as exc: raise HTTPException(409, str(exc)) from exc
    return _response(_public(case))


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> Any:
    return _response(_public(await run_in_threadpool(_load, case_id, await _session(request))))


@router.post("/cases/{case_id}/services")
async def set_services(case_id: str, value: ServicesInput, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    profile, network = commercial_store.profile(session["practice_id"], data["profile_id"]), _network(session["practice_id"], data["provider_network_id"])
    try:
        matched = commercial_store.matching_rates(session["practice_id"], profile or {}, network, data["service_date"], [line.model_dump() for line in value.lines])
        by_code = {row["code"]: row for row in matched}
        data["lines"] = [{"code": line.code.upper(), "units": line.units, "rate_id": by_code.get(line.code.upper(), {}).get("id"), "allowed_cents": by_code.get(line.code.upper(), {}).get("allowed_cents"), "cash_cents": by_code.get(line.code.upper(), {}).get("cash_cents")} for line in value.lines]
        missing = [line["code"] for line in data["lines"] if line["allowed_cents"] is None]
        case["review_tasks"] = ([f"No active contracted rate matches: {', '.join(missing)}."] if missing else ["Run Stedi eligibility or record verified payer evidence."])
        return _response(_public(await run_in_threadpool(_save, case, session, "services-confirmed")))
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/cases/{case_id}/evidence")
async def record_evidence(case_id: str, value: EvidenceInput, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    try:
        _record_evidence(case, session, value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc
    return _response(_public(await run_in_threadpool(_save, case, session, "eligibility-evidence-recorded")))


@router.post("/cases/{case_id}/eligibility")
async def stedi_eligibility(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if data.get("request_state") == "pending":
        raise HTTPException(409, "A Stedi eligibility request is already pending. Check the saved case before retrying.")
    if not data.get("lines") or any(line.get("allowed_cents") is None for line in data["lines"]):
        raise HTTPException(409, "Confirm the expected CPT/HCPCS codes, units, and matching clinic rates before a paid Stedi check.")
    if os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        raise HTTPException(409, "Stedi eligibility is disabled. Set STEDI_EXECUTION_MODE=commercial_pilot and STEDI_LIVE_ELIGIBILITY_ENABLED=true only when this clinic is ready for paid production checks.")
    if not object_store.enabled():
        raise HTTPException(503, "Protected evidence storage must be configured before a live Stedi request.")
    api_key = os.getenv("STEDI_API_KEY", "").strip()
    if not api_key: raise HTTPException(503, "Stedi production credentials are not configured on the backend.")
    payer, network = commercial_store.connection(session["practice_id"], data["payer_connection_id"]), _network(session["practice_id"], data["provider_network_id"])
    fields, patient = session["intake"]["fields"], session["intake"]["patient"]
    member_id = str(fields.get("member_id") or "").strip()
    first, last, subscriber_birth_date, is_self = _subscriber_identity(fields, patient)
    if not member_id:
        raise HTTPException(422, "Confirm the card member ID before sending Stedi eligibility.")
    rules = payer["rules"]
    service_day = date.fromisoformat(data["service_date"])
    if rules.get("future_dos_current_month") and service_day > date.today() and (service_day.year, service_day.month) != (date.today().year, date.today().month):
        raise HTTPException(422, "This payer permits future eligibility dates only through the end of the current month.")
    stc = rules.get("service_type_code")
    if not isinstance(stc, str) or not stc: raise HTTPException(422, "Configure exactly one Stedi service-type code for this payer/profile before eligibility.")
    body: dict[str, Any] = {"payerId": payer["stedi_payer_id"], "provider": {"npi": network["rendering_npi"], "name": {"organization": network["organization_name"]}}, "subscriber": {"memberId": member_id, "dateOfBirth": subscriber_birth_date, "name": {"person": {"firstName": first, "lastName": last}}}, "encounter": {"dates": {"service": data["service_date"]}, "services": [{"system": "STC", "value": stc}]}}
    if not is_self: body["dependent"] = {"dateOfBirth": patient["birth_date"], "name": {"person": {"firstName": patient["given_name"], "lastName": patient["family_name"]}}}
    data["request_state"] = "pending"; case = await run_in_threadpool(_save, case, session, "stedi-request-pending")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response = await client.post(STEDI_URL, headers={"Authorization": api_key, "Content-Type": "application/json"}, json=body)
        payload = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi request outcome is indeterminate; check Stedi activity before retrying."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-indeterminate")))
    if response.is_error or not isinstance(payload, dict):
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi/payer did not return usable eligibility. Review payer response in the protected audit record."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-error")))
    raw_key = None
    if object_store.enabled(): raw_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case_id, "stedi-eligibility.json", json.dumps(payload).encode(), "application/json")
    # The normalizer only accepts Stedi's documented individual, remaining,
    # in-network benefit rows. Missing/ambiguous values stay review-required.
    benefits = commercial.normalize_stedi_271(payload, stc, network["status"] == "verified")
    evidence = EvidenceInput(source="stedi", response_reference=str(payload.get("id") or "Stedi eligibility response"), requested_dos=data["service_date"], payer_checked_at=_now(), benefits=benefits)
    _record_evidence(case, session, evidence.model_dump(), raw_key)
    return _response(_public(await run_in_threadpool(_save, case, session, "stedi-evidence-recorded")))


@router.post("/cases/{case_id}/calculate")
async def calculate(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if not data.get("evidence_id"): raise HTTPException(409, "Record usable eligibility evidence before calculating.")
    if case.get("review_tasks"):
        raise HTTPException(409, "Resolve the returned authorization or referral requirement before calculating.")
    evidence, profile = commercial_store.evidence(session["practice_id"], data["evidence_id"]), commercial_store.profile(session["practice_id"], data["profile_id"])
    if not evidence or not profile: raise HTTPException(409, "The selected evidence or profile is no longer available.")
    try:
        case["estimate"] = commercial.estimate(data["lines"], evidence["normalized"], profile["rules"]); case["review_tasks"] = []
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-calculated")))
    except commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-review-required")))
