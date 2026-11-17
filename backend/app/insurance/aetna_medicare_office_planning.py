"""Prior-day Aetna Medicare PPO office copay and plan-share display."""
from datetime import date
import re

from app.insurance import aetna_fees
from app.insurance.identity import patient_benefits_bound


def estimate(result: dict, inputs: dict) -> dict | None:
    benefits = result.get('reference_benefits') or result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    plan = (result.get('plan_details') or {}).get('stedi') or benefits.get('plan_details') or {}
    codes = result.get('codes') or []
    try:
        visit = date.fromisoformat(str(inputs.get('service_date') or ''))
        returned = date.fromisoformat(str((benefits.get('returned_service_dates') or {}).get('start') or '')[:10])
    except ValueError:
        return None
    if (result.get('payment_mode') == 'cash'
            or not aetna_fees.is_aetna_payer(str(result.get('payer') or ''))
            or plan.get('names') != ['Aetna Medicare Value Plus (PPO)']
            or plan.get('group_number') != '000003-TX'
            or plan.get('plan_number') != '000003-TX000083'
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not False
            or (visit - returned).days != 1 or not patient_benefits_bound(identity)
            or benefits.get('network_status') == 'out_of_network' or benefits.get('has_secondary')
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('historical_only')
            or benefits.get('source_errors') or benefits.get('qmb_status') == 'protected'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or (result.get('recovery') or {}).get('reason_code') == 'same_policy_evidence_conflict'
            or len(codes) != 1 or codes[0].get('code') != '99213'
            or codes[0].get('modifiers') or codes[0].get('units') != 1
            or str(inputs.get('place_of_service') or '') != '11'):
        return None
    options = benefits.get('reported_options') or []
    def office(row: dict) -> bool:
        note = str(row.get('payer_note') or '')
        return (row.get('network') == 'IN_NETWORK'
                and '98' in str(row.get('service_type_code') or '').replace(' ', '').split(',')
                and not row.get('procedure_code') and not row.get('diagnosis_scoped')
                and (not row.get('places_of_service') or '11' in row['places_of_service'])
                and re.search(r'\bspecialist\b', note, re.I) is not None
                and re.search(r'\b(?:office|visit|evaluation)\b', note, re.I) is not None
                and re.search(r'\b(?:telemedicine|telehealth|virtual|remote)\b', note, re.I) is None)
    copays = {row.get('amount_cents') for row in options if row.get('kind') == 'copay' and office(row)}
    percents = {str(row.get('percent')) for row in options
                if row.get('kind') == 'coinsurance' and office(row) and row.get('percent') is not None}
    deductible = benefits.get('deductible_remaining_cents')
    oop = benefits.get('oop_remaining_cents')
    if (len(copays) != 1 or len(percents) > 1 or type(next(iter(copays))) is not int
            or percents and float(next(iter(percents))) != 0 or deductible != 0
            or type(oop) is not int or oop < 0
            or benefits.get('deductible_remaining_conflict_ids')
            or benefits.get('oop_remaining_conflict_ids')):
        return None
    copay = next(iter(copays))
    provider_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    rates = aetna_fees.matching_rates('advantage', visit.isoformat(), codes,
        place_of_service='11', provider_npi=provider_npi,
        patient_binding=str(identity.get('binding') or ''),
        product_binding='Aetna Medicare Value Plus (PPO)')
    if len(rates) != 1 or type(rates[0].get('allowed_cents')) is not int:
        return None
    allowed = rates[0]['allowed_cents']
    if not 0 <= copay <= min(allowed, oop):
        return None
    return {'status': 'planning_only', 'code': '99213', 'allowed_cents': allowed,
            'copay_cents': copay, 'plan_share_cents': allowed - copay,
            'deductible_remaining_cents': 0, 'oop_remaining_cents': oop,
            'coinsurance_percent': '0' if percents else None, 'deductible_applies_cents': copay,
            'deductible_waived_cents': copay, 'payer_response_date': returned.isoformat(),
            'service_date': visit.isoformat(), 'rate_source': rates[0].get('source_reference'),
            'collectable': False}
