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
def test_missing_evidence_defers_advance_without_asserting_zero_responsibility(result):
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'deferred_billing'
    assert 'estimate is not available yet' in value['patient_message']
    assert 'No insurance advance is collected' not in value['patient_message']


def test_unresolved_coverage_explains_the_actual_recovery_step():
    result = {'benefits': {}, 'price_range': {'lines': []},
              'eligibility_failure': {'kind': 'coverage_identity_missing', 'detail': 'eCW lacks policyholder details.'},
              'recovery': {'outcome': 'review_needed'}}
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'policy selection' in value['reason']
    result.pop('recovery')
    assert 'eCW lacks policyholder details.' in collection_guidance.build(result)['reason']


def test_secondary_coverage_retains_provider_enrollment_blocker():
    result = {'benefits': {'has_secondary': True}, 'eligibility_failure': {'kind': 'provider_enrollment'}}
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'coordinate benefits' in value['reason']
    assert 'provider enrollment' in value['reason']


def test_family_only_accumulators_do_not_become_individual_collection():
    result = scenario()
    result['price_range']['complete'] = False
    result['benefits']['review_reasons'] = ['Confirm the remaining individual deductible that applies to this visit.']
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'individual deductible' in value['reason']


def test_case_specific_amount_changes_and_payments_are_deducted_once():
    result = scenario()
    assert collection_guidance.build(result)['amount_cents'] == 3800
    assert collection_guidance.build(scenario(9123))['amount_cents'] == 9123
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000
    result['arrival_collection'] = {'amount_cents': 3000, 'payments_received_cents': 800}
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000


def test_conditional_calculation_is_for_planning_and_does_not_authorize_collection():
    result = scenario()
    result['price_range']['conditional'] = True
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'unresolved' in value['reason']


def test_conditional_total_can_preserve_independent_fixed_office_copay():
    result = scenario(9000)
    result['price_range']['conditional'] = True
    result['price_range']['lines'][0]['benefits'].update(
        copay_cents=3000, copay_period='DAY', deductible_applies=False,
        oop_remaining_cents=100000, coinsurance_percent=None)
    value = collection_guidance.build(result)
    assert value['basis'] == 'office_copay'
    assert value['amount_cents'] == 3000


@pytest.mark.parametrize('change', [{'active': False}, {'has_secondary': True}, {'service_date_matches': False},
                                  {'network_status': 'out_of_network'}, {'review_reasons': ['Eligibility evidence conflicts']}])
def test_contradictions_override_cached_numeric_amount(change):
    result = scenario()
    result['arrival_collection'] = {'amount_cents': 3800}
    result['benefits'].update(change)
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'deferred_billing'


@pytest.mark.parametrize('kind', ['referral', 'authorization'])
@pytest.mark.parametrize('requirement', [{'required': 'required', 'cleared': False}, {'status': 'denied'}, {'status': 'expired'}, {'status': 'pending'}])
def test_required_or_adverse_approval_does_not_become_an_assumed_approval(kind, requirement):
    result = scenario()
    result['price_range']['lines'][0]['benefits']['requirements'][kind] = requirement
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'deferred_billing'
    assert value['patient_actions']


def test_missing_network_defers_positive_amount_but_scoped_contract_can_support_assumed_estimate():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    assert collection_guidance.build(result)['status'] == 'deferred_billing'
    result['pricing_assumptions'] = ['Exact product participation is assumed for the scoped provider contract.']
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 3800 and value['network'] == 'Not reported'
    assert value['requirements']['authorization'] == 'No requirement reported'


def test_oop_zero_is_case_evidence_not_a_universal_free_care_default():
    result = scenario(0)
    result['benefits']['network_status'] = 'unknown'
    result['price_range']['complete'] = False
    result['planning_estimate'] = {'basis': 'out_of_pocket_limit', 'amount_cents': 0, 'assumptions': ['Covered in-network services.']}
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['basis'] == 'out_of_pocket_limit'
    assert value['services'][0]['patient_cents'] == 0
    result['identity']['status'] = 'review'
    assert collection_guidance.build(result)['status'] == 'deferred_billing'


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


def test_product_mismatch_never_falls_back_to_another_contract():
    mismatch = HTTPException(409, 'The clinic fee schedule is not matched to the exact payer product.')
    with patch.object(api, '_practice_path', side_effect=mismatch) as path:
        with pytest.raises(HTTPException, match='exact payer product'):
            api._previsit_practice_path('clinic', {'place_of_service': '11'}, {'payer_plan_names': ['Product']})
        assert path.call_count == 1
        assert path.call_args.args[1] == {'place_of_service': '11'}
        assert path.call_args.args[2]['payer_plan_names'] == ['Product']
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
    assert collection_guidance.build(result)['status'] == 'deferred_billing'


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


def office_coinsurance_payload():
    common = {'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
              'network': {'indicator': 'IN_NETWORK'}}
    return {'subscriber': {'dates': {'service': {'start': '2026-10-07'}}}, 'plans': [{'benefits': {
        'statuses': [{'status': 'Active Coverage'}],
        'coPayment': [{**common, 'amount': '0', 'messages': ['Specialist Visit or Evaluation', 'Walk In Clinic Visit']}],
        'coInsurance': [{**common, 'percent': '0.20', 'messages': ['All Other In-Network Providers',
            'Specialist Visit or Evaluation,COINS APPLIES TO OUT OF POCKET',
            'Walk In Clinic Visit,COINS APPLIES TO OUT OF POCKET',
            'Telemedicine General Medicine Visit,COINS APPLIES TO OUT OF POCKET',
            'Telemedicine Specialist Visit,COINS APPLIES TO OUT OF POCKET']}],
        'deductible': [{**common, 'amount': '0', 'timePeriod': 'REMAINING'}],
        'outOfPocket': [{**common, 'amount': '1500', 'timePeriod': 'REMAINING'}],
        'limitations': [{**common, 'messages': [
            "Our records indicate the provider ID you entered is participating in this patient's network."]}]}}]}


@pytest.mark.parametrize(('copay', 'coinsurance', 'fee', 'deductible', 'expected'), [
    ('0', '0.20', 25000, '0', None), ('0', '0.20', 17321, '0', None),
    ('0', '0.20', 17321, '20', None), ('0', '0', 25000, '0', 0),
    ('50', '0', 25000, '0', 5000), ('60', '0', 25000, '0', 6000)])
def test_office_collection_uses_actual_cost_share_and_active_schedule(copay, coinsurance, fee, deductible, expected):
    payload = office_coinsurance_payload()
    terms = payload['plans'][0]['benefits']
    terms['coPayment'][0]['amount'] = copay
    terms['coInsurance'][0]['percent'] = coinsurance
    terms['deductible'][0]['amount'] = deductible
    benefits = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07')
    result = {'benefits': benefits, 'issues': [], 'codes': [{'id': 'office', 'code': '99203', 'units': 1}]}
    with patch.object(api.commercial_store, 'list_connections', return_value=[]):
        api._attach_previsit_pricing(result, 'synthetic-clinic', {'service_date': '2026-10-07', 'place_of_service': '11'},
            {}, 'synthetic-payer', benefits, payload=payload)
    result['available_codes'] = result['codes']
    api.practice_prices.apply(result, {'id': 'active-version', 'filename': 'approved.csv', 'effective_from': '2026-01-01',
        'rows': [{'code': '99203', 'cash_cents': fee}]})
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == expected
    assert guidance['network_source'] == 'Payer statement about the requesting provider'
    assert all(line['allowed_cents'] is None for line in result['price_range']['lines'])
    assert result['price_range']['lines'][0]['benefits']['network_source'] == 'payer_participation_message'
    if coinsurance == '0.20':
        assert guidance['basis'] == 'deferred_billing'
        assert guidance.get('range_max_cents') is None


@pytest.mark.parametrize('change', ['absent', 'generic', 'negated', 'contradictory', 'out_of_network_conflict', 'date', 'inactive', 'conditional'])
def test_payer_participation_and_cost_share_guards_remain(change):
    payload = office_coinsurance_payload()
    terms = payload['plans'][0]['benefits']
    if change == 'absent': terms['limitations'] = []
    if change == 'generic': terms['limitations'][0]['messages'] = ['In-network specialist office benefit.']
    if change == 'negated': terms['limitations'][0]['messages'] = ["Our records indicate the provider ID you entered is not participating in this patient's network."]
    if change == 'contradictory': terms['limitations'][0]['messages'].append('The provider is out-of-network.')
    if change == 'out_of_network_conflict':
        terms['limitations'].append({**terms['limitations'][0], 'network': {'indicator': 'OUT_OF_NETWORK'},
            'messages': ["Our records indicate the provider ID you entered is not participating in this patient's network."]})
    if change == 'date': payload['subscriber']['dates']['service']['start'] = '2026-10-06'
    if change == 'inactive': terms['statuses'][0]['status'] = 'Inactive Coverage'
    if change == 'conditional': terms['coInsurance'][0]['messages'].append('FIRST 10 VISITS ONLY')
    benefits = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07', procedure_code='99203')
    if change == 'conditional':
        assert any('conditional benefit' in reason for reason in benefits['review_reasons'])
    elif change == 'negated':
        assert benefits['network_status'] == 'out_of_network'
        assert benefits['network_evidence_ids']
    else:
        assert benefits['network_status'] == 'unknown'
        assert benefits['network_evidence_ids'] == []


def test_legacy_collection_policy_cannot_supply_a_zero_without_benefits():
    result = {'collection_policy': 'defer_original_medicare_secondary', 'benefits': {'has_secondary': True},
              'eligibility_failure': {'kind': 'provider_enrollment'}}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert 'provider enrollment' in guidance['reason']


def test_ppo_label_does_not_invent_a_member_referral_requirement():
    result = scenario()
    result['plan_details'] = {'stedi': {'names': ['PREFERRED PROVIDER OPTION MEDICAL']}}
    assert collection_guidance.build(result)['requirements']['referral'] == 'No requirement reported'
    result['price_range']['lines'][0]['benefits']['requirements']['referral']['required'] = 'required'
    assert collection_guidance.build(result)['status'] == 'deferred_billing'


def test_source_statuses_are_not_replaced_with_calculation_assumptions():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    result['pricing_assumptions'] = ['Use the scoped clinic contract for this estimate.']
    result['network_evidence'] = {'network_name': 'Blue Choice PPO', 'product_assumed': True}
    result['price_range']['lines'][0]['benefits']['authorization_basis'] = 'Payer indicator'
    value = collection_guidance.build(result)
    assert value['network'] == 'Blue Choice PPO'
    assert value['network_source'] == 'Clinic contract; exact product match not confirmed'
    assert value['requirements']['authorization'] == 'Unknown in payer response'
    assert value['requirements']['referral'] == 'No requirement reported'
    assert 'assumed' not in ' '.join([value['network'], *value['requirements'].values()])
    result['referral_evidence'] = [{'reference': 'ServiceRequest/example'}]
    assert collection_guidance.build(result)['requirements']['referral'] == 'Referral record on file'


def test_unmatched_patient_product_displays_scoped_contract_candidate_without_network_claim():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    result['mapping_candidate'] = {'network': 'Blue Choice PPO', 'products': ['Preferred Provider Option Plus Medical']}
    value = collection_guidance.build(result)
    assert value['network'] == 'Blue Choice PPO contract; patient network unverified'
    assert 'exact product match not confirmed' in value['network_source']


def test_missing_deductible_can_use_the_actual_copay_as_partial_advance_with_explanation():
    result = scenario()
    result['price_range'].update(complete=False)
    terms = result['price_range']['lines'][0]['benefits']
    terms.update(copay_cents=6300, copay_period='DAY', coinsurance_percent='0', oop_remaining_cents=100000)
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 6300 and value['basis'] == 'office_copay'
    assert any('did not establish whether the deductible applies' in text for text in value['assumptions'])
    terms['copay_cents'] = 2900
    assert collection_guidance.build(result)['amount_cents'] == 2900
    result['price_range']['lines'][0]['allowed_cents'] = 2100
    assert collection_guidance.build(result)['amount_cents'] == 2100
    terms['deductible_applies'] = True
    assert collection_guidance.build(result)['basis'] == 'deferred_billing'


def test_saved_legacy_default_zero_is_rebuilt_from_evidence():
    result = {'benefits': {}, 'collection_guidance': {'amount_cents': 0, 'status': 'deferred'}}
    value = api._previsit_result({'previsit_context': {'result': result}})
    assert value['collection_guidance']['amount_cents'] is None
    assert value['collection_guidance']['basis'] == 'deferred_billing'


def test_surname_warning_does_not_hide_returned_benefits_or_block_amount():
    result = scenario(4800)
    result['identity'] = {'status': 'warning', 'message': 'Payer surname differs; first name, date of birth and member ID match.'}
    assert collection_guidance.build(result)['amount_cents'] == 4800
    assert result['benefits']['active'] is True


def test_emg_comparison_uses_shared_balances_and_never_adds_a_second_visit_copay():
    from test_visit_estimate import payload, DAY
    from app.insurance import cpt_rules
    raw = payload()
    codes = [{'id': 'office', 'code': '99203', 'units': 1}]
    rates = [{'code': code, 'allowed_cents': 10000} for code in ['99203', *cpt_rules.NCS_CODES, '95885', '95886']]
    result = {'codes': codes, 'benefits': commercial.normalize_stedi_271(raw, '98', True), 'identity': {'status': 'warning'}}
    result['price_range'] = visit_estimate.price_lines(codes, rates, result['benefits'], {}, payload=raw, service_date=DAY)
    option = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {'emg_required': True})
    assert option['scenario_count'] == 56
    assert option['additional_min_cents'] == option['additional_max_cents'] == 2000
    assert option['total_min_cents'] == option['total_max_cents'] == 8000
    assert not option['included']
    assert collection_guidance.emg_option(result, [], {'cost_share': 'payer_returned'}, raw, DAY, {})['scenario_count'] == 0
    assert collection_guidance.emg_option(result, [], {'cost_share': 'payer_returned'}, raw, DAY, {'emg_required': True})['total_min_cents'] is None
    # Explicit testing is priced once, with its actual quantity.
    result['codes'] += [{'id': code, 'code': code, 'units': 1} for code in ['95907', '95886']]
    exact = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {})
    assert exact['included'] and exact['scenario_count'] == 1 and exact['total_max_cents'] == 8000
    # Zero is allowed only when the payer actually reports an exhausted balance.
    raw['benefitsInformation'][2]['benefitAmount'] = '0'
    result['benefits'] = commercial.normalize_stedi_271(raw, '98', True)
    zero = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {})
    assert zero['additional_max_cents'] == zero['total_max_cents'] == 0
