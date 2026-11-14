"""Clinic approved payer fees read from PostgreSQL for a bound medical product."""
from __future__ import annotations

import re
import os
from datetime import date

from app.core.patient_store import _connection


def payer_key(payer_name: str) -> str | None:
    name = payer_name.casefold()
    if re.search(r'\bcigna\b', name):
        return 'cigna'
    if re.search(r'\bwellpoint\b', name):
        return 'wellpoint'
    if re.search(r'\b(?:united\s*health\s*care|uhc)\b', name):
        return 'uhc'
    if re.search(r'\bhumana\b', name):
        return 'humana'
    if re.search(r'\b(?:multiplan|multi\s*plan|phcs)\b', name):
        return 'multiplan'
    return None


def category(payer: str, plan_names: list[str] | None) -> str | None:
    """Never choose a schedule from a payer name alone or mixed products."""
    names = {str(name).strip().casefold() for name in plan_names or [] if str(name).strip()}
    if len(names) != 1:
        return None
    plan = names.pop()
    medicare = bool(re.search(r'\b(?:medicare|rppo|lppo|d[ -]?snp)\b', plan))
    if payer == 'wellpoint':
        return 'advantage' if medicare and re.search(r'\b(?:advantage|ppo|hmo|d[ -]?snp)\b', plan) else None
    if payer == 'humana':
        return 'medicare' if medicare else None
    if payer == 'multiplan':
        return 'phcs' if 'phcs' in plan and not medicare else None
    if payer in {'cigna', 'uhc'}:
        return 'commercial' if not medicare and not re.search(r'\b(?:medicaid|chip|dental|vision|pharmacy)\b', plan) else None
    return None


def schedule_for(payer_name: str, plan_names: list[str] | None) -> tuple[str, str] | None:
    key = payer_key(payer_name)
    plan_category = category(key, plan_names) if key else None
    return (key, plan_category) if plan_category else None


def fee_schedule_products(payer_name: str, plan: dict | None) -> list[str]:
    """Use a member's exact UHC Choice Plus network when UMR omits a plan name."""
    plan = plan or {}
    names = plan.get('names') or []
    if names:
        return names
    networks = plan.get('network_names') or []
    group_name = str(plan.get('group_name') or '')
    if (payer_key(payer_name) == 'uhc' and len(networks) == 1
            and re.sub(r'[^a-z0-9]', '', str(networks[0]).casefold()) == 'unitedhealthcarechoiceplus'
            and plan.get('group_number') and re.search(r'\b(?:employee|employer)\b', group_name, re.I)
            and not re.search(r'\b(?:medicare|medicaid|chip)\b', group_name, re.I)):
        return ['UnitedHealthcare Choice Plus']
    return []


def active_schedule(payer: str, plan_category: str, service_date: str) -> bool:
    try:
        day = date.fromisoformat(service_date)
    except (TypeError, ValueError):
        return False
    if not (os.environ.get('DATABASE_URL') or os.environ.get('DB_HOST')):
        return False
    with _connection() as db:
        row = db.execute('''SELECT EXISTS(SELECT 1 FROM payer_fee_schedule
            WHERE payer_key=%s AND category=%s AND effective_from<=%s) AS active''',
            (payer, plan_category, day)).fetchone()
    return bool(row and row['active'])


def schedule_for_service(payer_name: str, plan_names: list[str] | None,
                         service_date: str) -> tuple[str, str] | None:
    schedule = schedule_for(payer_name, plan_names)
    return schedule if schedule and active_schedule(*schedule, service_date) else None


def schedule_for_result(result: dict) -> tuple[str, str] | None:
    plan = (result.get('plan_details') or {}).get('stedi') or {}
    names = [plan['identified_product']] if plan.get('identified_product') else fee_schedule_products(str(result.get('payer') or ''), plan)
    if not names:
        names = fee_schedule_products(str(result.get('payer') or ''), (result.get('benefits') or {}).get('plan_details'))
    return schedule_for_service(str(result.get('payer') or ''), names,
                                str(result.get('service_date') or ''))


def matching_rates(payer: str, plan_category: str, service_date: str, lines: list[dict], *,
                   place_of_service: str, provider_npi: str, patient_binding: str,
                   product_binding: str) -> list[dict]:
    try:
        day = date.fromisoformat(service_date)
    except (TypeError, ValueError):
        return []
    if not all((payer, plan_category, place_of_service, provider_npi, patient_binding, product_binding)):
        return []
    requested = {str(line.get('code') or '').upper() for line in lines
                 if not line.get('modifiers') and type(line.get('units')) is int and line['units'] > 0}
    if not requested:
        return []
    with _connection() as db:
        rows = db.execute('''SELECT DISTINCT ON (code) code, allowed_cents, source_reference, effective_from
            FROM payer_fee_schedule WHERE payer_key=%s AND category=%s AND code=ANY(%s)
            AND effective_from<=%s ORDER BY code, effective_from DESC''',
            (payer, plan_category, list(requested), day)).fetchall()
    found = {row['code']: row for row in rows
             if place_of_service == '11' or not row['source_reference'].startswith('BCBSTX ')}
    return [{
        'id': f"{payer}:{plan_category}:{found[line['code']]['effective_from']}:{line['code']}",
        'line_id': line['id'], 'code': line['code'], 'modifier': '', 'units': line['units'],
        'allowed_cents': found[line['code']]['allowed_cents'], 'basis': 'payer_fee_schedule',
        'source_reference': found[line['code']]['source_reference'],
        'effective_from': str(found[line['code']]['effective_from']),
        'service_date': service_date, 'place_of_service': place_of_service,
        'provider_npi': provider_npi, 'patient_binding': patient_binding,
        'product_binding': product_binding,
    } for line in lines if line.get('code') in found and not line.get('modifiers')
        and type(line.get('units')) is int and line['units'] > 0]


def visit_rates(schedule: tuple[str, str], source: dict, benefits: dict,
                inputs: dict, selected: list[dict], rules: dict) -> tuple[list[dict], list[str]]:
    if source.get('payer_plan_bound') is not True:
        return [], ['Identify one patient-bound medical product in the approved payer fee category before pricing.']
    if benefits.get('network_status') == 'out_of_network':
        return [], ['Obtain the payer out-of-network allowed amount; the clinic in-network fee schedule does not apply.']
    rates = matching_rates(*schedule, str(inputs.get('service_date') or ''), selected,
        place_of_service=str(rules.get('place_of_service') or ''),
        provider_npi=str(rules.get('provider_npi') or ''),
        patient_binding=str(rules.get('patient_binding') or ''),
        product_binding=str(rules.get('product_binding') or ''))
    return rates, ([] if len(rates) == len(selected) else
                   ['The approved payer fee schedule has no unmodified rate for every selected CPT and visit.'])
