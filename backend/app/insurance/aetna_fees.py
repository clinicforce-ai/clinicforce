"""Clinic-wide Aetna allowed fees read from PostgreSQL."""
from __future__ import annotations

import re
from datetime import date

from app.core.patient_store import _connection


def is_aetna_payer(payer_name: str) -> bool:
    return bool(re.search(r'\baetna\b', payer_name, re.I))


def category(payer_name: str, plan_names: list[str] | None) -> str | None:
    """Use one identified medical product; an ambiguous plan is not priced."""
    if not is_aetna_payer(payer_name):
        return None
    names = {str(name).strip().casefold() for name in plan_names or [] if str(name).strip()}
    if len(names) != 1:
        return None
    name = names.pop()
    if re.search(r'\b(?:medicare advantage|medicare .*(?:ppo|hmo)|d[ -]?snp)\b', name):
        return 'advantage'
    if re.search(r'\b(?:medicare|medicaid|chip|dental|vision|pharmacy)\b', name):
        return None
    return 'commercial' if name not in {'aetna', 'ppo', 'hmo', 'medicare'} else None


def matching_rates(category_name: str, service_date: str, lines: list[dict], *,
                   place_of_service: str, provider_npi: str, patient_binding: str,
                   product_binding: str) -> list[dict]:
    """Select the latest dated row for each unmodified code and bind it to this visit."""
    try:
        day = date.fromisoformat(service_date)
    except (TypeError, ValueError):
        return []
    if (category_name not in {'advantage', 'commercial'} or not place_of_service
            or not provider_npi or not patient_binding or not product_binding):
        return []
    requested = {str(line.get('code') or '').upper() for line in lines
                 if not line.get('modifiers') and type(line.get('units')) is int and line['units'] > 0}
    if not requested:
        return []
    with _connection() as db:
        rows = db.execute('''SELECT DISTINCT ON (code) code,allowed_cents,source_reference,effective_from
            FROM aetna_fee_schedule WHERE category=%s AND code=ANY(%s) AND effective_from<=%s
            ORDER BY code,effective_from DESC''',
            (category_name, list(requested), day)).fetchall()
    found = {row['code']: row for row in rows}
    return [{
        'id': f"aetna:{category_name}:{found[line['code']]['effective_from']}:{line['code']}",
        'code': line['code'], 'modifier': '', 'line_id': line['id'],
        'allowed_cents': found[line['code']]['allowed_cents'],
        'basis': 'aetna_fee_schedule', 'source_reference': found[line['code']]['source_reference'],
        'effective_from': str(found[line['code']]['effective_from']),
        'service_date': service_date, 'place_of_service': place_of_service,
        'provider_npi': provider_npi, 'patient_binding': patient_binding,
        'product_binding': product_binding, 'units': line['units'],
    } for line in lines if line.get('code') in found and not line.get('modifiers')
        and type(line.get('units')) is int and line['units'] > 0]


def visit_rates(payer_name: str, medical_names: list[str], source: dict,
                benefits: dict, inputs: dict, selected: list[dict], rules: dict) -> tuple[str | None, list[dict], list[str]]:
    """Resolve one patient's Aetna category and applicable clinic-wide rates."""
    plan_category = category(payer_name, medical_names)
    if not plan_category or source.get('payer_plan_bound') is not True:
        return plan_category, [], ['Identify one patient-bound Aetna Advantage or Commercial medical product before applying its fee schedule.']
    if benefits.get('network_status') == 'out_of_network':
        return plan_category, [], ['Obtain the payer out-of-network allowed amount for each selected CPT; the Aetna in-network schedule does not apply.']
    rates = matching_rates(plan_category, str(inputs.get('service_date') or ''), selected,
        place_of_service=str(rules.get('place_of_service') or ''),
        provider_npi=str(rules.get('provider_npi') or ''),
        patient_binding=str(rules.get('patient_binding') or ''),
        product_binding=str(rules.get('product_binding') or ''))
    issues = [] if len(rates) == len(selected) else [
        'The Aetna fee schedule has no applicable unmodified rate for every selected CPT and visit.']
    return plan_category, rates, issues
