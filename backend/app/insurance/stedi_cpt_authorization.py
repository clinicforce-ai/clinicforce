"""Read exact CPT and place-of-service authorization terms from a Stedi 271."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def visit_coverage_bound(benefits: dict, service_date: str) -> bool:
    """Accept a dated active coverage period when the 271 omits a visit-date echo."""
    if benefits.get('active') is not True:
        return False
    matches = benefits.get('service_date_matches')
    if matches is not None:
        return matches is True
    coverage = benefits.get('coverage_evidence') or {}
    start, end = coverage.get('start'), coverage.get('end')
    return bool(coverage.get('status') == 'ACTIVE' and start and start <= service_date
                and (not end or end >= service_date))


def decisions(payload: dict, codes: list[str], pos: str, network: str) -> dict[str, str]:
    """A broad service row or a conflicting tier never decides an exact CPT."""
    if not re.fullmatch(r"\d{2}", pos) or network not in {'in_network', 'out_of_network'}:
        return {}
    plans = payload.get('plans') or []
    if len(plans) != 1:
        return {}
    tier = 'IN_NETWORK' if network == 'in_network' else 'OUT_OF_NETWORK'
    findings: dict[str, set[str]] = {code: set() for code in codes}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        for rows in (plan.get('benefits') or {}).values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                service = row.get('service') or {}
                code = str(service.get('value') or '')
                if (service.get('system') != 'CPT' or code not in findings
                        or row.get('diagnosis')
                        or (row.get('network') or {}).get('indicator') != tier
                        or row.get('placesOfService') and pos not in {
                            str(place.get('code')) for place in row['placesOfService'] if isinstance(place, dict)}):
                    continue
                indicator = str(row.get('priorAuthIndicator') or '').upper()
                if indicator:
                    findings[code].add(indicator if indicator in {'NOT_REQUIRED', 'REQUIRED'} else 'UNKNOWN')
    return {code: 'Not required' if values == {'NOT_REQUIRED'} else 'Required'
            for code, values in findings.items() if values in ({'NOT_REQUIRED'}, {'REQUIRED'})}


def financial_terms(payload: dict, code: str, pos: str, network: str) -> dict:
    """Read exact-CPT terms, including explicitly family-only accumulators."""
    if len(payload.get('plans') or []) != 1 or network not in {'in_network', 'out_of_network'}:
        return {}
    tier = 'IN_NETWORK' if network == 'in_network' else 'OUT_OF_NETWORK'
    benefits = payload['plans'][0].get('benefits') or {}
    family_only = any('this plan has only family accumulators' in str(note).casefold()
                      for rows in benefits.values() if isinstance(rows, list)
                      for row in rows if isinstance(row, dict)
                      for note in (row.get('messages') or []) if isinstance(note, str))
    level = 'FAMILY' if family_only else 'INDIVIDUAL'
    def scoped(kind: str, period: str | None = None) -> list[dict]:
        return [row for row in benefits.get(kind) or [] if isinstance(row, dict)
                and (row.get('service') or {}).get('system') == 'CPT'
                and str((row.get('service') or {}).get('value') or '') == code
                and not row.get('diagnosis')
                and (row.get('network') or {}).get('indicator') == tier
                and (not row.get('placesOfService') or pos in {
                    str(place.get('code')) for place in row['placesOfService'] if isinstance(place, dict)})
                and str(row.get('coverageLevel') or '').upper() == level
                and (period is None or str(row.get('timePeriod') or '').upper() == period)]
    if family_only and any(isinstance(row, dict)
                           and (row.get('service') or {}).get('system') == 'CPT'
                           and str((row.get('service') or {}).get('value') or '') == code
                           and not row.get('diagnosis')
                           and (row.get('network') or {}).get('indicator') == tier
                           and str(row.get('coverageLevel') or '').upper() == 'INDIVIDUAL'
                           for kind in ('coPayment', 'coInsurance', 'deductible', 'outOfPocket')
                           for row in benefits.get(kind) or []):
        return {}
    def one(kind: str, field: str, period: str | None = None, *, percent: bool = False) -> int | str | None:
        values = set()
        for row in scoped(kind, period):
            raw = row.get(field)
            if raw is None:
                continue
            try:
                value = Decimal(str(raw))
                if not value.is_finite() or value < 0 or value > (100 if percent else 1000000):
                    return None
                if percent:
                    value = value * 100 if value <= 1 else value
                    values.add(str(value))
                else:
                    values.add(int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
            except (InvalidOperation, TypeError, ValueError):
                return None
        return next(iter(values)) if len(values) == 1 else None
    copay = one('coPayment', 'amount')
    return {**({'coverage_level': level, 'family_only_accumulators': True} if family_only else {}),
            'coinsurance_percent': one('coInsurance', 'percent', percent=True),
            'copay_cents': copay, 'copay_unresolved': bool(scoped('coPayment')) and copay is None,
            'deductible_remaining_cents': one('deductible', 'amount', 'REMAINING'),
            'oop_remaining_cents': one('outOfPocket', 'amount', 'REMAINING')}
