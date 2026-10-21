"""Insurance v2 submission routes and operations."""

from __future__ import annotations


import re
import time
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.api.routers.insurance import _response
from app.insurance import availity_intake as intake, availity_results as results, stedi
from app.insurance.v2_decisions import canonical, service_lines, request_missing
from app.insurance.availity import PATHS, DEMO_SCENARIOS, unwrap
from app.logs.logger import log_info, log_warning

from app.api.routers import insurance_v2 as _v2

router = _v2.router
Resource = _v2.Resource

@router.post("/cases/{case_id}/transactions/{resource}", description=(
    "Submit a V2 payer transaction. A completed Stedi eligibility result includes active benefit rows "
    "and a patient-identity decision with mismatch field names. The production Stedi fallback does "
    "not infer provider participation from benefit network indicators or the demo network matrix."
))
async def submit(case_id: str, resource: Resource, request: Request):
    session = await _v2._session(request)
    settings, practice, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.require_payer_requests(case)
    existing = case["transactions"].get(resource)
    if existing:
        # A repeated POST is always a read, including after a timeout or process crash.
        return _response(_v2.public(case))
    _v2.idle(case)
    if case.get("source_mode") == "stedi":
        return await _v2.submit_stedi(case, session, resource, practice)
    missing = request_missing(case, resource)
    # Stedi needs fewer fields than Availity (no Availity submitter or address),
    # so do not reject a valid configured fallback with Availity-only gaps.
    if resource == "coverage" and _v2.use_stedi_without_availity(case, settings):
        return await _v2.stedi_coverage(case, session, practice, [], "Availity eligibility routing is unavailable for this clinic or payer.")
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
    body = _v2.request_body(case, resource)
    if settings.environment == "production":
        body = _v2.validate_request(case, resource, body, practice)
    elif resource != "coverage":
        body = {}  # Required by Availity's Demo protocol; never send uploaded patient data.
    case["transactions"][resource] = {"outcome": "submitting", "submitted_at": datetime.now(timezone.utc).isoformat(), "request_body": body,
                                      "source_refs": {k: {n: source[n] for n in ("filename", "sha256", "observed_at", "retrieved_at") if n in source} for k, source in case["sources"].items()},
                                      "message": "Submission started. If interrupted, refresh this case; do not create a duplicate transaction."}
    case["operation_until"] = time.time() + 60
    case = await _v2.save(case, session, f"{resource}-submission-started")
    api_resource = canonical(resource)
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[api_resource][0]) if settings.environment == "demo" else None
    started = time.monotonic()
    log_info("Insurance estimator", "Availity submission started: case=%s resource=%s environment=%s service_lines=%d", case["id"], resource, settings.environment, len(service_lines(case, resource)))
    response = await _v2.gateway(settings).request("POST", PATHS[api_resource], data=body if resource == "coverage" else None,
                                                body=body if resource != "coverage" else None, scenario=scenario, resource=api_resource)
    log_info("Insurance estimator", "Availity submission response: case=%s resource=%s outcome=%s http_status=%s transaction_id=%s resource_id=%s duration_ms=%d", case["id"], resource, response.get("outcome"), response.get("http_status"), response.get("transaction_id"), response.get("resource_id"), (time.monotonic() - started) * 1000)
    return await finish(case, session, resource, response, settings, practice)

@router.post("/cases/{case_id}/transactions/{resource}/refresh")
async def refresh(case_id: str, resource: Resource, request: Request):
    session = await _v2._session(request)
    settings, practice, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.require_payer_requests(case)
    transaction = case["transactions"].get(resource, {})
    if not transaction.get("resource_id") or transaction.get("outcome") not in {"pending", "unavailable"}:
        return _response(_v2.public(case))
    _v2.idle(case)
    if time.time() < transaction.get("next_poll_at", 0):
        return _response(_v2.public(case))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", transaction["resource_id"]):
        raise HTTPException(409, "The saved result ID is invalid. Contact support.")
    transaction["next_poll_at"] = time.time() + 25
    case["operation_until"] = time.time() + 60
    case = await _v2.save(case, session, f"{resource}-poll-started")
    api_resource = canonical(resource)
    scenario = (case["scenario"] if resource == "coverage" else DEMO_SCENARIOS[api_resource][1]) if settings.environment == "demo" else None
    started = time.monotonic()
    log_info("Insurance estimator", "Availity poll started: case=%s resource=%s resource_id=%s", case["id"], resource, transaction["resource_id"])
    response = await _v2.gateway(settings).request("GET", PATHS[api_resource] + "/" + transaction["resource_id"], scenario=scenario, resource=api_resource)
    log_info("Insurance estimator", "Availity poll response: case=%s resource=%s outcome=%s http_status=%s duration_ms=%d", case["id"], resource, response.get("outcome"), response.get("http_status"), (time.monotonic() - started) * 1000)
    return await finish(case, session, resource, response, settings, practice)

@router.post("/cases/{case_id}/transactions/{resource}/retry")
async def retry_rejected(case_id: str, resource: Resource, request: Request):
    """Explicit retry after access/rate-limit rejection; uncertain submissions cannot retry."""
    session = await _v2._session(request)
    _, _, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.require_payer_requests(case)
    _v2.idle(case)
    previous = case["transactions"].get(resource, {})
    if previous.get("http_status") not in {401, 403, 429} or previous.get("resource_id"):
        raise HTTPException(409, "Only a confirmed access or rate-limit rejection can be resubmitted. Resolve uncertain submissions with Availity first.")
    if time.time() < previous.get("next_poll_at", 0):
        raise HTTPException(429, "Wait until Availity's retry interval has elapsed before retrying.")
    case["previous_attempts"] = [*case.get("previous_attempts", []), {"resource": resource, **previous}][-20:]
    del case["transactions"][resource]
    await _v2.save(case, session, f"{resource}-rejected-attempt-archived")
    return await submit(case_id, resource, request)

async def finish(case: dict, session: dict, resource: str, response: dict, settings: Settings, practice: dict):
    reason = _v2.stedi_fallback_reason(response) if resource == "coverage" else None
    if reason and settings.environment == "production" and stedi.Settings.read().mode == "production" and stedi.fallback_enabled():
        log_warning("Insurance estimator", "Availity coverage fallback selected: case=%s outcome=%s reason=%s", case["id"], response.get("outcome"), reason)
        return await _v2.stedi_coverage(case, session, practice, [_v2.stedi_attempt(response, reason)], reason)
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
    case = await _v2.save(case, session, f"{resource}-{value['outcome']}")
    logger = log_info if value["outcome"] in {"complete", "pending", "retrying"} else log_warning
    logger("Insurance estimator", "Payer transaction finished: case=%s resource=%s source=availity outcome=%s http_status=%s resource_id=%s", case["id"], resource, value["outcome"], value.get("http_status"), value.get("resource_id"))
    return _response(_v2.public(case))
