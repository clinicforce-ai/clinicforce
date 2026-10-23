"""Synthetic service-specific deductible rules; no patient data or payer calls."""
from copy import deepcopy

import pytest

from app.insurance import commercial, collection_guidance, encounter_summary, visit_estimate
from testing.synthetic_policy import ADVANCE_POLICY

DAY = '2026-10-08'


def row(code, amount=None, stc='98', period=None, message=None):
    value = {'code': code, 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y',
             'serviceTypeCodes': [stc], 'authOrCertIndicator': 'N'}
    if amount is not None:
        value['benefitAmount'] = amount
    if period:
        value['timeQualifierCode'] = period
    if message:
        value['additionalInformation'] = [{'description': message}]
    return value


def payload():
    return {'benefitsInformation': [row('1', stc='30', message='No referral required'),
        row('B', '50', period='27', message='Specialist'), row('C', '2000', stc='30', period='23'),
        row('C', '1300', stc='30', period='29'), row('G', '5000', stc='30', period='29')]}


def run(raw, codes=None, rates=None):
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99214', service_date=DAY)
    benefits['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}  # Synthetic member match; tests isolate cost sharing.
    benefits['network_source'] = 'clinic_library_contract'
    price = visit_estimate.price_lines(codes or [{'id': 'office', 'code': '99214', 'units': 1}],
        rates if rates is not None else [{'code': '99214', 'allowed_cents': 20000}], benefits,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    guidance = collection_guidance.build({'benefits': benefits, 'price_range': price,
                                          'collection_policy': ADVANCE_POLICY})
    return benefits, price, guidance


@pytest.mark.parametrize('message', ['DEDUCTIBLE DOES NOT APPLY', 'NO DEDUCTIBLE',
    'NOT SUBJECT TO DEDUCTIBLE', 'DEDUCTIBLE WAIVED', 'COPAY BEFORE DEDUCTIBLE'])
def test_explicit_service_waiver_keeps_plan_balance_but_collects_only_copay(message):
    raw = payload()
    raw['benefitsInformation'][1]['additionalInformation'].append({'description': message})
    benefits, price, guidance = run(raw)
    assert benefits['deductible_applicability'] == 'DOES_NOT_APPLY'
    assert benefits['deductible_remaining_cents'] == 130000
    assert benefits['deductible_annual_cents'] == 200000
    assert price['complete'] and price['patient_max_cents'] == 5000
    assert price['lines'][0]['calculation']['deductible_cents'] == 0
    assert guidance['amount_cents'] == 5000


def test_service_zero_deductible_without_level_differs_from_zero_remaining():
    raw = payload()
    waiver = row('C', '0'); waiver.pop('coverageLevelCode')
    raw['benefitsInformation'].append(waiver)
    assert run(raw)[0]['deductible_applicability'] == 'DOES_NOT_APPLY'
    assert run(raw)[1]['patient_max_cents'] == 5000
    raw = payload(); raw['benefitsInformation'][3]['benefitAmount'] = '0'
    benefits, price, _ = run(raw)
    assert benefits['deductible_applicability'] == 'ALREADY_MET'
    assert benefits.get('deductible_applies') is not False
    assert price['patient_max_cents'] == 5000


def test_unknown_interaction_returns_disclosed_copay_not_annual_deductible_charge():
    benefits, price, guidance = run(payload())
    assert benefits['deductible_applicability'] == 'UNKNOWN'
    assert not price['complete']
    assert guidance['amount_cents'] == 5000 and guidance['basis'] == 'office_copay'
    assert any('did not establish whether the deductible applies' in text for text in guidance['assumptions'])


def test_after_deductible_uses_allowed_amount_and_requires_rate():
    raw = payload(); raw['benefitsInformation'][1]['additionalInformation'] = [{'description': 'Copay after deductible'}]
    benefits, price, guidance = run(raw)
    assert benefits['deductible_applicability'] == 'APPLIES'
    assert price['patient_max_cents'] == 20000 and guidance['amount_cents'] == 20000
    assert run(raw, rates=[])[2]['amount_cents'] is None


def test_three_unit_procedure_uses_per_unit_contract_rates_before_deductible():
    priced = commercial.estimate(
        [{'code': '11104', 'units': 1, 'allowed_cents': 11660},
         {'code': '11105', 'units': 2, 'allowed_cents': 5625}],
        {'active': True, 'coverage_level': 'individual', 'network_status': 'in_network',
         'deductible_remaining_cents': 100000, 'oop_remaining_cents': 207549,
         'coinsurance_percent': '20'},
        {'cost_share': 'coinsurance'},
    )
    assert priced['allowed_cents'] == 22910
    assert priced['deductible_cents'] == 22910
    assert priced['coinsurance_cents'] == 0
    assert priced['patient_cents'] == 22910


def test_conflicting_or_conditional_waivers_cannot_erase_deductible():
    raw = payload(); raw['benefitsInformation'][1]['additionalInformation'] = [{'description': 'Deductible waived'}]
    raw['benefitsInformation'].append(row('C', '1000', period='23'))
    benefits, price, guidance = run(raw)
    assert benefits['deductible_applicability'] == 'UNKNOWN'
    assert not price['complete'] and guidance['amount_cents'] is None
    raw = payload(); raw['benefitsInformation'][1]['additionalInformation'] = [{'description': 'Deductible waived for first three visits only'}]
    assert run(raw)[0]['deductible_applicability'] == 'UNKNOWN'
    assert run(raw)[2]['amount_cents'] is None


def test_office_waiver_does_not_waive_ncs_deductible():
    raw = payload(); raw['benefitsInformation'].append(row('C', '0'))
    coinsurance = row('A', stc='73'); coinsurance['benefitPercent'] = '.2'
    raw['benefitsInformation'].extend([coinsurance, row('C', '100', stc='73', period='29')])
    codes = [{'id': code, 'code': code, 'units': 1} for code in ('99214', '95907')]
    rates = [{'code': code['code'], 'allowed_cents': 20000} for code in codes]
    _, price, _ = run(raw, codes, rates)
    assert price['complete'] and price['patient_max_cents'] == 17000
    assert [line['calculation']['deductible_cents'] for line in price['lines']] == [0, 10000]


def test_new_stedi_shape_keeps_same_waiver_semantics():
    base = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'},
            'service': {'system': 'STC', 'value': '98'}}
    raw = {'plans': [{'benefits': {'statuses': [{**base, 'status': 'ACTIVE_COVERAGE'}],
        'coPayment': [{**base, 'amount': '50', 'timePeriod': 'VISIT', 'messages': ['Specialist', 'Deductible does not apply']}],
        'deductible': [{**base, 'amount': '1300', 'timePeriod': 'REMAINING', 'service': {'system': 'STC', 'value': '30'}}],
        'outOfPocket': [{**base, 'amount': '5000', 'timePeriod': 'REMAINING'}]}}]}
    benefits, price, guidance = run(raw)
    assert benefits['deductible_applicability'] == 'DOES_NOT_APPLY'
    assert price['patient_max_cents'] == 5000 and guidance['amount_cents'] == 5000


def test_waiver_for_different_service_or_out_of_network_is_not_applied():
    for other in (row('C', '0', stc='73'), {**row('C', '0'), 'inPlanNetworkIndicatorCode': 'N'}):
        raw = payload(); raw['benefitsInformation'].append(deepcopy(other))
        assert run(raw)[0]['deductible_applicability'] == 'UNKNOWN'


def test_sanitized_neurology_visit_keeps_copay_when_testing_rates_are_missing():
    raw = {'benefitsInformation': [
        row('1', stc='30'),
        row('B', '30', stc='98', period='27', message='Specialist'),
        row('C', '300', stc='30', period='23'),
        row('C', '223.78', stc='30', period='29'),
        row('G', '2923.78', stc='30', period='29'),
    ]}
    raw['benefitsInformation'][1]['additionalInformation'].append({'description': 'Deductible does not apply'})
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY)
    benefits['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    benefits['network_source'] = 'clinic_library_contract'
    codes = [{'id': 'office', 'code': '99204', 'units': 1},
             {'id': 'nerve', 'code': '95911', 'units': 1},
             {'id': 'muscle', 'code': '95886', 'units': 1}]
    rates = [{'code': '99204', 'allowed_cents': 15000, 'cash_cents': 25000}]
    price = visit_estimate.price_lines(codes, rates, benefits, {'cost_share': 'payer_returned'},
                                       payload=raw, service_date=DAY)
    result = {'benefits': benefits, 'price_range': price, 'collection_policy': ADVANCE_POLICY}
    result['collection_guidance'] = collection_guidance.build(result)
    summary = encounter_summary.build(result)
    assert benefits['deductible_annual_cents'] == 30000
    assert benefits['deductible_remaining_cents'] == 22378
    assert benefits['oop_remaining_cents'] == 292378
    assert price['lines'][0]['calculation']['deductible_cents'] == 0
    assert [line['rate_resolution']['status'] for line in price['lines']] == [
        'RESOLVED', 'RATE_UNAVAILABLE', 'RATE_UNAVAILABLE']
    assert result['collection_guidance']['amount_cents'] == 3000
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert price['lines'][0]['cash_cents'] == 25000
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['estimated_later_balance_cents'] is None
    assert summary['unpriced_codes'] == ['95911', '95886']
