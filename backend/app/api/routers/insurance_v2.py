"""Patient-share-scoped eCW-to-Availity workflow."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.routers.insurance import _response, _session as _insurance_session, _scope
from app.api.settings import MAX_UPLOAD_BYTES
from app.core.patient_store import get_patient_profile
from app.core import object_store, staff_store
from app.insurance import availity_intake as intake, availity_results as results, cpt_rules, cpt_suggester, ecw_backend, golden, stedi, stedi_estimate, stedi_results, store
from app.insurance.v2_decisions import (
    demo_inputs,
    canonical,
    service_lines,
    refresh_missing,
    preserve_ecw_snapshot,
    request_missing,
    _suggestion_context,
    _ecw_coded_groups,
    _without_codes,
    build_service_suggestions,
    front_desk_summary,
    pricing_prerequisites,
)
from app.insurance.availity import Settings, gateway, PATHS, DEMO_SCENARIOS, unwrap
from app.logs.logger import log_info, log_warning


router = APIRouter(prefix="/api/doctor/insurance/v2", tags=["doctor-insurance-v2"])
Resource = Literal["coverage", "estimate", "procedure_estimate", "authorization", "referral"]
ECW_FETCH_TIMEOUT_SECONDS = 25


async def _session(request: Request) -> dict:
    session = await _insurance_session(request)
    if session.get("practice_id"):
        configured = os.getenv("STAFF_INTEGRATION_PRACTICE_IDS", "").strip()
        if configured:
            configured_practices = {value.strip() for value in configured.split(",") if value.strip()}
        else:
            configured_practices = {os.getenv("STAFF_INTEGRATION_PRACTICE_ID", "").strip()}
        if session["practice_id"] not in configured_practices:
            raise HTTPException(403, "Connected insurance is not configured for this clinic. Your intake is saved; use document review.")
    return session


def validate_staff_coverage(fields: dict, inputs: dict, source: dict, practice: dict) -> None:
    """Compare card details that can actually be confirmed independently of eCW."""
    subscriber = inputs.get("subscriber") or {}
    pairs = (("member ID", fields.get("member_id"), inputs.get("member_id")),
             ("group number", fields.get("group_number"), inputs.get("group_number")),
             ("subscriber name", fields.get("subscriber_name"), " ".join(str(subscriber.get(key) or "") for key in ("firstName", "lastName")).strip()),
             ("subscriber birth date", fields.get("subscriber_birth_date"), subscriber.get("birthDate")))
    for label, confirmed, connected in pairs:
        if confirmed and connected and intake.normalize(str(confirmed)) != intake.normalize(str(connected)):
            raise HTTPException(409, f"The eCW {label} differs from the confirmed intake. Review the coverage in eCW before checking benefits; your patient information is saved.")
    payer = intake.normalize(str(fields.get("payer") or ""))
    if not payer:
        return
    connected_id = intake.normalize(str(inputs.get("payer_id") or ""))
    connected_name = intake.normalize(str(source.get("payer") or ""))
    if payer in {connected_id, connected_name}:
        return
    # eCW did not identify a payer ID: retain the staff-confirmed card as the
    # source for the scanner fallback. A name alone is not a safe mismatch.
    if not connected_id:
        return
    mapped = {intake.normalize(value) for key, value in practice.get("payer_aliases", {}).items() if intake.normalize(key) == payer}
    if len(mapped) == 1 and (not connected_id or connected_id in mapped):
        return
    raise HTTPException(409, "The confirmed payer could not be matched to the eCW coverage. Ask the administrator to check the payer name and approved payer mapping, or use document review. Your card is saved.")


def apply_staff_card_coverage(fields: dict, inputs: dict, missing: list[str], practice: dict) -> bool:
    """Fill only eCW coverage fields absent from a staff-confirmed scanned card."""
    relationship = {"self": "18", "spouse": "01", "child": "19", "other": "G8"}.get(fields.get("relationship"), fields.get("relationship"))
    subscriber = inputs.setdefault("subscriber", {})
    if not inputs.get("relationship") and relationship in {"18", "01", "19", "G8"}:
        inputs["relationship"] = relationship
    if not inputs.get("member_id") and fields.get("member_id"):
        inputs["member_id"] = fields["member_id"]
    if not subscriber.get("memberId") and inputs.get("member_id"):
        subscriber["memberId"] = inputs["member_id"]
    if not inputs.get("group_number") and fields.get("group_number"):
        inputs["group_number"] = fields["group_number"]
    if not inputs.get("payer_id"):
        payer = intake.normalize(str(fields.get("payer") or ""))
        inputs["payer_id"] = next((value for name, value in practice.get("payer_aliases", {}).items()
                                    if payer and intake.normalize(name) == payer), None)
    if inputs.get("relationship") == "18":
        patient = inputs.get("patient") or {}
        for target, source in (("firstName", "patient_first_name"), ("lastName", "patient_last_name"), ("birthDate", "patient_birth_date")):
            if not subscriber.get(target) and patient.get(source):
                subscriber[target] = patient[source]
    elif fields.get("subscriber_name"):
        first, _, last = " ".join(str(fields["subscriber_name"]).split()).partition(" ")
        if not subscriber.get("firstName"):
            subscriber["firstName"] = first
        if not subscriber.get("lastName") and last:
            subscriber["lastName"] = last
        if not subscriber.get("birthDate") and fields.get("subscriber_birth_date"):
            subscriber["birthDate"] = fields["subscriber_birth_date"]
    complete = bool(inputs.get("member_id") and inputs.get("payer_id") and inputs.get("relationship") and
                    all(subscriber.get(key) for key in ("firstName", "lastName", "birthDate", "memberId")))
    if not complete:
        return False
    resolved = ("eClinicalWorks did not identify one active primary coverage for this appointment.",
                "The eClinicalWorks coverage is missing the subscriber identity, relationship or member ID required by Availity.",
                "Administrator: map the exact eClinicalWorks payer name or identifier")
    inputs["coverage_missing"] = [item for item in inputs.get("coverage_missing", []) if not item.startswith(resolved)]
    inputs["review_missing"] = [item for item in inputs.get("review_missing", []) if not item.startswith(resolved)]
    missing[:] = [item for item in missing if not item.startswith(resolved)]
    return True


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["Coverages-Complete-i", "Coverages-InProgress-i", "Coverages-Retrying-i", "Coverages-PayerError1-i", "Coverages-RequestError1-i"] = "Coverages-Complete-i"
    appointment_id: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9.\-]+$")


class ServiceChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    quantity: int = Field(ge=1, le=10_000)
    service_status: Literal["scheduled", "possible"]


class ConfirmServices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[ServiceChoice] = Field(min_length=1, max_length=20)


class ManualReview(BaseModel):
    """Staff-recorded payer confirmation for the Stedi benefits-only path."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["payer_portal", "payer_call"]
    network_status: Literal["in_network", "out_of_network", "unconfirmed"]
    network_reference: str = Field(default="", max_length=200)
    referral_status: Literal["unconfirmed", "not_required", "approved", "required"]
    referral_reference: str = Field(default="", max_length=200)
    authorization_status: Literal["unconfirmed", "not_required", "approved", "required"]
    authorization_reference: str = Field(default="", max_length=200)
    price_path: Literal["pending", "cash"]


def configuration() -> tuple[Settings, dict, str]:
    try:
        settings = Settings.read()
        try:
            practice = json.loads(os.getenv("AVAILITY_PRACTICE_CONFIG", "{}"))
        except json.JSONDecodeError:
            if settings.environment != "demo":
                raise
            practice = {}
        if not isinstance(practice, dict):
            raise ValueError
        providers = practice.get("providers", {})
        if not isinstance(providers, dict):
            raise ValueError
        invalid_providers = {k: v for k, v in providers.items()
                             if not re.fullmatch(r"[1-4]\d{9}", k) or not isinstance(v, dict)}
        if invalid_providers:
            if settings.environment != "demo":
                raise ValueError
            practice = {**practice, "providers": {k: v for k, v in providers.items() if k not in invalid_providers}}
        if not isinstance(practice.get("payer_aliases", {}), dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", v) for k, v in practice.get("payer_aliases", {}).items()):
            raise ValueError
        if not isinstance(practice.get("stedi_payer_ids", {}), dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", v) for k, v in practice.get("stedi_payer_ids", {}).items()):
            raise ValueError
        networks = practice.get("stedi_provider_networks", [])
        if not isinstance(networks, list) or any(not isinstance(entry, dict) or set(("payer_id", "provider_npi", "place_of_service", "plan_name", "status")) - set(entry) or
                                                 entry.get("status") not in {"in_network", "out_of_network"} or
                                                 not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ("payer_id", "provider_npi", "place_of_service", "plan_name"))
                                                 for entry in networks):
            raise ValueError
        if not isinstance(practice.get("locations", {}), dict) or any(not isinstance(k, str) or not k or not isinstance(v, dict) for k, v in practice.get("locations", {}).items()):
            raise ValueError
        if not isinstance(practice.get("enrolled_payers", []), list) or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value) for value in practice.get("enrolled_payers", [])):
            raise ValueError
        if "production_enabled" in practice and not isinstance(practice["production_enabled"], bool):
            raise ValueError
        ZoneInfo(practice.get("timezone", "America/New_York"))
        binding = hashlib.sha256((settings.binding + json.dumps(practice, sort_keys=True) + ecw_backend.binding()).encode()).hexdigest()
        return settings, practice, binding
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise HTTPException(503, "The Availity server configuration is invalid. Check environment, scope, URLs and practice settings.") from None


def stedi_temporary_enabled(settings: Settings, practice: dict) -> bool:
    """Stedi is an explicit bridge only; Availity production always wins."""
    return settings.environment == "demo" and practice.get("stedi_temporary_enabled") is True and stedi.Settings.read().mode == "production"


def _normalized(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def stedi_network(case: dict, practice: dict, coverage: dict) -> dict:
    inputs = case["inputs"]
    plans = {_normalized(plan.get("name") or plan.get("planName")) for plan in coverage.get("plans", [])}
    plans.discard("")
    if len(plans) > 1:
        return {"provider_network_status": "unconfirmed",
                "provider_network": "Stedi returned different plan names; identify the applicable medical product before using the clinic network matrix."}
    service_date = str(inputs.get("service_date") or "")
    matched = []
    for entry in practice.get("stedi_provider_networks", []):
        if (entry.get("payer_id") != inputs.get("payer_id") or entry.get("provider_npi") != (inputs.get("provider") or {}).get("npi") or
                entry.get("place_of_service") != inputs.get("place_of_service") or _normalized(entry.get("plan_name")) not in plans):
            continue
        if entry.get("effective_from") and str(entry["effective_from"]) > service_date:
            continue
        if entry.get("effective_to") and str(entry["effective_to"]) < service_date:
            continue
        matched.append(entry)
    statuses = {entry["status"] for entry in matched}
    if len(statuses) > 1:
        return {"provider_network_status": "unconfirmed",
                "provider_network": "Clinic network rows conflict for this payer, plan, provider, place of service and date; correct the matrix before quoting."}
    if statuses:
        status = statuses.pop()
        return {"provider_network_status": status,
                "provider_network": "Confirmed by clinic contract matrix" if status == "in_network" else "Confirmed out-of-network by clinic contract matrix"}
    return {"provider_network_status": "unconfirmed",
            "provider_network": "No clinic network row matches this payer, plan, provider, place of service and date."}


def public(case: dict) -> dict:
    # Raw payer responses and account configuration never leave the backend.
    visible = {k: case[k] for k in ("id", "created_at", "expires_at", "environment", "revision", "sources", "missing", "inputs", "discovery", "suggestions", "manual_review", "events") if k in case}
    if "inputs" in visible:
        visible["inputs"] = {k: v for k, v in visible["inputs"].items() if k not in {"submitter_id", "provider", "rendering_provider"}}
        provider = case["inputs"].get("provider", {})
        visible["inputs"]["provider"] = {k: provider.get(k) for k in ("npi", "lastName", "addressLine1", "city", "stateCode", "zipCode")}
    visible["sources"] = {k: {n: v for n, v in s.items() if n not in {"fields", "lines", "object_key"}} for k, s in case.get("sources", {}).items()}
    visible["transactions"] = {k: {n: v for n, v in t.items() if n not in {"payload", "request_body"}} for k, t in case.get("transactions", {}).items()}
    visible["missing_by_resource"] = {resource: request_missing(case, resource) for resource in ("coverage", "estimate", "procedure_estimate", "authorization", "referral")}
    visible["summary"] = front_desk_summary(case)
    return visible


async def load(case_id: str, session: dict, binding: str) -> dict:
    case = await run_in_threadpool(store.get_case, case_id, session["patient_id"], session["share_manifest_id"], **_scope(session))
    if not case or case.get("source_mode") not in {"availity", "stedi"}:
        raise HTTPException(404, "Insurance case not found or expired")
    if case.get("binding") != binding:
        raise HTTPException(409, "The Availity account, environment or practice settings changed. Start a new V2 case.")
    return case


async def patient_identity(session: dict) -> dict:
    if session.get("practice_id"):
        patient = session["intake"]["patient"]
        return {"patient_first_name": patient["given_name"], "patient_last_name": patient["family_name"],
                "patient_birth_date": patient["birth_date"], "gender": "U"}
    profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
    if not profile:
        raise HTTPException(422, "The QR patient profile is missing")
    return {"patient_first_name": profile.given_name, "patient_last_name": profile.family_name,
            "patient_birth_date": profile.birth_date, "gender": {"male": "M", "female": "F"}.get(profile.administrative_gender, "U")}


async def save(case: dict, session: dict, event: str) -> dict:
    case["events"] = [*case.get("events", []), {"stage": event, "at": datetime.now(timezone.utc).isoformat()}][-100:]
    saved = await run_in_threadpool(store.save_v2, case, session["patient_id"], session["share_manifest_id"], event, case["revision"], **_scope(session))
    if not saved:
        raise HTTPException(409, "This case changed in another request. Refresh the case before continuing.")
    return saved


def idle(case: dict) -> None:
    if case.get("operation_until", 0) > time.time() or any(t.get("outcome") == "submitting" for t in case["transactions"].values()):
        raise HTTPException(409, "A payer operation is in progress. Refresh the saved case; do not submit a duplicate.")


def require_payer_requests(case: dict) -> None:
    if case.get("source_mode") == "stedi":
        return
    if case.get("input_mode") == "ecw" and case.get("environment") != "production":
        raise HTTPException(409, "The live eClinicalWorks appointment is loaded, but Availity is still in demo. No patient data was submitted; activate the production Availity connection to run payer checks.")


def apply_fee_schedule(case: dict, source: dict) -> None:
    lines = case["inputs"].get("lines", [])
    if case.get("sources", {}).get("fees") and any(line.get("charge") is not None and not line.get("charge_source") for line in lines):
        # Older reviews did not distinguish imported charges from visit charges.
        # A matching amount is not sufficient evidence that the CSV supplied it.
        raise HTTPException(409, "This older review does not record the source of each billed charge. The existing fee file is unchanged. Start a new review before replacing it.")
    rates = {line["code"]: line for line in source["lines"]}
    for line in lines:
        rate = rates.get(line["code"], {})
        line.setdefault("charge_source", "visit" if line.get("charge") is not None else "fee_schedule")
        if line["charge_source"] != "visit":
            line["charge_source"] = "fee_schedule"
            line["charge"] = rate.get("charge")
        line["description"] = rate.get("description") or line.get("description")
        line["cash_cents"] = rate.get("cash_cents")
        line["allowed_cents"] = rate.get("allowed_cents")
        line["service_status"] = line.get("service_status") or rate.get("service_status") or "scheduled"
    case["sources"]["fees"] = source
    refresh_missing(case)


@router.get("/configuration")
async def get_configuration(request: Request):
    session = await _session(request)
    settings, practice, _ = configuration()
    temporary_stedi = stedi_temporary_enabled(settings, practice)
    staff_ecw = bool(session.get("practice_id")) and settings.environment == "demo" and not temporary_stedi
    return _response({"environment": settings.environment, "credentials_configured": bool(settings.client_id and settings.client_secret) or temporary_stedi,
                      "practice_configured": bool(practice.get("providers") and (practice.get("submitter_id") or temporary_stedi)),
                      "ecw_configured": ecw_backend.configured() if session.get("practice_id") or settings.environment == "production" or temporary_stedi else True,
                      "production_enabled": settings.environment == "production" and practice.get("production_enabled") is True,
                      "stedi_temporary_enabled": temporary_stedi,
                      "message": ("ClinicForce will load the live appointment, use Stedi eligibility, and calculate only a clinic-contract-rate estimate. Switching Availity to production disables this temporary path."
                                  if temporary_stedi else "ClinicForce will load the live appointment and available insurance details from eClinicalWorks."
                                  if staff_ecw else "Synthetic transactions only; demo results do not describe the QR patient."
                                  if settings.environment == "demo" else "ClinicForce loads the scheduled patient, coverage, providers and coded services from eClinicalWorks, then checks Availity and uses Stedi when Availity eligibility is unavailable.")})


@router.get("/cases/latest")
async def latest(request: Request):
    session = await _session(request)
    _, _, binding = configuration()
    case = await run_in_threadpool(store.latest_v2, session["patient_id"], session["share_manifest_id"], binding, **_scope(session))
    return _response({"case": public(case) if case else None})


@router.get("/appointments")
async def appointments(request: Request):
    session = await _session(request)
    settings, practice, _ = configuration()
    if not session.get("practice_id") and settings.environment != "production":
        return _response({"appointments": []})
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks practice connection is temporarily unavailable.")
    try:
        values = await ecw_backend.list_appointments(await patient_identity(session), practice)
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _response({"appointments": values})


@router.post("/cases")
async def create(body: Start, request: Request):
    session = await _session(request)
    settings, practice, binding = configuration()
    if session.get("practice_id") and settings.environment != "production":
        raise HTTPException(409, "Live Availity verification is not activated. Your intake is saved; use document review. Synthetic demo results cannot verify this patient.")
    if settings.environment == "production" and practice.get("production_enabled") is not True:
        raise HTTPException(409, "An administrator must finish Availity contracting, enrollment and practice setup, then enable production in AVAILITY_PRACTICE_CONFIG.")
    if session.get("practice_id"):
        patient = session["intake"]["patient"]
        identity = {"patient_first_name": patient["given_name"], "patient_last_name": patient["family_name"],
                    "patient_birth_date": patient["birth_date"], "gender": "U",
                    "ecw_patient_id": patient.get("ecw_patient_id")}
    else:
        profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
        if not profile:
            raise HTTPException(422, "The QR patient profile is missing")
        identity = {"patient_first_name": profile.given_name, "patient_last_name": profile.family_name,
                    "patient_birth_date": profile.birth_date, "gender": {"male": "M", "female": "F"}.get(profile.administrative_gender, "U")}
    use_ecw = bool(session.get("practice_id")) or settings.environment == "production" or stedi_temporary_enabled(settings, practice)
    existing = None
    if not use_ecw:
        inputs, missing, sources = demo_inputs(), [], {}
    else:
        if not ecw_backend.configured():
            raise HTTPException(503, "The eClinicalWorks practice connection is temporarily unavailable.")
        existing = await run_in_threadpool(
            store.latest_v2, session["patient_id"], session["share_manifest_id"], binding, **_scope(session),
        )
        fetch_identity = dict(identity)
        previous_date = str(((existing or {}).get("sources", {}).get("ecw", {}) or {}).get("appointment_date") or "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", previous_date):
            fetch_identity["ecw_appointment_date"] = previous_date
        try:
            inputs, missing, source = await asyncio.wait_for(
                ecw_backend.fetch_inputs(fetch_identity, practice), timeout=ECW_FETCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(502, "eClinicalWorks did not respond in time. Try again; if this continues, confirm that this practice activated the backend app.") from None
        except ecw_backend.EcwBackendError as exc:
            raise HTTPException(502, str(exc)) from exc
        ecw_patient_id = source.pop("_patient_id", None)
        if ecw_patient_id:
            identity["ecw_patient_id"] = ecw_patient_id
        preserve_ecw_snapshot(existing, inputs, source)
        sources = {"ecw": source}
        if session.get("practice_id"):
            if ecw_patient_id:
                await run_in_threadpool(staff_store.set_ecw_patient_id, session["patient_id"], session["practice_id"], ecw_patient_id)
            validate_staff_coverage(session["intake"]["fields"], inputs, source, practice)
            if apply_staff_card_coverage(session["intake"]["fields"], inputs, missing, practice):
                sources["card"] = {"system": "ClinicForce insurance-card scanner", "observed_at": session["intake"]["updated_at"],
                                   "notes": ["Staff-confirmed card fields filled coverage values not returned by eClinicalWorks."]}
        if existing and not existing.get("transactions"):
            fee_source = existing.get("sources", {}).get("fees")
            existing.update(profile=identity, sources={**existing.get("sources", {}), **sources},
                            inputs=inputs, missing=missing, discovery={})
            existing.pop("suggestions", None)
            if fee_source:
                apply_fee_schedule(existing, fee_source)
            return _response(public(await save(existing, session, "ecw-refreshed")))
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], session["share_manifest_id"], binding,
                                       {"source_mode": "stedi" if stedi_temporary_enabled(settings, practice) else "availity", "binding": binding, "environment": settings.environment,
                                        "input_mode": "ecw" if use_ecw else "synthetic",
                                        "revision": 0, "profile": identity, "sources": sources, "inputs": inputs, "missing": missing,
                                        "transactions": {}, "discovery": {}, "events": [], "scenario": body.scenario},
                                       **({"expected_intake_updated_at": session["intake"]["updated_at"]} if session.get("practice_id") else {}),
                                       **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _response(public(case))


@router.get("/cases/{case_id}", description=(
    "Read the saved V2 case and payer results. Completed Stedi coverage keeps benefit rows and a "
    "patient-identity decision containing mismatch field names, without payer demographic values."
))
async def get_case(case_id: str, request: Request):
    session = await _session(request)
    _, _, binding = configuration()
    return _response(public(await load(case_id, session, binding)))


@router.put("/cases/{case_id}/manual-review")
async def save_manual_review(case_id: str, body: ManualReview, request: Request):
    """Record a staff-confirmed portal/call result; never turn it into a PCE quote."""
    session = await _session(request)
    _, _, binding = configuration()
    case = await load(case_id, session, binding)
    idle(case)
    coverage = case.get("transactions", {}).get("coverage", {})
    if coverage.get("source") != "stedi" or coverage.get("outcome") != "complete":
        raise HTTPException(409, "Manual payer verification is available only after a completed Stedi eligibility result.")
    values = {key: value.strip() if isinstance(value, str) else value for key, value in body.model_dump().items()}
    checks = (("network", values["network_status"], values["network_reference"]),
              ("referral", values["referral_status"], values["referral_reference"]),
              ("authorization", values["authorization_status"], values["authorization_reference"]))
    missing = [label for label, status, reference in checks if status != "unconfirmed" and not reference]
    if missing:
        raise HTTPException(422, "Record the payer portal confirmation or call reference for: " + ", ".join(missing) + ".")
    case["manual_review"] = {**values, "verified_at": datetime.now(timezone.utc).isoformat()}
    return _response(public(await save(case, session, "stedi-manual-payer-review-recorded")))


from app.api.routers.v2_flow.insurance_v2_services import upload, suggest_services, confirm_services, requirements


from app.api.routers.v2_flow.insurance_v2_eligibility import request_body, validate_request, submit_stedi, stedi_fallback_reason, stedi_attempt, stedi_coverage, use_stedi_without_availity


from app.api.routers.v2_flow.insurance_v2_submission import submit, refresh, retry_rejected, finish
