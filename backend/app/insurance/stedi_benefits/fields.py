"""Parse and qualify fields from a Stedi 271 eligibility response."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


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
