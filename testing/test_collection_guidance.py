from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from app.insurance import collection_guidance, commercial, visit_estimate
from app.api.routers import commercial_estimator as api


def scenario(amount=3800):
    return {'benefits': {'active': True, 'network_status': 'in_network'},
            'identity': {'status': 'warning'},
            'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': amount, 'patient_max_cents': amount,
                'lines': [{'id': 'office', 'code': '99203', 'units': 1,
                    'calculation': {'patient_cents': amount}, 'benefits': {'active': True,
                    'requirements': {kind: {'required': 'unknown', 'cleared': False} for kind in ('referral', 'authorization')}}}]}}


@pytest.mark.parametrize('result', [{}, {'eligibility_failure': {'title': 'Timeout'}},
    {'benefits': {'active': False}}, {'benefits': {'active': True, 'network_status': 'out_of_network'}},
    {'benefits': {'active': True}, 'price_range': {'lines': [], 'mode': 'options'}}])
def test_errors_always_defer_with_numeric_zero_without_claiming_free_care(result):
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['status'] == 'deferred'
    assert 'does not mean your care is free' in value['patient_message']


def test_case_specific_amount_changes_and_payments_are_deducted_once():
    result = scenario()
    assert collection_guidance.build(result)['amount_cents'] == 3800
    assert collection_guidance.build(scenario(9123))['amount_cents'] == 9123
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000
    result['arrival_collection'] = {'amount_cents': 3000, 'payments_received_cents': 800}
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000


@pytest.mark.parametrize('change', [{'active': False}, {'has_secondary': True}, {'service_date_matches': False},
                                  {'network_status': 'out_of_network'}, {'review_reasons': ['Eligibility evidence conflicts']}])
def test_contradictions_override_cached_numeric_amount(change):
    result = scenario()
    result['arrival_collection'] = {'amount_cents': 3800}
    result['benefits'].update(change)
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['status'] == 'deferred'


@pytest.mark.parametrize('kind', ['referral', 'authorization'])
@pytest.mark.parametrize('requirement', [{'required': 'required', 'cleared': False}, {'status': 'denied'}, {'status': 'expired'}, {'status': 'pending'}])
def test_required_or_adverse_approval_does_not_become_an_assumed_approval(kind, requirement):
    result = scenario()
    result['price_range']['lines'][0]['benefits']['requirements'][kind] = requirement
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['status'] == 'deferred'
    assert value['patient_actions']


def test_missing_network_defers_positive_amount_but_scoped_contract_can_support_assumed_estimate():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    assert collection_guidance.build(result)['status'] == 'deferred'
    result['pricing_assumptions'] = ['Exact product participation is assumed for the scoped provider contract.']
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 3800 and value['network'] == 'In network · assumed'
    assert 'approval' in ' '.join(value['assumptions'])


def test_oop_zero_is_case_evidence_not_a_universal_free_care_default():
    result = scenario(0)
    result['benefits']['network_status'] = 'unknown'
    result['price_range']['complete'] = False
    result['planning_estimate'] = {'basis': 'out_of_pocket_limit', 'amount_cents': 0, 'assumptions': ['Covered in-network services.']}
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['basis'] == 'out_of_pocket_limit'
    assert value['services'][0]['patient_cents'] == 0
    result['identity']['status'] = 'review'
    assert collection_guidance.build(result)['status'] == 'deferred'


def test_new_patient_alternatives_collect_the_lowest_estimate_and_explain_final_complexity():
    result = scenario()
    result['price_range'].update(mode='alternatives', patient_max_cents=12000)
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 3800
    assert 'Final complexity' in ' '.join(value['assumptions'])


def office_payload():
    base = {'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
            'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'DAY', 'priorAuthIndicator': 'U'}
    return {'plans': [{'benefits': {'statuses': [{'status': 'Active'}], 'coPayment': [
        {**base, 'amount': '40', 'placesOfService': [{'code': '11'}]},
        {**base, 'amount': '20', 'placesOfService': [{'code': '10'}]}]}}]}


def test_office_and_telehealth_benefits_are_scoped_to_actual_visit_location():
    payload = office_payload()
    args = {'procedure_code': '99203', 'service_date': '2026-09-29'}
    office = commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)
    remote = commercial.normalize_stedi_271(payload, '98', True, place_of_service='10', **args)
    assert office['copay_cents'] == 4000 and remote['copay_cents'] == 2000
    assert office['selected_benefit_ids'] == ['plans[0].benefits.coPayment[0]']
    assert commercial.normalize_stedi_271(payload, '98', True, **args)['copay_cents'] is None
    assert office['authorization_signal'] == 'unknown'  # Explicit U is not an absent indicator.
    del payload['plans'][0]['benefits']['coPayment'][0]['priorAuthIndicator']
    absent = commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)
    assert absent['authorization_signal'] == 'not_required'
    payload['plans'][0]['benefits']['coverageBasis'] = [{'service': {'system': 'CPT', 'value': '99203'}, 'priorAuthIndicator': 'REQUIRED'}]
    assert commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)['authorization_signal'] == 'required'


def test_product_label_fallback_does_not_bypass_provider_location_or_date_matching():
    mismatch = HTTPException(409, 'The clinic fee schedule is not matched to the exact payer product.')
    with patch.object(api, '_practice_path', side_effect=[mismatch, ({}, {'network_name': 'PPO'}, {})]) as path:
        _, network, _ = api._previsit_practice_path('clinic', {'place_of_service': '11'}, {'payer_plan_names': ['Product']})
        assert network['product_assumed'] is True
        assert path.call_args.args[1] == {'place_of_service': '11'}
        assert path.call_args.args[2]['payer_plan_names'] == []
    with patch.object(api, '_practice_path', side_effect=HTTPException(409, 'Provider record missing')) as path:
        with pytest.raises(HTTPException):
            api._previsit_practice_path('clinic', {}, {})
        assert path.call_count == 1


def test_standalone_office_copay_is_one_per_visit_and_requires_unambiguous_terms():
    result = scenario()
    result['price_range'].update(complete=False, mode='alternatives')
    line = result['price_range']['lines'][0]
    line['benefits'].update(copay_cents=4700, copay_period='DAY', deductible_applies=False, oop_remaining_cents=100000)
    result['price_range']['lines'] = [{**deepcopy(line), 'code': str(code)} for code in range(99202, 99206)]
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 4700 and value['basis'] == 'office_copay'
    result['price_range']['lines'][0]['benefits']['copay_cents'] = 6200
    assert collection_guidance.build(result)['status'] == 'deferred'


def test_unmapped_service_cannot_inherit_office_zero_cost_benefit():
    payload = office_payload()
    payload['plans'][0]['benefits']['outOfPocket'] = [{'service': {'system': 'STC', 'value': '30'},
        'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'REMAINING', 'amount': '0'}]
    benefits = commercial.normalize_stedi_271(payload, '98', True)
    priced = visit_estimate.price_lines([{'id': 'unknown', 'code': '99999', 'units': 1}], [], benefits,
        {'place_of_service': '11'}, payload=payload, service_date='2026-09-29')
    assert 'needs mapping' in priced['lines'][0]['benefit_basis']
    assert visit_estimate.planning_estimate(priced, benefits, {}) is None
    value = collection_guidance.build({'benefits': benefits, 'price_range': priced})
    assert value['services'][0]['copay_cents'] is None
    assert value['services'][0]['selected_benefit_ids'] == []


def test_plain_office_description_is_not_a_visit_count_restriction():
    payload = office_payload()
    row = payload['plans'][0]['benefits']['coPayment'][0]
    row['messages'] = ['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL;OFFICE VISIT - PROFESSIONAL']
    def normalized():
        return commercial.normalize_stedi_271(payload, '98', True, procedure_code='99203', place_of_service='11')
    assert not any('conditional benefit' in r for r in normalized()['review_reasons'])
    row['messages'] = ['OFFICE VISIT - PROFESSIONAL;FIRST 10 VISITS ONLY']
    assert any('conditional benefit' in r for r in normalized()['review_reasons'])


def test_ppo_referral_assumption_is_limited_to_routine_office_and_never_overrides_required():
    result = scenario()
    result['plan_details'] = {'stedi': {'names': ['PREFERRED PROVIDER OPTION MEDICAL']}}
    assert collection_guidance.build(result)['requirements']['referral'] == 'Not required · assumed'
    result['price_range']['lines'][0]['benefits']['requirements']['referral']['required'] = 'required'
    assert collection_guidance.build(result)['status'] == 'deferred'
    result['plan_details']['stedi']['names'] = ['HMO']
    result['price_range']['lines'][0]['benefits']['requirements']['referral']['required'] = 'unknown'
    assert collection_guidance.build(result)['requirements']['referral'] == 'No requirement reported'
