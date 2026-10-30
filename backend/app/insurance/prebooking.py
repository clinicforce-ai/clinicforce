"""Interpret one unsaved Stedi check for a prospective clinic patient."""
from __future__ import annotations

from app.insurance import commercial, identity


def request_body(details: dict, payer: dict, provider: dict) -> dict:
    body = {
        "payerId": payer["stediId"],
        "provider": {"name": {"organization": provider["name"]}, "npi": provider["npi"]},
        "subscriber": {"name": {"person": {"firstName": details["given_name"],
                                             "lastName": details["family_name"]}},
                       "dateOfBirth": details["birth_date"]},
        "encounter": {"services": [{"system": "STC", "value": "30"}]},
    }
    if details["member_id"]:
        body["subscriber"]["memberId"] = details["member_id"]
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
    person = dependent if dependent else subscriber
    matched = (_person_matches(person, details["given_name"], details["family_name"], details["birth_date"])
               and bool(identity.normalized(subscriber.get("memberId")))
               and (not details["member_id"] or identity.normalized(subscriber.get("memberId")) == identity.normalized(details["member_id"])))
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
