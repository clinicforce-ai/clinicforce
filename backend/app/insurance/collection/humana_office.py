"""Clinic-directed Humana Medicare PPO specialist copay advance."""
from __future__ import annotations

import re

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, review: dict | None, *, allowed: bool) -> int | None:
    """Use the visit copay as a refundable advance when the selected office evidence agrees."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    plan = ((result.get('plan_details') or {}).get('stedi') or benefits.get('plan_details') or {})
    protection = result.get('billing_protection') or {}
    if (not allowed or guidance.get('amount_cents') is not None
            or not re.search(r'\bhumana\b', str(result.get('payer') or ''), re.I)
            or not plan.get('medicare_ppo_evidence') or len(plan.get('names') or []) != 1
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not patient_benefits_bound(result.get('identity') or benefits.get('identity') or {})
            or not verified_in_network(benefits) or benefits.get('network_source') != 'clinic_library_contract'
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('historical_only')
            or benefits.get('source_errors') or benefits.get('qmb_status') == 'protected'
            or protection.get('status') == 'qmb'
            or result.get('refresh_requires_selection') is True
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or price.get('mode') != 'services' or price.get('conditional')
            or price.get('service_issues') or len(lines) != 1):
        return None
    line = lines[0]
    terms = line.get('benefits') or {}
    copay, fee, oop = terms.get('copay_cents'), line.get('allowed_cents'), terms.get('oop_remaining_cents')
    if (not re.fullmatch(r'992(?:0[2-5]|1[2-5])', str(line.get('code') or ''))
            or line.get('units') != 1 or line.get('modifiers')
            or line.get('allowed_basis') != 'payer_fee_schedule'
            or (line.get('rate_resolution') or {}).get('status') != 'RESOLVED'
            or type(fee) is not int or fee <= 0 or type(copay) is not int or copay <= 0
            or type(oop) is not int or oop < copay or copay > fee
            or terms.get('active') is not True or terms.get('copay_period') != 'VISIT'
            or str(terms.get('coinsurance_percent')) not in {'0', '0.0', '0.00'}
            or terms.get('deductible_applies') is True or terms.get('source_errors')
            or line.get('conditions')
            or any('remaining individual deductible' not in str(issue).lower()
                   for issue in line.get('issues') or [])):
        return None
    source = next((row for row in terms.get('reported_options') or []
                   if row.get('id') == terms.get('copay_id') and row.get('kind') == 'copay'), None)
    if (not source or source.get('amount_cents') != copay
            or '98' not in str(source.get('service_type_code') or '').split(', ')
            or source.get('diagnosis_scoped')
            or source.get('procedure_code') not in {None, '', line['code']}
            or source.get('network') in {'OUT_OF_NETWORK', 'O'}):
        return None
    selected = result.get('selected_ids') or []
    if selected and selected != [line.get('id')]:
        return None
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents') if payments.get('reference') else 0
    if type(paid) is not int or paid < 0:
        return None
    amount = max(0, copay - paid)
    guidance.update(amount_cents=amount, status='estimated', basis='humana_specialist_copay_advance',
        payer_reported_office_copay_cents=copay,
        reason='Refundable specialist office copay advance from the matched Humana Medicare PPO benefit. Final liability follows the processed claim and member billing protections.',
        patient_message='This refundable office copay advance is applied to the processed claim; excess is refunded.',
        collection_rule={'mode': 'humana_specialist_copay_advance',
                         'reference': 'Clinic-directed matched Humana Medicare PPO specialist office copay'})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
    return amount
