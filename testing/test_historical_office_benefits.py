"""De-identified shapes from saved office responses; amounts are evidence, not targets."""
from copy import deepcopy

import pytest

from app.insurance import collection_guidance, commercial, visit_estimate

DAY = '2026-10-01'
RULES = {'cost_share': 'payer_returned', 'office_service_types': ['BQ', 'BY', '98'], 'place_of_service': '11'}


def row(amount=None, *, stc='98', level='INDIVIDUAL', messages=None, period='', **extra):
    return {'service': {'system': 'STC', 'value': stc}, 'coverageLevel': level,
            'network': {'indicator': 'IN_NETWORK'}, 'messages': messages or [],
            'timePeriod': period, **({'amount': amount} if amount is not None else {}), **extra}


def aetna():
    return {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coPayment': [row('25', level='EMPLOYEE_ONLY', messages=['All Other In-Network Providers', 'GYN Visit', 'Specialist Visit or Evaluation'], placesOfService=[{'code': '11'}]),
                      row('15', level='EMPLOYEE_ONLY', messages=['Primary Care Visit or Evaluation']),
                      row('25', level='EMPLOYEE_ONLY', messages=['Telemedicine Specialist Visit'])],
        'coInsurance': [row(level='EMPLOYEE_ONLY', percent='0', messages=['Specialist Visit or Evaluation', 'Primary Care Visit or Evaluation'], placesOfService=[{'code': '11'}])],
        'deductible': [row('0', stc='30', period='REMAINING')],
        'outOfPocket': [row('0', stc='30', period='REMAINING')],
        'limitations': [row(level='EMPLOYEE_ONLY', messages=['All Other In-Network Providers', 'Specialist Visit or Evaluation/Plan Ded Waived', 'Primary Care Visit or Evaluation/Plan Ded Waived']),
                       row('5450', period='CALENDAR_YEAR', messages=['All Other In-Network Providers']),
                       row('4585', period='REMAINING', messages=['COPAY MAX']),
                       row('10035', level='FAMILY', period='REMAINING', messages=['COPAY MAX'])]}}]}


def uhc():
    return {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coPayment': [row('25', stc='BY'), row('75', stc='BY'), row('75', stc='BY', messages=['Provider Role OTHER'], placesOfService=[{'code': '11'}])],
        'coInsurance': [row(stc='BY', percent='0'), row(stc='BY', percent='0', messages=['Provider Role OTHER'], placesOfService=[{'code': '11'}])],
        'deductible': [row('4048.34', stc='30', period='REMAINING')],
        'outOfPocket': [row('4276.46', stc='30', period='REMAINING')]}}]}


def replay(raw, *, confirmed_network=True):
    benefits = commercial.normalize_stedi_271(raw, '98', False, service_date=DAY)
    benefits['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}  # De-identified saved terms have no patient fields.
    price = visit_estimate.price_lines([{'id': 'visit', 'code': '99203', 'units': 1}], [], benefits, RULES, payload=raw, service_date=DAY)
    if confirmed_network:
        # Synthetic clinic participation keeps these tests focused on payer copay selection.
        benefits['network_status'] = 'in_network'
        benefits['network_source'] = 'clinic_library_contract'
    result = {'benefits': benefits, 'price_range': price,
              'planning_estimate': visit_estimate.planning_estimate(price, benefits, benefits['identity'])}
    return collection_guidance.build(result), price['lines'][0]['benefits']


def test_employee_only_specialist_copay_uses_separate_cap_without_contract_allowance():
    guidance, terms = replay(aetna())
    assert guidance['amount_cents'] == 2500 and guidance['basis'] == 'office_copay'
    assert terms['oop_remaining_cents'] == 0 and terms['copay_remaining_cents'] == 458500
    assert terms['deductible_applies'] is False
    assert 'plans[0].benefits.limitations[2]' in guidance['services'][0]['selected_benefit_ids']
    assert any('separate remaining copay maximum' in note for note in guidance['assumptions'])
    raw = aetna()
    raw['plans'][0]['benefits']['limitations'][2]['amount'] = '10'
    assert replay(raw)[0]['amount_cents'] == 1000
    raw['plans'][0]['benefits']['limitations'][2]['amount'] = '0'
    assert replay(raw)[0]['amount_cents'] == 0


@pytest.mark.parametrize('amount', ['-1', 'NaN', 'bad'])
def test_invalid_separate_cap_cannot_turn_met_general_oop_into_free_care(amount):
    raw = aetna()
    raw['plans'][0]['benefits']['limitations'][2]['amount'] = amount
    assert replay(raw)[0]['amount_cents'] is None


def test_employee_terms_cannot_be_applied_to_a_dependent_or_unrelated_services():
    raw = aetna()
    raw['dependent'] = {'firstName': 'Dependent'}
    assert replay(raw)[0]['amount_cents'] is None
    terms = commercial.normalize_stedi_271(aetna(), '73', False, procedure_code='95886', service_date=DAY)
    assert terms['copay_cents'] is None


def test_provider_other_role_resolves_specialist_copay_and_discloses_remaining_deductible():
    guidance, terms = replay(uhc())
    assert guidance['amount_cents'] == 7500 and terms['deductible_remaining_cents'] == 404834
    assert terms['selected_benefit_ids'] == ['plans[0].benefits.coPayment[2]', 'plans[0].benefits.coInsurance[1]']
    assert any('additional deductible responsibility' in note for note in guidance['assumptions'])
    raw = uhc()
    raw['plans'][0]['benefits']['coPayment'][2]['messages'] = []
    assert replay(raw)[0]['amount_cents'] is None  # Neither smallest nor largest wins by default.


def test_historical_in_network_copay_waits_for_product_participation():
    assert replay(uhc(), confirmed_network=False)[0]['amount_cents'] is None


def test_unknown_service_limit_or_conflicting_specialist_term_still_blocks_advance():
    raw = aetna()
    raw['plans'][0]['benefits']['limitations'].append(row(messages=['First three visits only']))
    assert replay(raw)[0]['amount_cents'] is None
    raw = aetna()
    conflicting = deepcopy(raw['plans'][0]['benefits']['coPayment'][0])
    conflicting['amount'] = '50'
    raw['plans'][0]['benefits']['coPayment'].append(conflicting)
    assert replay(raw)[0]['amount_cents'] is None


def test_met_family_oop_supports_zero_advance_without_copying_family_balances_to_individual():
    raw = {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coInsurance': [row(percent='.2')],
        'deductible': [row('0', level='FAMILY', period='REMAINING')],
        'outOfPocket': [row('0', level='FAMILY', period='REMAINING')]}}]}
    guidance, terms = replay(raw)
    assert guidance['amount_cents'] == 0 and guidance['basis'] == 'out_of_pocket_limit'
    assert terms['deductible_remaining_cents'] is None and terms['oop_remaining_cents'] is None
    assert terms['family_oop_zero_applies'] is True
    assert terms['family_oop_ids'] == ['plans[0].benefits.outOfPocket[0]']
    assert any(option['coverage_level'] == 'FAMILY' for option in terms['reported_options'])
    raw['plans'][0]['benefits']['outOfPocket'][0]['amount'] = '500'
    assert replay(raw)[0]['amount_cents'] is None
    raw['plans'][0]['benefits']['outOfPocket'][0]['amount'] = '0'
    raw['plans'][0]['benefits']['limitations'] = [row('100', level='FAMILY', period='REMAINING', messages=['COPAY MAX'])]
    assert replay(raw)[0]['amount_cents'] is None
