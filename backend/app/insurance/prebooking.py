"""Interpret one unsaved Stedi check for a prospective clinic patient."""
from __future__ import annotations

import re

from app.insurance import commercial, cpt_rules, identity, visit_estimate


def request_body(details: dict, payer: dict, provider: dict, *, service_type: str = "30",
                 service_date: str | None = None) -> dict:
    subscriber_first = details.get("subscriber_given_name") if details.get("relationship") == "dependent" else details["given_name"]
    subscriber_last = details.get("subscriber_family_name") if details.get("relationship") == "dependent" else details["family_name"]
    subscriber_birth = details.get("subscriber_birth_date") if details.get("relationship") == "dependent" else details["birth_date"]
    body = {
        "payerId": payer["stediId"],
        "provider": {"name": {"organization": provider["name"]}, "npi": provider["npi"]},
        "subscriber": {"name": {"person": {"firstName": subscriber_first,
                                             "lastName": subscriber_last}},
                       "dateOfBirth": subscriber_birth},
        "encounter": {"services": [{"system": "STC", "value": service_type}]},
    }
    if details.get("member_id"):
        body["subscriber"]["memberId"] = details["member_id"]
    if details.get("relationship") == "dependent":
        body["dependent"] = {"name": {"person": {"firstName": details["given_name"],
                                                   "lastName": details["family_name"]}},
                             "dateOfBirth": details["birth_date"]}
    if service_date:
        body["encounter"]["dates"] = {"service": {"start": service_date}}
    return body


def _person_matches(returned: dict, first: str, last: str, birth_date: str) -> bool:
    if not isinstance(returned, dict) or not isinstance(returned.get("name") or {}, dict):
        return False
    person = commercial.payer_person(returned)
    return (identity.normalized(person.get("firstName")) == identity.normalized(first)
            and identity.normalized(person.get("lastName")) == identity.normalized(last)
            and identity.birth_date(person.get("dateOfBirth")) == birth_date)


def matches_identity(payload: dict, details: dict) -> bool:
    """Bind every paid response to the entered person and policy."""
    subscriber = payload.get("subscriber") if isinstance(payload.get("subscriber"), dict) else {}
    dependent = payload.get("dependent") if isinstance(payload.get("dependent"), dict) else {}
    person = dependent if dependent else subscriber
    matched = (_person_matches(person, details["given_name"], details["family_name"], details["birth_date"])
               and bool(identity.normalized(subscriber.get("memberId")))
               and (not details["member_id"] or identity.normalized(subscriber.get("memberId")) == identity.normalized(details["member_id"])))
    if details.get("relationship") == "dependent":
        matched = (matched and bool(dependent) and _person_matches(
            subscriber, details["subscriber_given_name"], details["subscriber_family_name"],
            details["subscriber_birth_date"]))
    return matched


def evaluate(payload: dict, details: dict, service_date: str) -> dict:
    """Only a person-bound STC 30 response can establish current medical coverage."""
    result = {"status": "needs_review", "coverage_start": None, "coverage_end": None,
              "message": "The payer did not confirm current medical coverage. Review the details with the payer."}
    if not matches_identity(payload, details):
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
        result.update(status="active_today", message="General medical coverage is active for the checked date. Recheck for the booked visit.")
    elif benefits.get("active") is False and coverage.get("status") in {"INACTIVE", "EXPIRED", "FUTURE"}:
        result.update(status="inactive_today", message="The payer reports no active general medical coverage for the checked date. Verify before deciding how to book.")
    return result


def plan(reason: str, visit_type: str, suggestions: dict) -> dict:
    """Sanitize preliminary coding ideas; nothing is selected or priced yet."""
    groups = []
    seen = set()
    for group in (suggestions.get("groups") or [])[:8]:
        if not isinstance(group, dict):
            continue
        codes = []
        for candidate in (group.get("codes") or [])[:12]:
            code = str(candidate.get("code") or "").upper() if isinstance(candidate, dict) else ""
            if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code) or code in seen:
                continue
            seen.add(code)
            codes.append({"code": code, "reason": str(candidate.get("reason") or "Possible planned service")[:500],
                          "role": cpt_rules.service_definition(code)["role"],
                          "service_type": visit_estimate.service_type(code, {})})
        if codes:
            groups.append({"label": str(group.get("label") or "Possible services")[:120],
                           "selection_mode": "single" if group.get("selection_mode") == "single" else "multiple",
                           "codes": codes})
    if visit_type in {"new", "established"} and not any(
            cpt_rules.service_definition(row["code"])["group"] == "visit"
            for group in groups for row in group["codes"]):
        levels = range(99202, 99206) if visit_type == "new" else range(99212, 99216)
        groups.insert(0, {"label": "Office visit level (choose only if planned)", "selection_mode": "single",
                          "codes": [{"code": str(code), "reason": "Clinician must confirm the anticipated visit level.",
                                     "role": "consultation", "service_type": "98"} for code in levels]})
    return {"groups": groups, "notes": [str(note)[:500] for note in (suggestions.get("notes") or [])[:5]],
            "message": "These are possible services from the visit reason. Confirm the clinical plan and code before checking cost."}


def validate_services(services: list[dict], visit_type: str, reason: str) -> list[dict]:
    codes = [row["code"] for row in services]
    if len(set(codes)) != len(codes):
        raise ValueError("Choose each planned code only once.")
    if sum(cpt_rules.service_definition(code)["group"] == "visit" for code in codes) > 1:
        raise ValueError("Choose only one office visit level.")
    if visit_type == "new" and any(code in {str(n) for n in range(99212, 99216)} for code in codes):
        raise ValueError("Established-patient visit codes do not match a new patient.")
    if visit_type == "established" and any(code in {str(n) for n in range(99202, 99206)} for code in codes):
        raise ValueError("New-patient visit codes do not match an established patient.")
    issues = cpt_rules.service_selection_issues(codes)
    if issue := cpt_rules.dbs_plan_issue(codes, {"reason": reason}):
        issues.append(issue)
    if issues:
        raise ValueError(issues[0])
    return [{"id": f"prospective:{row['code']}", "code": row["code"], "units": row["units"],
             "modifiers": [], "source": "Clinician-confirmed prospective plan", "confirmed": True}
            for row in services]
