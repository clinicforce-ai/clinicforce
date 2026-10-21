"""Synthetic cases for appointment interpretation and cross-payer planning."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
import pytest
from app.api.routers import commercial_estimator as api
from app.insurance import commercial, cpt_suggester, ecw_backend, identity, practice_prices, visit_estimate, collection_guidance


@pytest.mark.parametrize('label', ['Follow Up', 'follow-up', 'Followup', 'F/U', 'FU', 'Established Patient'])
def test_ecw_followup_label_selects_an_editable_followup(label):
    kind = ecw_backend._visit_type({'type': [{'text': label}]})
    assert kind == 'established'
    inputs = {'visit_type': kind, 'reason': 'Reason: 3 month f/u; Visit type: Follow Up'}
    codes = api._previsit_codes(inputs)
    with patch.object(cpt_suggester.boto3, 'client') as model:
        plan = cpt_suggester.recommend_visit_plan(inputs, {}, codes)
    model.assert_not_called()
    assert plan['recommended_ids'] == []
    assert 'alternatives' in plan['assumptions'][0]


def test_biopsy_catalog_and_combined_visit_preserve_all_procedures_and_units():
    inputs = {'visit_type': 'established', 'reason': 'Reason: 3 month f/u; skin bx; 3 sites; Visit type: Follow Up'}
    available = [*api._previsit_codes(inputs), *[{'id': c, 'code': c, 'units': 1, 'source': 'CPT planning catalog'} for c in ('11104', '11105', '95907', '95910', '95886')]]
    plan = cpt_suggester.recommend_visit_plan(inputs, {}, available)
    rows = {r['id']: r for r in available}
    assert [(rows[key]['code'], plan['recommended_units'][key]) for key in plan['recommended_ids']] == [('11104', 1), ('11105', 2)]
    assert api._previsit_service_types(inputs) == ['2', '98', '30']
    inputs['reason'] = 'Skin punch biopsy; 2 sites'
    plan = cpt_suggester.recommend_visit_plan(inputs, {}, available)
    assert [(rows[key]['code'], plan['recommended_units'][key]) for key in plan['recommended_ids']] == [('11104', 1), ('11105', 1)]
    inputs['reason'] = 'Follow Up; discuss skin biopsy results'
    with patch.object(cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline')):
        plan = cpt_suggester.recommend_visit_plan(inputs, {}, available)
    assert not any(rows[key]['code'] in {'11104', '11105'} for key in plan['recommended_ids'])


def test_eligibility_window_boundary_and_service_categories():
    today = datetime.now(ZoneInfo('America/New_York')).date()
    assert api._eligibility_timing((today + timedelta(days=7)).isoformat()) is None
    assert api._eligibility_timing((today + timedelta(days=8)).isoformat())['check_from'] == (today + timedelta(days=1)).isoformat()
    assert api._previsit_service_types({'reason': 'Visit type: EMG Visit'}) == ['73', '30']
    assert api._previsit_service_types({'lines': [{'code': code, 'service_status': 'scheduled'} for code in ['99213', '95910', '95886']]}) == ['98', '73', '30']


def test_uhc_suffix_requires_exact_self_demographics_and_never_strips_other_ids():
    coverage = {'patient_first': 'Alex', 'patient_last': 'Example', 'patient_birth_date': '1980-01-01',
                'subscriber_first': 'Alex', 'subscriber_last': 'Example', 'member_id': '12345678900', 'relationship': '18'}
    raw = {'payerId': '87726', 'subscriber': {'memberId': '123456789', 'dateOfBirth': '1980-01-01', 'firstName': 'Alex', 'lastName': 'Example'}}
    assert identity.compare(raw, coverage)['status'] == 'warning'
    for changes in [{'payerId': 'OTHER'}, {'dependent': {'firstName': 'Alex', 'lastName': 'Example', 'dateOfBirth': '1980-01-01', 'relationToSubscriber': 'CHILD'}}]:
        assert identity.compare({**raw, **changes}, coverage)['status'] == 'review'
    for changes in [{'memberId': '123456788'}, {'dateOfBirth': '1980-01-02'}, {'firstName': 'Other'}]:
        assert identity.compare({**raw, 'subscriber': {**raw['subscriber'], **changes}}, coverage)['status'] == 'review'
    assert identity.compare(raw, {**coverage, 'member_id': '12345678901'})['status'] == 'review'


def diagnostic_payload():
    base = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_AND_OUT_OF_NETWORK'}, 'service': {'system': 'STC', 'value': '73'}}
    return {'plans': [{'benefits': {'statuses': [{**base, 'status': 'ACTIVE_COVERAGE'}],
        'coPayment': [{**base, 'amount': '0', 'timePeriod': 'DAY'}],
        'coInsurance': [{**base, 'percent': '.05', 'timePeriod': 'DAY'}],
        'deductible': [{**base, 'amount': '0', 'timePeriod': 'REMAINING'}],
        'outOfPocket': [{**base, 'amount': '3000', 'timePeriod': 'REMAINING'}]}}]}


def diagnostic_result(raw):
    benefits = commercial.normalize_stedi_271(raw, '73', False)
    codes = [{'id': c, 'code': c, 'units': 1} for c in ['95910', '95886']]
    result = {'benefits': benefits, 'codes': codes, 'available_codes': codes, 'identity': {'status': 'warning'},
              'price_range': visit_estimate.price_lines(codes, [], benefits, {}, payload=raw, service_date='2026-09-30')}
    practice_prices.apply(result, {'id': 'schedule', 'filename': 'cash.csv', 'effective_from': '2026-01-01',
        'rows': [{'code': '95910', 'cash_cents': 30000}, {'code': '95886', 'cash_cents': 10000}]})
    return result


def test_diagnostic_percentage_without_payer_allowance_stays_unknown():
    result = diagnostic_result(diagnostic_payload())
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert value['basis'] == 'missing_collection_inputs'
    assert all(row['benefits']['resolved_service_type'] == '73' for row in result['price_range']['lines'])
    paid = collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 500}})
    assert paid['amount_cents'] is None
    for change in [{'active': False}, {'has_secondary': True}]:
        invalid = deepcopy(result);invalid['benefits'].update(change)
        assert collection_guidance.build(invalid)['basis'] == 'missing_collection_inputs'
    invalid = deepcopy(result);invalid['identity']['status'] = 'review'
    assert collection_guidance.build(invalid)['basis'] == 'missing_collection_inputs'
    raw = diagnostic_payload()
    raw['plans'][0]['benefits']['coInsurance'][0]['network']['indicator'] = 'IN_NETWORK'
    assert collection_guidance.build(diagnostic_result(raw))['basis'] == 'missing_collection_inputs'
    raw = diagnostic_payload();raw['plans'][0]['benefits']['coInsurance'][0]['percent'] = '.20'
    assert collection_guidance.build(diagnostic_result(raw))['amount_cents'] is None


def test_specialist_office_rows_do_not_mix_with_primary_care_or_emg():
    base = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_AND_OUT_OF_NETWORK'}, 'service': {'system': 'STC', 'value': 'BY'}, 'timePeriod': 'DAY'}
    raw = {'plans': [{'benefits': {'statuses': [{**base, 'status': 'ACTIVE_COVERAGE'}], 'coPayment': [
        {**base, 'amount': '5', 'messages': ['OFFICE VISIT PRIMARY CARE PROV']},
        {**base, 'amount': '10', 'messages': ['OFFICE VISIT SPECIALIST']}], 'coInsurance': [{**base, 'percent': '0', 'messages': ['OFFICE VISIT SPECIALIST']}]}}]}
    normalized = commercial.normalize_stedi_271(raw, '98', False, procedure_code='99213', office_service_types=['BQ', 'BY', '98'])
    assert normalized['copay_cents'] == 1000 and normalized['network_independent']
    assert not any('copay or coinsurance' in reason for reason in normalized['review_reasons'])
    assert commercial.normalize_stedi_271(raw, '73', False, procedure_code='95910')['copay_cents'] is None


def test_diagnostic_only_response_keeps_active_coverage_at_case_level():
    raw = diagnostic_payload()
    result = api._previsit_benefits(raw, {'active_coverages': 1}, '2026-09-30', '73')
    assert result['active'] is True
    assert result['coinsurance_percent'] == '5.00'


def test_generic_emg_selects_an_editable_pair_without_inventing_an_anatomical_diagnosis():
    with patch.object(cpt_suggester.boto3, 'client') as model:
        plan = cpt_suggester.recommend_visit_plan({'reason': 'Visit type: EMG Visit'}, {}, [{'id': c, 'code': c, 'units': 1, 'source': 'CPT planning catalog'} for c in ('11104', '11105', '95907', '95910', '95886')])
    model.assert_not_called()
    assert plan['recommended_ids'] == ['95907', '95886']
    assert 'cash_selection' not in plan
    assert len(plan['assumptions']) == 1 and 'Initial planning scope' in plan['assumptions'][0]
