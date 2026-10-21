"""Only a sourced allowance for the exact out-of-network visit can price a CPT."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import asyncio
import json
import pytest
from fastapi import HTTPException

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_status as status
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_payer_message
from app.insurance import payer_allowance


DAY = '2026-10-14'
PLAN = {'names': ['Synthetic Regional PPO'], 'plan_number': 'synthetic-plan'}
PRODUCT = payer_allowance.product_binding(PLAN)
LINE = {'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}
QUOTE = {'id': 'payer-allowance:synthetic', 'line_id': 'office', 'code': '99203',
         'modifiers': '', 'units': 1, 'payer_id': 'uhc-route', 'service_date': DAY,
         'provider_npi': '2222222222', 'place_of_service': '11',
         'patient_binding': 'synthetic-binding', 'product_binding': PRODUCT,
         'unit_allowed_cents': 12000,
         'channel': 'payer_portal', 'reference': 'synthetic quote 12345',
         'valid_through': DAY, 'verified_at': '2026-10-14T12:00:00Z'}
SCOPE = {'payer_id': 'uhc-route', 'service_date': DAY, 'provider_npi': '2222222222',
         'place_of_service': '11', 'patient_binding': 'synthetic-binding',
         'product_binding': PRODUCT}
OON_POLICY = {'mode': 'defer_to_claim', 'approved_by': 'synthetic-admin',
              'approved_at': '2026-10-01T12:00:00Z', 'reference': 'Synthetic approval',
              'allow_supported_oon_advance': True}


def test_quote_rejects_changed_patient_provider_payer_cpt_units_and_expiration():
    review = {'payer_allowances': {'office': QUOTE}}
    assert payer_allowance.quoted_rates(review, [LINE], **SCOPE)[0]['allowed_cents'] == 12000
    for field, changed in [('payer_id', 'other'), ('patient_binding', 'other'),
                           ('product_binding', 'other'),
                           ('provider_npi', '3333333333'), ('place_of_service', '22'),
                           ('service_date', '2026-10-15')]:
        assert payer_allowance.quoted_rates(review, [LINE], **{**SCOPE, field: changed}) == []
    for mutation in [{'units': 2}, {'code': '99204'}, {'modifiers': ['25']}]:
        assert payer_allowance.quoted_rates(review, [{**LINE, **mutation}], **SCOPE) == []
    expired = deepcopy(review)
    expired['payer_allowances']['office']['valid_through'] = '2026-10-13'
    assert payer_allowance.quoted_rates(expired, [LINE], **SCOPE) == []


def test_saved_payer_network_label_requires_a_persisted_rendering_npi_binding():
    case = {'previsit_context': {'source': {'rendering_provider': {
        'npi': '2222222222', 'first_name': 'Synthetic', 'last_name': 'Clinician'}},
        'inputs': {'rendering_provider': {'npi': '2222222222'}}}}
    original = {'benefits': {'active': True, 'network_status': 'out_of_network',
                             'network_source': 'payer_participation_message',
                             'network_evidence_ids': ['synthetic-payer-row']},
                'price_range': {'complete': True, 'patient_max_cents': 2400,
                    'lines': [{**LINE, 'allowed_cents': 12000, 'unit_allowed_cents': 12000,
                               'calculation': {'patient_cents': 2400},
                               'benefits': {'network_status': 'out_of_network',
                                            'network_source': 'payer_participation_message'}}]},
                'arrival_collection': {'amount_cents': 2400}}
    legacy = deepcopy(original)
    recheck_saved_payer_message(legacy, case)
    assert legacy['benefits']['active'] is True
    assert legacy['benefits']['network_status'] == 'unknown'
    assert legacy['price_range']['patient_max_cents'] is None
    assert legacy['price_range']['lines'][0]['calculation'] is None
    assert legacy['price_range']['lines'][0]['benefits']['network_status'] == 'unknown'
    assert 'arrival_collection' not in legacy

    bound = deepcopy(original)
    bound['benefits']['network_provider_npi'] = '2222222222'
    recheck_saved_payer_message(bound, case)
    assert bound['price_range']['patient_max_cents'] == 2400
    wrong_provider = deepcopy(bound)
    wrong_provider['benefits']['network_provider_npi'] = '3333333333'
    recheck_saved_payer_message(wrong_provider, case)
    assert wrong_provider['price_range']['patient_max_cents'] is None


def test_previsit_uses_quote_only_for_selected_oon_service():
    inputs = {'service_date': DAY, 'place_of_service': '11'}
    source = {'rendering_provider': {'npi': '2222222222', 'first_name': 'Synthetic',
                                     'last_name': 'Clinician'}}
    benefits = {'active': True, 'coverage_level': 'individual', 'network_status': 'out_of_network',
                'network_source': 'clinic_nonparticipation_rule',
                'identity': {'binding': 'synthetic-binding', 'match_version': 2,
                             'status': 'matched'}, 'plan_details': PLAN,
                'service_date_matches': True,
                'cost_share': 'coinsurance', 'coinsurance_percent': '20',
                'deductible_remaining_cents': 0, 'oop_remaining_cents': 100000,
                'referral_signal': 'not_required', 'authorization_signal': 'not_required',
                'review_reasons': [], 'requires_review': False}
    review = {'selected_ids': ['office'], 'payer_allowances': {'office': QUOTE}}
    priced, issues = api._previsit_range('clinic', inputs, source, 'payer-local',
                                             [LINE], benefits, review=review,
                                             payer_route_id='uhc-route')
    assert not issues
    assert priced['lines'][0]['allowed_basis'] == 'payer_out_of_network'
    assert priced['lines'][0]['allowed_cents'] == 12000
    assert priced['complete'] and priced['patient_max_cents'] == 2400
    changed = deepcopy(review)
    changed['payer_allowances']['office']['patient_binding'] = 'other'
    priced, issues = api._previsit_range('clinic', inputs, source, 'payer-local',
                                             [LINE], benefits, review=changed,
                                             payer_route_id='uhc-route')
    assert not priced['complete'] and priced['lines'][0]['allowed_cents'] is None
    assert any('payer out-of-network allowed amount' in issue for issue in issues)


def test_previsit_pricing_keeps_the_stedi_route_distinct_from_local_connection(monkeypatch):
    inputs = {'service_date': DAY, 'place_of_service': '11',
              'rendering_provider': {'npi': '2222222222'}}
    source = {'rendering_provider': {'npi': '2222222222', 'first_name': 'Synthetic',
                                     'last_name': 'Clinician'},
              'billing_provider': {'npi': '1111111111'},
              'location_id': 'office', 'plan': 'Synthetic Regional PPO'}
    benefits = {'active': True, 'coverage_level': 'individual', 'network_status': 'unknown',
                'identity': {'binding': 'synthetic-binding', 'match_version': 2,
                             'status': 'matched'}, 'plan_details': PLAN,
                'service_date_matches': True, 'cost_share': 'coinsurance',
                'coinsurance_percent': '20', 'deductible_remaining_cents': 0,
                'oop_remaining_cents': 100000, 'referral_signal': 'not_required',
                'authorization_signal': 'not_required', 'review_reasons': [],
                'requires_review': False}
    result = {'payer': 'United Healthcare', 'codes': [LINE], 'issues': [],
              'payment_mode': 'insurance', 'service_date': DAY,
              'selected_ids': ['office'], 'benefits': benefits,
              'identity': benefits['identity']}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *a: [
        {'id': 'payer-local', 'stedi_payer_id': 'uhc-route',
         'status': 'staff_guidance_enabled'}])
    def nonparticipating(*a):
        raise HTTPException(409, 'A sourced nonparticipation rule applies to this provider, product, location and date.')
    monkeypatch.setattr(api, '_previsit_practice_path', nonparticipating)
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'uhc-route', benefits,
        review={'selected_ids': ['office'], 'payer_allowances': {'office': QUOTE}})
    assert benefits['network_source'] == 'clinic_nonparticipation_rule'
    assert result['price_range']['complete']
    assert result['price_range']['patient_max_cents'] == 2400
    assert api.collection_guidance.build(result)['amount_cents'] is None
    result['collection_policy'] = OON_POLICY
    assert api.collection_guidance.build(result)['amount_cents'] == 2400


def test_payer_confirmed_oon_quote_prices_without_an_in_network_connection(monkeypatch):
    inputs = {'service_date': DAY, 'place_of_service': '11',
              'rendering_provider': {'npi': '2222222222'}}
    source = {'rendering_provider': {'npi': '2222222222', 'first_name': 'Synthetic',
                                     'last_name': 'Clinician'}}
    benefits = {'active': True, 'coverage_level': 'individual',
                'network_status': 'out_of_network',
                'network_source': 'payer_participation_message',
                'identity': {'binding': 'synthetic-binding', 'match_version': 2,
                             'status': 'matched'}, 'plan_details': PLAN,
                'service_date_matches': True, 'cost_share': 'coinsurance',
                'coinsurance_percent': '20', 'deductible_remaining_cents': 0,
                'oop_remaining_cents': 100000, 'referral_signal': 'not_required',
                'authorization_signal': 'not_required', 'review_reasons': [],
                'requires_review': False}
    result = {'payer': 'United Healthcare', 'codes': [LINE], 'issues': [],
              'payment_mode': 'insurance', 'service_date': DAY,
              'selected_ids': ['office'], 'benefits': benefits,
              'identity': benefits['identity']}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *a: [])
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'uhc-route', benefits,
        review={'selected_ids': ['office'], 'payer_allowances': {'office': QUOTE}})
    assert result['price_range']['complete']
    assert result['price_range']['lines'][0]['allowed_basis'] == 'payer_out_of_network'
    assert result['price_range']['patient_max_cents'] == 2400
    assert not result['issues']
    assert api.collection_guidance.build(result)['amount_cents'] is None
    result['collection_policy'] = OON_POLICY
    assert api.collection_guidance.build(result)['amount_cents'] == 2400

    without_quote = {**result, 'issues': []}
    api._attach_previsit_pricing(without_quote, 'clinic', inputs, source, 'uhc-route', benefits,
        review={'selected_ids': ['office']})
    assert not without_quote['price_range']['complete']
    assert any('payer out-of-network allowed amount' in issue for issue in without_quote['issues'])

    in_network = {**benefits, 'network_status': 'in_network'}
    without_contract = {**result, 'benefits': in_network, 'issues': []}
    api._attach_previsit_pricing(without_contract, 'clinic', inputs, source, 'uhc-route', in_network,
        review={'selected_ids': ['office'], 'payer_allowances': {'office': QUOTE}})
    assert not without_contract['price_range']['complete']
    assert any('no single approved plan and provider contract' in issue
               for issue in without_contract['issues'])


def test_admin_can_record_a_visit_bound_quote_but_staff_cannot(monkeypatch):
    inputs = {'service_date': DAY, 'place_of_service': '11'}
    source = {'appointment_id': 'synthetic-visit', 'rendering_provider': {
        'npi': '2222222222', 'first_name': 'Synthetic', 'last_name': 'Clinician'}}
    token = api._visit_context_token(inputs, source)
    result = {'codes': [LINE], 'available_codes': [LINE], 'benefits': {
        'active': True, 'network_status': 'out_of_network',
        'network_source': 'clinic_nonparticipation_rule', 'plan_details': PLAN},
        'identity': {'match_version': 2, 'status': 'matched', 'binding': 'synthetic-binding'}}
    checked_at = datetime.now(timezone.utc).isoformat()
    case = {'source_mode': 'commercial_previsit', 'revision': 1,
            'previsit': {'request_state': 'complete', 'checked_at': checked_at},
            'previsit_raw_object_key': 'synthetic-protected-object',
            'visit_review': {'context_token': token, 'selected_ids': ['office']},
            'previsit_context': {'context_token': token, 'inputs': inputs, 'source': source,
                                 'payer_id': 'uhc-route', 'result': result}}
    session = {'patient_id': 'synthetic-patient', 'share_manifest_id': '',
               'practice_id': 'clinic', 'intake_id': 'synthetic-intake',
               'staff': {'id': 'staff', 'role': 'staff', 'display_name': 'Reviewer'}}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    monkeypatch.setattr(api.store, 'get_case', lambda *a, **kw: case)
    monkeypatch.setattr(api, '_ecw_identity', lambda *a: {})
    monkeypatch.setattr(api.ecw_backend, 'fetch_inputs', AsyncMock(return_value=(inputs, [], source)))
    monkeypatch.setattr(api, '_ecw_practice_config', lambda *a: {})
    monkeypatch.setattr(api.library_pricing, 'prepare', lambda *a: [])
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *a: json.dumps({}).encode())
    monkeypatch.setattr(api, '_finish_previsit', lambda *a, **kw: None)
    monkeypatch.setattr(api, '_save', lambda row, *a: row)
    monkeypatch.setattr(api, '_previsit_result', lambda row, *a: row['visit_review'])
    value = api.VisitReviewInput(context_token=token, revision=1, payer_allowance={
        'line_id': 'office', 'unit_allowed_cents': 12000, 'channel': 'payer_portal',
        'reference': 'synthetic quote 12345', 'valid_through': DAY})
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(api.update_visit_review('case', value, None))
    assert forbidden.value.status_code == 403
    session['staff']['role'] = 'admin'
    saved = asyncio.run(api.update_visit_review('case', value, None))
    assert saved['payer_allowances']['office']['patient_binding'] == 'synthetic-binding'
    assert saved['payer_allowances']['office']['payer_id'] == 'uhc-route'
    assert saved['payer_allowances']['office']['product_binding'] == PRODUCT
    assert saved['payer_allowances']['office']['unit_allowed_cents'] == 12000
    changed = api.VisitReviewInput(context_token=token, revision=1, confirmed_units={'office': 2})
    assert 'office' not in asyncio.run(api.update_visit_review('case', changed, None)).get('payer_allowances', {})


def test_saved_quote_is_removed_when_quote_or_clinic_network_rule_changes(monkeypatch):
    source = {'rendering_provider': {'npi': '2222222222', 'first_name': 'Synthetic',
                                     'last_name': 'Clinician'}}
    case = {'previsit_context': {'payer_id': 'uhc-route', 'inputs': {
        'service_date': DAY, 'place_of_service': '11'}, 'source': source},
        'visit_review': {'payer_allowances': {'office': QUOTE}}}
    result = {'benefits': {'network_status': 'out_of_network',
                           'network_source': 'clinic_nonparticipation_rule',
                           'plan_details': PLAN},
              'identity': {'binding': 'synthetic-binding'},
              'plan_details': {'stedi': {'names': ['Synthetic Regional PPO']}},
              'price_range': {'complete': True, 'patient_max_cents': 2400,
                  'lines': [{**LINE, 'allowed_basis': 'payer_out_of_network',
                             'allowed_cents': 12000, 'unit_allowed_cents': 12000,
                             'rate_source': 'Payer quote synthetic quote 12345',
                             'calculation': {'patient_cents': 2400}}]}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *a: [
        {'id': 'payer-local', 'stedi_payer_id': 'uhc-route', 'status': 'staff_guidance_enabled'}])
    message = 'A sourced nonparticipation rule applies to this provider, product, location and date.'
    def nonparticipating(*a):
        raise HTTPException(409, message)
    monkeypatch.setattr(api, '_previsit_practice_path', nonparticipating)
    status._recheck_saved_payer_allowance(result, case, 'clinic')
    assert result['price_range']['patient_max_cents'] == 2400
    changed = deepcopy(result)
    changed_case = deepcopy(case)
    changed_case['visit_review']['payer_allowances']['office']['patient_binding'] = 'other'
    status._recheck_saved_payer_allowance(changed, changed_case, 'clinic')
    assert changed['price_range']['patient_max_cents'] is None
    assert changed['price_range']['lines'][0]['calculation'] is None
    monkeypatch.setattr(api, '_previsit_practice_path', lambda *a: ({}, {}, {}))
    changed = deepcopy(result)
    status._recheck_saved_payer_allowance(changed, case, 'clinic')
    assert changed['price_range']['patient_max_cents'] is None
    assert changed['benefits']['network_status'] == 'unknown'
