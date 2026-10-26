"""Insurance v2 eligibility routes and operations."""

from __future__ import annotations


import time
from datetime import datetime, timezone

from fastapi import HTTPException

from app.api.routers.insurance import _response
from app.insurance import availity_results as results, identity, stedi, stedi_estimate, stedi_results
from app.insurance.v2_decisions import canonical, service_lines, request_missing

from app.api.routers import insurance_v2 as _v2


def stedi_patient_binding(case: dict, payload: dict) -> dict:
    """Keep the identity decision without exposing returned member demographics."""
    inputs = case.get("inputs") or {}
    patient, subscriber = inputs.get("patient") or {}, inputs.get("subscriber") or {}
    expected = {"patient_first": patient.get("patient_first_name"),
                "patient_last": patient.get("patient_last_name"),
                "patient_birth_date": patient.get("patient_birth_date"),
                "member_id": inputs.get("member_id") or subscriber.get("memberId"),
                "subscriber_first": subscriber.get("firstName"),
                "subscriber_last": subscriber.get("lastName"),
                "relationship": inputs.get("relationship"),
                "payer_id": inputs.get("payer_id")}
    comparison = identity.compare(payload, expected)
    return {"status": comparison["status"], "match_version": comparison["match_version"],
            "binding": comparison["binding"] if identity.patient_benefits_bound(comparison) else None,
            "differences": [row["field"] for row in comparison.get("differences") or []],
            "policy_differences": [row["field"] for row in comparison.get("policy_differences") or []],
            "missing_fields": comparison.get("missing_fields") or []}


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
    _v2.requirements = case.get("discovery", {}).get("requirements", {}).get(canonical(resource), {})
    if not _v2.requirements.get("available"):
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
    elements = _v2.requirements["configuration"]["elements"]
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
        case = await _v2.save(case, session, "coverage-stedi-submission-started")
        response = await stedi.Client(stedi.Settings.read()).check(body)
        value = {**case["transactions"][resource], **response, "source": "stedi", "resource_id": response.get("check_id"),
                 "checked_at": datetime.now(timezone.utc).isoformat(), "next_poll_at": time.time()}
        payload = results.obj(response.get("payload"))
        result = stedi_results.coverage(payload) if response.get("outcome") == "complete" else {}
        if result:
            result["identity"] = stedi_patient_binding(case, payload)
            result.update(_v2.stedi_network(case, practice, result,
                practice_id=str(session.get('practice_id') or ''), payload=payload))
        value["result"] = result
        value["message"] = "Stedi returned eligibility and benefits." if response.get("outcome") == "complete" else response.get("message")
        case["transactions"][resource], case["operation_until"] = value, 0
        return _response(_v2.public(await _v2.save(case, session, f"coverage-stedi-{value['outcome']}")))
    coverage = case["transactions"].get("coverage", {})
    if coverage.get("outcome") != "complete" or coverage.get("result", {}).get("active") is not True:
        raise HTTPException(409, "Complete an active Stedi eligibility check before calculating an estimate.")
    result = stedi_estimate.calculate(coverage["result"], service_lines(case, resource))
    case["transactions"][resource] = {"outcome": "complete", "source": "stedi_local", "submitted_at": datetime.now(timezone.utc).isoformat(),
                                       "checked_at": datetime.now(timezone.utc).isoformat(), "source_refs": source_refs, "result": result,
                                       "message": result["message"]}
    return _response(_v2.public(await _v2.save(case, session, f"{resource}-stedi-local-estimate")))

def stedi_fallback_reason(response: dict) -> str | None:
    """Only fall back after Availity's coverage answer is conclusively unusable."""
    if response.get("outcome") == "unavailable" and not response.get("resource_id"):
        return "Availity was unavailable before it returned a coverage result."
    if response.get("http_status") == 501 and not response.get("resource_id"):
        return "Availity confirmed that this coverage route is not available."
    payload = response.get("payload")
    if response.get("outcome") == "payer_error" and (payload == [] or payload == {} or not isinstance(payload, dict)):
        return "Availity returned an empty or unusable coverage response."
    if response.get("outcome") == "payer_error" and isinstance(payload, dict):
        coverages = payload.get("coverages")
        if coverages == []:
            return "Availity returned an empty coverage response."
    return None

def stedi_attempt(response: dict, reason: str) -> dict:
    return {"source": "availity", "outcome": response.get("outcome"), "http_status": response.get("http_status"),
            "transaction_id": response.get("transaction_id"), "reason": reason,
            "checked_at": datetime.now(timezone.utc).isoformat()}

async def stedi_coverage(case: dict, session: dict, practice: dict, attempts: list[dict], reason: str) -> dict:
    settings = stedi.Settings.read()
    previous = case["transactions"].get("coverage", {"submitted_at": datetime.now(timezone.utc).isoformat(),
                                                        "source_refs": {k: {n: source[n] for n in ("filename", "sha256", "observed_at", "retrieved_at") if n in source}
                                                                        for k, source in case["sources"].items()}})
    attempts = [*previous.get("attempts", []), *attempts][-10:]
    body, error = stedi.request_body(case["inputs"], practice)
    if error:
        case["transactions"]["coverage"] = {**previous, "attempts": attempts, "outcome": "payer_error", "source": "stedi",
                                                "message": f"{reason} {error}", "checked_at": datetime.now(timezone.utc).isoformat(), "result": {}}
        case["operation_until"] = 0
        return _response(_v2.public(await _v2.save(case, session, "coverage-stedi-not-configured")))
    case["transactions"]["coverage"] = {**previous, "attempts": attempts, "outcome": "submitting", "source": "stedi", "request_body": body,
                                            "message": "Availity did not return usable coverage. Submitting a Stedi eligibility check."}
    case = await _v2.save(case, session, "coverage-stedi-fallback-started")
    response = await stedi.Client(settings).check(body)
    value = {**case["transactions"]["coverage"], **response, "source": "stedi", "resource_id": response.get("check_id"),
             "checked_at": datetime.now(timezone.utc).isoformat(), "next_poll_at": time.time()}
    payload = results.obj(response.get("payload"))
    value["result"] = stedi_results.coverage(payload) if response.get("outcome") == "complete" else {}
    if value["result"]:
        value["result"]["identity"] = stedi_patient_binding(case, payload)
    if response.get("outcome") == "complete":
        value["message"] = "Stedi returned eligibility and benefits after Availity did not return a usable coverage response."
    case["transactions"]["coverage"] = value
    case["operation_until"] = 0
    return _response(_v2.public(await _v2.save(case, session, f"coverage-stedi-{value['outcome']}")))

def use_stedi_without_availity(case: dict, settings: Settings) -> bool:
    if settings.environment != "production" or stedi.Settings.read().mode != "production" or not stedi.fallback_enabled():
        return False
    coverage_requirement = case.get("discovery", {}).get("requirements", {}).get("coverage", {})
    return bool(case.get("discovery")) and not coverage_requirement.get("available")
