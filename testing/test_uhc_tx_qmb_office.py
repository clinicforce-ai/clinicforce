"""Synthetic member-bound UHC Texas QMB office visit and collection boundaries."""
from copy import deepcopy
import hashlib
import json

import pytest

from app.api.routers import commercial_estimator as api
from app.insurance import collection_guidance, published_plan_benefits
from app.insurance.contract_requirements import evaluate
from app.insurance.stedi_benefits.fields import plan_details
from app.api.routers.commercial_flow.commercial_previsit_saved_pricing import restore_saved_published_product, restore_saved_uhc_qmb_product


DAY = '2026-11-18'
PLAN = {'names': ['TX UHC DUAL COMPLETE HMOPOS CO QMB H4514-024-000 W'],
        'plan_number': 'TXSNPQ7W', 'group_number': 'TXDSNP'}
IDENTITY = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}


def test_active_single_plan_status_exposes_product_identifiers():
    payload = {'plans': [{'name': PLAN['names'][0], 'benefits': {'statuses': [
        {'status': 'ACTIVE', 'service': {'system': 'STC', 'value': '30'},
         'additionalInformation': {'plan': {'number': 'TXSNPQ7W'},
                                   'groupOrPolicyNumber': 'TXDSNP'}}]}}]}
    assert plan_details(payload)['plan_number'] == 'TXSNPQ7W'
    assert plan_details(payload)['group_number'] == 'TXDSNP'
    payload['plans'].append({'name': 'Another medical plan'})
    assert plan_details(payload)['plan_number'] is None


def case():
    benefits = {'active': True, 'service_date_matches': True,
                'network_status': 'unknown', 'identity': IDENTITY,
                'deductible_annual_cents': 28300, 'deductible_remaining_cents': 0,
                'oop_annual_cents': 925000, 'oop_remaining_cents': 0}
    published_plan_benefits.apply_plan_terms(benefits, PLAN, DAY, True)
    return {'benefits': benefits, 'identity': IDENTITY, 'service_date': DAY,
            'payer': 'United Healthcare', 'plan_details': {'stedi': deepcopy(PLAN)},
            'price_range': {'mode': 'services', 'conditional': True,
                            'service_issues': [], 'lines': [
                                {'id': 'office', 'code': '99213', 'units': 1,
                                 'allowed_cents': None,
                                 'benefits': {'active': True, 'copay_cents': 0,
                                              'coinsurance_percent': '20',
                                              'identity': IDENTITY,
                                              'network_status': 'unknown'}}]},
            'selected_ids': [], 'service_selection_status': 'preview'}


def test_exact_qmb_product_collects_zero_despite_raw_accumulators_and_unknown_network():
    result = case()
    assert result['benefits']['published_plan_rule'] == published_plan_benefits.UHC_TX_QMB_H4514_024_2026
    assert result['benefits']['deductible_annual_cents'] == 28300
    assert result['benefits']['oop_annual_cents'] == 925000
    guidance = collection_guidance.build(result)
    assert (guidance['amount_cents'], guidance['basis']) == (0, 'published_uhc_tx_qmb_office')


def test_saved_projection_refreshes_match_and_rejects_changed_product():
    result = case()
    result['benefits'].pop('published_plan_rule')
    saved = {'previsit_context': {'result': result, 'source': {}, 'inputs': {}, 'payer_id': ''}}
    projected = api._previsit_result(saved)
    assert projected['collection_guidance']['amount_cents'] == 0
    assert projected['collection_guidance']['basis'] == 'published_uhc_tx_qmb_office'
    changed = deepcopy(saved)
    changed['previsit_context']['result']['plan_details']['stedi']['plan_number'] = 'OTHER'
    assert api._previsit_result(changed)['collection_guidance']['basis'] != 'published_uhc_tx_qmb_office'


def test_saved_undated_active_qmb_response_collects_zero_only_for_bound_product():
    result = case()
    result['benefits'].update(service_date_matches=None, coverage_evidence={
        'status': 'ACTIVE', 'start': None, 'end': None}, returned_service_dates={},
        coverage_dates_invalid=False, coverage_status_conflict=False)
    result['benefits'].pop('published_plan_rule')
    saved = {'previsit_context': {'result': result, 'source': {},
                                  'inputs': {'service_date': DAY}, 'payer_id': ''}}
    projected = api._previsit_result(saved)
    assert projected['collection_guidance']['amount_cents'] == 0
    assert projected['collection_guidance']['basis'] == 'published_uhc_tx_qmb_office'
    for changes in (
        {'coverage_evidence': {'status': 'ACTIVE', 'start': '2026-11-19', 'end': None}},
        {'coverage_evidence': {'status': 'UNKNOWN', 'start': None, 'end': None}},
        {'service_date_matches': False}, {'source_errors': [{'kind': 'payer_error'}]},
    ):
        changed = deepcopy(saved)
        changed['previsit_context']['result']['benefits'].update(changes)
        assert api._previsit_result(changed)['collection_guidance']['basis'] != 'published_uhc_tx_qmb_office'


def test_older_saved_response_restores_identifiers_only_from_its_verified_raw(monkeypatch):
    result = case()
    result['benefits'].update(service_date_matches=None, coverage_evidence={
        'status': 'ACTIVE', 'start': None, 'end': None})
    result['plan_details']['stedi'].pop('plan_number')
    result['plan_details']['stedi'].pop('group_number')
    result['benefits'].pop('published_plan_rule')
    payload = {'payerId': '87726', 'plans': [{'name': PLAN['names'][0], 'benefits': {'statuses': [
        {'status': 'ACTIVE', 'service': {'system': 'STC', 'value': '30'},
         'additionalInformation': {'plan': {'number': 'TXSNPQ7W'},
                                   'groupOrPolicyNumber': 'TXDSNP'}}]}}]}
    raw = json.dumps(payload).encode()
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda _: raw)
    saved = {'previsit_raw_object_key': 'synthetic-key',
             'previsit_raw_sha256': hashlib.sha256(raw).hexdigest()}
    restore_saved_published_product(result, saved)
    assert result['plan_details']['stedi']['plan_number'] == 'TXSNPQ7W'
    assert result['benefits']['published_plan_rule'] == published_plan_benefits.UHC_TX_QMB_H4514_024_2026
    assert api._previsit_result({'previsit_context': {'result': result, 'source': {},
                                                      'inputs': {}, 'payer_id': ''}})['collection_guidance']['amount_cents'] == 0
    changed = case()
    changed['plan_details']['stedi'] = {'names': PLAN['names']}
    restore_saved_uhc_qmb_product(changed, {**saved, 'previsit_raw_sha256': 'wrong'})
    assert 'plan_number' not in changed['plan_details']['stedi']


def test_exact_product_reports_referral_and_delegated_authorization_review():
    result = case()
    result.update(codes=[{'id': 'office', 'code': '99213', 'units': 1}], benefit_source='Stedi')
    context = {'service_date': DAY, 'payer_names': {'unitedhealthcare'},
               'plans': PLAN['names'], 'member_id': 'SYNTHETIC',
               'pos': '11', 'rendering_npi': '1111111111'}
    decisions = evaluate(result, context, [])
    assert decisions['referral']['status'] == 'Required'
    assert decisions['authorization']['status'] == 'Verification required'


@pytest.mark.parametrize('change', [
    'different_plan', 'different_group', 'different_name', 'other_year', 'unbound',
    'inactive', 'source_error', 'secondary', 'other_service', 'nonzero_copay',
])
def test_zero_collection_requires_exact_product_and_single_supported_office_service(change):
    result = deepcopy(case())
    if change in {'different_plan', 'different_group', 'different_name', 'other_year', 'unbound'}:
        plan = deepcopy(PLAN)
        day = DAY
        bound = True
        if change == 'different_plan':
            plan['plan_number'] = 'OTHER'
        elif change == 'different_group':
            plan['group_number'] = 'OTHER'
        elif change == 'different_name':
            plan['names'] = ['UnitedHealthcare Dual Complete']
        elif change == 'other_year':
            day = '2027-01-01'
        else:
            bound = False
        result['benefits'].pop('published_plan_rule')
        published_plan_benefits.apply_plan_terms(result['benefits'], plan, day, bound)
    elif change == 'inactive':
        result['benefits']['active'] = False
    elif change == 'source_error':
        result['benefits']['source_errors'] = [{'kind': 'missing_raw'}]
    elif change == 'secondary':
        result['benefits']['has_secondary'] = True
    elif change == 'other_service':
        result['price_range']['lines'].append({'id': 'other', 'code': '95908', 'units': 1,
                                                'benefits': {'active': True}})
    elif change == 'nonzero_copay':
        result['price_range']['lines'][0]['benefits']['copay_cents'] = 1000
    assert collection_guidance.build(result)['basis'] != 'published_uhc_tx_qmb_office'
