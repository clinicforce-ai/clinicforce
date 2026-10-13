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


def _deductible_message(note: str) -> bool | None:
    """Interpret complete, unqualified phrases; preserve conditional prose."""
    text = note.strip().rstrip('.').casefold()
    if re.fullmatch(r'(?:deductible (?:does not apply|waived)|no deductible|not subject to deductible|copay (?:applies )?before deductible)', text):
        return False
    if re.fullmatch(r'(?:(?:\$[\d,.]+ )?copay(?:ment)? )?(?:applies )?(?:after deductible(?: is met)?|subject to deductible|deductible applies)', text):
        return True
    return None


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
    network_names = list(dict.fromkeys(str(network.get('name') or '').strip()
                                        for person in (subscriber, payload.get('dependent') or {})
                                        if isinstance(person, dict)
                                        for network in [((person.get('additionalInformation') or {}).get('planNetwork') or {})]
                                        if isinstance(network, dict) and network.get('name')))
    return {"names": names, "plan_number": subscriber.get("planNumber") or info.get("planNumber") or (additional.get('plan') or {}).get('number'),
            "group_number": subscriber.get("groupNumber") or info.get("groupNumber") or (additional.get("group") or {}).get("number"),
            "network_names": network_names, "source": "Stedi"}


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


def coverage_evidence(payload: dict, service_date: str | None, entries: list[dict] | None = None) -> dict:
    """Preserve payer status independently of date-filtered cost-sharing rows."""
    rows = [row for row in _stedi_entries(payload) if not row.get('serviceTypeCodes') or '30' in row['serviceTypeCodes']] if entries is None else entries
    statuses = [row for row in rows if row.get('code') in {'status', '1', '2', '3', '4', '5', '6', '7', '8', 'I'}]
    text = ' '.join(str(row.get('status') or '') for row in statuses).lower()
    inactive = any(row.get('code') in {'6', '7', '8', 'I'} for row in statuses) or bool(re.search(r'\b(inactive|ineligible|terminated|cancelled|canceled|expired)\b', text))
    active = any(row.get('code') in {'1', '2', '3', '4', '5'} for row in statuses) or bool(re.search(r'\b(active|eligible)\b', text))
    reported = 'CONFLICT' if active and inactive else 'INACTIVE' if inactive else 'ACTIVE' if active else 'UNKNOWN'
    bounds = [payload.get('planDateInformation') or {},
              ((payload.get('dependent') or payload.get('subscriber') or {}).get('dates') or {})]
    if len(payload.get('plans') or []) == 1:
        plan = payload['plans'][0]
        bounds.extend([plan.get('dates') or {}, plan.get('planDateInformation') or {}])
    starts, ends = [], []
    def iso(value):
        value = str(value)
        return date.fromisoformat(f'{value[:4]}-{value[4:6]}-{value[6:]}' if re.fullmatch(r'\d{8}', value) else value).isoformat()
    try:
        for bound in bounds:
            for key, value in bound.items():
                if not value or key not in {'planBegin', 'planEnd', 'eligibilityBegin', 'eligibilityEnd', 'plan', 'eligibility', 'policyEffective', 'policyExpiration'}:
                    continue
                if isinstance(value, dict):
                    start, end = value.get('start'), value.get('end')
                elif re.fullmatch(r'\d{8}-\d{8}', str(value)):
                    start, end = str(value).split('-')
                else:
                    start = value if key.endswith(('Begin', 'Effective')) else None
                    end = value if key.endswith(('End', 'Expiration')) else None
                if start:
                    starts.append(iso(start))
                if end:
                    ends.append(iso(end))
        start, end = max(starts, default=None), min(ends, default=None)
        state = reported
        if start and end and start > end:
            state = 'CONFLICT'
        elif state != 'CONFLICT' and service_date:
            if start and service_date < start:
                state = 'FUTURE'
            elif end and service_date > end:
                state = 'EXPIRED'
        if eligibility_errors(payload):
            state = 'UNKNOWN'
        return {'status': state, 'reported_status': reported, 'start': start, 'end': end}
    except (ValueError, TypeError):
        return {'status': 'UNKNOWN', 'reported_status': reported, 'start': None, 'end': None}


def _provider_participation(note: str, provider_npi: str | None = None) -> bool:
    return bool(re.search(r"(?:^|[.!?]\s+)Our records indicate the provider ID you entered is participating in this patient's network\.(?:\s|$)", note, re.I)
        or provider_npi and re.fullmatch(r'Provider is in[ -]network based on NPI ID provided in request\.?', note.strip(), re.I))


def normalize_stedi_271(payload: dict[str, Any], service_type_code: str, provider_network_verified: bool,
                        other_active_coverage: bool = False, *, procedure_code: str | None = None,
                        service_date: str | None = None, selected_ids: list[str] | None = None,
                        office_service_types: list[str] | None = None, place_of_service: str | None = None,
                        visit_context: dict | None = None, provider_npi: str | None = None) -> dict[str, Any]:
    """Normalize individual benefits for the provider's verified network tier.

    A benefit network indicator is not provider participation. Use a verified
    clinic record or an explicit payer assertion about the requesting provider.
    """
    entries = _stedi_entries(payload)
    echoed_npi = str((payload.get('provider') or {}).get('npi') or '')
    provider_npi = provider_npi if re.fullmatch(r'\d{10}', provider_npi or '') and (not echoed_npi or echoed_npi == provider_npi) else None
    excluded_service_ids = []
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
        # Diagnosis context must establish an unrelated visit. Unknown/TMJ/jaw
        # context retains the exclusion; an office CPT alone cannot disprove TMJ.
        context = visit_context or {}
        diagnoses = [str(code).upper().replace('.', '') for code in (context.get('diagnoses') or [])]
        unrelated_tmj = (bool(diagnoses) and any(not code.startswith('Z') and code != 'R69' for code in diagnoses)
            and all(re.fullmatch(r'[A-Z][0-9][A-Z0-9]{1,5}', code) for code in diagnoses)
            and not any(code.startswith(('M26', 'R6884')) for code in diagnoses)
            and not re.search(r'\b(?:TMJ|TMD|temporomandibular|jaw)\b', str(context.get('reason') or ''), re.I))
        def unrelated_exclusion(row: dict) -> bool:
            labels = _benefit_notes([row.get('messages'), row.get('additionalInformation')])
            return bool(unrelated_tmj and row.get('code') == 'I' and not _procedure(row) and labels
                and all(re.fullmatch(r'TMJ (?:PCP AND TMJ Specialist|Specialist|PCP)', label.strip(), re.I) for label in labels))
        excluded_service_ids = [row['evidence_path'] for row in entries if unrelated_exclusion(row)]
        entries = [row for row in entries if row['evidence_path'] not in excluded_service_ids]
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
        if place_of_service == '11':
            entries = [row for row in entries if _procedure(row) or not (
                (labels := _benefit_notes([row.get('messages'), row.get('additionalInformation')]))
                and all(re.fullmatch(r'Telemedicine (?:General Medicine|Specialist) Visit', label.strip(), re.I) for label in labels))]
        priorities = office_service_types or ['98', 'BY', '1']
        if 'BQ' in priorities:
            # Keep generic and specialist rows,
            # excluding rows explicitly limited to primary care.
            entries = [row for row in entries if not (
                re.search(r'\b(?:primary care|PCP)\b', ' '.join(_benefit_notes([row.get('messages'), row.get('additionalInformation')])), re.I)
                and not re.search(r'\bspecialist\b', ' '.join(_benefit_notes([row.get('messages'), row.get('additionalInformation')])), re.I))]
            for kind in ('A', 'B'):
                specialist = [row for row in entries if row.get('code') == kind and any(
                    re.search(r'\bspecialist\b|\bprovider role other\b', note, re.I)
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
    # A benefit row names a schedule, not the provider's participation. Only
    # an explicit statement about the requesting provider can select the
    # out-of-network tier automatically. Contrary clinic evidence blocks it.
    def provider_row_in_date(row: dict) -> bool:
        if not service_date:
            return True
        plan = (payload.get('plans') or [])[row['plan_index']] if 'plan_index' in row else payload
        person_dates = (payload.get('dependent') or payload.get('subscriber') or {}).get('dates') or {}
        bounds = [person_dates, payload.get('planDateInformation') or {}, plan.get('planDateInformation') or {},
                  plan.get('dates') or {}, row.get('benefitsDateInformation') or {}, row.get('dates') or {}]
        try:
            return all(_dates_apply(bound, service_date) for bound in bounds)
        except (ValueError, TypeError):
            return False
    provider_rows = [row for row in _stedi_entries(payload) if relevant_service(row) and provider_row_in_date(row)]
    negative_provider_ids = [row['evidence_path'] for row in provider_rows if any(
        re.search(r'\bprovider(?: ID you entered)? is (?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
        for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    positive_provider_ids = [row['evidence_path'] for row in provider_rows if any(
        _provider_participation(note, provider_npi)
        for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    network_conflict = bool(negative_provider_ids and (positive_provider_ids or provider_network_verified))
    selected_tier = 'out_of_network' if negative_provider_ids and not network_conflict else 'in_network'
    accepted_networks = ({'N', 'NO', 'OUT_OF_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'} if selected_tier == 'out_of_network'
                         else {'Y', 'YES', 'IN_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'})
    entries = [row for row in entries if relevant_service(row)
               and (_network_indicator(row) in accepted_networks or
                    not _network_indicator(row) and (row.get('code') in {'status', '1', '2', '3', '4', '5', '6', '7', '8', 'I', 'R'}
                                                       or row.get('code') == 'D' and _procedure(row) == procedure_code))]
    coverage = coverage_evidence(payload, service_date, entries)
    if coverage['status'] == 'UNKNOWN' and selected_tier == 'out_of_network':
        # A payer may return plan status only on its in-network schedule.
        # That proves enrollment, while the separate OON rows supply terms.
        broad_status = coverage_evidence(payload, service_date)
        if broad_status['status'] == 'ACTIVE':
            coverage = broad_status
    invalid_dates = False
    person_dates = (payload.get('dependent') or payload.get('subscriber') or {}).get('dates') or {}
    returned_service = person_dates.get('service') or (payload.get('planDateInformation') or {}).get('service') or {}
    if isinstance(returned_service, str):
        parts = returned_service.split('-') if re.fullmatch(r'\d{8}-\d{8}', returned_service) else [returned_service]
        try:
            parsed = [date.fromisoformat(f'{part[:4]}-{part[4:6]}-{part[6:]}' if re.fullmatch(r'\d{8}', part) else part).isoformat() for part in parts]
            returned_service = {'start': parsed[0], 'end': parsed[-1]}
        except ValueError:
            returned_service = {}
            invalid_dates = True
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
            and _network_indicator(row) in accepted_networks
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
    if statuses and coverage['status'] not in {'EXPIRED', 'FUTURE'}:
        # Historical/status rows for other dates must not contradict the
        # applicable, most specific status. Retain the original report separately.
        coverage['status'] = 'CONFLICT' if inactive and has_active else 'INACTIVE' if inactive else 'ACTIVE' if has_active else 'UNKNOWN'
    if coverage['status'] in {'INACTIVE', 'EXPIRED', 'FUTURE'}:
        active = False
    elif coverage['status'] == 'CONFLICT':
        active = None
    elif active is None and coverage['status'] == 'ACTIVE':
        active = True
    response_failed = str(payload.get("status") or "").upper() in {"ERROR", "FAILED", "REJECTED"} or bool(eligibility_errors(payload))
    if response_failed or any(row.get("code") == "V" for row in entries):
        active = None
        coverage['status'] = 'UNKNOWN'
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
        individual = level in {"INDIVIDUAL", "IND"} or (level in {"EMP", "EMPLOYEE_ONLY"} and not payload.get('dependent'))
        service_waiver = (not level and row.get('code') == 'C' and specificity >= 2
                          and _stedi_cents(row.get('benefitAmount')) == 0
                          and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'', '23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'})
        if (not individual and not service_waiver and not (not level and row.get("code") in {"A", "B"})) or indicator not in accepted_networks or not specificity:
            continue
        wanted.append((specificity, row))
    # A CPT-specific cost share replaces a broader category cost share for
    # this CPT. Deductible and out-of-pocket accumulators remain plan-wide.
    cost_share_scope = max((score for score, row in wanted if row.get('code') in {'A', 'B'}), default=0)
    wanted = [(score, row) for score, row in wanted
              if row.get('code') not in {'A', 'B'} or score == cost_share_scope]
    def pick(code: str, remaining: bool = False, periods: set[str] | None = None, *, plan_wide: bool = False) -> dict[str, Any] | None:
        periods = {'29', 'REMAINING'} if remaining else periods
        matches = [(specificity, row) for specificity, row in wanted if str(row.get("code") or "").upper() == code and (periods is None or str(row.get("timeQualifierCode") or row.get("timeQualifier") or "").upper() in periods)]
        if plan_wide:
            matches = [(score, row) for score, row in matches if not _procedure(row) and '30' in (row.get('serviceTypeCodes') or [])]
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
    annual_deductible = pick('C', periods={'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'}, plan_wide=True)
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
    # A met family out-of-pocket limit covers every member's covered in-network
    # cost share. Keep it separate from the individual accumulator: a positive
    # family balance cannot establish this member's remaining responsibility.
    family_oop = [row for row in relevant if row.get('code') == 'G'
                  and (not _procedure(row) or _procedure(row) == procedure_code)
                  and str(row.get('coverageLevel') or row.get('coverageLevelCode') or '').upper() in {'FAMILY', 'FAM'}
                  and _network_indicator(row) in accepted_networks
                  and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'29', 'REMAINING'}]
    family_scores = [(3 if _procedure(row) == procedure_code and procedure_code else
                      2 if service_type_code in (row.get('serviceTypeCodes') or []) else 1, row)
                     for row in family_oop]
    family_best = [row for score, row in family_scores if score == max(s for s, _ in family_scores)] if family_scores else []
    family_amounts = {_stedi_cents(row.get('benefitAmount')) for row in family_best}
    family_oop_cents = next(iter(family_amounts)) if len(family_amounts) == 1 else None
    family_oop_zero_applies = (family_oop_cents == 0 and not any(
        re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I)
        for row in relevant for note in notes([row.get('messages'), row.get('additionalInformation')])))
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
    other_active_coverage = other_active_coverage or any(row.get('code') == 'R' for row in relevant)
    multiple_plans = len({row["plan_index"] for row in entries if "plan_index" in row and row.get("code") in {"status", "A", "B", "C", "G"}}) > 1
    # Some plans track office copays separately from the general OOP balance.
    # Retain both buckets: a met general maximum must not erase that copay.
    copay_caps = [(score, row) for score, row in wanted if row.get('code') == 'F'
                  and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'29', 'REMAINING'}
                  and any(re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I)
                          for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    cap_rows = [row for score, row in copay_caps if score == max(s for s, _ in copay_caps)] if copay_caps else []
    cap_values = {_stedi_cents(row.get('benefitAmount')) for row in cap_rows}
    copay_cap = next(iter(cap_values)) if len(cap_values) == 1 and None not in cap_values else None
    waived_rows = [row for _, row in wanted if office and row.get('code') == 'F' and any(
        re.fullmatch(r'(?:Specialist Visit or Evaluation|(?:Specialist )?Office Visit)/Plan Ded Waived', note.strip(), re.I)
        for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    deductible_signals = [(False, row) for row in waived_rows]
    for specificity, row in wanted:
        period = str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper()
        amount = _stedi_cents(row.get('benefitAmount')) if row.get('code') == 'C' else None
        # A zero remaining balance means met, never waived. General positive
        # accumulators do not prove that today's service uses the deductible.
        if amount is not None and (specificity >= 2 or amount == 0) and (period in {'', '23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'}
                or specificity >= 2 and amount > 0 and period in {'29', 'REMAINING'}):
            deductible_signals.append((amount > 0, row))
        if (row.get('evidence_path') in {x.get('evidence_path') for x in (copay, coinsurance) if x}
                or specificity >= 2 and row.get('code') == 'C'):
            for note in notes([row.get('messages'), row.get('additionalInformation')]):
                for label in note.split(';'):
                    applies = _deductible_message(label)
                    if applies is not None:
                        deductible_signals.append((applies, row))
    deductible_values = {value for value, _ in deductible_signals}
    deductible_applies = next(iter(deductible_values)) if len(deductible_values) == 1 else None
    deductible_conflict = len(deductible_values) > 1
    deductible_applicability = ('UNKNOWN' if deductible_conflict else 'DOES_NOT_APPLY' if deductible_applies is False
                               else 'ALREADY_MET' if deductible_cents == 0 else 'APPLIES' if deductible_applies is True else 'UNKNOWN')
    def unresolved_limit(row):
        if str(row.get('code') or '').upper() not in {'F', 'Y'}:
            return False
        labels = notes([row.get('messages'), row.get('additionalInformation')])
        # General administrative messages do not establish a service limit.
        if not row.get('benefitAmount') and not row.get('quantity') and all(
            (_provider_participation(note, provider_npi) or re.search(r'participating in this patient|may require precert|Plan includes NAP|^SELF-FUNDED$|^All Other In-Network Providers$|/Plan Ded Waived$', note, re.I))
            for note in labels) and labels:
            return False
        if copay_cap is not None and row.get('code') == 'F' and row.get('benefitAmount') is not None:
            # Annual/remaining copay maxima come as individual and family rows.
            # Only recognize the annual row when paired with an explicit cap.
            return not (row in cap_rows or any(re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I) for note in labels)
                        or str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'23', 'CALENDAR_YEAR'}
                        and labels == ['All Other In-Network Providers'])
        return True
    limitations = any(unresolved_limit(row) for row in relevant)
    conditional_terms = selected_ids is None and any(re.search(r"\b(first|after|before|beginning|visits?|waived|unless|only|limit|specialist|primary care)\b", note, re.IGNORECASE) for row in (copay, coinsurance) if row for note in notes([row.get("messages"), row.get("additionalInformation")])
        if not all(_deductible_message(label) is not None or re.fullmatch(r"(?:All Other In-Network Providers\s+)?(?:(?:specialist |physician )?office visits?(?: (?:specialist|primary care(?: prov(?:ider)?)?))?|Specialist(?: AND PCP)?|PCP AND Specialist|per visit|(?:Specialist|Primary Care) Visit or Evaluation|GYN Visit|Walk In Clinic Visit|Telemedicine (?:General Medicine|Specialist) Visit|phy visit\s*-\s*specialist|(?:PCP AND )?BenefitTier Non Tier 1 Specialist|diagnostic medical procedure)(?:\s*-\s*professional)?(?:\s*,\s*COINS APPLIES TO OUT OF POCKET)?", label.strip(), re.I)
                   for label in note.split(';') if label.strip()))
    source_errors = []
    for _, row in wanted:
        for field in ('benefitAmount', 'benefitPercent', 'percent'):
            value = row.get(field)
            if value is None or value == '':
                continue
            try:
                number = Decimal(str(value))
                invalid = not number.is_finite() or number < 0 or number > (100 if field != 'benefitAmount' else 1000000)
            except Exception:
                invalid = True
            if invalid:
                source_errors.append({'path': row['evidence_path'] + '.' + field,
                    'kind': 'invalid_financial_value',
                    'message': 'Invalid financial evidence: the payer returned an invalid amount or percentage. Verify the source before collection.'})
    review_reasons = list(dict.fromkeys(error['message'] for error in source_errors))
    if inactive and has_active:
        review_reasons.append('The payer returned both active coverage and inactive or noncovered benefits for the selected service scope. Verify which coverage restriction applies before estimating.')
    if invalid_dates:
        review_reasons.append("Verify the payer eligibility dates; an applicable date value could not be interpreted.")
    if date_matches is False:
        review_reasons.append(f"Payer evidence covers service date {returned_service['start']}; eligibility for the requested appointment on {service_date} needs verification.")
    if active is not True:
        review_reasons.append("Confirm active coverage for the visit date with the payer.")
    if deductible_conflict:
        review_reasons.append('Conflicting deductible applicability evidence requires review before calculation.')
    if deductible_cents is None and deductible_applies is not False:
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
    participation = [row['evidence_path'] for row in relevant if any(
        _provider_participation(note, provider_npi)
        for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    conflicting_network = any(re.search(r'\b(?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
                              for note in descriptions)
    # A contrary statement about this provider may sit on an out-of-network
    # row excluded from the in-network cost-sharing calculation above.
    conflicting_network |= any(re.search(r'\bprovider(?: ID you entered)? is (?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
        for row in provider_rows for note in notes([row.get('messages'), row.get('additionalInformation')]))
    if network_conflict:
        review_reasons.append('Provider-network evidence reports nonparticipation or contradicts the in-network assertion. Verify participation before estimating.')
    payer_network_verified = bool(participation and not conflicting_network and active is True
        and not multiple_plans and not invalid_dates and date_matches is not False and not source_errors)
    out_of_network_verified = bool(negative_provider_ids and not network_conflict and active is True
        and not multiple_plans and not invalid_dates and date_matches is not False and not source_errors)
    return {"active": active, "coverage_evidence": coverage,
            "excluded_service_evidence_ids": excluded_service_ids,
            "coverage_status_conflict": bool(inactive and has_active) or coverage['status'] == 'CONFLICT',
            "coverage_dates_invalid": invalid_dates,
            "source_errors": source_errors, "service_date_matches": date_matches, "returned_service_dates": returned_service,
            "coverage_level": "individual" if wanted else "unknown",
            "network_status": "out_of_network" if out_of_network_verified else "in_network" if provider_network_verified and not network_conflict or payer_network_verified else "unknown",
            "network_source": 'payer_participation_message' if out_of_network_verified or payer_network_verified else 'clinic_record' if provider_network_verified and not network_conflict else None,
            "network_evidence_ids": negative_provider_ids if out_of_network_verified else participation if payer_network_verified else [],
            "deductible_remaining_cents": deductible_cents, "oop_remaining_cents": oop_cents,
            "deductible_annual_cents": _stedi_cents(annual_deductible.get('benefitAmount')) if annual_deductible else None,
            "deductible_applicability": deductible_applicability,
            "deductible_applicability_ids": list(dict.fromkeys(row['evidence_path'] for _, row in deductible_signals)),
            "family_oop_remaining_cents": family_oop_cents,
            "family_oop_zero_applies": family_oop_zero_applies,
            "family_oop_ids": [row['evidence_path'] for row in family_best] if family_oop_zero_applies else [],
            "copay_cents": copay_cents, "coinsurance_percent": str(coinsurance_percent) if coinsurance_percent is not None else None,
            "has_secondary": other_active_coverage or multiple_plans, "requires_review": bool(review_reasons),
            "selected_benefit_ids": [path for row in (copay, coinsurance) if row for path in row.get('evidence_paths', [row['evidence_path']])],
            "deductible_id": deductible.get("evidence_path") if deductible else None,
            "deductible_annual_id": annual_deductible.get("evidence_path") if annual_deductible else None,
            "oop_id": oop.get("evidence_path") if oop else None,
            "copay_id": copay.get("evidence_path") if copay else None,
            "coinsurance_id": coinsurance.get("evidence_path") if coinsurance else None,
            "copay_remaining_cents": copay_cap,
            "copay_limit_ids": [row['evidence_path'] for row in cap_rows],
            "separate_copay_limit": bool(cap_rows),
            **({'deductible_applies': deductible_applies} if deductible_applies is not None else {}),
            'deductible_waiver_ids': [row['evidence_path'] for value, row in deductible_signals if value is False],
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
    """Calculate a primary-plan share only from an allowance for the verified tier."""
    if not lines:
        raise CommercialError("Confirm at least one service before calculating an estimate.")
    if benefits.get("active") is not True:
        raise CommercialError("Active primary coverage is required for an insured estimate.")
    if benefits.get("coverage_level") != "individual":
        raise CommercialError("A compatible individual benefit level is required for an automatic estimate.")
    out_of_network = benefits.get('network_status') == 'out_of_network'
    if out_of_network:
        if (benefits.get('network_source') != 'payer_participation_message' or
                rules.get('allowed_basis') != 'payer_out_of_network' or
                any(line.get('allowed_source') != 'payer_out_of_network' or not line.get('rate_id') for line in lines)):
            raise CommercialError('A verified payer out-of-network allowed amount is required for every selected CPT.')
    elif benefits.get("network_status") != "in_network":
        raise CommercialError("Confirmed in-network provider participation is required for an automatic estimate.")
    if benefits.get("has_secondary"):
        raise CommercialError("Multiple active coverages require staff review.")
    if benefits.get("requires_review"):
        raise CommercialError(" ".join(benefits.get("review_reasons") or
                                      ["The selected benefits contain an unresolved limit, authorization, referral, or payer review."]))
    if benefits.get('separate_copay_limit'):
        raise CommercialError('Separate copay and general out-of-pocket limits require a scoped copay advance or plan review.')
    if _benefit_cents(benefits, 'oop_remaining_cents') == 0:
        # Covered in-network services cannot exceed an exhausted patient cap.
        # These calculation inputs do not overwrite the returned benefit facts.
        benefits = {**benefits, 'deductible_remaining_cents': 0, 'coinsurance_percent': '0', 'copay_cents': 0}
        rules = {**rules, 'cost_share': 'coinsurance'}
    if benefits.get('deductible_applies') is False:
        benefits = {**benefits, 'deductible_remaining_cents': 0}
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
        if has_copay and _benefit_cents(benefits, "deductible_remaining_cents") and benefits.get('deductible_applies') is not True:
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
        "cost_share": cost_share, "network_tier": 'out_of_network' if out_of_network else 'in_network',
        "possible_additional_out_of_network_charges_cents": None if out_of_network else 0,
        "calculation_version": "commercial-v1", "disclaimer": "Pre-service estimate only; final responsibility depends on services performed and payer adjudication." +
            (' Out-of-network balance billing may add charges above the payer allowed amount.' if out_of_network else ''),
    }
