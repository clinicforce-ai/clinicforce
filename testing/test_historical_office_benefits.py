"""De-identified shapes from saved office responses; amounts are evidence, not targets."""
from copy import deepcopy

import pytest

from app.insurance import collection_guidance, commercial, visit_estimate
from testing.synthetic_policy import COPAY_POLICY

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


def replay(raw, *, confirmed_network=True, place_of_service=None):
    benefits = commercial.normalize_stedi_271(raw, '98', False, service_date=DAY,
                                               place_of_service=place_of_service)
    benefits['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}  # De-identified saved terms have no patient fields.
    price = visit_estimate.price_lines([{'id': 'visit', 'code': '99203', 'units': 1}], [], benefits, RULES, payload=raw, service_date=DAY)
    if confirmed_network:
        # Synthetic clinic participation keeps these tests focused on payer copay selection.
        benefits['network_status'] = 'in_network'
        benefits['network_source'] = 'clinic_library_contract'
    result = {'benefits': benefits, 'price_range': price,
              'collection_policy': COPAY_POLICY,
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


def test_employee_and_children_specialist_terms_apply_to_office_but_xray_terms_do_not_price_ncs():
    raw = {'plans': [{'name': 'Open Access MC', 'benefits': {
        'statuses': [{'status': 'Active', 'service': {'system': 'STC', 'value': '30'}}],
        'coPayment': [row('50', level='EMPLOYEE_AND_CHILDREN',
            messages=['Specialist Visit or Evaluation'], placesOfService=[{'code': '11'}]),
            row('50', stc='73', level='EMPLOYEE_AND_CHILDREN',
                messages=['Xray and Lab by a Specialist'])],
        'coInsurance': [row(level='EMPLOYEE_AND_CHILDREN', percent='0',
            messages=['Specialist Visit or Evaluation']),
            row(stc='73', level='EMPLOYEE_AND_CHILDREN', percent='.1',
                messages=['Complex Imaging'])],
        'limitations': [row(level='EMPLOYEE_AND_CHILDREN',
            messages=['Specialist Visit or Evaluation/Plan Ded Waived'])]}}]}
    office = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203',
        service_date=DAY, place_of_service='11')
    ncs = commercial.normalize_stedi_271(raw, '73', True, procedure_code='95908',
        service_date=DAY, place_of_service='11')
    assert office['copay_cents'] == 5000
    assert office['coinsurance_percent'] == '0'
    assert office['deductible_applies'] is False
    assert ncs['copay_cents'] is None
    assert ncs['coinsurance_percent'] is None


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


@pytest.mark.parametrize('other_office_label', ['Office Visit', 'Primary Care Visit or Evaluation'])
def test_qualified_specialist_copay_beats_other_office_row_without_a_rate(other_office_label):
    raw = {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coPayment': [row('35', messages=[other_office_label], placesOfService=[{'code': '11'}]),
                      row('50', messages=['Specialist'], placesOfService=[{'code': '11'}])],
        'deductible': [row('0', stc='30', period='REMAINING')]}}]}
    guidance, terms = replay(raw)
    assert terms['copay_cents'] == 5000
    assert guidance['basis'] == 'office_copay'
    assert guidance['amount_cents'] == 5000


def test_auth_metadata_does_not_make_equal_specialist_copays_conflict():
    raw = {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coPayment': [row('35', messages=['Office Visit'], placesOfService=[{'code': '11'}]),
                      row('50', messages=['Specialist'], placesOfService=[{'code': '11'}]),
                      row('50', messages=['Specialist'], placesOfService=[{'code': '11'}],
                          priorAuthIndicator='REQUIRED')]}}]}
    guidance, terms = replay(raw)
    assert terms['copay_cents'] == 5000
    assert terms['authorization_signal'] == 'unknown'
    assert set(terms['selected_benefit_ids']) == {
        'plans[0].benefits.coPayment[1]', 'plans[0].benefits.coPayment[2]'}


def test_telemedicine_and_aba_copays_do_not_price_in_person_office():
    raw = {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'coPayment': [
            row('35', messages=['TELEMEDICINE EXCEPT WHEN PREVENTIVE'], period='VISIT'),
            row('50', messages=['SPECIALIST; TELEMEDICINE EXCEPT WHEN PREVENTIVE'], period='VISIT'),
            row('35', messages=['APPLIED BEHAVIOR ANALYSIS FOR AUTISM SPECTRUM DISORDER'],
                period='VISIT', priorAuthIndicator='REQUIRED'),
            row('50', messages=['SPECIALIST; APPLIED BEHAVIOR ANALYSIS FOR AUTISM SPECTRUM DISORDER'],
                period='VISIT', priorAuthIndicator='REQUIRED')],
        'coInsurance': [row(stc='98', percent='.2',
            messages=['AGENTS, DRUGS AND/OR SUPPLIES ADMINISTERED OR OBTAINED IN CONNECTION WITH CARE'])]}}]}
    guidance, terms = replay(raw, place_of_service='11')
    assert terms['copay_cents'] is None
    assert terms['coinsurance_percent'] is None
    assert guidance['amount_cents'] is None
    assert len(terms['excluded_service_evidence_ids']) == 5
    assert len([option for option in terms['reported_options'] if option['kind'] == 'copay']) == 4


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


def test_diagnostic_medical_family_oop_is_kept_away_from_office_care():
    raw = {'plans': [{'benefits': {'statuses': [
        {'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'},
         'planCoverageDescription': 'PPO DENTAL'},
        {'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'},
         'planCoverageDescription': 'PPO MEDICAL'}],
        'coInsurance': [row(stc='98', percent='0', messages=['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL'])],
        'outOfPocket': [row('0', stc='30', level='FAMILY', period='REMAINING',
                            messages=['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL'])]}}]}
    terms = commercial.normalize_stedi_271(raw, '73', False,
                                            procedure_code='95909', service_date=DAY)
    assert terms['family_oop_zero_applies'] is True
    office = commercial.normalize_stedi_271(raw, '98', False,
                                             procedure_code='99213', service_date=DAY,
                                             place_of_service='11')
    assert office['family_oop_zero_applies'] is False
    assert office['family_oop_remaining_cents'] is None
    raw['plans'][0]['benefits']['outOfPocket'][0]['amount'] = '0'
    raw['plans'][0]['benefits']['limitations'] = [row('100', level='FAMILY', period='REMAINING', messages=['COPAY MAX'])]
    assert replay(raw)[0]['amount_cents'] is None


def test_office_only_family_oop_is_kept_away_from_diagnostic_testing():
    raw = {'plans': [{'name': 'PPO MEDICAL', 'benefits': {
        'statuses': [{'status': 'Active', 'service': {'system': 'STC', 'value': '30'}}],
        'outOfPocket': [row('0', stc='30', level='FAMILY', period='REMAINING',
                            messages=['OFFICE VISIT - PROFESSIONAL'])]}}]}
    terms = commercial.normalize_stedi_271(raw, '73', True,
                                            procedure_code='95909', service_date=DAY)
    assert terms['family_oop_zero_applies'] is False


def test_separate_diagnostic_and_office_inquiries_apply_met_family_medical_cap_to_combined_visit():
    diagnostic = {'plans': [{'benefits': {'statuses': [
        {'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'},
         'planCoverageDescription': 'PPO DENTAL'},
        {'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'},
         'planCoverageDescription': 'PPO MEDICAL'}],
        'outOfPocket': [row('0', stc='30', level='FAMILY', period='REMAINING',
                            messages=['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL'])]}}]}
    office = {'plans': [{'name': 'PPO MEDICAL', 'benefits': {
        'statuses': [{'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'}}],
        'outOfPocket': [row('0', stc='30', level='FAMILY', period='REMAINING',
                            messages=['OFFICE VISIT - PROFESSIONAL'])]}}]}
    diagnostic['_service_payloads'] = {'98': office}
    benefits = commercial.normalize_stedi_271(diagnostic, '73', True, service_date=DAY)
    benefits.update(network_status='in_network', network_source='clinic_group_agreement',
                    identity={'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2})
    codes = [{'id': code, 'code': code, 'units': 1} for code in ('95909', '95886', '99213')]
    rates = [{'code': code, 'allowed_cents': amount, 'source_reference': 'Synthetic contract'}
             for code, amount in [('95909', 12644), ('95886', 8918), ('99213', 7615)]]
    price = visit_estimate.price_lines(codes, rates, benefits, RULES,
                                       payload=diagnostic, service_date=DAY)
    assert [line['benefits']['family_oop_zero_applies'] for line in price['lines']] == [True, True, True]
    planning = visit_estimate.planning_estimate(price, benefits, benefits['identity'])
    assert planning['amount_cents'] == 0
    guidance = collection_guidance.build({'benefits': benefits, 'price_range': price,
                                          'planning_estimate': planning,
                                          'collection_policy': COPAY_POLICY})
    assert guidance['amount_cents'] == 0 and guidance['basis'] == 'out_of_pocket_limit'
