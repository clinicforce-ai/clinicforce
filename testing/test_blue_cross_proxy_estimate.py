"""Synthetic diagnostic-visit checks for the disclosed cross-payer fee proxy."""

from copy import deepcopy

from app.insurance import amount_explanation, collection_guidance, commercial, contract_requirements, cpt_rules, encounter_summary
from app.api.schemas.commercial import CollectionPolicyInput
from app.insurance.blue_cross_proxy import estimate
from app.insurance.requirements.published import _published_rule
from app.api.routers import commercial_estimator as api  # Register workflow imports.
from app.api.routers.commercial_flow import commercial_previsit_status as saved_status
from app.api.routers.commercial_flow import commercial_previsit_rate_reference as rate_reference


def _result(*, group='13705', coinsurance='10', network='out_of_network'):
    codes = [('ncs', '95908', 1, 10511), ('emg', '95886', 2, 8918)]
    return {
        'payer': 'UnitedHealthcare',
        'insurance_details': {'group_number': group},
        'plan_details': {'stedi': {'names': ['UnitedHealthcare Group Medicare Advantage (PPO)']}},
        'benefits': {'active': True, 'service_date_matches': True, 'network_status': network,
                     'network_source': 'clinic_contract_inventory', 'oop_remaining_cents': 97500,
                     'identity': {'status': 'matched', 'match_version': 2,
                                  'binding': 'synthetic-binding'}},
        'codes': [{'id': key, 'code': code, 'units': units, 'modifiers': []}
                  for key, code, units, _ in codes],
        'price_range': {'mode': 'services', 'service_issues': [], 'lines': [
            {'id': key, 'code': code, 'units': units, 'modifiers': [], 'benefits': {
                'active': True, 'service_date_matches': True, 'network_status': network,
                'copay_cents': 0, 'coinsurance_percent': coinsurance,
                'deductible_remaining_cents': None, 'oop_remaining_cents': 97500,
            }} for key, code, units, _ in codes]},
        'temporary_rate_reference': {'source': 'NMCT Clinical Library Blue Cross fee schedule',
            'complete': True, 'lines': [
                {'id': key, 'code': code, 'units': units, 'modifiers': [],
                 'unit_reference_cents': cents, 'source_reference': 'library:synthetic'}
                for key, code, units, cents in codes]},
    }


def test_same_day_ncs_and_standalone_emg_is_invalid():
    assert cpt_rules.service_definition('95861')['service_type'] == '73'
    assert cpt_rules.service_selection_issues(['95908', '95861'])
    assert not cpt_rules.service_selection_issues(['95908', '95886'])


def test_diagnostic_stedi_terms_do_not_inherit_office_copay():
    payload = {'subscriber': {'memberId': 'SYNTHETIC', 'firstName': 'Sample',
                              'lastName': 'Patient', 'dateOfBirth': '1980-01-01'},
        'benefitsInformation': [
            {'code': '1', 'serviceTypeCodes': ['30'],
             'planCoverageDescription': 'LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'},
            {'code': 'B', 'serviceTypeCodes': ['73'], 'benefitAmount': '0',
             'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N',
             'timeQualifierCode': '7', 'messages': ['Diagnostic Medical']},
            {'code': 'A', 'serviceTypeCodes': ['73'], 'benefitPercent': '.10',
             'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N',
             'messages': ['Diagnostic Medical']},
            {'code': 'B', 'serviceTypeCodes': ['98'], 'benefitAmount': '15',
             'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N',
             'messages': ['Office visit']},
        ]}
    for code in ('95908', '95886', '95861'):
        terms = commercial.normalize_stedi_271(payload, cpt_rules.service_definition(code)['service_type'],
            False, service_date='2026-10-27', procedure_code=code,
            provider_out_of_network_verified=True)
        assert terms['copay_cents'] == 0
        assert terms['coinsurance_percent'] == '10.00'


def test_scoped_proxy_calculates_diagnostic_share_without_claiming_payer_allowance():
    result = _result()
    proxy = estimate(result, '2026-10-27')
    assert proxy['reference_total_cents'] == 28347
    assert proxy['coinsurance_cents'] == 2835
    assert proxy['patient_share_cents'] == 2835
    assert proxy['deductible_cents'] == 0
    assert proxy['basis'] == 'bcbstx_fee_proxy'
    assert result['price_range'].get('allowed_min_cents') is None
    result['plan_details']['stedi']['names'] = [
        'LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    assert estimate(result, '2026-10-27')['patient_share_cents'] == 2835


def test_in_network_group_ppo_uses_remaining_deductible_and_rounds_each_cpt():
    result = _result(group='other-group', coinsurance='5', network='in_network')
    result['plan_details']['stedi']['names'] = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    result['benefits'].update(network_source='payer_participation_message',
                              deductible_annual_cents=40000, deductible_remaining_cents=0,
                              oop_remaining_cents=270430)
    result['codes'][1]['units'] = 1
    result['price_range']['lines'][1]['units'] = 1
    result['temporary_rate_reference']['lines'][1]['units'] = 1
    for line in result['price_range']['lines']:
        line['benefits'].update(deductible_remaining_cents=0, oop_remaining_cents=270430)
    proxy = estimate(result, '2026-10-28')
    assert proxy['basis'] == 'bcbstx_uhc_in_network_fee_proxy'
    assert proxy['reference_total_cents'] == 19429
    assert proxy['deductible_cents'] == 0
    assert proxy['coinsurance_cents'] == proxy['patient_share_cents'] == 972
    assert [line['coinsurance_cents'] for line in proxy['lines']] == [526, 446]
    assert result['price_range'].get('allowed_min_cents') is None
    result['price_range']['lines'][0]['benefits']['deductible_remaining_cents'] = 100
    assert estimate(result, '2026-10-28') is None
    result['price_range']['lines'][0]['benefits']['deductible_remaining_cents'] = 0
    result['fee_proxy_estimate'] = estimate(result, '2026-10-28')
    result['service_selection_status'] = 'approved'
    result['collection_policy'] = {'mode': 'collect_supported_advance',
        'approved_by': 'admin', 'approved_at': '2026-10-27T12:00:00Z',
        'reference': 'Synthetic clinic policy', 'allow_blue_cross_proxy_advance': True}
    assert collection_guidance.build(result)['amount_cents'] is None
    result['billing_protection'] = {'status': 'not_qmb', 'reference': 'synthetic-check',
        'verified_at': '2026-10-28T12:00:00Z', 'identity_binding': 'synthetic-binding'}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 972
    assert guidance['basis'] == 'bcbstx_proxy_advance'
    result['collection_guidance'] = guidance
    assert encounter_summary.build(result)['collect_today_cents'] == 972


def test_trs_follow_up_adds_specialist_copay_to_diagnostic_proxy():
    result = _result(group='15725', coinsurance='5', network='in_network')
    result['plan_details']['stedi']['names'] = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    result['benefits'].update(network_source='payer_participation_message',
                              deductible_remaining_cents=0, oop_remaining_cents=270430)
    result['codes'][1]['units'] = 1
    result['price_range']['lines'][1]['units'] = 1
    result['temporary_rate_reference']['lines'][1]['units'] = 1
    for line in result['price_range']['lines']:
        line['benefits'].update(deductible_remaining_cents=0, oop_remaining_cents=270430)
    result['separate_em_planned'] = True
    result['visit_type'] = None
    result['codes'].append({'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []})
    result['price_range']['lines'].append({'id': 'office', 'code': '99213', 'units': 1,
                                          'modifiers': [], 'benefits': {}})
    result['temporary_rate_reference']['complete'] = False
    proxy = estimate(result, '2026-10-28')
    assert proxy['copay_cents'] == 1000
    assert proxy['coinsurance_cents'] == 972
    assert proxy['patient_share_cents'] == 1972
    assert proxy['reference_total_cents'] == 19429
    result['fee_proxy_estimate'] = proxy
    result['service_selection_status'] = 'approved'
    result['collection_policy'] = {'mode': 'collect_supported_advance',
        'approved_by': 'admin', 'approved_at': '2026-10-27T12:00:00Z',
        'reference': 'Synthetic clinic policy', 'allow_blue_cross_proxy_advance': True}
    result['billing_protection'] = {'status': 'not_qmb', 'reference': 'synthetic-check',
        'verified_at': '2026-10-28T12:00:00Z', 'identity_binding': 'synthetic-binding'}
    assert collection_guidance.build(result)['amount_cents'] == 1972
    assert estimate({**result, 'separate_em_planned': False}, '2026-10-28') is None
    assert estimate({**result, 'visit_type': 'new'}, '2026-10-28') is None
    result['price_range']['lines'][-1]['benefits']['copay_cents'] = 500
    assert estimate(result, '2026-10-28') is None


def test_in_network_group_ppo_requirement_uses_product_and_selected_cpts():
    result = _result(group='15725', network='in_network')
    result['benefit_source'] = 'Stedi'
    result['plan_details']['stedi']['names'] = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    context = {'service_date': '2026-10-28', 'payer_names': {'unitedhealthcare'},
               'group_number': '15725', 'member_id': 'synthetic-member', 'pos': '11'}
    plans = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    assert _published_rule('referral', result, context, plans, {'95908', '95886'})['status'] == 'Not required'
    decision = _published_rule('authorization', result, context, plans, {'95908', '95886'})
    assert decision['status'] == 'Not required'
    assert decision['sources'][0]['source_type'] == 'clinic_confirmation'
    evaluated = contract_requirements.evaluate(result, {**context, 'plans': plans}, [])
    assert all(procedure['referral']['status'] == procedure['authorization']['status'] == 'Not required'
               for procedure in evaluated['procedures'])
    assert _published_rule('authorization', result, context, plans, {'95908', '99213'})['status'] == 'Verification required'
    assert _published_rule('authorization', result, {**context, 'group_number': 'another-group'}, plans,
                           {'95908', '95886'})['status'] == 'Verification required'


def test_proxy_accepts_active_dated_coverage_when_visit_date_echo_is_absent():
    result = _result()
    result['benefits']['service_date_matches'] = None
    result['benefits']['coverage_evidence'] = {
        'status': 'ACTIVE', 'start': '2026-01-01', 'end': None}
    assert estimate(result, '2026-10-27')['patient_share_cents'] == 2835
    result['benefits']['coverage_evidence']['end'] = '2026-10-26'
    assert estimate(result, '2026-10-27') is None


def test_proxy_rejects_incomplete_rates_or_unresolved_deductible():
    assert estimate(_result(network='in_network'), '2026-10-27') is None
    result = _result()
    result['temporary_rate_reference']['lines'].pop()
    assert estimate(result, '2026-10-27') is None
    result = _result(group='another-plan')
    assert estimate(result, '2026-10-27') is None
    result = _result(coinsurance=None)
    assert estimate(result, '2026-10-27') is None
    result = _result()
    result['identity'] = {'status': 'review', 'match_version': 2, 'binding': 'other'}
    assert estimate(result, '2026-10-27') is None


def test_proxy_uses_only_unique_out_of_network_medical_oop_balance():
    result = _result()
    result['benefits']['oop_remaining_cents'] = None
    result['benefits']['reported_options'] = [{
        'kind': 'out_of_pocket', 'service_type_code': '30',
        'coverage_level': 'INDIVIDUAL', 'time_period': 'REMAINING',
        'network': 'OUT_OF_NETWORK', 'plan_association': 'medical',
        'amount_cents': 97500}]
    assert estimate(result, '2026-10-27')['patient_share_cents'] == 2835
    result['benefits']['reported_options'][0]['network'] = 'IN_NETWORK'
    assert estimate(result, '2026-10-27') is None


def test_saved_diagnostic_rows_restore_proxy_when_cpt_benefits_cannot_rehydrate():
    result = _result()
    for line in result['price_range']['lines']:
        line['benefits'] = {}
        line['allowed_cents'] = None
    result['benefits'].update(
        resolved_service_type='73', copay_cents=0, coinsurance_percent='10.0',
        selected_benefit_ids=['diagnostic-copay', 'diagnostic-coinsurance'],
        reported_options=[
            {'id': 'diagnostic-copay', 'kind': 'copay', 'service_type_code': '73',
             'network': 'IN_AND_OUT_OF_NETWORK', 'coverage_level': 'INDIVIDUAL',
             'plan_index': 0, 'amount_cents': 0},
            {'id': 'diagnostic-coinsurance', 'kind': 'coinsurance',
             'service_type_code': '73', 'network': 'IN_AND_OUT_OF_NETWORK',
             'coverage_level': 'INDIVIDUAL', 'plan_index': 0, 'percent': '10.0'},
        ])
    assert estimate(result, '2026-10-27')['patient_share_cents'] == 2835
    result['benefits']['reported_options'][1]['service_type_code'] = '98'
    assert estimate(result, '2026-10-27') is None
    result['benefits']['reported_options'][1]['service_type_code'] = '73'
    result['benefits']['reported_options'].append(
        {'kind': 'copay', 'procedure_code': '95886', 'amount_cents': 1500})
    assert estimate(result, '2026-10-27') is None


def test_proxy_advance_requires_explicit_policy_and_qmb_clearance():
    result = _result()
    result['service_selection_status'] = 'approved'
    result['fee_proxy_estimate'] = estimate(result, '2026-10-27')
    result['collection_policy'] = {'mode': 'collect_supported_advance',
        'approved_by': 'admin', 'approved_at': '2026-10-26T12:00:00Z',
        'reference': 'Synthetic clinic fee proxy policy',
        'allow_supported_oon_advance': True, 'allow_blue_cross_proxy_advance': True}
    result['billing_protection'] = {'status': 'not_qmb', 'reference': 'synthetic-check',
        'verified_at': '2026-10-27T12:00:00Z', 'identity_binding': 'synthetic-binding'}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 2835
    assert guidance['basis'] == 'bcbstx_proxy_advance'
    result['collection_guidance'] = guidance
    assert encounter_summary.build(result)['collect_today_cents'] == 2835
    assert 'BCBS Texas fee proxy' in amount_explanation.build(result, guidance)['sentence']
    result['collection_guidance'] = {**guidance, 'amount_cents': 2834}
    assert encounter_summary.build(result)['integrity_error'] == 'collection_amount_mismatch'
    result['collection_guidance'] = guidance

    result['billing_protection']['status'] = 'qmb'
    assert collection_guidance.build(result)['amount_cents'] == 0
    result['billing_protection']['status'] = 'not_qmb'
    result['collection_policy'].pop('allow_blue_cross_proxy_advance')
    assert collection_guidance.build(result)['amount_cents'] is None


def test_proxy_collection_policy_has_explicit_opt_in():
    request = CollectionPolicyInput(mode='collect_supported_advance',
        reference='Synthetic group-plan policy', allow_supported_oon_advance=True,
        allow_blue_cross_proxy_advance=True)
    assert request.allow_blue_cross_proxy_advance is True


def test_saved_read_rechecks_current_fee_rows_before_showing_proxy(monkeypatch):
    result = _result()
    result.update(service_date='2026-10-27', fee_proxy_estimate=estimate(result, '2026-10-27'))
    case = {'revision': 1, 'previsit_context': {
        'result': result, 'payer_id': 'synthetic-uhc-route',
        'inputs': {'service_date': '2026-10-27'}, 'source': {}}}
    monkeypatch.setattr(saved_status, '_recheck_saved_clinic_pricing', lambda *args: None)
    monkeypatch.setattr(saved_status, 'recheck_saved_network_evidence', lambda *args: None)
    monkeypatch.setattr(saved_status, '_recheck_saved_payer_allowance', lambda *args: None)
    monkeypatch.setattr(saved_status, 'refresh_saved_requirements', lambda *args: None)
    monkeypatch.setattr(saved_status._api.commercial_store, 'list_connections', lambda *args: [
        {'stedi_payer_id': 'synthetic-uhc-route', 'display_name': 'UnitedHealthcare',
         'rules': {}}])
    fresh = deepcopy(result['temporary_rate_reference'])
    fresh['lines'][0]['unit_reference_cents'] = 10000
    monkeypatch.setattr(rate_reference, '_attach_temporary_uhc_rates',
                        lambda reopened, *args: reopened.update(temporary_rate_reference=fresh))
    reopened = saved_status._previsit_result(case, 'synthetic-clinic')
    assert reopened['fee_proxy_estimate']['reference_total_cents'] == 27836
    assert reopened['fee_proxy_estimate']['patient_share_cents'] == 2784

    fresh['complete'] = False
    reopened = saved_status._previsit_result(case, 'synthetic-clinic')
    assert 'fee_proxy_estimate' not in reopened


def test_saved_phcs_read_rebuilds_blue_cross_reference(monkeypatch):
    result = _result()
    result['payer'] = 'phcs'
    result.pop('plan_details')
    result['temporary_rate_reference']['lines'][0]['unit_reference_cents'] = 1
    case = {'revision': 1, 'previsit_context': {
        'result': result, 'payer_id': '', 'inputs': {'service_date': '2026-10-30'}, 'source': {}}}
    for name in ('_recheck_saved_clinic_pricing', 'recheck_saved_network_evidence',
                 '_recheck_saved_payer_allowance', 'refresh_saved_requirements'):
        monkeypatch.setattr(saved_status, name, lambda *args: None)
    monkeypatch.setattr(saved_status._api.commercial_store, 'list_connections', lambda *args: [])
    monkeypatch.setattr(rate_reference, '_attach_temporary_blue_cross_rates',
        lambda reopened, *args: reopened.update(temporary_rate_reference={
            'source': 'synthetic current schedule', 'lines': [{'unit_reference_cents': 11156}]}))
    reopened = saved_status._previsit_result(case, 'synthetic-clinic')
    assert reopened['temporary_rate_reference']['lines'][0]['unit_reference_cents'] == 11156


def test_group_ppo_oon_diagnostic_requirements_are_scoped_to_group_and_codes():
    result = _result()
    result['benefit_source'] = 'Stedi'
    context = {'service_date': '2026-10-27', 'payer_names': {'unitedhealthcare'},
        'group_number': '13705', 'member_id': 'synthetic-member', 'pos': '11'}
    plans = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    assert _published_rule('referral', result, context, plans, {'95908', '95886'})['status'] == 'Not required'
    assert _published_rule('authorization', result, context, plans, {'95908', '95886'})['status'] == 'Not required'
    assert _published_rule('authorization', result, context, plans, {'95908', '99999'})['status'] == 'Verification required'
    assert _published_rule('authorization', result, {**context, 'group_number': 'other'},
                           plans,
                           {'95908', '95886'})['status'] == 'Verification required'


def test_group_ppo_diagnostic_clearance_uses_active_dated_in_network_coverage():
    result = _result(network='in_network')
    result['benefit_source'] = 'Stedi'
    result['benefits'].update(service_date_matches=None, coverage_evidence={
        'status': 'ACTIVE', 'start': '2026-01-01', 'end': None})
    context = {'service_date': '2026-10-27', 'payer_names': {'unitedhealthcare'},
        'group_number': '13705', 'member_id': 'synthetic-member', 'pos': '11'}
    plans = ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']
    assert _published_rule('referral', result, context, plans, {'95908', '95886'})['status'] == 'Not required'
    assert _published_rule('authorization', result, context, plans, {'95908', '95886'})['status'] == 'Not required'
