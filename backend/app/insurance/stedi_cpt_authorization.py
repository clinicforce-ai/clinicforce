"""Read exact CPT and place-of-service authorization terms from a Stedi 271."""
from __future__ import annotations

import re


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
        for row in (plan.get('benefits') or {}).get('coverageBasis') or []:
            if not isinstance(row, dict):
                continue
            service = row.get('service') or {}
            code = str(service.get('value') or '')
            if (service.get('system') != 'CPT' or code not in findings
                    or (row.get('network') or {}).get('indicator') != tier
                    or pos not in {str(place.get('code')) for place in row.get('placesOfService') or [] if isinstance(place, dict)}):
                continue
            indicator = str(row.get('priorAuthIndicator') or '').upper()
            findings[code].add(indicator if indicator in {'NOT_REQUIRED', 'REQUIRED'} else 'UNKNOWN')
    return {code: 'Not required' if values == {'NOT_REQUIRED'} else 'Required'
            for code, values in findings.items() if values in ({'NOT_REQUIRED'}, {'REQUIRED'})}
