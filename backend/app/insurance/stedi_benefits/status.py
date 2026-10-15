"""Resolve dated Stedi coverage status for selected benefit rows."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.insurance.stedi_benefits.fields import (
    _dates_apply, _network_indicator, _procedure,
    coverage_evidence, eligibility_errors,
)


@dataclass
class CoverageStatus:
    entries: list[dict[str, Any]]
    service_type_code: str
    coverage: dict[str, Any]
    active: bool | None
    inactive: bool
    has_active: bool
    invalid_dates: bool
    date_matches: bool | None
    returned_service: dict[str, Any]
    response_failed: bool


def resolve_coverage_status(payload: dict[str, Any], entries: list[dict[str, Any]],
                            service_date: str | None, service_type_code: str,
                            selected_tier: str, office: bool,
                            selected_ids: list[str] | None, priorities: list[str],
                            accepted_networks: set[str]) -> CoverageStatus:
    """Retain unknown and conflicting dates instead of inferring active coverage."""
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
    return CoverageStatus(entries, service_type_code, coverage, active, inactive,
                          has_active, invalid_dates, date_matches, returned_service,
                          response_failed)
