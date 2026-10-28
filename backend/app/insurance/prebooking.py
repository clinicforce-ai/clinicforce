"""Interpret one unsaved Stedi check for a prospective clinic patient."""
from __future__ import annotations

from app.insurance import commercial, identity, stedi


def request_body(details: dict, payer: dict, provider: dict) -> dict:
    self_policy = details["relationship"] == "self"
    subscriber = {
        "firstName": details["given_name"] if self_policy else details["subscriber_given_name"],
        "lastName": details["family_name"] if self_policy else details["subscriber_family_name"],
        "birthDate": details["birth_date"] if self_policy else details["subscriber_birth_date"],
        "memberId": details["member_id"],
    }
    body, error = stedi.request_body({
        "payer_id": payer["id"], "provider": {"npi": provider["billing_npi"]},
        "relationship": "18" if self_policy else "19", "subscriber": subscriber,
        "patient": {"patient_first_name": details["given_name"],
                    "patient_last_name": details["family_name"],
                    "patient_birth_date": details["birth_date"]},
    }, {"stedi_payer_ids": {payer["id"]: payer["stedi_payer_id"]},
        "providers": {provider["billing_npi"]: {"stedi_organization_name": provider["organization_name"]}}},
        service_type="30")
    if error or not body:
        raise ValueError("The selected payer or clinic provider cannot be used for an eligibility check.")
    return body


def _person_matches(returned: dict, first: str, last: str, birth_date: str) -> bool:
    if not isinstance(returned, dict) or not isinstance(returned.get("name") or {}, dict):
        return False
    person = commercial.payer_person(returned)
    return (identity.normalized(person.get("firstName")) == identity.normalized(first)
            and identity.normalized(person.get("lastName")) == identity.normalized(last)
            and identity.birth_date(person.get("dateOfBirth")) == birth_date)


def evaluate(payload: dict, details: dict, service_date: str) -> dict:
    """Only a person-bound STC 30 response can establish current medical coverage."""
    subscriber = payload.get("subscriber") if isinstance(payload.get("subscriber"), dict) else {}
    dependent = payload.get("dependent") if isinstance(payload.get("dependent"), dict) else {}
    self_policy = details["relationship"] == "self"
    matched = (identity.normalized(subscriber.get("memberId")) == identity.normalized(details["member_id"])
               and _person_matches(subscriber,
                   details["given_name"] if self_policy else details["subscriber_given_name"],
                   details["family_name"] if self_policy else details["subscriber_family_name"],
                   details["birth_date"] if self_policy else details["subscriber_birth_date"])
               and (self_policy or _person_matches(dependent, details["given_name"],
                                                   details["family_name"], details["birth_date"])))
    result = {"status": "needs_review", "coverage_start": None, "coverage_end": None,
              "message": "The payer did not confirm current medical coverage. Review the details with the payer."}
    if not matched:
        result["message"] = "The payer response did not confirm this person's identity and policy. Review the details with the payer."
        return result
    if commercial.eligibility_errors(payload) or str(payload.get("status") or "").upper() in {"ERROR", "FAILED", "REJECTED"}:
        return result
    try:
        benefits = commercial.normalize_stedi_271(payload, "30", False, service_date=service_date)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return result
    coverage = benefits.get("coverage_evidence") or {}
    result.update(coverage_start=coverage.get("start"), coverage_end=coverage.get("end"))
    if (benefits.get("coverage_dates_invalid") or benefits.get("coverage_status_conflict")
            or benefits.get("service_date_matches") is False or benefits.get("source_errors")):
        return result
    if benefits.get("active") is True and coverage.get("status") == "ACTIVE":
        result.update(status="active_today", message="General medical coverage is active today. Recheck for the booked visit.")
    elif benefits.get("active") is False and coverage.get("status") in {"INACTIVE", "EXPIRED", "FUTURE"}:
        result.update(status="inactive_today", message="The payer reports no active general medical coverage today. Verify before deciding how to book.")
    return result
