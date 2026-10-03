"""Staff-only Stedi commercial eligibility and estimate workflow.

This route is deliberately separate from the existing Availity workflow.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store
from app.insurance import commercial, commercial_store, ecw_backend, golden, store
from app.logs.logger import log_info, log_warning


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


class EcwVisitInput(BaseModel):
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
    source: Literal["staff_export", "ecw_document", "stedi"]
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
    result = {key: case.get(key) for key in ("id", "created_at", "revision", "source_mode", "commercial", "ecw_summary", "estimate", "review_tasks") if key in case}
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


def _identity(session: dict) -> dict[str, str]:
    patient = session["intake"]["patient"]
    return {"patient_first_name": patient["given_name"], "patient_middle_initial": session["intake"]["fields"].get("middle_initial", ""),
            "patient_last_name": patient["family_name"], "patient_birth_date": patient["birth_date"]}


def _ecw_practice(practice_id: str) -> dict:
    networks = commercial_store.list_networks(practice_id)
    providers = {npi: {} for row in networks for npi in (row["rendering_npi"], row["billing_npi"])}
    locations: dict[str, dict] = {}
    for row in networks:
        key = row["location_id"]
        candidate = {"billing_provider_npi": row["billing_npi"], "place_of_service": row["place_of_service"]}
        if key not in locations:
            locations[key] = candidate
        elif locations[key] != candidate:
            locations[key] = {}  # Ambiguous location mappings must not supply a guessed provider/POS.
    return {"providers": providers, "locations": locations}


async def _read_ecw(session: dict, appointment_id: str) -> tuple[dict, dict]:
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks practice connection is unavailable.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(_identity(session), _ecw_practice(session["practice_id"]), appointment_id)
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    return inputs, source


def _visit_summary(inputs: dict, source: dict) -> dict:
    lines = [{"code": row["code"], "units": row.get("units"), "status": row.get("service_status")}
             for row in inputs.get("lines", [])]
    return {"appointment_id": source.get("appointment_id"), "service_date": inputs.get("service_date"),
            "status": source.get("appointment_status"), "reason": inputs.get("reason"),
            "payer": source.get("payer"), "member_available": bool(inputs.get("member_id")),
            "rendering_npi": inputs.get("rendering_provider", {}).get("npi"),
            "location": source.get("location"), "lines": lines,
            "active_coverages": source.get("active_coverages", 0)}


def _match_connection(practice_id: str, payer_name: str) -> dict:
    key = re.sub(r"[^a-z0-9]", "", payer_name.casefold())
    matches = [row for row in commercial_store.list_connections(practice_id)
               if key and key in {re.sub(r"[^a-z0-9]", "", str(name).casefold())
                                  for name in [row["display_name"], *row.get("aliases", [])]}]
    if len(matches) != 1 or matches[0]["category"] != "commercial_medical" or matches[0]["status"] != "staff_guidance_enabled":
        raise commercial.CommercialError("Map this eCW payer to one enabled commercial Stedi connection before estimating.")
    return matches[0]


def _match_network_and_profile(practice_id: str, payer: dict, inputs: dict, source: dict) -> tuple[dict, dict]:
    service_date = inputs["service_date"]
    rendering_npi = inputs.get("rendering_provider", {}).get("npi")
    location_id = source.get("location_id")
    plan = re.sub(r"[^a-z0-9]", "", str(source.get("plan") or "").casefold())
    networks = [row for row in commercial_store.list_networks(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["status"] == "verified"
                and row["rendering_npi"] == rendering_npi and row["location_id"] == location_id
                and (not row.get("plan_matcher") or (plan and re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
                and row["effective_from"] <= service_date <= (row.get("effective_to") or "9999-12-31")]
    if inputs.get("place_of_service"):
        networks = [row for row in networks if row["place_of_service"] == inputs["place_of_service"]]
    if len(networks) != 1:
        raise commercial.CommercialError("Verify one provider, plan, location, POS, and effective network contract for this appointment.")
    network = networks[0]
    profiles = [row for row in commercial_store.list_profiles(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["provider_network_id"] == network["id"]
                and row["status"] == "staff_guidance_enabled" and row.get("plan_matcher", "") == network.get("plan_matcher", "")]
    if len(profiles) != 1:
        raise commercial.CommercialError("Enable one reviewed estimation profile for this payer, plan, and provider network.")
    return network, profiles[0]


def _coverage_snapshot(inputs: dict, source: dict, fields: dict) -> dict:
    def compact(value: object) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())
    card_member, ecw_member = str(fields.get("member_id") or "").strip(), str(inputs.get("member_id") or "").strip()
    if card_member and ecw_member and compact(card_member) != compact(ecw_member):
        raise commercial.CommercialError("The card member ID differs from eCW coverage. Correct the patient or policy before checking Stedi.")
    subscriber = inputs.get("subscriber") or {}
    name = str(fields.get("subscriber_name") or "").split()
    relationship = inputs.get("relationship") or ({"self": "18", "spouse": "01", "child": "19", "other": "G8"}.get(fields.get("relationship")) if fields.get("member_id") else None)
    patient = inputs["patient"]
    snapshot = {"member_id": ecw_member or card_member, "relationship": relationship,
                "subscriber_first": subscriber.get("firstName") or (name[0] if name else None),
                "subscriber_last": subscriber.get("lastName") or (name[-1] if len(name) > 1 else None),
                "subscriber_birth_date": subscriber.get("birthDate") or fields.get("subscriber_birth_date"),
                "patient_first": patient.get("patient_first_name"), "patient_last": patient.get("patient_last_name"),
                "patient_birth_date": patient.get("patient_birth_date"), "payer_name": source.get("payer") or fields.get("payer"),
                "active_coverages": source.get("active_coverages", 0)}
    if relationship == "18":
        snapshot["subscriber_first"] = snapshot["subscriber_first"] or snapshot["patient_first"]
        snapshot["subscriber_last"] = snapshot["subscriber_last"] or snapshot["patient_last"]
        snapshot["subscriber_birth_date"] = snapshot["subscriber_birth_date"] or snapshot["patient_birth_date"]
    for label, card, connected in (("subscriber name", fields.get("subscriber_name"),
                                    " ".join(str(subscriber.get(key) or "") for key in ("firstName", "lastName"))),
                                   ("subscriber birth date", fields.get("subscriber_birth_date"), subscriber.get("birthDate"))):
        if card and connected and compact(card) != compact(connected):
            raise commercial.CommercialError(f"The card {label} differs from eCW coverage. Review the policy before checking Stedi.")
    return snapshot


def stedi_request_body(payer: dict, network: dict, coverage: dict, service_date: str, stc: str) -> dict:
    needed = ("member_id", "relationship", "subscriber_first", "subscriber_last", "subscriber_birth_date",
              "patient_first", "patient_last", "patient_birth_date")
    if any(not coverage.get(key) for key in needed):
        raise commercial.CommercialError("eCW or the confirmed card is missing member, subscriber, relationship, or patient details required for Stedi.")
    body = {"payerId": payer["stedi_payer_id"],
            "provider": {"npi": network["rendering_npi"], "name": {"organization": network["organization_name"]}},
            "subscriber": {"memberId": coverage["member_id"], "dateOfBirth": coverage["subscriber_birth_date"],
                           "name": {"person": {"firstName": coverage["subscriber_first"], "lastName": coverage["subscriber_last"]}}},
            "encounter": {"dates": {"service": service_date}, "services": [{"system": "STC", "value": stc}]}}
    if coverage["relationship"] not in {"18", "self"}:
        body["dependent"] = {"dateOfBirth": coverage["patient_birth_date"],
                             "name": {"person": {"firstName": coverage["patient_first"], "lastName": coverage["patient_last"]}}}
    return body


def _ecw_binding(case: dict) -> str:
    return hashlib.sha256(json.dumps({"ecw": case["ecw"], "commercial": case["commercial"]}, sort_keys=True).encode()).hexdigest()


def _evidence_context(case: dict, session: dict, network: dict) -> dict:
    data, ecw = case["commercial"], case["ecw"]
    return {"practice_id": session["practice_id"], "patient_id": session["patient_id"],
            "coverage_id": session["intake"]["id"], "subscriber_relationship": ecw["coverage"].get("relationship"),
            "payer_connection_id": data["payer_connection_id"], "service_date": data["service_date"],
            "rendering_npi": network["rendering_npi"], "place_of_service": network["place_of_service"],
            "service_category": data["service_category"], "appointment_id": ecw["appointment_id"],
            "member_id": ecw["coverage"].get("member_id"), "ecw_coverage_id": ecw.get("coverage_id")}


async def _verify_ecw_fresh(case: dict, session: dict) -> None:
    ecw = case.get("ecw")
    if not ecw:
        raise commercial.CommercialError("Start a new estimate from a confirmed eCW appointment.")
    inputs, source = await _read_ecw(session, ecw["appointment_id"])
    current = _coverage_snapshot(inputs, source, session["intake"]["fields"])
    if (current != ecw["coverage"] or source.get("patient_id") != ecw["patient_id"]
            or source.get("coverage_id") != ecw.get("coverage_id")
            or source.get("location_id") != ecw.get("location_id") or source.get("plan") != ecw.get("plan")
            or inputs.get("rendering_provider", {}).get("npi") != ecw.get("rendering_npi")
            or inputs.get("service_date") != case["commercial"]["service_date"]
            or _visit_summary(inputs, source)["lines"] != case["ecw_summary"]["lines"]):
        raise commercial.CommercialError("eCW appointment or coverage changed. Start a new estimate from the current visit.")


def _record_evidence(case: dict, session: dict, data: dict[str, Any], raw_object_key: str | None = None) -> dict:
    commercial_case = case["commercial"]
    if data["requested_dos"] != commercial_case["service_date"]:
        raise commercial.CommercialError("Eligibility evidence must use the case service date.")
    network = _network(session["practice_id"], commercial_case["provider_network_id"])
    context = _evidence_context(case, session, network)
    if raw_object_key:
        data["raw_object_key"] = raw_object_key
    evidence = commercial_store.save_evidence(session["practice_id"], case["id"], commercial.context_fingerprint(os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), context), data)
    commercial_case["evidence_id"] = evidence["id"]; commercial_case["request_state"] = "complete"; case["estimate"] = None
    case["review_tasks"] = ["Confirm referral and authorization status, if the payer requires either."]
    return evidence


@router.get("/configuration")
async def configuration(request: Request) -> dict:
    session = await _session(request)
    return _config(session["practice_id"])


@router.get("/ecw/appointments")
async def ecw_appointments(request: Request) -> dict:
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks practice connection is unavailable.")
    try:
        visits = await ecw_backend.list_appointments(_identity(session), _ecw_practice(session["practice_id"]))
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"appointments": visits}


@router.post("/ecw/preview")
async def ecw_preview(value: EcwVisitInput, request: Request) -> dict:
    session = await _session(request)
    inputs, source = await _read_ecw(session, value.appointment_id)
    return _visit_summary(inputs, source)


@router.post("/cases/from-ecw")
async def start_from_ecw(value: EcwVisitInput, request: Request) -> Any:
    session = await _session(request)
    inputs, source = await _read_ecw(session, value.appointment_id)
    practice_id = session["practice_id"]
    try:
        coverage = _coverage_snapshot(inputs, source, session["intake"]["fields"])
        payer = _match_connection(practice_id, str(coverage.get("payer_name") or ""))
        if session["intake"]["fields"].get("payer"):
            card_payer = _match_connection(practice_id, session["intake"]["fields"]["payer"])
            if card_payer["id"] != payer["id"]:
                raise commercial.CommercialError("The card payer differs from eCW coverage. Review the policy before checking Stedi.")
        network, profile = _match_network_and_profile(practice_id, payer, inputs, source)
        planned = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled"]
        lines = []
        for row in planned:
            units = str(row.get("units") or "")
            if not units.isdigit() or not 1 <= int(units) <= 10000 or row.get("modifiers"):
                raise commercial.CommercialError("Confirm whole-number units and modifiers for every eCW planned service before estimating.")
            lines.append({"code": row["code"], "units": int(units)})
        matched = commercial_store.matching_rates(practice_id, profile, network, inputs["service_date"], lines)
        by_code = {row["code"]: row for row in matched}
        priced = [{**line, "rate_id": by_code.get(line["code"], {}).get("id"),
                   "allowed_cents": by_code.get(line["code"], {}).get("allowed_cents"),
                   "cash_cents": by_code.get(line["code"], {}).get("cash_cents")} for line in lines]
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    workflow = {"source_mode": "commercial", "commercial": {"payer_connection_id": payer["id"], "provider_network_id": network["id"],
                "profile_id": profile["id"], "service_date": inputs["service_date"], "service_category": profile["service_category"],
                "lines": priced, "evidence_id": None, "request_state": "not_requested"},
                "ecw": {"patient_id": source["patient_id"], "appointment_id": value.appointment_id,
                        "coverage_id": source.get("coverage_id"), "location_id": source.get("location_id"),
                        "rendering_npi": inputs.get("rendering_provider", {}).get("npi"), "plan": source.get("plan"), "coverage": coverage},
                "ecw_summary": _visit_summary(inputs, source),
                "review_tasks": (["eCW did not return confirmed CPT/HCPCS services. A clinician or coder must confirm them."] if not priced else
                                 [f"No approved contracted rate for {', '.join(line['code'] for line in priced if line['allowed_cents'] is None)}."]
                                 if any(line["allowed_cents"] is None for line in priced) else
                                 ["Ready to check Stedi eligibility for this scheduled visit."])}
    binding = _ecw_binding(workflow)
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding, workflow,
                                       expected_intake_updated_at=session["intake"]["updated_at"], **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    log_info("Commercial estimator", "eCW estimate case created: case=%s service_lines=%d", case["id"], len(priced))
    return _response(_public(case))


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
    if data.get("request_state") != "not_requested":
        raise HTTPException(409, "A payer check already exists. Start a new intake if the planned services changed.")
    if not case.get("ecw"):
        raise HTTPException(409, "Start this estimate from a confirmed eCW appointment.")
    try:
        await _verify_ecw_fresh(case, session)
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    profile, network = commercial_store.profile(session["practice_id"], data["profile_id"]), _network(session["practice_id"], data["provider_network_id"])
    try:
        matched = commercial_store.matching_rates(session["practice_id"], profile or {}, network, data["service_date"], [line.model_dump() for line in value.lines])
        by_code = {row["code"]: row for row in matched}
        data["lines"] = [{"code": line.code.upper(), "units": line.units, "rate_id": by_code.get(line.code.upper(), {}).get("id"), "allowed_cents": by_code.get(line.code.upper(), {}).get("allowed_cents"), "cash_cents": by_code.get(line.code.upper(), {}).get("cash_cents")} for line in value.lines]
        data["evidence_id"] = None; data["request_state"] = "not_requested"; case["estimate"] = None
        missing = [line["code"] for line in data["lines"] if line["allowed_cents"] is None]
        case["review_tasks"] = ([f"No active contracted rate matches: {', '.join(missing)}."] if missing else ["Run Stedi eligibility or record verified payer evidence."])
        return _response(_public(await run_in_threadpool(_save, case, session, "services-confirmed")))
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/cases/{case_id}/evidence")
async def record_evidence(case_id: str, value: EvidenceInput, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if value.source == "stedi":
        raise HTTPException(403, "Stedi evidence must come directly from the Stedi eligibility response.")
    if not case.get("ecw"):
        raise HTTPException(409, "Start this review from a confirmed eCW appointment.")
    try:
        reviewed = value.model_dump(); reviewed["benefits"]["requires_review"] = True
        _record_evidence(case, session, reviewed)
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc
    return _response(_public(await run_in_threadpool(_save, case, session, "eligibility-evidence-recorded")))


@router.post("/cases/{case_id}/eligibility")
async def stedi_eligibility(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if not case.get("ecw"):
        raise HTTPException(409, "Start a new estimate from a confirmed eCW appointment.")
    if data.get("request_state") != "not_requested":
        raise HTTPException(409, "A Stedi request already exists for this case. Check its saved result before retrying.")
    try:
        await _verify_ecw_fresh(case, session)
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    if os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        raise HTTPException(409, "Stedi eligibility is disabled. Set STEDI_EXECUTION_MODE=commercial_pilot and STEDI_LIVE_ELIGIBILITY_ENABLED=true only when this clinic is ready for paid production checks.")
    if not object_store.enabled():
        raise HTTPException(503, "Protected evidence storage must be configured before a live Stedi request.")
    api_key = os.getenv("STEDI_API_KEY", "").strip()
    if not api_key: raise HTTPException(503, "Stedi production credentials are not configured on the backend.")
    if not os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", "").strip():
        raise HTTPException(503, "Protected eligibility evidence fingerprinting is not configured.")
    payer, network = commercial_store.connection(session["practice_id"], data["payer_connection_id"]), _network(session["practice_id"], data["provider_network_id"])
    profile = commercial_store.profile(session["practice_id"], data["profile_id"])
    if not payer or not profile or payer["status"] != "staff_guidance_enabled" or profile["status"] != "staff_guidance_enabled" or network["status"] != "verified":
        raise HTTPException(409, "Payer, provider network, and profile must be approved before Stedi eligibility.")
    if not network["effective_from"] <= data["service_date"] <= (network.get("effective_to") or "9999-12-31"):
        raise HTTPException(409, "Provider-network evidence is not effective for this appointment.")
    if not data["lines"] or any(line.get("allowed_cents") is None for line in data["lines"]):
        raise HTTPException(409, "Confirm CPT/HCPCS services and matching contracted rates before a paid Stedi check.")
    try:
        current_rates = commercial_store.matching_rates(session["practice_id"], profile, network, data["service_date"], data["lines"])
        if len(current_rates) != len(data["lines"]) or any(line.get("rate_id") != rate["id"] or line.get("allowed_cents") != rate["allowed_cents"] for line, rate in zip(data["lines"], current_rates)):
            raise commercial.CommercialError("Contracted rates changed. Start a new estimate from this appointment.")
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    coverage = case["ecw"]["coverage"]
    if coverage.get("active_coverages", 0) > 1:
        raise HTTPException(409, "Multiple active eCW coverages require coordination-of-benefits review.")
    rules = payer["rules"]
    service_day = date.fromisoformat(data["service_date"])
    if rules.get("future_dos_current_month") and service_day > date.today() and (service_day.year, service_day.month) != (date.today().year, date.today().month):
        raise HTTPException(422, "This payer permits future eligibility dates only through the end of the current month.")
    stc = rules.get("service_type_code")
    if not isinstance(stc, str) or not stc: raise HTTPException(422, "Configure exactly one Stedi service-type code for this payer/profile before eligibility.")
    try:
        body = stedi_request_body(payer, network, coverage, data["service_date"], stc)
    except commercial.CommercialError as exc:
        raise HTTPException(422, str(exc)) from exc
    data["request_state"] = "pending"; case = await run_in_threadpool(_save, case, session, "stedi-request-pending")
    started = time.monotonic()
    log_info("Commercial estimator", "Stedi eligibility started: case=%s service_lines=%d", case_id, len(data["lines"]))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response = await client.post(STEDI_URL, headers={"Authorization": api_key, "Content-Type": "application/json"}, json=body)
        payload = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        log_warning("Commercial estimator", "Stedi eligibility indeterminate: case=%s duration_ms=%d", case_id, (time.monotonic() - started) * 1000)
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi request outcome is indeterminate; check Stedi activity before retrying."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-indeterminate")))
    if response.is_error or not isinstance(payload, dict):
        log_warning("Commercial estimator", "Stedi eligibility rejected: case=%s http_status=%s duration_ms=%d", case_id, response.status_code, (time.monotonic() - started) * 1000)
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi/payer did not return usable eligibility. Review payer response in the protected audit record."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-error")))
    log_info("Commercial estimator", "Stedi eligibility response: case=%s http_status=%s transaction_id=%s duration_ms=%d", case_id, response.status_code, payload.get("id"), (time.monotonic() - started) * 1000)
    raw_key = None
    if object_store.enabled(): raw_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case_id, "stedi-eligibility.json", json.dumps(payload).encode(), "application/json")
    # The normalizer only accepts Stedi's documented individual, remaining,
    # in-network benefit rows. Missing/ambiguous values stay review-required.
    benefits = commercial.normalize_stedi_271(payload, stc, True, coverage.get("active_coverages", 0) > 1)
    returned_member = (payload.get("subscriber") or {}).get("memberId") if isinstance(payload.get("subscriber"), dict) else None
    if returned_member and re.sub(r"\W", "", str(returned_member)).casefold() != re.sub(r"\W", "", coverage["member_id"]).casefold():
        benefits["requires_review"] = True
        benefits["normalization_notes"].append("Stedi returned a different member ID.")
    evidence = EvidenceInput(source="stedi", response_reference=str(payload.get("id") or "Stedi eligibility response"), requested_dos=data["service_date"], payer_checked_at=_now(), expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), benefits=benefits)
    _record_evidence(case, session, evidence.model_dump(), raw_key)
    try:
        case["estimate"] = commercial.estimate(data["lines"], benefits, profile["rules"])
        case["review_tasks"] = []
    except commercial.CommercialError as exc:
        case["estimate"] = None
        case["review_tasks"] = [str(exc)]
    log_info("Commercial estimator", "Stedi eligibility processed: case=%s estimate_available=%s review_tasks=%d", case_id, case["estimate"] is not None, len(case["review_tasks"]))
    return _response(_public(await run_in_threadpool(_save, case, session, "stedi-evidence-recorded")))


@router.post("/cases/{case_id}/calculate")
async def calculate(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if not data.get("evidence_id"): raise HTTPException(409, "Record usable Stedi eligibility evidence before calculating.")
    try:
        await _verify_ecw_fresh(case, session)
        practice_id = session["practice_id"]
        evidence = commercial_store.evidence(practice_id, data["evidence_id"])
        profile = commercial_store.profile(practice_id, data["profile_id"])
        payer = commercial_store.connection(practice_id, data["payer_connection_id"])
        network = _network(practice_id, data["provider_network_id"])
        if not evidence or not profile or not payer or evidence["source"] != "stedi":
            raise commercial.CommercialError("A direct, protected Stedi response is required for an automatic insured estimate.")
        if payer["status"] != "staff_guidance_enabled" or network["status"] != "verified" or profile["status"] != "staff_guidance_enabled":
            raise commercial.CommercialError("Payer, network, or estimation profile is no longer approved.")
        if not network["effective_from"] <= data["service_date"] <= (network.get("effective_to") or "9999-12-31"):
            raise commercial.CommercialError("Provider-network evidence is not effective for this visit.")
        expiry = evidence.get("expires_at")
        if not expiry or datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
            raise commercial.CommercialError("Stedi benefits are stale. Start a new visit review and check eligibility again.")
        expected = commercial.context_fingerprint(os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), _evidence_context(case, session, network))
        if evidence["case_id"] != case_id or evidence["context_hash"] != expected or evidence["requested_dos"] != data["service_date"]:
            raise commercial.CommercialError("Stedi evidence does not match this patient, policy, provider, and appointment.")
        current_rates = commercial_store.matching_rates(practice_id, profile, network, data["service_date"], data["lines"])
        if len(current_rates) != len(data["lines"]) or any(line.get("rate_id") != rate["id"] or line.get("allowed_cents") != rate["allowed_cents"] for line, rate in zip(data["lines"], current_rates)):
            raise commercial.CommercialError("Contracted rates changed. Start a new estimate from the current visit.")
        case["estimate"] = commercial.estimate(data["lines"], evidence["normalized"], profile["rules"]); case["review_tasks"] = []
        log_info("Commercial estimator", "Estimate recalculated: case=%s estimate_available=true", case_id)
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-calculated")))
    except commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        log_warning("Commercial estimator", "Estimate requires review: case=%s", case_id)
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-review-required")))
