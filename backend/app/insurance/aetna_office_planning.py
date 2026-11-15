"""A prior-date Aetna office estimate for display, never a collection decision."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re

from app.insurance import aetna_fees
from app.insurance.identity import patient_benefits_bound


def estimate(result: dict, inputs: dict) -> dict | None:
    benefits = result.get('reference_benefits') or result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    plan = (result.get('plan_details') or {}).get('stedi') or benefits.get('plan_details') or {}
    names = [plan['identified_product']] if plan.get('identified_product') else plan.get('names') or []
    codes = result.get('codes') or []
    visit_day = str(inputs.get('service_date') or '')
    try:
        day = date.fromisoformat(visit_day)
        response_day = date.fromisoformat(str((benefits.get('returned_service_dates') or {}).get('start') or '')[:10])
    except ValueError:
        return None
    if (result.get('payment_mode') == 'cash'
            or ((result.get('collection_guidance') or {}).get('collection_rule') or {}).get('mode')
                in {'cms_msp_part_b_deferral', 'aetna_choice_family_oop_deferral'}
            or aetna_fees.category(str(result.get('payer') or ''), names) != 'commercial'
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not False
            or not 1 <= (day - response_day).days <= 7 or not patient_benefits_bound(identity)
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('network_conflict') or benefits.get('network_status') == 'out_of_network'
            or benefits.get('historical_only')
            or benefits.get('source_errors') or benefits.get('qmb_status') == 'protected'
            or (result.get('recovery') or {}).get('reason_code') == 'same_policy_evidence_conflict'
            or len(codes) != 1 or codes[0].get('code') not in {'99202', '99203', '99204', '99205',
                                                               '99212', '99213', '99214', '99215'}
            or codes[0].get('modifiers') or codes[0].get('units') != 1
            or str(inputs.get('place_of_service') or '') != '11'):
        return None
    options = benefits.get('reported_options') or []
    office = [row for row in options if row.get('kind') == 'coinsurance'
              and row.get('network') == 'IN_NETWORK'
              and '98' in str(row.get('service_type_code') or '').replace(' ', '').split(',')
              and not row.get('procedure_code') and not row.get('diagnosis_scoped')
              and (not row.get('places_of_service') or '11' in row['places_of_service'])
              and re.search(r'\bSpecialist Visit or Evaluation\b', str(row.get('payer_note') or ''), re.I)
              and not re.search(r'\btelemedicine|telehealth\b', str(row.get('payer_note') or ''), re.I)]
    percentages = {str(row.get('percent')) for row in office if row.get('percent') is not None}
    deductible = benefits.get('deductible_remaining_cents')
    oop = benefits.get('oop_remaining_cents')
    if (len(percentages) != 1 or type(deductible) is not int or deductible < 0
            or type(oop) is not int or oop < 0 or benefits.get('deductible_remaining_conflict_ids')
            or benefits.get('oop_remaining_conflict_ids')):
        return None
    percent = Decimal(next(iter(percentages)))
    if not Decimal('0') <= percent <= Decimal('100'):
        return None
    provider_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    rates = aetna_fees.matching_rates('commercial', visit_day, codes,
        place_of_service='11', provider_npi=provider_npi,
        patient_binding=str(identity.get('binding') or identity.get('reconciliation_rule') or ''),
        product_binding=str(names[0]))
    if len(rates) != 1 or type(rates[0].get('allowed_cents')) is not int:
        return None
    allowed = rates[0]['allowed_cents']
    if allowed < 0:
        return None
    waived = min(oop, int((Decimal(allowed) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
    deductible_part = min(allowed, deductible)
    after_deductible = int((Decimal(allowed - deductible_part) * percent / 100).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))
    applies = min(oop, deductible_part + after_deductible)
    return {'status': 'planning_only', 'code': codes[0]['code'], 'allowed_cents': allowed,
            'deductible_remaining_cents': deductible, 'oop_remaining_cents': oop,
            'coinsurance_percent': str(percent), 'deductible_applies_cents': applies,
            'deductible_waived_cents': waived, 'payer_response_date': response_day.isoformat(),
            'service_date': visit_day, 'rate_source': rates[0].get('source_reference'),
            'collectable': False}


def attach(result: dict, inputs: dict) -> None:
    """Refresh a saved display without failing the read when fee storage is down."""
    result.pop('aetna_office_planning', None)
    try:
        planning = estimate(result, inputs)
    except Exception:
        return
    if planning:
        result['aetna_office_planning'] = planning
