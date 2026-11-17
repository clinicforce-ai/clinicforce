"""One established Aetna office follow-up with a linked, exhausted medical limit."""
from copy import deepcopy
from unittest.mock import patch

from app.insurance import aetna_office_planning, amount_explanation, collection_guidance
from app.insurance.collection.benefit_gates import apply_aetna_office_individual_oop_deferral


def _office_case():
    identity = {'match_version': 2, 'status': 'matched', 'binding': 'synthetic-person'}
    options = [
        {'id': 'synthetic-oop-annual', 'kind': 'out_of_pocket', 'service_type_code': '30',
         'coverage_level': 'INDIVIDUAL', 'network': 'IN_NETWORK',
         'time_period': 'CALENDAR_YEAR', 'plan_association': 'medical', 'amount_cents': 400000},
        {'id': 'synthetic-oop', 'kind': 'out_of_pocket', 'service_type_code': '30',
         'coverage_level': 'INDIVIDUAL', 'network': 'IN_NETWORK',
         'time_period': 'REMAINING', 'plan_association': 'medical', 'amount_cents': 0},
        {'id': 'synthetic-copay', 'kind': 'copay', 'service_type_code': '98',
         'coverage_level': 'EMPLOYEE_ONLY', 'network': 'IN_NETWORK',
         'amount_cents': 0, 'payer_note': 'Specialist Visit or Evaluation'},
        {'id': 'synthetic-coinsurance', 'kind': 'coinsurance', 'service_type_code': '98',
         'coverage_level': 'EMPLOYEE_ONLY', 'network': 'IN_NETWORK',
         'percent': '20.0', 'payer_note': 'Specialist Visit or Evaluation'},
    ]
    benefits = {'active': True, 'identity': identity, 'service_date_matches': False,
                'returned_service_dates': {'start': '2026-11-17'},
                'plan_details': {'names': ['Aetna Choice POS II']},
                'network_status': 'unknown', 'deductible_remaining_cents': 0,
                'oop_annual_cents': 400000, 'oop_remaining_cents': 0,
                'oop_id': 'synthetic-oop',
                'reported_options': options}
    line = {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': [],
            'benefits': {'active': True, 'service_date_matches': False}}
    result = {'payer': 'Aetna', 'service_date': '2026-11-18',
              'checked_at': '2026-11-17T12:00:00Z', 'payment_mode': 'insurance',
              'identity': identity, 'benefits': benefits,
              'plan_details': {'stedi': benefits['plan_details']},
              'codes': [{'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}],
              'price_range': {'mode': 'services', 'complete': False,
                              'service_issues': [], 'lines': [line]},
              'appointment_context': {'place_of_service': '11'}}
    inputs = {'service_date': '2026-11-18', 'place_of_service': '11',
              'rendering_provider': {'npi': '1234567890'}}
    return result, inputs


def test_followup_fee_and_oop_cap_produce_zero_checkin_with_formula():
    result, inputs = _office_case()
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 7553, 'source_reference': 'synthetic schedule'}]):
        aetna_office_planning.attach(result, inputs)
    planning = result['aetna_office_planning']
    assert planning['allowed_cents'] == 7553
    assert planning['gross_coinsurance_cents'] == 1511
    assert planning['deductible_waived_cents'] == 0
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['collection_rule']['mode'] == 'aetna_office_individual_oop_deferral'
    assert '$75.53' in guidance['reason'] and '$15.11' in guidance['reason']
    assert 'Collect $0' in amount_explanation.build(result, guidance)['sentence']
    existing_zero_copay = {'amount_cents': 0, 'status': 'zero_advance',
                           'collection_rule': {'mode': 'payer_reported_zero_copay'}}
    apply_aetna_office_individual_oop_deferral(result, existing_zero_copay,
        member_bound=True, source_errors=[], adverse=False, same_policy_conflict=False)
    assert existing_zero_copay['collection_rule']['mode'] == 'aetna_office_individual_oop_deferral'


def test_followup_zero_rule_needs_linked_benefits_and_one_office_service():
    result, inputs = _office_case()
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 7553}]):
        aetna_office_planning.attach(result, inputs)
    for path, replacement in (
        ('oop_id', None), ('oop_remaining_cents', 100), ('has_secondary', True),
        ('network_status', 'out_of_network'), ('source_errors', [{'kind': 'invalid'}]),
        ('service_date_matches', None), ('oop_annual_cents', 0),
        ('separate_copay_limit', True),
    ):
        changed = deepcopy(result)
        changed['benefits'][path] = replacement
        assert collection_guidance.build(changed)['amount_cents'] is None, path
    changed = deepcopy(result)
    changed['price_range']['lines'].append({'id': 'second', 'code': '95911', 'units': 1,
                                            'benefits': {'active': True}})
    assert collection_guidance.build(changed)['amount_cents'] is None


def test_exact_date_followup_uses_same_exhausted_individual_limit():
    result, inputs = _office_case()
    result['benefits']['service_date_matches'] = True
    result['benefits']['returned_service_dates']['start'] = result['service_date']
    result['price_range']['lines'][0]['benefits']['service_date_matches'] = True
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 7553}]):
        aetna_office_planning.attach(result, inputs)
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['collection_rule']['mode'] == 'aetna_office_individual_oop_deferral'
