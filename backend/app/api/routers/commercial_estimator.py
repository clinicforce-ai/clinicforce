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
from pydantic import BaseModel, ConfigDict, Field

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store
from app.insurance import commercial, commercial_store, golden, store


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
    case["review_tasks"] = ["Confirm referral and authorization status, if the payer requires either."]
    return evidence


@router.get("/configuration")
async def configuration(request: Request) -> dict:
    session = await _session(request)
    return _config(session["practice_id"])


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
    if os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        raise HTTPException(409, "Stedi eligibility is disabled. Set STEDI_EXECUTION_MODE=commercial_pilot and STEDI_LIVE_ELIGIBILITY_ENABLED=true only when this clinic is ready for paid production checks.")
    if not object_store.enabled():
        raise HTTPException(503, "Protected evidence storage must be configured before a live Stedi request.")
    api_key = os.getenv("STEDI_API_KEY", "").strip()
    if not api_key: raise HTTPException(503, "Stedi production credentials are not configured on the backend.")
    payer, network = commercial_store.connection(session["practice_id"], data["payer_connection_id"]), _network(session["practice_id"], data["provider_network_id"])
    fields, patient = session["intake"]["fields"], session["intake"]["patient"]
    member_id, relationship = str(fields.get("member_id") or "").strip(), str(fields.get("relationship") or "self")
    subscriber_name = str(fields.get("subscriber_name") or "").strip() or f"{patient['given_name']} {patient['family_name']}"
    first, _, last = subscriber_name.partition(" ")
    if not member_id or not first or not last or not patient.get("birth_date"):
        raise HTTPException(422, "Confirm the card member ID, subscriber name, and birth date before sending Stedi eligibility.")
    rules = payer["rules"]
    service_day = date.fromisoformat(data["service_date"])
    if rules.get("future_dos_current_month") and service_day > date.today() and (service_day.year, service_day.month) != (date.today().year, date.today().month):
        raise HTTPException(422, "This payer permits future eligibility dates only through the end of the current month.")
    stc = rules.get("service_type_code")
    if not isinstance(stc, str) or not stc: raise HTTPException(422, "Configure exactly one Stedi service-type code for this payer/profile before eligibility.")
    body: dict[str, Any] = {"payerId": payer["stedi_payer_id"], "provider": {"npi": network["rendering_npi"], "name": {"organization": network["organization_name"]}}, "subscriber": {"memberId": member_id, "dateOfBirth": patient["birth_date"], "name": {"person": {"firstName": first, "lastName": last}}}, "encounter": {"dates": {"service": data["service_date"]}, "services": [{"system": "STC", "value": stc}]}}
    if relationship != "self": body["dependent"] = {"dateOfBirth": patient["birth_date"], "name": {"person": {"firstName": patient["given_name"], "lastName": patient["family_name"]}}}
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
    evidence, profile = commercial_store.evidence(session["practice_id"], data["evidence_id"]), commercial_store.profile(session["practice_id"], data["profile_id"])
    if not evidence or not profile: raise HTTPException(409, "The selected evidence or profile is no longer available.")
    try:
        case["estimate"] = commercial.estimate(data["lines"], evidence["normalized"], profile["rules"]); case["review_tasks"] = []
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-calculated")))
    except commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-review-required")))
