"""Pure, source-neutral commercial-estimate rules.

This module deliberately does not call a payer.  It validates the durable
configuration/evidence selected by staff and either produces an explainable
estimate or a precise review reason.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from typing import Any


class CommercialError(ValueError):
    pass


def eligibility_errors(payload: dict) -> list[dict]:
    """Both Stedi layouts can put AAA errors on the subscriber or dependent."""
    rows = list(payload.get('errors') or [])
    parties = [payload.get('subscriber'), payload.get('dependent'), *(payload.get('dependents') or [])]
    for party in parties:
        if isinstance(party, dict):
            rows.extend(party.get('aaaErrors') or [])
    return [row for row in rows if isinstance(row, dict)]


def eligibility_failure(payload: dict) -> dict:
    """Safe, actionable explanations; arbitrary response text stays protected."""
    errors = eligibility_errors(payload)
    codes = sorted({str(row.get('code')) for row in errors if re.fullmatch(r'\d{2}', str(row.get('code') or ''))})
    validation = any(row.get('path') for row in errors)
    title, action = 'The payer could not complete the check', 'Review the saved payer response before checking again.'
    if '72' in codes:
        title = 'The payer rejected the member ID'
        action = 'Compare the member ID and insurer in eCW with the insurance card. Correct the policy in eCW, then check again.'
    elif '73' in codes:
        title = 'The payer rejected the subscriber name'
        action = 'The member ID was sent, but the payer could not validate the subscriber name. Compare the policyholder name in eCW with the current card and payer record; a saved member ID does not resolve a name mismatch.'
    elif set(codes) & {'62', '63'}:
        title = 'The payer could not verify this appointment date'
        action = 'The requested service date is outside this payer’s inquiry window. Recheck closer to the appointment; earlier eligibility does not confirm this visit.'
    elif set(codes) & {'65', '67', '71', '75', '76'}:
        title = 'The payer could not match the patient'
        action = 'Compare the policyholder name, date of birth and member ID in eCW with the insurance card, then check again after correction.'
    elif set(codes) & {'42', '80'}:
        title = 'The payer is temporarily unavailable'
        action = 'Check again after the payer service recovers.'
    elif set(codes) & {'41', '43', '79'}:
        title = 'Payer or provider access needs attention'
        action = 'The clinic administrator should review the payer connection and provider enrollment before checking again.'
    elif validation:
        title = 'ClinicForce could not submit a valid request'
        action = 'The integration needs correction; this is not an inactive-coverage result.'
    return {'kind': 'request_validation' if validation else 'eligibility_rejected',
            'codes': codes, 'title': title, 'action': action,
            'detail': 'No usable eligibility benefits were returned. This does not establish inactive coverage.'}


SERVICE_CATEGORIES = {"office", "emg_ncs"}
RATE_SOURCE_TYPES = {"payer_feed", "contract_export", "transparency_file", "manual"}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _money(value: object, label: str) -> int:
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except Exception as exc:
        raise CommercialError(f"{label} must be a dollar amount.") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        raise CommercialError(f"{label} is outside the supported range.")
    cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if cents != amount * 100:
        raise CommercialError(f"{label} must have at most two decimal places.")
    return int(cents)


def _integer(value: object, label: str, maximum: int = 10000) -> int:
    try:
        result = int(str(value).strip())
    except Exception as exc:
        raise CommercialError(f"{label} must be a whole number.") from exc
    if not 1 <= result <= maximum:
        raise CommercialError(f"{label} must be between 1 and {maximum}.")
    return result


def context_fingerprint(secret: str, context: dict[str, object]) -> str:
    """Return a non-reversible lookup key; callers must not log its input."""
    if not secret:
        raise CommercialError("Commercial evidence fingerprinting is not configured.")
    fields = ("practice_id", "patient_id", "coverage_id", "subscriber_relationship", "payer_connection_id",
              "service_date", "rendering_npi", "place_of_service", "service_category", "appointment_id", "member_id", "ecw_coverage_id")
    canonical = "|".join(str(context.get(field) or "") for field in fields)
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def parse_rate_csv(content: bytes, filename: str) -> list[dict[str, object]]:
    """Parse a governed CSV, never a patient or payer response file."""
    if not filename.lower().endswith(".csv"):
        raise CommercialError("Rate imports must be UTF-8 CSV files.")
    try:
        reader = csv.DictReader(StringIO(content.decode("utf-8-sig")), strict=True)
        rows = list(reader)
    except Exception as exc:
        raise CommercialError("Rate import is not a readable UTF-8 CSV.") from exc
    if not rows or len(rows) > 20000:
        raise CommercialError("Rate imports must contain 1–20,000 rows.")
    aliases = {
        "cpt": "code", "hcpcs": "code", "procedurecode": "code", "code": "code",
        "description": "description", "allowedamount": "allowed", "contractedallowedamount": "allowed",
        "cashprice": "cash", "modifier": "modifier", "unitrule": "unit_rule", "network": "network",
        "plan": "plan_matcher", "planmatcher": "plan_matcher", "pos": "pos", "placeofservice": "pos",
        "providernpi": "provider_npi", "renderingnpi": "provider_npi", "tin": "tin", "taxid": "tin",
        "effectivestart": "effective_from", "effectivefrom": "effective_from", "effectiveend": "effective_to",
        "effectiveto": "effective_to",
    }
    mapped = {_key(name): aliases.get(_key(name)) for name in reader.fieldnames or []}
    required = {"code", "allowed", "network", "pos", "effective_from"}
    if not required.issubset(set(mapped.values())):
        raise CommercialError("Rate CSV requires CPT/HCPCS, Allowed Amount, Network, POS, and Effective From columns.")
    parsed: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for row_number, raw in enumerate(rows, 2):
        row = {mapped[_key(name)]: str(value or "").strip() for name, value in raw.items()
               if mapped.get(_key(name))}
        code = row.get("code", "").replace(".", "").upper()
        if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
            raise CommercialError(f"Rate CSV row {row_number} has an invalid CPT/HCPCS code.")
        for field in ("network", "pos", "effective_from"):
            if not row.get(field):
                raise CommercialError(f"Rate CSV row {row_number} is missing {field.replace('_', ' ')}.")
        if row.get("effective_to") and row["effective_to"] < row["effective_from"]:
            raise CommercialError(f"Rate CSV row {row_number} ends before it starts.")
        key = (code, row.get("modifier", ""), row["network"].casefold(), row["pos"],
               row.get("plan_matcher", "").casefold(), row.get("provider_npi", ""))
        if key in seen:
            raise CommercialError(f"Rate CSV contains duplicate matching rows for {code}.")
        seen.add(key)
        parsed.append({
            "row_number": row_number, "code": code, "description": row.get("description", "")[:300],
            "allowed_cents": _money(row.get("allowed"), f"Allowed Amount on row {row_number}"),
            "cash_cents": _money(row["cash"], f"Cash Price on row {row_number}") if row.get("cash") else None,
            "modifier": row.get("modifier", "")[:10], "unit_rule": row.get("unit_rule", "per_unit")[:40],
            "network": row["network"][:120], "plan_matcher": row.get("plan_matcher", "")[:200],
            "pos": row["pos"][:20], "provider_npi": row.get("provider_npi", "")[:10],
            "tin": row.get("tin", "")[:20], "effective_from": row["effective_from"],
            "effective_to": row.get("effective_to") or None,
        })
    return parsed


def _benefit_cents(evidence: dict[str, Any], key: str) -> int | None:
    value = evidence.get(key)
    if value in {None, ""}:
        return None
    try:
        cents = int(str(value).strip())
    except Exception as exc:
        raise CommercialError(f"{key.replace('_', ' ')} must be whole cents.") from exc
    if not 0 <= cents <= 100_000_000:
        raise CommercialError(f"{key.replace('_', ' ')} is outside the supported range.")
    return cents


def _stedi_cents(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _stedi_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep source scope and paths while adapting both Stedi response layouts."""
    entries = [{**row, "evidence_path": f"benefitsInformation[{i}]"}
               for i, row in enumerate(payload.get("benefitsInformation") or []) if isinstance(row, dict)]
    for index, plan in enumerate(payload.get("plans") or []):
        if not isinstance(plan, dict):
            continue
        for i, row in enumerate(plan.get("benefitsInformation") or []):
            if isinstance(row, dict):
                entries.append({**row, "plan_index": index, "evidence_path": f"plans[{index}].benefitsInformation[{i}]"})
        benefits = plan.get("benefits") or {}
        if not isinstance(benefits, dict):
            continue
        for category, code in (("statuses", "status"), ("coInsurance", "A"), ("coPayment", "B"),
                               ("deductible", "C"), ("outOfPocket", "G"), ("limitations", "F"),
                               ("exclusions", "I"), ("nonCovered", "I"), ("benefitDescription", "D"), ("coverageBasis", "D")):
            for i, row in enumerate(benefits.get(category) or []):
                if not isinstance(row, dict):
                    continue
                service = row.get("service") or {}
                stcs = ([str(service["value"])] if isinstance(service, dict) and
                        str(service.get("system") or "").upper() == "STC" and service.get("value") else row.get("serviceTypeCodes") or [])
                entries.append({**row, "code": code, "benefitAmount": row.get("amount"),
                                "timeQualifier": row.get("timePeriod"), "serviceTypeCodes": stcs,
                                "plan_index": index, "planCoverage": plan.get("name"),
                                "evidence_path": f"plans[{index}].benefits.{category}[{i}]"})
    return entries


def _procedure(row: dict) -> str:
    service = row.get("service") or {}
    if isinstance(service, dict) and str(service.get("system", "")).upper() in {"CPT", "HCPCS", "CDT"}:
        return str(service.get("value") or "")
    return str((row.get("compositeMedicalProcedureIdentifier") or {}).get("procedureCode") or "")


def _network_indicator(row: dict) -> str:
    network = row.get("network")
    return str((network.get("indicator") or network.get("code") or "") if isinstance(network, dict)
               else row.get("inPlanNetworkIndicatorCode") or row.get("inPlanNetworkIndicator") or "").upper()


def payer_person(person: dict) -> dict:
    name = (person.get('name') or {}).get('person') or person
    return {**{key: name.get(key) for key in ('firstName', 'lastName', 'middleName')},
            **{key: person.get(key) for key in ('memberId', 'dateOfBirth')}}


def plan_details(payload: dict) -> dict:
    rows = _stedi_entries(payload)
    names = list(dict.fromkeys(str(row.get("planCoverage") or row.get("planCoverageDescription") or "").strip()
                              for row in rows if not row.get("serviceTypeCodes") or "30" in row["serviceTypeCodes"]))
    names = list(dict.fromkeys([str(plan['name']).strip() for plan in payload.get('plans', [])
                               if isinstance(plan, dict) and plan.get('name')] + [name for name in names if name]))
    subscriber = payload.get("subscriber") or {}
    info = payload.get("planInformation") or {}
    additional = (payload.get('dependent') or subscriber).get("additionalInformation") or subscriber.get('additionalInformation') or {}
    return {"names": names, "plan_number": subscriber.get("planNumber") or info.get("planNumber") or (additional.get('plan') or {}).get('number'),
            "group_number": subscriber.get("groupNumber") or info.get("groupNumber") or (additional.get("group") or {}).get("number"),
            "source": "Stedi"}


def reported_stedi_options(payload: dict[str, Any], service_type_code: str) -> list[dict[str, Any]]:
    """Preserve distinct payer terms without selecting one for a price calculation."""
    kinds = {"A": "coinsurance", "B": "copay", "C": "deductible", "G": "out_of_pocket", "F": "limit"}
    result = []
    for row in _stedi_entries(payload):
        code = str(row.get("code") or "").upper()
        service_types = [str(value) for value in row.get("serviceTypeCodes") or []]
        if code not in kinds:
            continue
        indicator = _network_indicator(row)
        raw_percent = row.get("benefitPercent") if row.get("benefitPercent") is not None else row.get("percent")
        try:
            percent = Decimal(str(raw_percent)) if raw_percent is not None else None
            if percent is not None and Decimal("0") <= percent <= Decimal("1"):
                percent *= 100
            if percent is not None and not Decimal("0") <= percent <= Decimal("100"):
                percent = None
        except (ArithmeticError, TypeError, ValueError):
            percent = None
        def note_text(value: Any) -> str:
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return " ".join(filter(None, (note_text(item) for item in value)))
            if isinstance(value, dict):
                return " ".join(filter(None, (note_text(value.get(key)) for key in ("description", "message", "text", "messages"))))
            return ""
        result.append({"id": row["evidence_path"], "kind": kinds[code], "service_type_code": ", ".join(service_types),
                       "procedure_code": _procedure(row), "coverage_level": row.get("coverageLevel") or row.get("coverageLevelCode"),
                       "plan_index": row.get("plan_index"), "plan_name": row.get("planCoverage") or row.get("planCoverageDescription"),
                       "network": indicator, "time_period": str(row.get("timeQualifierCode") or row.get("timeQualifier") or ""),
                       "amount_cents": _stedi_cents(row.get("benefitAmount")),
                       "quantity": row.get('quantity') if isinstance(row.get('quantity'), dict) else None,
                       "percent": str(percent) if percent is not None else None,
                       "payer_note": note_text([row.get("messages"), row.get("additionalInformation")])})
    return result


def _dates_apply(dates: dict, service_date: str) -> bool:
    """Check documented eligibility/plan bounds without treating an annual total as remaining."""
    from datetime import date
    visit = date.fromisoformat(service_date)
    for key, value in dates.items():
        if key not in {'planBegin', 'planEnd', 'eligibilityBegin', 'eligibilityEnd', 'benefitBegin', 'benefitEnd', 'plan', 'eligibility', 'benefit', 'policyEffective', 'policyExpiration'} or not value:
            continue
        if isinstance(value, dict):
            start, end = value.get('start'), value.get('end')
            if start and visit < date.fromisoformat(str(start)):
                return False
            if end and visit > date.fromisoformat(str(end)):
                return False
            continue
        parts = str(value).split('-') if re.fullmatch(r'\d{8}-\d{8}', str(value)) else [str(value)]
        parsed = [date.fromisoformat(f'{part[:4]}-{part[4:6]}-{part[6:]}' if re.fullmatch(r'\d{8}', part) else part) for part in parts]
        if len(parsed) == 2:
            if not parsed[0] <= visit <= parsed[1]:
                return False
        elif key.endswith(('Begin', 'Effective')) and visit < parsed[0]:
            return False
        elif key.endswith(('End', 'Expiration')) and visit > parsed[0]:
            return False
    return True


def _benefit_notes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [note for item in value for note in _benefit_notes(item)]
    if isinstance(value, dict):
        return [note for key in ('description', 'message', 'text', 'messages') for note in _benefit_notes(value.get(key))]
    return []


def normalize_stedi_271(payload: dict[str, Any], service_type_code: str, provider_network_verified: bool,
                        other_active_coverage: bool = False, *, procedure_code: str | None = None,
                        service_date: str | None = None, selected_ids: list[str] | None = None,
                        office_service_types: list[str] | None = None, place_of_service: str | None = None) -> dict[str, Any]:
    """Normalize only unambiguous individual in-network schedule benefits.

    A 271 benefit network is not provider participation.  That is supplied by
    the separately verified provider-network record.
    """
    entries = _stedi_entries(payload)
    if place_of_service:
        # Office and telehealth terms can share STC 98 but are different benefits.
        entries = [row for row in entries if not row.get('placesOfService') or
                   any(str(pos.get('code') or '') == place_of_service for pos in row['placesOfService'] if isinstance(pos, dict))]
    diagnostic = procedure_code in {*(str(n) for n in range(95907, 95914)), '95885', '95886', '95887'}
    if diagnostic:
        # Sleep studies and radiology share STC 73 with EMG. Their presence
        # must not prevent use of an explicitly labelled diagnostic benefit.
        entries = [row for row in entries if _procedure(row) or not any(
            re.fullmatch(r'(?:sleep study|x[ -]?ray)(?:\s*-\s*professional)?', note.strip(), re.I)
            for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    if diagnostic and service_type_code == '73' and not any('73' in (row.get('serviceTypeCodes') or []) and row.get('code') in {'A', 'B'} for row in entries):
        # Some plans return diagnostic procedure terms under 98. Require that
        # description; an office-only copay is not an EMG benefit.
        diagnostic_rows = [row for row in entries if '98' in (row.get('serviceTypeCodes') or [])
            and any(re.search(r'\bdiagnostic medical(?: procedure)?\b', note, re.I)
                    for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
        if diagnostic_rows:
            service_type_code = '98'
            entries = [row for row in entries if '98' not in (row.get('serviceTypeCodes') or []) or row in diagnostic_rows]
    office = procedure_code in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
    if office and selected_ids is None:
        # Explicitly different services sometimes share an office STC. Retain
        # every raw row in reported_options, but exclude these from office math.
        unrelated = r'(?:injections?|diagnostic lab(?:oratory)?|laboratory|(?:diagnostic )?x[ -]?ray|acupuncture|applied behavior analysis|chemotherapy)(?:\s*-\s*professional)?'
        def other_services_only(row: dict) -> bool:
            # A shared accumulator can list office visits alongside injections
            # and lab work. Discard only a list entirely naming other services.
            labels = [label.strip() for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')])
                      for label in note.split(';') if label.strip()
                      and not re.fullmatch(r'(?:no referral required|referral not required|(?:prior )?authorization (?:not )?required)\.?', label.strip(), re.I)]
            return bool(labels) and all(re.fullmatch(unrelated, label, re.I) for label in labels)
        entries = [row for row in entries if _procedure(row) or not other_services_only(row)]
        priorities = office_service_types or ['BY', '98']
        if 'BQ' in priorities:
            # Neurology is specialist care. Keep generic and specialist rows,
            # excluding rows explicitly limited to primary care.
            entries = [row for row in entries if not any(re.search(r'\b(?:primary care|PCP)\b', note, re.I)
                and not re.search(r'\bspecialist\b', note, re.I)
                for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
            for kind in ('A', 'B'):
                specialist = [row for row in entries if row.get('code') == kind and any(
                    re.search(r'\bspecialist\b', note, re.I)
                    for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
                if place_of_service and any(row.get('placesOfService') for row in specialist):
                    specialist = [row for row in specialist if row.get('placesOfService')]
                if specialist:
                    entries = [row for row in entries if row.get('code') != kind or _procedure(row) or row in specialist]
    def relevant_service(row: dict) -> bool:
        procedure = _procedure(row)
        if procedure:
            return procedure == procedure_code
        stcs = {str(value) for value in row.get("serviceTypeCodes") or []}
        return not stcs or bool(stcs & ({*priorities, "30"} if office and selected_ids is None else {service_type_code, "30"}))
    # Out-of-network noncoverage and restrictions must not cancel in-network
    # coverage. Raw reported options retain both network schedules below.
    entries = [row for row in entries if relevant_service(row)
               and _network_indicator(row) not in {'N', 'NO', 'OUT_OF_NETWORK'}]
    invalid_dates = False
    person_dates = (payload.get('dependent') or payload.get('subscriber') or {}).get('dates') or {}
    returned_service = person_dates.get('service') or {}
    date_matches = None
    if service_date and returned_service.get('start'):
        try:
            date_matches = date.fromisoformat(returned_service['start']) <= date.fromisoformat(service_date) <= date.fromisoformat(returned_service.get('end') or returned_service['start'])
        except (ValueError, TypeError):
            invalid_dates = True
    if service_date:
        applicable = []
        for row in entries:
            plan = (payload.get('plans') or [])[row['plan_index']] if 'plan_index' in row else payload
            bounds = [person_dates, payload.get('planDateInformation') or {}, plan.get('planDateInformation') or {},
                      plan.get('dates') or {}, row.get('benefitsDateInformation') or {}, row.get('dates') or {}]
            try:
                if all(_dates_apply(bound, service_date) for bound in bounds):
                    applicable.append(row)
            except (ValueError, TypeError):
                invalid_dates = True
        entries = applicable
    if office and selected_ids is None:
        # Use the best returned category that actually contains cost sharing.
        service_type_code = next((stc for stc in priorities if any(
            row.get('code') in {'A', 'B'} and stc in (row.get('serviceTypeCodes') or [])
            and _network_indicator(row) in {'Y', 'YES', 'IN_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'}
            for row in entries)), service_type_code)
        entries = [row for row in entries if _procedure(row) or not row.get('serviceTypeCodes')
                   or set(row['serviceTypeCodes']) & {service_type_code, '30'}]
    statuses = [row for row in entries if row.get("code") in {"status", "1", "2", "3", "4", "5", "6", "7", "8", "I"}]
    if statuses:
        specificity = lambda row: 3 if _procedure(row) else 2 if service_type_code in (row.get("serviceTypeCodes") or []) else 1
        best = max(map(specificity, statuses))
        statuses = [row for row in statuses if specificity(row) == best]
    status_text = " ".join(str(row.get("status") or "") for row in statuses).casefold()
    inactive = any(token in status_text for token in ("inactive", "ineligible", "terminated", "cancelled", "canceled", "expired")) or any(row.get("code") in {"6", "7", "8", "I"} for row in statuses)
    has_active = "active" in status_text.replace("inactive", "") or "eligible" in status_text.replace("ineligible", "") or any(row.get("code") in {"1", "2", "3", "4", "5"} for row in statuses)
    active = None if inactive and has_active else False if inactive else True if has_active else None
    response_failed = str(payload.get("status") or "").upper() in {"ERROR", "FAILED", "REJECTED"} or bool(eligibility_errors(payload))
    if response_failed or any(row.get("code") == "V" for row in entries):
        active = None
    wanted = []
    for row in entries:
        level = str(row.get("coverageLevel") or row.get("coverageLevelCode") or "").upper()
        network = row.get("network")
        indicator = str((network.get("indicator") or network.get("code")) if isinstance(network, dict)
                        else row.get("inPlanNetworkIndicatorCode") or row.get("inPlanNetworkIndicator") or "").upper()
        service_types = {str(value) for value in row.get("serviceTypeCodes", []) if value is not None}
        # Specific requested STC wins; general medical (30) is an allowed
        # fallback only when no specific counterpart exists.
        specificity = 3 if _procedure(row) and _procedure(row) == procedure_code else 2 if service_type_code in service_types else 1 if "30" in service_types else 0
        if (level not in {"INDIVIDUAL", "IND"} and not (not level and row.get("code") in {"A", "B"})) or indicator not in {"Y", "YES", "IN_NETWORK", "IN_AND_OUT_OF_NETWORK", "W"} or not specificity:
            continue
        wanted.append((specificity, row))
    def pick(code: str, remaining: bool = False) -> dict[str, Any] | None:
        matches = [(specificity, row) for specificity, row in wanted if str(row.get("code") or "").upper() == code and (not remaining or str(row.get("timeQualifierCode") or row.get("timeQualifier") or "").upper() in {"29", "REMAINING"})]
        if not matches:
            return None
        if selected_ids is not None and code in {"A", "B"}:
            matches = [(score, row) for score, row in matches if row["evidence_path"] in selected_ids]
            if not matches:
                return None
        best = max(score for score, _ in matches)
        selected = [row for score, row in matches if score == best]
        # Compare every source field, including unmodelled restrictions. Only
        # evidence location differs for an actual duplicate.
        signatures = {json.dumps({k: v for k, v in row.items() if k != 'evidence_path'}, sort_keys=True)
                      for row in selected}
        return {**selected[0], 'evidence_paths': [row['evidence_path'] for row in selected]} if len(signatures) == 1 else None
    deductible, oop = pick("C", True), pick("G", True)
    copay, coinsurance = pick("B"), pick("A")
    relevant = [row for row in entries if not row.get("serviceTypeCodes") or
                service_type_code in {str(value) for value in row.get("serviceTypeCodes", [])} or
                "30" in {str(value) for value in row.get("serviceTypeCodes", [])}]
    def scoped_auth(row: dict) -> str:
        indicator = str(row.get('authOrCertIndicator') or row.get('priorAuthIndicator') or '').upper()
        # A category contains many procedures. A requirement tied to a named
        # benefit (for example ABA) is not a requirement for every CPT in it.
        if indicator in {'Y', 'REQUIRED'} and row.get('messages') and not _procedure(row):
            return 'UNKNOWN'
        return indicator
    authorization_values = {scoped_auth(row) for row in relevant}
    authorization_signal = ("required" if authorization_values & {"Y", "REQUIRED"}
                            else "unknown" if not authorization_values & {"N", "NOT_REQUIRED"} or authorization_values & {"U", "UNKNOWN"}
                            else "not_required")
    def notes(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [note for item in value for note in notes(item)]
        if isinstance(value, dict):
            return [note for key in ("description", "message", "text", "messages") for note in notes(value.get(key))]
        return []
    descriptions = [note.casefold() for row in relevant for field in ("additionalInformation", "messages")
                    for note in notes(row.get(field))]
    authorization_basis = 'Payer indicator' if authorization_values - {''} else 'Not confirmed'
    if any(scoped_auth(row) == 'UNKNOWN' and row.get('messages') for row in relevant):
        authorization_basis = 'Payer requirement is attached to a named benefit; applicability to this service is not established'
    auth_notes = [note for note in descriptions if re.search(r'authoriz|precert|pre-cert', note)]
    if auth_notes:
        # Free text is evidence to review, not a standardized approval signal.
        if authorization_signal != 'required':
            authorization_signal = 'unknown'
        authorization_basis = 'Payer notes require review'
    elif authorization_values <= {''} and active is True and wanted and not response_failed:
        authorization_signal = 'not_required'
        authorization_basis = 'Derived from absent indicator in applicable response (Stedi guidance)'
    # Contradictory terms must not be cleared by one "not required" message.
    no_referral = any("referral not required" in note or "no referral required" in note for note in descriptions)
    referral_required = any(re.search(r"referral(?:s)? (?:is |are )?required", re.sub(r"no referral required|referral not required", "", note)) for note in descriptions)
    referral_signal = "required" if referral_required else "not_required" if no_referral else "unknown"
    deductible_cents = _stedi_cents(deductible.get("benefitAmount")) if deductible else None
    oop_cents = _stedi_cents(oop.get("benefitAmount")) if oop else None
    copay_cents = _stedi_cents(copay.get("benefitAmount")) if copay else None
    try:
        raw_percent = (coinsurance.get("benefitPercent") if coinsurance.get("benefitPercent") is not None else coinsurance.get("percent")) if coinsurance else None
        coinsurance_percent = Decimal(str(raw_percent)) if raw_percent is not None else None
        # X12 percentage values are commonly serialized as fractions (for
        # example, .20 means 20%), while some payer JSON layouts use 20.
        if coinsurance_percent is not None and Decimal("0") <= coinsurance_percent <= Decimal("1"):
            coinsurance_percent *= 100
        if coinsurance_percent is not None and not Decimal("0") <= coinsurance_percent <= Decimal("100"):
            coinsurance_percent = None
    except Exception:
        coinsurance_percent = None
    multiple_plans = len({row["plan_index"] for row in entries if "plan_index" in row and row.get("code") in {"status", "A", "B", "C", "G"}}) > 1
    limitations = any(str(row.get("code") or "").upper() in {"F", "Y"} for row in relevant)
    conditional_terms = selected_ids is None and any(re.search(r"\b(first|after|before|beginning|visits?|waived|unless|only|limit|specialist|primary care)\b", note, re.IGNORECASE) for row in (copay, coinsurance) if row for note in notes([row.get("messages"), row.get("additionalInformation")])
        if not all(re.fullmatch(r"(?:(?:specialist |physician )?office visits?(?: (?:specialist|primary care(?: prov(?:ider)?)?))?|phy visit\s*-\s*specialist|(?:PCP AND )?BenefitTier Non Tier 1 Specialist|diagnostic medical procedure)(?:\s*-\s*professional)?", label.strip(), re.I)
                   for label in note.split(';') if label.strip()))
    review_reasons = []
    if invalid_dates:
        review_reasons.append("Verify the payer eligibility dates; an applicable date value could not be interpreted.")
    if date_matches is False:
        review_reasons.append(f"Payer evidence covers service date {returned_service['start']}; eligibility for the requested appointment on {service_date} needs verification.")
    if active is not True:
        review_reasons.append("Confirm active coverage for the visit date with the payer.")
    if deductible_cents is None:
        review_reasons.append("Confirm the remaining individual deductible that applies to this visit.")
    if oop_cents is None:
        review_reasons.append("Confirm the remaining individual out-of-pocket maximum.")
    if copay_cents is None and coinsurance_percent is None:
        review_reasons.append("Confirm which copay or coinsurance term applies, including any visit-count conditions.")
    if authorization_signal != "not_required":
        review_reasons.append("Confirm whether authorization applies to this service and, if required, that it is approved.")
    if referral_signal != "not_required":
        review_reasons.append("Confirm the plan's referral requirement and, if required, that a referral is on file.")
    if conditional_terms:
        review_reasons.append("Confirm which conditional benefit applies to the selected service.")
    if limitations:
        review_reasons.append("Review the payer's benefit limits before estimating patient cost.")
    if multiple_plans or other_active_coverage:
        review_reasons.append("Confirm which plan pays first and how additional coverage applies.")
    return {"active": active, "service_date_matches": date_matches, "returned_service_dates": returned_service,
            "coverage_level": "individual" if wanted else "unknown",
            "network_status": "in_network" if provider_network_verified else "unknown",
            "deductible_remaining_cents": deductible_cents, "oop_remaining_cents": oop_cents,
            "copay_cents": copay_cents, "coinsurance_percent": str(coinsurance_percent) if coinsurance_percent is not None else None,
            "has_secondary": other_active_coverage or multiple_plans, "requires_review": bool(review_reasons),
            "selected_benefit_ids": [path for row in (copay, coinsurance) if row for path in row.get('evidence_paths', [row['evidence_path']])],
            "deductible_id": deductible.get("evidence_path") if deductible else None,
            "oop_id": oop.get("evidence_path") if oop else None,
            "copay_id": copay.get("evidence_path") if copay else None,
            "authorization_basis": authorization_basis,
            "benefit_assumptions": ['The returned non-Tier-1 specialist benefit is used; the provider tier has not been confirmed.'] if any(
                'non tier 1 specialist' in note.casefold() for row in (copay, coinsurance) if row
                for note in _benefit_notes(row.get('messages'))) else [],
            "copay_period": str((copay or {}).get("timeQualifierCode") or (copay or {}).get("timeQualifier") or ""),
            "plan_details": plan_details(payload),
            "authorization_signal": authorization_signal, "referral_signal": referral_signal,
            "review_reasons": review_reasons,
            "normalization_notes": ["Stedi 271 benefit schedule does not prove provider participation."],
            "resolved_service_type": service_type_code,
            "network_independent": bool(copay or coinsurance) and all(_network_indicator(row) in {"W", "IN_AND_OUT_OF_NETWORK"} for row in (copay, coinsurance, deductible, oop) if row),
            "reported_options": reported_stedi_options(payload, service_type_code)}


def estimate(lines: list[dict[str, Any]], benefits: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Calculate one in-network primary-plan encounter or fail closed."""
    if not lines:
        raise CommercialError("Confirm at least one service before calculating an estimate.")
    if benefits.get("active") is not True:
        raise CommercialError("Active primary coverage is required for an insured estimate.")
    if benefits.get("coverage_level") != "individual":
        raise CommercialError("A compatible individual benefit level is required for an automatic estimate.")
    if benefits.get("network_status") != "in_network":
        raise CommercialError("Confirmed in-network provider participation is required for an automatic estimate.")
    if benefits.get("has_secondary"):
        raise CommercialError("Multiple active coverages require staff review.")
    if benefits.get("requires_review"):
        raise CommercialError(" ".join(benefits.get("review_reasons") or
                                      ["The selected benefits contain an unresolved limit, authorization, referral, or payer review."]))
    if _benefit_cents(benefits, 'oop_remaining_cents') == 0:
        # Covered in-network services cannot exceed an exhausted patient cap.
        # These calculation inputs do not overwrite the returned benefit facts.
        benefits = {**benefits, 'deductible_remaining_cents': 0, 'coinsurance_percent': '0', 'copay_cents': 0}
        rules = {**rules, 'cost_share': 'coinsurance'}
    cost_share = rules.get("cost_share")
    if cost_share == "payer_returned":
        has_copay = benefits.get("copay_cents") is not None
        has_coinsurance = benefits.get("coinsurance_percent") is not None
        # A returned zero-percent term adds no cost share to the fixed copay.
        if has_copay and benefits.get('coinsurance_percent') is not None and Decimal(str(benefits['coinsurance_percent'])) == 0:
            has_coinsurance = False
        elif has_coinsurance and benefits.get("copay_cents") == 0:
            has_copay = False
        if has_copay == has_coinsurance:
            raise CommercialError("Payer benefits did not identify one unambiguous office-visit cost-share method.")
        if has_copay and _benefit_cents(benefits, "deductible_remaining_cents"):
            raise CommercialError("Payer did not confirm whether the office-visit copay is subject to the remaining deductible.")
        cost_share = "copay" if has_copay else "coinsurance"
    if cost_share not in {"coinsurance", "copay", "cumulative"}:
        raise CommercialError("The estimation profile does not define its copay/coinsurance rule.")
    deductible_remaining = _benefit_cents(benefits, "deductible_remaining_cents")
    oop_remaining = _benefit_cents(benefits, "oop_remaining_cents")
    if deductible_remaining is None or oop_remaining is None:
        raise CommercialError("Remaining individual deductible and out-of-pocket values are required.")
    coinsurance_percent = benefits.get("coinsurance_percent")
    copay_cents = _benefit_cents(benefits, "copay_cents")
    if cost_share in {"copay", "cumulative"} and copay_cents is None:
        raise CommercialError("A copay amount or verified not-applicable benefit is required by this profile.")
    copay_cents = copay_cents or 0
    if cost_share in {"coinsurance", "cumulative"}:
        try:
            coinsurance = Decimal(str(coinsurance_percent))
        except Exception as exc:
            raise CommercialError("A coinsurance percentage is required by this profile.") from exc
        if not Decimal("0") <= coinsurance <= Decimal("100"):
            raise CommercialError("Coinsurance percentage must be between 0 and 100.")
    else:
        coinsurance = Decimal("0")
    allowed = 0
    explanation = []
    for line in lines:
        amount = line.get("allowed_cents")
        if not isinstance(amount, int) or amount < 0:
            raise CommercialError(f"No matching allowed rate exists for {line.get('code') or 'a selected service'}.")
        units = _integer(line.get("units"), f"Units for {line.get('code') or 'service'}")
        total = amount * units
        allowed += total
        explanation.append({"code": line.get("code"), "units": units, "allowed_cents": total, "rate_id": line.get("rate_id")})
    deductible = min(allowed, deductible_remaining)
    remainder = allowed - deductible
    coinsurance_cents = int((Decimal(remainder) * coinsurance / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    copay = copay_cents if cost_share in {"copay", "cumulative"} else 0
    raw_patient = deductible + coinsurance_cents + copay
    patient = min(allowed, raw_patient, oop_remaining)
    return {
        "allowed_cents": allowed, "deductible_cents": deductible, "coinsurance_cents": coinsurance_cents,
        "copay_cents": copay, "oop_adjustment_cents": raw_patient - patient,
        "patient_cents": patient, "plan_cents": allowed - patient, "lines": explanation,
        "cost_share": cost_share, "calculation_version": "commercial-v1", "disclaimer": "Pre-service estimate only; final responsibility depends on services performed and payer adjudication.",
    }
