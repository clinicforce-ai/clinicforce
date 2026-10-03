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
    """Fill only coverage values eCW did not return from a staff-confirmed card."""
    subscriber = inputs.setdefault("subscriber", {})
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
    """Compare stated card details; missing EHR values remain normal input gaps."""
    subscriber = inputs.get("subscriber") or {}
    relationships = {"self": "18", "spouse": "01", "child": "19", "other": "G8"}
    relationship = relationships.get(fields.get("relationship"), fields.get("relationship"))
    pairs = (("member ID", fields.get("member_id"), inputs.get("member_id")),
             ("group number", fields.get("group_number"), inputs.get("group_number")),
             ("subscriber relationship", relationship, inputs.get("relationship")),
             ("subscriber name", fields.get("subscriber_name"), " ".join(str(subscriber.get(key) or "") for key in ("firstName", "lastName")).strip()),
             ("subscriber birth date", fields.get("subscriber_birth_date"), subscriber.get("birthDate")))
    for label, confirmed, connected in pairs:
        if confirmed and connected and intake.normalize(str(confirmed)) != intake.normalize(str(connected)):
            raise HTTPException(409, f"The eCW {label} differs from the confirmed card. Review the coverage in eCW before checking benefits; your card is saved.")
    payer = intake.normalize(str(fields.get("payer") or ""))
    if not payer:
        return
    connected_id = intake.normalize(str(inputs.get("payer_id") or ""))
    connected_name = intake.normalize(str(source.get("payer") or ""))
    # An eCW payer name without a configured Availity ID is an explicit input gap,
    # not evidence that the confirmed card conflicts with the connected coverage.
    if not connected_id:
        return
    if payer in {connected_id, connected_name}:
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
    service_date = str(inputs.get("service_date") or "")
    for entry in practice.get("stedi_provider_networks", []):
        if (entry.get("payer_id") != inputs.get("payer_id") or entry.get("provider_npi") != (inputs.get("provider") or {}).get("npi") or
                entry.get("place_of_service") != inputs.get("place_of_service") or _normalized(entry.get("plan_name")) not in plans):
            continue
        if entry.get("effective_from") and str(entry["effective_from"]) > service_date:
            continue
        if entry.get("effective_to") and str(entry["effective_to"]) < service_date:
            continue
        status = entry["status"]
        return {"provider_network_status": status,
                "provider_network": "Confirmed by clinic contract matrix" if status == "in_network" else "Confirmed out-of-network by clinic contract matrix"}
    return {"provider_network_status": "unconfirmed", "provider_network": "Unverified — add a matching clinic provider-network contract before quoting."}


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


def demo_inputs() -> dict:
    return {"patient": {"patient_first_name": "GEORGE", "patient_last_name": "KOVAL", "patient_birth_date": "1999-01-01", "gender": "M"},
            "subscriber": {"firstName": "GEORGE", "lastName": "KOVAL", "birthDate": "1999-01-01", "memberId": "H39522358"},
            "relationship": "18", "member_id": "H39522358", "payer_id": "BCBSF", "group_number": "",
            "provider": {"lastName": "Availity synthetic provider", "npi": "1234567893"}, "submitter_id": "G2509",
            "rendering_provider": {"lastName": "Synthetic service provider", "npi": "1234567893"}, "service_type": "1",
            "service_date": datetime.now(timezone.utc).date().isoformat(), "place_of_service": "11", "diagnoses": ["J3089"],
            "patient_phone": "5552347700", "reason": "Availity's published synthetic transaction",
            "coverage_missing": [], "review_missing": [], "lines": [{"code": "92523", "description": "Availity synthetic service",
                                                                        "units": "100", "charge": "250.00", "cash_cents": None,
                                                                        "service_status": "scheduled", "modifiers": [], "diagnosis": "J3089"},
                                                                       {"code": "95913", "description": "Possible synthetic follow-up procedure",
                                                                        "units": "1", "charge": "900.00", "cash_cents": None,
                                                                        "service_status": "possible", "modifiers": [], "diagnosis": "J3089"}]}


def canonical(resource: str) -> str:
    return "estimate" if resource == "procedure_estimate" else resource


def service_lines(case: dict, resource: str) -> list[dict]:
    status = "possible" if resource == "procedure_estimate" else "scheduled"
    return [line for line in case["inputs"].get("lines", []) if line.get("service_status", "scheduled") == status]


def refresh_missing(case: dict) -> None:
    """Recalculate service gaps after eCW, fee CSV, or a confirmed suggestion changes lines."""
    inputs = case["inputs"]
    line_gaps = ("eClinicalWorks did not return clinician-coded", "eClinicalWorks did not associate every coded service",
                 "eClinicalWorks did not return the intended units", "eClinicalWorks FHIR did not return each billed line amount",
                 "Upload the clinic fee CSV")
    review = [item for item in inputs.get("review_missing", []) if not item.startswith(line_gaps)]
    lines = inputs.get("lines", [])
    diagnoses = inputs.get("diagnoses", [])
    if not lines:
        review.append("No CPT/HCPCS service has been confirmed for this appointment.")
    if not diagnoses and case.get("source_mode") != "stedi":
        review.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if case.get("source_mode") != "stedi" and lines and any(not line.get("diagnosis") or line["diagnosis"] not in diagnoses for line in lines):
        review.append("eClinicalWorks did not associate every coded service with one of the returned ICD-10 diagnoses.")
    if any(not line.get("units") for line in lines):
        review.append("Confirm the intended units for every coded service.")
    inputs["review_missing"] = list(dict.fromkeys(review))
    estimate = list(inputs["review_missing"])
    if case.get("source_mode") == "stedi":
        if any(line.get("allowed_cents") is None for line in lines):
            estimate.append("Upload the clinic contract-rate CSV with a Contracted Allowed Amount for every selected CPT/HCPCS service.")
    else:
        if not inputs.get("patient_phone"):
            estimate.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
        if any(line.get("charge") is None for line in lines):
            estimate.append("Upload the clinic fee CSV with a Billed Charge for every selected CPT/HCPCS service.")
    case["missing"] = list(dict.fromkeys(estimate))


def preserve_ecw_snapshot(existing: dict | None, inputs: dict, source: dict) -> None:
    """Do not replace known eCW facts with blanks when an optional refresh query stalls."""
    def absent(value) -> bool:
        return not any(value.values()) if isinstance(value, dict) else not value

    if not existing:
        return
    old_source = (existing.get("sources", {}).get("ecw", {}) or {})
    if old_source.get("appointment_date") != source.get("appointment_date"):
        return
    old_inputs = existing.get("inputs", {})
    timed_out = set(source.get("timed_out_resources", []))
    preserved: set[str] = set()
    if "Coverage" in timed_out:
        for key in ("subscriber", "relationship", "member_id", "payer_id", "group_number"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("Coverage")
        if not source.get("payer") and old_source.get("payer"):
            source["payer"] = old_source["payer"]
            preserved.add("Coverage")
    if timed_out & {"ServiceRequest", "Procedure", "Condition"}:
        for key in ("lines", "diagnoses"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("coded services")
    if "Patient" in timed_out and not inputs.get("patient_phone") and old_inputs.get("patient_phone"):
        inputs["patient_phone"] = old_inputs["patient_phone"]
        preserved.add("Patient")
    if "Practitioner" in timed_out:
        for key in ("provider", "rendering_provider"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("Practitioner")
    if "Location" in timed_out:
        for key in ("place_of_service", "service_type"):
            if not inputs.get(key) and old_inputs.get(key):
                inputs[key] = old_inputs[key]
                preserved.add("Location")
        if not source.get("location") and old_source.get("location"):
            source["location"] = old_source["location"]
            preserved.add("Location")
    if preserved:
        source["preserved_resources"] = sorted(preserved)
        source["missing"] = [*source.get("missing", []),
                             "Using the last saved eClinicalWorks values for: " + ", ".join(sorted(preserved)) + "."]


def request_missing(case: dict, resource: str) -> list[str]:
    inputs = case["inputs"]
    if resource == "coverage":
        return inputs.get("coverage_missing", [])
    if case.get("source_mode") == "stedi":
        if resource not in {"estimate", "procedure_estimate"}:
            return ["Stedi temporary mode supports eligibility and local estimates only; review authorization and referral manually."]
        lines = service_lines(case, resource)
        label = "possible procedure" if resource == "procedure_estimate" else "scheduled"
        missing = list(inputs.get("coverage_missing", []))
        if not inputs.get("place_of_service"):
            missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
        if not lines:
            missing.append(f"No {label} service is confirmed for this request.")
        if any(not line.get("units") for line in lines):
            missing.append("Confirm the intended units for every requested service.")
        if any(line.get("allowed_cents") is None for line in lines):
            missing.append("Upload the clinic contract-rate CSV with a Contracted Allowed Amount for every requested CPT/HCPCS service.")
        return list(dict.fromkeys(missing))
    line_gaps = ("eClinicalWorks did not return clinician-coded", "eClinicalWorks did not associate every coded service",
                 "eClinicalWorks did not return the intended units", "Confirm the intended units",
                 "No CPT/HCPCS service", "eClinicalWorks FHIR did not return each billed line amount", "Upload the clinic fee CSV")
    if canonical(resource) == "estimate":
        missing = list(inputs.get("coverage_missing", []))
        if not inputs.get("place_of_service"):
            missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
    else:
        missing = [item for item in inputs.get("review_missing", []) if not item.startswith(line_gaps)]
    lines = service_lines(case, resource)
    label = "possible procedure" if resource == "procedure_estimate" else "scheduled"
    if not lines:
        missing.append(f"No {label} service is confirmed for this request.")
    diagnoses = inputs.get("diagnoses", [])
    if not diagnoses:
        missing.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if lines and any(not line.get("diagnosis") or line["diagnosis"] not in diagnoses for line in lines):
        missing.append("eClinicalWorks did not associate every requested service with a returned ICD-10 diagnosis.")
    if any(not line.get("units") for line in lines):
        missing.append("Confirm the intended units for every requested service.")
    if canonical(resource) == "estimate":
        if not inputs.get("patient_phone"):
            missing.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
        if any(line.get("charge") is None for line in lines):
            missing.append("Upload the clinic fee CSV with a Billed Charge for every requested CPT/HCPCS service.")
        missing.extend(pricing_prerequisites(case))
    return list(dict.fromkeys(missing))


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


def _suggestion_context(inputs: dict) -> str:
    """Make the limits of eCW appointment data explicit to coding decision support."""
    reason = str(inputs.get("reason") or "").strip()
    visit_type = inputs.get("visit_type")
    status = (f"Patient visit status from eClinicalWorks: {visit_type} patient."
              if visit_type in {"new", "established"}
              else "Patient visit status: eClinicalWorks did not explicitly label this appointment new or established; do not infer it.")
    return "\n".join((reason, status,
                       "E/M complexity: eClinicalWorks did not return documented medical decision making or qualifying time. Do not choose an E/M level without clinician confirmation."))


def _ecw_coded_groups(inputs: dict, rates: list[dict]) -> tuple[list[dict], list[str]]:
    """Present actual eCW coded services first; a fee file only adds pricing."""
    available = {str(rate.get("code") or "").upper() for rate in rates}
    coded, unavailable = [], []
    for line in inputs.get("lines", []):
        code = str(line.get("code") or "").upper()
        if not code:
            continue
        coded.append({"code": code, "description": str(line.get("description") or code),
                      "reason": "Clinician-coded service returned by eClinicalWorks for this appointment.",
                      "requires_quantity": not bool(line.get("units")), "unit_label": "unit"})
        if code not in available:
            unavailable.append(code)
    if not coded:
        return [], list(dict.fromkeys(unavailable))
    return [{"id": "ecw-clinician-coded-services", "label": "Services coded in eClinicalWorks",
             "category": "other", "selection_mode": "multiple",
             "description": "These codes came from the eClinicalWorks appointment. Confirm the intended services and units before sending any payer request.",
             "requires_groups": [], "codes": coded}], list(dict.fromkeys(unavailable))


def _without_codes(suggestions: dict, excluded: set[str]) -> dict:
    """Never show the same source code again as a model-generated alternative."""
    groups = []
    for group in suggestions.get("groups", []):
        if not isinstance(group, dict):
            continue
        codes = [candidate for candidate in group.get("codes", [])
                 if isinstance(candidate, dict) and str(candidate.get("code") or "").upper() not in excluded]
        if codes:
            groups.append({**group, "codes": codes})
    return {**suggestions, "groups": groups}


def build_service_suggestions(case: dict) -> tuple[list[dict], list[dict], list[str]]:
    """Build clinician-reviewable choices from source codes plus appointment context."""
    rates = case.get("sources", {}).get("fees", {}).get("lines", [])
    inputs = case.get("inputs", {})
    history = case.get("sources", {}).get("ecw", {}).get("recent_completed_appointments", [])
    source_groups, missing_fee_codes = _ecw_coded_groups(inputs, rates)
    source_codes = {candidate["code"] for group in source_groups for candidate in group["codes"]}
    suggested = (cpt_suggester.suggest_cpt_codes(_suggestion_context(inputs), rates, history) if rates
                 else cpt_suggester.suggest_preliminary_cpt_codes(_suggestion_context(inputs), history))
    raw = _without_codes(suggested, source_codes)
    raw["groups"] = [*source_groups, *raw.get("groups", [])]
    lines, groups = cpt_rules.grouped_candidates(raw, rates, allow_unpriced=not rates)
    groups.sort(key=lambda group: group["id"] != "ecw-clinician-coded-services")
    notes = [str(note)[:1000] for note in raw.get("notes", []) if isinstance(note, str)][:20]
    if missing_fee_codes:
        notes.append("eClinicalWorks returned coded service(s) absent from this clinic fee CSV: " + ", ".join(missing_fee_codes) + ". Add those fee rows before estimating them.")
    if not rates:
        notes.append("These are unpriced clinical review candidates. Upload the clinic fee CSV before creating a payer request or an estimate.")
    if inputs.get("visit_type") not in {"new", "established"}:
        notes.append("eClinicalWorks did not explicitly return new-versus-established status. Confirm it with the clinician; do not infer it from this appointment.")
    notes.append("CPT choices require clinician review. eCW did not provide medical decision making or qualifying time, so ClinicForce cannot establish an E/M complexity level.")
    return lines, groups, notes


def front_desk_summary(case: dict) -> dict:
    coverage_transaction = case.get("transactions", {}).get("coverage", {})
    coverage = coverage_transaction.get("result", {})
    coverage_source = coverage_transaction.get("source") or coverage.get("source")
    plans = coverage.get("plans") or []
    plan_text = " ".join(str(plan.get(key) or "") for plan in plans for key in ("name", "planName", "insuranceType"))
    hmo = bool(re.search(r"\bHMO\b", plan_text, re.I))
    benefits = [benefit for plan in plans for benefit in plan.get("benefits", [])]
    referral_required = hmo or any(benefit.get("referralNumber") or "referral required" in str(benefit.get("description", "")).lower() for benefit in benefits)
    authorization_required = any(benefit.get("authorizationRequired") is True for benefit in benefits)
    authorization_unknown = any(benefit.get("authorizationRequiredUnknown") is True for benefit in benefits)
    service_date = case.get("inputs", {}).get("service_date") or ""

    def review(kind: str) -> dict:
        result = case.get("transactions", {}).get(kind, {}).get("result", {})
        procedures = result.get("procedures") or []
        procedure = procedures[0] if len(procedures) == 1 else {}
        start = result.get("certificationEffectiveDate") or result.get("fromDate") or procedure.get("certificationEffectiveDate") or procedure.get("fromDate")
        end = result.get("certificationExpirationDate") or result.get("toDate") or procedure.get("certificationExpirationDate") or procedure.get("toDate")
        reference = result.get("certificationNumber") or procedure.get("certificationNumber")
        approved = result.get("status_code") in {"A1", "NA"} and all(item.get("statusCode") in {None, "A1", "NA"} for item in procedures)
        if kind == "referral" and referral_required and result.get("status_code") == "A1":
            approved = bool(reference and start and end and start[:10] <= service_date <= end[:10])
        return {"required": referral_required if kind == "referral" else authorization_required,
                "unknown": False if kind == "referral" else authorization_unknown,
                "approved": approved, "status": result.get("status"), "reference": reference,
                "effective": start, "expiration": end}

    referral, authorization = review("referral"), review("authorization")
    manual = case.get("manual_review") if isinstance(case.get("manual_review"), dict) else {}
    labels = {"unconfirmed": "Not verified", "not_required": "Not required", "approved": "Approved", "required": "Required / pending"}
    if coverage_source == "stedi":
        for target, status_key, reference_key in ((referral, "referral_status", "referral_reference"),
                                                  (authorization, "authorization_status", "authorization_reference")):
            status = manual.get(status_key)
            if status in labels:
                target.update(required=status in {"approved", "required"}, unknown=status == "unconfirmed",
                              approved=status in {"approved", "not_required"}, status=labels[status],
                              reference=manual.get(reference_key) or None)
    scheduled = case.get("transactions", {}).get("estimate", {}).get("result", {})
    possible = case.get("transactions", {}).get("procedure_estimate", {}).get("result", {})

    def price(kind: str, result: dict) -> dict:
        lines = service_lines(case, kind)
        cash = None
        if lines and all(line.get("cash_cents") is not None for line in lines):
            try:
                total = sum(Decimal(line["cash_cents"]) * Decimal(str(line.get("units") or 1)) for line in lines)
                cash = int(total) if total == total.to_integral_value() else None
            except (InvalidOperation, ValueError):
                pass
        return {"codes": [line["code"] for line in lines], "insurance_cents": result.get("patient_cents") if result.get("estimate_available") else None,
                "allowed_cents": result.get("allowed_cents") if result.get("estimate_available") else None,
                "cash_cents": cash, "network": result.get("provider_network"), "network_status": result.get("provider_network_status", "unconfirmed")}

    scheduled_price, possible_price = price("estimate", scheduled), price("procedure_estimate", possible)
    if coverage_source == "stedi" and manual.get("network_status") in {"in_network", "out_of_network"}:
        network = str(manual["network_status"])
        scheduled_price.update(network=network.replace("_", " ").title() + " (staff verified)", network_status=network)
        possible_price.update(network=network.replace("_", " ").title() + " (staff verified)", network_status=network)
    active = coverage.get("active")
    if active is False:
        action, heading = "self_pay", "Coverage inactive — use the clinic cash price"
    elif active is not True:
        action, heading = "verify", "Verify eligibility before scheduling"
    elif coverage_source == "stedi":
        if referral["required"] and not referral["approved"]:
            action, heading = "do_not_schedule", "Do not schedule — referral requires payer confirmation"
        elif authorization["required"] and not authorization["approved"]:
            action, heading = "do_not_schedule", "Do not schedule — authorization requires payer confirmation"
        elif manual.get("price_path") == "cash":
            action, heading = "self_pay", "Cash price selected — insurance estimate remains pending"
        else:
            action, heading = "verify", "Insurance estimate pending — verify payer details before scheduling"
    elif referral["required"] and not referral["approved"]:
        action, heading = "do_not_schedule", "Do not schedule — referral required"
    elif authorization["required"] and not authorization["approved"]:
        action, heading = "do_not_schedule", "Do not schedule — authorization required"
    elif authorization["unknown"] and not authorization["approved"]:
        action, heading = "verify", "Review the authorization requirement before scheduling"
    elif scheduled_price["network_status"] == "out_of_network":
        action, heading = "review_oon", "Out-of-network — review OON benefits before collecting"
    elif scheduled_price["insurance_cents"] is not None:
        action, heading = "collect", f"Collect ${scheduled_price['insurance_cents'] / 100:,.2f} for the scheduled visit"
    else:
        action, heading = "verify", "Complete the scheduled visit estimate"
    return {"action": action, "heading": heading, "coverage": "active" if active is True else "inactive" if active is False else "unconfirmed",
            "hmo": hmo, "referral": referral, "authorization": authorization,
            "scheduled": scheduled_price, "possible": possible_price,
            "manual_review": {"source": manual.get("source"), "network_status": manual.get("network_status"),
                              "price_path": manual.get("price_path")}}


def pricing_prerequisites(case: dict) -> list[str]:
    """Require the flowchart's payer-review decision before an insured quote."""
    summary = front_desk_summary(case)
    referral = summary["referral"]
    authorization = summary["authorization"]
    missing = []
    if referral["required"] and not referral["approved"]:
        missing.append("Complete and obtain an approved referral before requesting an insured price estimate.")
    if authorization["required"] and not authorization["approved"]:
        missing.append("Complete and obtain an approved prior authorization before requesting an insured price estimate.")
    elif authorization["unknown"] and not authorization["approved"]:
        missing.append("Confirm the prior-authorization requirement with Availity before requesting an insured price estimate.")
    return missing


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


@router.get("/cases/{case_id}")
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


@router.post("/cases/{case_id}/documents/{kind}")
async def upload(case_id: str, kind: str, request: Request, file: UploadFile = File(...)):
    session = await _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    if settings.environment == "demo" and kind != "fees":
        raise HTTPException(409, "Demo uses Availity's synthetic inputs and does not accept patient documents.")
    if case.get("input_mode") == "ecw" and kind != "fees":
        raise HTTPException(409, "This case reads its inputs directly from eClinicalWorks. Start another case to refresh the appointment.")
    idle(case)
    if any(r != "coverage" for r in case["transactions"]) or (kind in {"card", "provider"} and case["transactions"]):
        raise HTTPException(409, "These inputs belong to a submitted transaction. Start a new case to replace them.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "V2 input documents must be 15 MB or smaller")
    try:
        filename = Path(file.filename or "document").name
        if kind == "fees":
            source = await run_in_threadpool(intake.read_fee_schedule, content, filename)
            object_key = None
            if session.get("practice_id"):
                if not object_store.enabled():
                    raise HTTPException(503, "Document storage is unavailable. The fee file has not been saved.")
                try:
                    object_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case_id,
                                                        filename, content, "text/csv")
                except Exception:
                    raise HTTPException(503, "The fee file could not be saved. Please try again.") from None
                source.update(object_key=object_key, content_type="text/csv")
            try:
                apply_fee_schedule(case, source)
                case.pop("suggestions", None)
                saved = await save(case, session, "fee-schedule-imported")
            except Exception:
                if object_key:
                    try:
                        await run_in_threadpool(object_store.delete_object, object_key)
                    except Exception:
                        pass
                raise
            return _response(public(saved))
        source = await run_in_threadpool(intake.read_source, kind, content, filename, practice.get("payer_aliases", {}))
    except golden.GoldenCaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    except golden.OCRServiceError:
        raise HTTPException(503, "Card OCR is unavailable. Check the server OCR installation or upload a text-based card PDF.") from None
    case["sources"][kind] = source
    inputs, missing = intake.assemble(case["sources"], case["profile"], practice)
    if case["transactions"] and any(inputs.get(key) != case["inputs"].get(key) for key in
                                    ("patient", "subscriber", "relationship", "member_id", "payer_id", "group_number", "provider", "submitter_id", "service_date")):
        raise HTTPException(409, "The new source changes the patient, subscriber, payer, provider or coverage date. Start a new case for those changes.")
    case["inputs"], case["missing"] = inputs, missing
    if not case["transactions"]:
        case["discovery"] = {}
    return _response(public(await save(case, session, f"{kind}-imported")))


@router.post("/cases/{case_id}/suggestions")
async def suggest_services(case_id: str, request: Request):
    session = await _session(request)
    _, _, binding = configuration()
    case = await load(case_id, session, binding)
    idle(case)
    if any(key in case["transactions"] for key in ("estimate", "procedure_estimate", "authorization", "referral")):
        raise HTTPException(409, "Start a new case before changing services that have already been submitted.")
    rates = case.get("sources", {}).get("fees", {}).get("lines", [])
    reason = str(case.get("inputs", {}).get("reason") or "").strip()
    if not reason and not case.get("inputs", {}).get("lines"):
        raise HTTPException(422, "eClinicalWorks must provide a reason for visit or a clinician-coded service before ClinicForce can show CPT/HCPCS choices.")
    try:
        lines, groups, notes = await run_in_threadpool(build_service_suggestions, case)
    except (ValueError, golden.GoldenCaseError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        raise HTTPException(503, "Service suggestions are temporarily unavailable. Staff can use clinician-coded services from eClinicalWorks.") from None
    case["suggestions"] = {"groups": groups, "lines": lines, "notes": notes,
                           "requires_fee_schedule": not bool(rates)}
    return _response(public(await save(case, session, "service-suggestions-generated")))


@router.post("/cases/{case_id}/services")
async def confirm_services(case_id: str, body: ConfirmServices, request: Request):
    session = await _session(request)
    _, _, binding = configuration()
    case = await load(case_id, session, binding)
    idle(case)
    if any(key in case["transactions"] for key in ("estimate", "procedure_estimate", "authorization", "referral")):
        raise HTTPException(409, "Start a new case before changing services that have already been submitted.")
    suggestions = case.get("suggestions") or {}
    choices = body.lines
    codes = [choice.code.upper() for choice in choices]
    quantities = {choice.code.upper(): choice.quantity for choice in choices}
    try:
        cpt_rules.validate_selection(codes, suggestions.get("groups", []), suggestions.get("lines", []), quantities)
    except golden.GoldenCaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    candidates = {line["code"]: line for line in suggestions.get("lines", [])}
    unknown = set(codes) - set(candidates)
    if unknown:
        raise HTTPException(422, "Choose only services returned for this visit and clinic fee CSV.")
    diagnosis = case["inputs"].get("diagnoses", [])
    case["inputs"]["lines"] = [{"code": code, "description": candidates[code].get("description"),
                                  "units": str(quantities[code]), "charge": candidates[code].get("charge"),
                                  "charge_source": "fee_schedule" if candidates[code].get("charge") is not None else "unpriced_suggestion",
                                  "cash_cents": candidates[code].get("cash_cents"), "allowed_cents": candidates[code].get("allowed_cents"),
                                  "service_status": next(choice.service_status for choice in choices if choice.code.upper() == code),
                                  "modifiers": [], "diagnosis": diagnosis[0] if len(diagnosis) == 1 else None}
                                 for code in codes]
    refresh_missing(case)
    return _response(public(await save(case, session, "services-confirmed-by-staff")))


@router.post("/cases/{case_id}/requirements")
async def requirements(case_id: str, request: Request):
    session = await _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    require_payer_requests(case)
    idle(case)
    if case.get("source_mode") == "stedi":
        case["discovery"] = {"checked_at": datetime.now(timezone.utc).isoformat(), "outcome": "complete",
                             "message": "Stedi temporary mode uses the configured payer map and clinic contract matrix; it does not query Availity metadata.",
                             "requirements": {resource: {"available": resource in {"coverage", "estimate", "procedure_estimate"}}
                                              for resource in ("coverage", "estimate", "procedure_estimate", "authorization", "referral")}}
        return _response(public(await save(case, session, "stedi-configuration-checked")))
    payer = case["inputs"].get("payer_id")
    if not payer:
        raise HTTPException(422, "The eClinicalWorks payer must be mapped to its verified Availity payer ID first.")
    client = gateway(settings)
    # No member information is sent during payer discovery. Independent metadata calls.
    queries = [("coverage", "270", None), ("estimate", "professional-claims", "PRE_DETERMINATION"),
               ("authorization", "service-reviews", "HS"), ("referral", "service-reviews", "SC")]
    async def find(kind, type_name, subtype):
        params = {"type": type_name, "payerId": payer}
        if subtype:
            params["subtypeId"] = subtype
        response = await client.request("GET", "/v1/configurations", params=params)
        configs = results.rows(results.obj(response.get("payload")).get("configurations"))
        config = next((c for c in configs if str(c.get("payerId")) == payer and c.get("type") == type_name and
                       (not subtype or c.get("subtypeId", c.get("subTypeId", subtype)) == subtype)), {})
        return kind, {"outcome": response["outcome"], "message": response.get("message"), "configuration": config,
                      "available": response["outcome"] == "complete" and isinstance(config.get("elements"), dict) and bool(config.get("elements"))}
    found = await asyncio.gather(*(find(*q) for q in queries))
    routes = await client.request("GET", "/v1/availity-payer-list", params={"payerId": payer, "submissionMode": "API"})
    payer_payload = routes.get("payload", [])
    payers = payer_payload.get("payers", []) if isinstance(payer_payload, dict) else payer_payload
    requirements_by_kind = dict(found)
    requirements_by_kind["procedure_estimate"] = requirements_by_kind["estimate"]
    case["discovery"] = {"checked_at": datetime.now(timezone.utc).isoformat(), "requirements": requirements_by_kind,
                         "payers": payers if routes["outcome"] == "complete" else [],
                         "message": routes.get("message"), "outcome": routes["outcome"]}
    return _response(public(await save(case, session, "payer-requirements-checked")))


def request_body(case: dict, resource: str) -> dict:
    i = case["inputs"]
    p, sub, provider = i["patient"], i["subscriber"], i["provider"]
    if resource == "coverage":
        body = {"payerId": i["payer_id"], "providerNpi": provider["npi"], "providerLastName": provider["lastName"],
                "providerTaxId": provider.get("ein"), "submitterId": i["submitter_id"], "memberId": i["member_id"],
                "patientFirstName": p["patient_first_name"], "patientLastName": p["patient_last_name"],
                "patientBirthDate": p["patient_birth_date"], "patientGender": p["gender"],
                "subscriberRelationship": i["relationship"], "groupNumber": i.get("group_number"),
                "asOfDate": i["service_date"], "serviceType": "30"}
        return {k: v for k, v in body.items() if v}
    diagnoses = [{"qualifierCode": "ABK" if n == 0 else "ABF", "code": code} for n, code in enumerate(i["diagnoses"])]
    lines = service_lines(case, resource)
    if canonical(resource) == "estimate":
        return {"coverageId": case["transactions"]["coverage"]["resource_id"], "payerId": i["payer_id"],
                "billingProvider": {k: provider[k] for k in ("lastName", "npi", "ein", "addressLine1", "city", "stateCode", "zipCode") if k in provider},
                "patient": {"phone": i["patient_phone"]},
                "claimInformation": {"placeOfServiceCode": i["place_of_service"], "diagnoses": diagnoses,
                                     **({"renderingProvider": {k: v for k, v in i["rendering_provider"].items() if k != "ein"}} if i.get("rendering_provider", {}).get("lastName") else {}),
                                     "serviceLines": [{"procedureCode": r["code"], "quantity": r["units"], "amount": r["charge"],
                                                       "diagnosisCodePointer1": str(i["diagnoses"].index(r["diagnosis"]) + 1),
                                                       **{f"modifierCode{n}": value for n, value in enumerate(r["modifiers"], 1)}} for r in lines]}}
    return {"payer": {"id": i["payer_id"]},
            "requestingProvider": {**{k: v for k, v in provider.items() if k != "ein"}, "submitterId": i["submitter_id"]},
            "subscriber": sub,
            "patient": {"firstName": p["patient_first_name"], "lastName": p["patient_last_name"], "birthDate": p["patient_birth_date"],
                        "genderCode": p["gender"], "subscriberRelationshipCode": i["relationship"]},
            "requestTypeCode": "SC" if resource == "referral" else "HS", "serviceTypeCode": i["service_type"],
            "placeOfServiceCode": i["place_of_service"], "fromDate": i["service_date"], "toDate": i["service_date"], "diagnoses": diagnoses,
            "renderingProviders": [{**{k: v for k, v in i["rendering_provider"].items() if k != "ein"}, "roleCode": "SJ"}],
            "procedures": [{"qualifierCode": "HC", "code": r["code"], "quantity": r["units"], "quantityTypeCode": "UN",
                            "fromDate": i["service_date"], "toDate": i["service_date"],
                            **{f"modifier{n}Code": v for n, v in enumerate(r["modifiers"], 1)}} for r in lines]}


def validate_request(case: dict, resource: str, body: dict, practice: dict) -> dict:
    """Apply published payer requirements; leave complex rules to payer validation."""
    requirements = case.get("discovery", {}).get("requirements", {}).get(canonical(resource), {})
    if not requirements.get("available"):
        raise HTTPException(409, "Fetch payer requirements first. If this product is unavailable, ask Availity to activate it for this payer and clinic.")
    if resource == "coverage":
        payer_rows = case.get("discovery", {}).get("payers", [])
        if not isinstance(payer_rows, list):
            raise HTTPException(409, "Availity did not return a usable payer routing list.")
        routes = []
        for payer in results.rows(payer_rows):
            if str(payer.get("payerId")) == case["inputs"]["payer_id"]:
                r = payer.get("processingRoutes")
                routes.extend([r] if isinstance(r, dict) else results.rows(r))
        eligible = [r for r in routes if str(r.get("submissionMode", "")).upper() == "API" and r.get("availability") is True and
                    ("270" in str(r.get("transactionDescription", "")) or "eligib" in str(r.get("transactionDescription", "")).lower())]
        if not eligible:
            raise HTTPException(409, "Availity has not confirmed an available eligibility API route for this payer. Ask Availity about payer connectivity/contracting.")
        if all(r.get("enrollmentRequired") is not False for r in eligible) and case["inputs"]["payer_id"] not in practice.get("enrolled_payers", []):
            raise HTTPException(409, "This payer requires enrollment. Ask the administrator to record completed enrollment in the practice configuration.")
    elements = requirements["configuration"]["elements"]
    if resource == "coverage":
        body = {k: v for k, v in body.items() if not isinstance(elements.get(k), dict) or elements[k].get("allowed") is not False}
    def get_value(path):
        value = body
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value
    absent = [rule.get("label") or key for key, rule in elements.items() if isinstance(rule, dict) and rule.get("required") is True and get_value(key) in (None, "", [])]
    if absent:
        raise HTTPException(422, "The payer requires additional eClinicalWorks or practice data: " + ", ".join(absent) + ". No inquiry was submitted.")
    return body


async def submit_stedi(case: dict, session: dict, resource: str, practice: dict) -> dict:
    """Run Stedi eligibility or the isolated local calculator; never call Availity."""
    missing = request_missing(case, resource)
    if missing:
        raise HTTPException(422, "Missing Stedi or clinic-contract data: " + " ".join(missing))
    if resource in {"authorization", "referral"}:
        raise HTTPException(409, "Stedi temporary mode supports eligibility and local estimates only. Review authorization and referral manually.")
    source_refs = {key: {name: source[name] for name in ("filename", "sha256", "observed_at", "retrieved_at") if name in source}
                   for key, source in case["sources"].items()}
    if resource == "coverage":
        body, error = stedi.request_body(case["inputs"], practice)
        if error:
            raise HTTPException(422, error)
        case["transactions"][resource] = {"outcome": "submitting", "source": "stedi", "submitted_at": datetime.now(timezone.utc).isoformat(),
                                           "request_body": body, "source_refs": source_refs, "message": "Submitting Stedi eligibility check."}
        case["operation_until"] = time.time() + 60
        case = await save(case, session, "coverage-stedi-submission-started")
        response = await stedi.Client(stedi.Settings.read()).check(body)
        value = {**case["transactions"][resource], **response, "source": "stedi", "resource_id": response.get("check_id"),
                 "checked_at": datetime.now(timezone.utc).isoformat(), "next_poll_at": time.time()}
        result = stedi_results.coverage(results.obj(response.get("payload"))) if response.get("outcome") == "complete" else {}
        if result:
            result.update(stedi_network(case, practice, result))
        value["result"] = result
        value["message"] = "Stedi returned eligibility and benefits." if response.get("outcome") == "complete" else response.get("message")
        case["transactions"][resource], case["operation_until"] = value, 0
        return _response(public(await save(case, session, f"coverage-stedi-{value['outcome']}")))
    coverage = case["transactions"].get("coverage", {})
    if coverage.get("outcome") != "complete" or coverage.get("result", {}).get("active") is not True:
        raise HTTPException(409, "Complete an active Stedi eligibility check before calculating an estimate.")
    result = stedi_estimate.calculate(coverage["result"], service_lines(case, resource))
    case["transactions"][resource] = {"outcome": "complete", "source": "stedi_local", "submitted_at": datetime.now(timezone.utc).isoformat(),
                                       "checked_at": datetime.now(timezone.utc).isoformat(), "source_refs": source_refs, "result": result,
                                       "message": result["message"]}
    return _response(public(await save(case, session, f"{resource}-stedi-local-estimate")))


@router.post("/cases/{case_id}/transactions/{resource}")
async def submit(case_id: str, resource: Resource, request: Request):
    session = await _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    require_payer_requests(case)
    existing = case["transactions"].get(resource)
    if existing:
        # A repeated POST is always a read, including after a timeout or process crash.
        return _response(public(case))
    idle(case)
    if case.get("source_mode") == "stedi":
        return await submit_stedi(case, session, resource, practice)
    missing = request_missing(case, resource)
    # Stedi needs fewer fields than Availity (no Availity submitter or address),
    # so do not reject a valid configured fallback with Availity-only gaps.
    if resource == "coverage" and use_stedi_without_availity(case, settings):
        return await stedi_coverage(case, session, practice, [], "Availity eligibility routing is unavailable for this clinic or payer.")
    if settings.environment == "production" and missing:
        raise HTTPException(422, "Missing API or practice data: " + " ".join(missing))
    if resource != "coverage" and not service_lines(case, resource):
        raise HTTPException(422, missing[-1])
    if resource != "coverage":
        coverage = case["transactions"].get("coverage", {})
        if coverage.get("source") == "stedi":
            raise HTTPException(409, "Stedi returned eligibility and benefits only. Review authorization, referral, and patient pricing manually.")
        if coverage.get("outcome") != "complete" or not coverage.get("resource_id"):
            raise HTTPException(409, "Complete an Availity coverage inquiry first.")
        if settings.environment == "production" and coverage.get("result", {}).get("active") is not True:
            raise HTTPException(409, "Availity did not confirm active coverage for this appointment. Stop the insured-price path and review the payer response.")
        if canonical(resource) == "estimate" and coverage.get("result", {}).get("pce_available") is False and settings.environment == "production":
            raise HTTPException(409, "Availity says Professional Patient Cost Estimator is unavailable for this coverage. Ask about payer/product support; no local price will be substituted.")
    if settings.environment == "production" and resource in {"authorization", "referral"}:
        rendering = case["inputs"].get("rendering_provider", {})
        if not rendering.get("npi") or not rendering.get("lastName") or not re.fullmatch(r"[A-Z0-9]{1,3}", case["inputs"].get("service_type") or ""):
            raise HTTPException(422, "eClinicalWorks and the practice configuration must identify an approved Rendering Provider NPI and Service Type Code before a service review can be submitted.")
    body = request_body(case, resource)
    if settings.environment == "production":
        body = validate_request(case, resource, body, practice)
    elif resource != "coverage":
        body = {}  # Required by Availity's Demo protocol; never send uploaded patient data.
    case["transactions"][resource] = {"outcome": "submitting", "submitted_at": datetime.now(timezone.utc).isoformat(), "request_body": body,
                                      "source_refs": {k: {n: source[n] for n in ("filename", "sha256", "observed_at", "retrieved_at") if n in source} for k, source in case["sources"].items()},
                                      "message": "Submission started. If interrupted, refresh this case; do not create a duplicate transaction."}
    case["operation_until"] = time.time() + 60
    case = await save(case, session, f"{resource}-submission-started")
    api_resource = canonical(resource)
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[api_resource][0]) if settings.environment == "demo" else None
    started = time.monotonic()
    log_info("Insurance estimator", "Availity submission started: case=%s resource=%s environment=%s service_lines=%d", case["id"], resource, settings.environment, len(service_lines(case, resource)))
    response = await gateway(settings).request("POST", PATHS[api_resource], data=body if resource == "coverage" else None,
                                                body=body if resource != "coverage" else None, scenario=scenario, resource=api_resource)
    log_info("Insurance estimator", "Availity submission response: case=%s resource=%s outcome=%s http_status=%s transaction_id=%s resource_id=%s duration_ms=%d", case["id"], resource, response.get("outcome"), response.get("http_status"), response.get("transaction_id"), response.get("resource_id"), (time.monotonic() - started) * 1000)
    return await finish(case, session, resource, response, settings, practice)


@router.post("/cases/{case_id}/transactions/{resource}/refresh")
async def refresh(case_id: str, resource: Resource, request: Request):
    session = await _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    require_payer_requests(case)
    transaction = case["transactions"].get(resource, {})
    if not transaction.get("resource_id") or transaction.get("outcome") not in {"pending", "unavailable"}:
        return _response(public(case))
    idle(case)
    if time.time() < transaction.get("next_poll_at", 0):
        return _response(public(case))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", transaction["resource_id"]):
        raise HTTPException(409, "The saved result ID is invalid. Contact support.")
    transaction["next_poll_at"] = time.time() + 25
    case["operation_until"] = time.time() + 60
    case = await save(case, session, f"{resource}-poll-started")
    api_resource = canonical(resource)
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[api_resource][1]) if settings.environment == "demo" else None
    started = time.monotonic()
    log_info("Insurance estimator", "Availity poll started: case=%s resource=%s resource_id=%s", case["id"], resource, transaction["resource_id"])
    response = await gateway(settings).request("GET", PATHS[api_resource] + "/" + transaction["resource_id"], scenario=scenario, resource=api_resource)
    log_info("Insurance estimator", "Availity poll response: case=%s resource=%s outcome=%s http_status=%s duration_ms=%d", case["id"], resource, response.get("outcome"), response.get("http_status"), (time.monotonic() - started) * 1000)
    return await finish(case, session, resource, response, settings, practice)


@router.post("/cases/{case_id}/transactions/{resource}/retry")
async def retry_rejected(case_id: str, resource: Resource, request: Request):
    """Explicit retry after access/rate-limit rejection; uncertain submissions cannot retry."""
    session = await _session(request)
    _, _, binding = configuration()
    case = await load(case_id, session, binding)
    require_payer_requests(case)
    idle(case)
    previous = case["transactions"].get(resource, {})
    if previous.get("http_status") not in {401, 403, 429} or previous.get("resource_id"):
        raise HTTPException(409, "Only a confirmed access or rate-limit rejection can be resubmitted. Resolve uncertain submissions with Availity first.")
    if time.time() < previous.get("next_poll_at", 0):
        raise HTTPException(429, "Wait until Availity's retry interval has elapsed before retrying.")
    case["previous_attempts"] = [*case.get("previous_attempts", []), {"resource": resource, **previous}][-20:]
    del case["transactions"][resource]
    await save(case, session, f"{resource}-rejected-attempt-archived")
    return await submit(case_id, resource, request)


async def finish(case: dict, session: dict, resource: str, response: dict, settings: Settings, practice: dict):
    reason = stedi_fallback_reason(response) if resource == "coverage" else None
    if reason and settings.environment == "production" and stedi.Settings.read().mode == "production" and stedi.fallback_enabled():
        log_warning("Insurance estimator", "Availity coverage fallback selected: case=%s outcome=%s reason=%s", case["id"], response.get("outcome"), reason)
        return await stedi_coverage(case, session, practice, [stedi_attempt(response, reason)], reason)
    previous = case["transactions"][resource]
    value = {**previous, **response, "message": response.get("message"), "checked_at": datetime.now(timezone.utc).isoformat(),
             "next_poll_at": time.time() + response.get("retry_after", 5)}
    if not response.get("resource_id") and previous.get("resource_id"):
        value["resource_id"] = previous["resource_id"]
    payload = results.obj(response.get("payload"))
    value["result"] = (results.coverage(payload) if resource == "coverage" else
                        results.estimate(payload, service_lines(case, resource), case["environment"] == "demo") if canonical(resource) == "estimate" else results.review(payload))
    if response.get("outcome") == "complete" and case["environment"] == "production":
        data = unwrap(payload, canonical(resource)) if canonical(resource) != "estimate" else results.obj(payload.get("result"))
        member = results.obj(data.get("subscriber")).get("memberId")
        payer = results.obj(data.get("payer"))
        payer_id = payer.get("payerId") or payer.get("id")
        patient = results.obj(data.get("patient"))
        expected = case["inputs"]["patient"]
        mismatched = (member and intake.normalize(member) != intake.normalize(case["inputs"]["member_id"])) or (payer_id and payer_id != case["inputs"]["payer_id"])
        for actual, key in ((patient.get("firstName"), "patient_first_name"), (patient.get("lastName"), "patient_last_name"), (str(patient.get("birthDate", ""))[:10], "patient_birth_date")):
            if actual and intake.normalize(actual) != intake.normalize(expected[key]):
                mismatched = True
        if mismatched:
            value.update(outcome="payer_error", result={}, message="Availity returned identity details that differ from the submitted patient or payer. Contact the payer before continuing.")
    case["transactions"][resource] = value
    case["operation_until"] = 0
    case = await save(case, session, f"{resource}-{value['outcome']}")
    logger = log_info if value["outcome"] in {"complete", "pending", "retrying"} else log_warning
    logger("Insurance estimator", "Payer transaction finished: case=%s resource=%s source=availity outcome=%s http_status=%s resource_id=%s", case["id"], resource, value["outcome"], value.get("http_status"), value.get("resource_id"))
    return _response(public(case))
