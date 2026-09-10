"""Patient-share-scoped Availity workflow with document-only request intake."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict

from app.api.routers.insurance import _response, _session
from app.api.settings import MAX_UPLOAD_BYTES
from app.core.patient_store import get_patient_profile
from app.insurance import availity_intake as intake, availity_results as results, golden, store
from app.insurance.availity import Settings, gateway, PATHS, DEMO_SCENARIOS, unwrap


router = APIRouter(prefix="/api/doctor/insurance/v2", tags=["doctor-insurance-v2"])
Resource = Literal["coverage", "estimate", "authorization", "referral"]


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["Coverages-Complete-i", "Coverages-InProgress-i", "Coverages-Retrying-i", "Coverages-PayerError1-i", "Coverages-RequestError1-i"] = "Coverages-Complete-i"


def configuration() -> tuple[Settings, dict, str]:
    try:
        settings = Settings.read()
        practice = {} if settings.environment == "demo" else json.loads(os.getenv("AVAILITY_PRACTICE_CONFIG", "{}"))
        if not isinstance(practice, dict):
            raise ValueError
        providers = practice.get("providers", {})
        if not isinstance(providers, dict) or any(not re.fullmatch(r"[1-4]\d{9}", k) or not isinstance(v, dict) for k, v in providers.items()):
            raise ValueError
        if not isinstance(practice.get("payer_aliases", {}), dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in practice.get("payer_aliases", {}).items()):
            raise ValueError
        ZoneInfo(practice.get("timezone", "America/New_York"))
        binding = hashlib.sha256((settings.binding + json.dumps(practice, sort_keys=True)).encode()).hexdigest()
        return settings, practice, binding
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise HTTPException(503, "The Availity server configuration is invalid. Check environment, scope, URLs and practice settings.") from None


def public(case: dict) -> dict:
    # Raw payer responses and account configuration never leave the backend.
    visible = {k: case[k] for k in ("id", "created_at", "expires_at", "environment", "revision", "sources", "missing", "inputs", "discovery", "events") if k in case}
    if "inputs" in visible:
        visible["inputs"] = {k: v for k, v in visible["inputs"].items() if k not in {"submitter_id", "provider", "rendering_provider"}}
        provider = case["inputs"].get("provider", {})
        visible["inputs"]["provider"] = {k: provider.get(k) for k in ("npi", "lastName", "addressLine1", "city", "stateCode", "zipCode")}
    visible["sources"] = {k: {n: v for n, v in s.items() if n not in {"fields", "lines"}} for k, s in case.get("sources", {}).items()}
    visible["transactions"] = {k: {n: v for n, v in t.items() if n not in {"payload", "request_body"}} for k, t in case.get("transactions", {}).items()}
    return visible


async def load(case_id: str, session: dict, binding: str) -> dict:
    case = await run_in_threadpool(store.get_case, case_id, session["patient_id"], session["share_manifest_id"])
    if not case or case.get("source_mode") != "availity":
        raise HTTPException(404, "Availity case not found or expired")
    if case.get("binding") != binding:
        raise HTTPException(409, "The Availity account, environment or practice settings changed. Start a new V2 case.")
    return case


async def save(case: dict, session: dict, event: str) -> dict:
    case["events"] = [*case.get("events", []), {"stage": event, "at": datetime.now(timezone.utc).isoformat()}][-100:]
    saved = await run_in_threadpool(store.save_v2, case, session["patient_id"], session["share_manifest_id"], event, case["revision"])
    if not saved:
        raise HTTPException(409, "This case changed in another request. Refresh the case before continuing.")
    return saved


def idle(case: dict) -> None:
    if case.get("operation_until", 0) > time.time() or any(t.get("outcome") == "submitting" for t in case["transactions"].values()):
        raise HTTPException(409, "An Availity operation is in progress. Refresh the saved case; do not submit a duplicate.")


def demo_inputs() -> dict:
    return {"patient": {"patient_first_name": "GEORGE", "patient_last_name": "KOVAL", "patient_birth_date": "1999-01-01", "gender": "M"},
            "subscriber": {"firstName": "GEORGE", "lastName": "KOVAL", "birthDate": "1999-01-01", "memberId": "H39522358"},
            "relationship": "18", "member_id": "H39522358", "payer_id": "BCBSF", "group_number": "",
            "provider": {"lastName": "Availity synthetic provider", "npi": "1234567893"}, "submitter_id": "G2509",
            "rendering_provider": {"lastName": "Synthetic service provider", "npi": "1234567893"}, "service_type": "1",
            "service_date": datetime.now(timezone.utc).date().isoformat(), "place_of_service": "11", "diagnoses": ["J3089"],
            "patient_phone": "5552347700", "reason": "Availity's published synthetic transaction",
            "coverage_missing": [], "review_missing": [], "lines": [{"code": "92523", "units": "100", "charge": "250.00", "modifiers": [], "diagnosis": "J3089"}]}


@router.get("/configuration")
async def get_configuration(request: Request):
    _session(request)
    settings, practice, _ = configuration()
    return _response({"environment": settings.environment, "credentials_configured": bool(settings.client_id and settings.client_secret),
                      "practice_configured": bool(practice.get("providers") and practice.get("submitter_id")),
                      "production_enabled": settings.environment == "production" and practice.get("production_enabled") is True,
                      "message": "Synthetic transactions only; demo results do not describe the QR patient." if settings.environment == "demo" else "Live inquiries for the opened QR patient and configured clinic. Upload source documents and review the extracted request."})


@router.get("/cases/latest")
async def latest(request: Request):
    session = _session(request)
    _, _, binding = configuration()
    case = await run_in_threadpool(store.latest_v2, session["patient_id"], session["share_manifest_id"], binding)
    return _response({"case": public(case) if case else None})


@router.post("/cases")
async def create(body: Start, request: Request):
    session = _session(request)
    settings, practice, binding = configuration()
    if settings.environment == "production" and practice.get("production_enabled") is not True:
        raise HTTPException(409, "An administrator must finish Availity contracting, enrollment and practice setup, then enable production in AVAILITY_PRACTICE_CONFIG.")
    profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
    if not profile:
        raise HTTPException(422, "The QR patient profile is missing")
    identity = {"patient_first_name": profile.given_name, "patient_last_name": profile.family_name,
                "patient_birth_date": profile.birth_date, "gender": {"male": "M", "female": "F"}.get(profile.administrative_gender, "U")}
    inputs, missing = (demo_inputs(), []) if settings.environment == "demo" else intake.assemble({}, identity, practice)
    case = await run_in_threadpool(store.create_case, session["patient_id"], session["share_manifest_id"], binding,
                                   {"source_mode": "availity", "binding": binding, "environment": settings.environment,
                                    "revision": 0, "profile": identity, "sources": {}, "inputs": inputs, "missing": missing,
                                    "transactions": {}, "discovery": {}, "events": [], "scenario": body.scenario})
    return _response(public(case))


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request):
    session = _session(request)
    _, _, binding = configuration()
    return _response(public(await load(case_id, session, binding)))


@router.post("/cases/{case_id}/documents/{kind}")
async def upload(case_id: str, kind: str, request: Request, file: UploadFile = File(...)):
    session = _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    if settings.environment == "demo":
        raise HTTPException(409, "Demo uses Availity's synthetic inputs and does not accept patient documents.")
    idle(case)
    if any(r != "coverage" for r in case["transactions"]) or (kind in {"card", "provider"} and case["transactions"]):
        raise HTTPException(409, "These inputs belong to a submitted transaction. Start a new case to replace them.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "V2 input documents must be 15 MB or smaller")
    try:
        source = await run_in_threadpool(intake.read_source, kind, content, Path(file.filename or "document").name, practice.get("payer_aliases", {}))
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


@router.post("/cases/{case_id}/requirements")
async def requirements(case_id: str, request: Request):
    session = _session(request)
    settings, _, binding = configuration()
    case = await load(case_id, session, binding)
    idle(case)
    payer = case["inputs"].get("payer_id")
    if not payer:
        raise HTTPException(422, "Upload the registration export containing Availity Payer ID first.")
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
    case["discovery"] = {"checked_at": datetime.now(timezone.utc).isoformat(), "requirements": dict(found),
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
    if resource == "estimate":
        return {"coverageId": case["transactions"]["coverage"]["resource_id"], "payerId": i["payer_id"],
                "billingProvider": {k: provider[k] for k in ("lastName", "npi", "ein", "addressLine1", "city", "stateCode", "zipCode") if k in provider},
                "patient": {"phone": i["patient_phone"]},
                "claimInformation": {"placeOfServiceCode": i["place_of_service"], "diagnoses": diagnoses,
                                     **({"renderingProvider": {k: v for k, v in i["rendering_provider"].items() if k != "ein"}} if i.get("rendering_provider", {}).get("lastName") else {}),
                                     "serviceLines": [{"procedureCode": r["code"], "quantity": r["units"], "amount": r["charge"],
                                                       "diagnosisCodePointer1": str(i["diagnoses"].index(r["diagnosis"]) + 1),
                                                       **{f"modifierCode{n}": value for n, value in enumerate(r["modifiers"], 1)}} for r in i["lines"]]}}
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
                            **{f"modifier{n}Code": v for n, v in enumerate(r["modifiers"], 1)}} for r in i["lines"]]}


def validate_request(case: dict, resource: str, body: dict, practice: dict) -> dict:
    """Apply published payer requirements; leave complex rules to payer validation."""
    requirements = case.get("discovery", {}).get("requirements", {}).get(resource, {})
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
        raise HTTPException(422, "The payer requires additional source data: " + ", ".join(absent) + ". Obtain the corresponding registration/order/provider export; its mapping must be added if this format is not supported. No inquiry was submitted.")
    return body


@router.post("/cases/{case_id}/transactions/{resource}")
async def submit(case_id: str, resource: Resource, request: Request):
    session = _session(request)
    settings, practice, binding = configuration()
    case = await load(case_id, session, binding)
    existing = case["transactions"].get(resource)
    if existing:
        # A repeated POST is always a read, including after a timeout or process crash.
        return _response(public(case))
    idle(case)
    missing = case["inputs"]["coverage_missing"] if resource == "coverage" else case["inputs"]["review_missing"] if resource in {"authorization", "referral"} else case["missing"]
    if settings.environment == "production" and missing:
        raise HTTPException(422, "Missing source documents: " + " ".join(missing))
    if resource != "coverage":
        coverage = case["transactions"].get("coverage", {})
        if coverage.get("outcome") != "complete" or not coverage.get("resource_id"):
            raise HTTPException(409, "Complete an Availity coverage inquiry first.")
        if resource == "estimate" and coverage.get("result", {}).get("pce_available") is False and settings.environment == "production":
            raise HTTPException(409, "Availity says Professional Patient Cost Estimator is unavailable for this coverage. Ask about payer/product support; no local price will be substituted.")
    if settings.environment == "production" and resource in {"authorization", "referral"}:
        rendering = case["inputs"].get("rendering_provider", {})
        if not rendering.get("npi") or not rendering.get("lastName") or not re.fullmatch(r"[A-Z0-9]{1,3}", case["inputs"].get("service_type") or ""):
            raise HTTPException(422, "The order export must identify an approved Rendering Provider NPI and Service Type Code before an authorization/referral can be submitted. Ask the clinic for that export.")
    body = request_body(case, resource)
    if settings.environment == "production":
        # PCE 2 currently requires a current-day coverage. Do not silently estimate a different date.
        if resource == "estimate" and case["inputs"]["service_date"] != datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date().isoformat():
            raise HTTPException(409, "This PCE integration supports current-day estimates. Obtain a current-day encounter export or ask Availity about support for the intended service date.")
        body = validate_request(case, resource, body, practice)
    elif resource != "coverage":
        body = {}  # Required by Availity's Demo protocol; never send uploaded patient data.
    case["transactions"][resource] = {"outcome": "submitting", "submitted_at": datetime.now(timezone.utc).isoformat(), "request_body": body,
                                      "source_refs": {k: {n: source[n] for n in ("filename", "sha256", "observed_at")} for k, source in case["sources"].items()},
                                      "message": "Submission started. If interrupted, refresh this case; do not create a duplicate transaction."}
    case["operation_until"] = time.time() + 60
    case = await save(case, session, f"{resource}-submission-started")
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[resource][0]) if settings.environment == "demo" else None
    response = await gateway(settings).request("POST", PATHS[resource], data=body if resource == "coverage" else None,
                                                body=body if resource != "coverage" else None, scenario=scenario, resource=resource)
    return await finish(case, session, resource, response)


@router.post("/cases/{case_id}/transactions/{resource}/refresh")
async def refresh(case_id: str, resource: Resource, request: Request):
    session = _session(request)
    settings, _, binding = configuration()
    case = await load(case_id, session, binding)
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
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[resource][1]) if settings.environment == "demo" else None
    response = await gateway(settings).request("GET", PATHS[resource] + "/" + transaction["resource_id"], scenario=scenario, resource=resource)
    return await finish(case, session, resource, response)


@router.post("/cases/{case_id}/transactions/{resource}/retry")
async def retry_rejected(case_id: str, resource: Resource, request: Request):
    """Explicit retry after access/rate-limit rejection; uncertain submissions cannot retry."""
    session = _session(request)
    _, _, binding = configuration()
    case = await load(case_id, session, binding)
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


async def finish(case: dict, session: dict, resource: str, response: dict):
    previous = case["transactions"][resource]
    value = {**previous, **response, "message": response.get("message"), "checked_at": datetime.now(timezone.utc).isoformat(),
             "next_poll_at": time.time() + response.get("retry_after", 5)}
    if not response.get("resource_id") and previous.get("resource_id"):
        value["resource_id"] = previous["resource_id"]
    payload = results.obj(response.get("payload"))
    value["result"] = (results.coverage(payload) if resource == "coverage" else
                        results.estimate(payload, case["inputs"]["lines"], case["environment"] == "demo") if resource == "estimate" else results.review(payload))
    if response.get("outcome") == "complete" and case["environment"] == "production":
        data = unwrap(payload, resource) if resource != "estimate" else results.obj(payload.get("result"))
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
    return _response(public(case))
