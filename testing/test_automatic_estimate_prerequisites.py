"""Automatic office planning and existing contract reuse, with synthetic evidence only."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from app.api.routers import commercial_estimator as api
from test_result_first_estimate import response
from testing.synthetic_policy import COPAY_POLICY


@pytest.fixture
def appointment(monkeypatch):
    day = date.today().isoformat()
    payer = {'id': 'connection-a', 'stedi_payer_id': 'route-a', 'display_name': 'Example Health', 'category': 'commercial_medical',
             'status': 'staff_guidance_enabled', 'rules': {'service_type_code': '98',
                                                         'collection_policy': COPAY_POLICY}}
    network = {'id': 'network-a', 'payer_connection_id': payer['id'], 'status': 'verified',
               'billing_npi': '1111111111', 'rendering_npi': '2222222222', 'location_id': 'office',
               'place_of_service': '11', 'plan_matcher': 'Choice Medical', 'effective_from': '2020-01-01',
               'evidence_reference': 'library:signed-contract', 'network_name': 'Example network'}
    profile = {'id': 'profile-a', 'payer_connection_id': payer['id'], 'provider_network_id': network['id'],
               'status': 'staff_guidance_enabled', 'plan_matcher': 'Choice Medical', 'rules': {
                   'library_source': {'document_id': 'signed-contract', 'reference': 'library:signed-contract',
                                      'sha256': 'synthetic-hash', 'locator': 'Page 1'}}}
    rates = [{'code': str(code), 'allowed_cents': 20000, 'modifier': '', 'source_reference': 'Synthetic rate'}
             for code in range(99212, 99216)]
    inputs = {'payer_id': payer['id'], 'service_date': day, 'visit_type': 'established',
              'reason': 'Follow up', 'lines': [], 'place_of_service': '11',
              'rendering_provider': {'npi': network['rendering_npi']}, 'member_id': 'ABC123', 'relationship': '19',
              'subscriber': {'firstName': 'Sam', 'lastName': 'Other', 'birthDate': '1980-01-01'},
              'patient': {'patient_first_name': 'Alex', 'patient_last_name': 'Morgan', 'patient_birth_date': '2001-02-03'}}
    source = {'appointment_id': 'visit-a', 'coverage_id': 'coverage-a', 'active_coverages': 1,
              'payer': 'Example Health', 'plan': 'Choice Medical', 'location_id': 'office',
              'billing_provider': {'npi': network['billing_npi']}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda practice: [payer] if practice == 'clinic-a' else [])
    monkeypatch.setattr(api.commercial_store, 'connection', lambda practice, key: payer if practice == 'clinic-a' and key == payer['id'] else None)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda practice: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda practice: [profile])
    monkeypatch.setattr(api.commercial_store, 'list_rate_versions', lambda practice: [
        {'payer_connection_id': payer['id'], 'status': 'active', 'effective_from': '2020-01-01'}])
    monkeypatch.setattr(api.commercial_store, 'matching_rates', lambda *args: rates)
    monkeypatch.setattr(api.staff_store, 'list_library', lambda practice: [{
        'id': 'signed-contract', 'kind': 'contract', 'content_type': 'application/pdf',
        'payer': 'Example Health', 'label': 'Synthetic signed contract', 'effective_from': '2020-01-01',
    }] if practice == 'clinic-a' else [])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda key, practice: {
        'id': key, 'kind': 'contract', 'content_type': 'application/pdf', 'sha256': 'synthetic-hash',
        'label': 'Synthetic signed contract', 'effective_from': '2020-01-01'} if practice == 'clinic-a' else None)
    monkeypatch.setattr(api.practice_prices, 'current', lambda *args: None)
    monkeypatch.setattr(api.stedi_financial, 'credentials', lambda *args: 'synthetic-key')
    monkeypatch.setattr(api.object_store, 'enabled', lambda: True)
    monkeypatch.setenv('STEDI_EXECUTION_MODE', 'commercial_pilot')
    monkeypatch.setenv('STEDI_LIVE_ELIGIBILITY_ENABLED', 'true')
    return inputs, source, payer, network


def test_uncoded_followup_is_ready_for_automatic_planning(appointment):
    inputs, source, _, _ = appointment
    result = api._appointment_readiness('clinic-a', {'practice_id': 'clinic-a', 'intake': {'fields': {}}}, inputs, source)
    assert result['ready'], result['blockers']
    assert result['ecw']['scheduled_codes'] == []  # No invented EHR coding.


def test_multiple_active_policies_allow_selected_coverage_inquiry_but_require_cob(appointment):
    inputs, source, _, _ = appointment
    source['active_coverages'] = 3
    session = {'practice_id': 'clinic-a', 'intake': {'fields': {}}}
    result = api._appointment_readiness('clinic-a', session, inputs, source)
    assert result['ready'], result['blockers']
    assert result['blockers'] == []
    assert any('coordination of benefits' in action.lower() for action in result['coverage_followups'])

    source['coverage_id'] = None  # Tied eCW order leaves no safe policy to inquire about.
    ambiguous = api._appointment_readiness('clinic-a', session, inputs, source)
    assert not ambiguous['ready']
    assert any('active coverage' in reason.lower() for reason in ambiguous['blockers'])


@pytest.mark.parametrize('mode', ['deferred', 'verified', 'wrong_route', 'other_clinic', 'expired_network', 'unknown_mapping', 'wrong_product'])
def test_estimate_uses_existing_mapping_without_staff_confirmation(appointment, mode):
    inputs, source, payer, network = appointment
    payload = response() if mode in {'verified', 'wrong_product'} else None
    route = payer['stedi_payer_id'] if payload else ''
    clinic = 'other-clinic' if mode == 'other_clinic' else 'clinic-a'
    if mode == 'wrong_route':
        route = 'different-returned-route'
    if mode == 'expired_network':
        network['effective_to'] = '2020-01-02'
    if mode == 'unknown_mapping':
        inputs['payer_id'] = 'unapproved-connection'
    if mode == 'wrong_product':
        payload = deepcopy(payload)
        payload['benefitsInformation'][0]['planCoverageDescription'] = 'Another product'
    result = {'issues': [], 'service_date': inputs['service_date'],
              'benefits': api._previsit_benefits(payload or {}, api._coverage_snapshot(inputs, source), inputs['service_date'])}
    if payload:
        result['checked_at'] = datetime.now(timezone.utc).isoformat()
    else:
        result['eligibility_timing'] = {'status': 'too_early', 'check_from': inputs['service_date'], 'message': 'Checking opens later.'}
    case = {}
    api._finish_previsit(result, case, inputs, source, payload, {'practice_id': clinic}, route)
    assert [(row['code'], row['units'], row['confirmed']) for row in result['codes']] == [('99213', 1, False)]
    assert result['selected_ids'] == [next(row['id'] for row in result['available_codes'] if row['code'] == '99213')]
    assert result['service_plan']['selection_source'] == 'suggested'
    assert not case['visit_review'].get('selected_ids')
    if mode in {'deferred', 'verified'}:
        assert not result['issues'], result['issues']
        assert next(row for row in result['price_range']['lines'] if row['code'] == '99213')['allowed_cents'] == 20000
        assert case['previsit_context']['payer_id'] == payer['stedi_payer_id']
        diagnosis = api._insurance_diagnosis(case, result, result['collection_guidance'])
        assert 'PROVIDER_NETWORK_NOT_VERIFIED' not in diagnosis['reasonCodes']
        assert [row['code'] for row in result['price_range']['lines']] == ['99213']
        if mode == 'deferred':
            assert diagnosis['eligibilityStatus'] == 'NOT_VERIFIED'
    else:
        assert all(row['allowed_cents'] is None for row in result['price_range']['lines'])
        assert result['issues']
    if mode == 'verified':
        assert result['collection_guidance']['amount_cents'] == 4000
    elif mode == 'wrong_product':
        assert result['benefits']['network_status'] == 'unknown'
        assert result['collection_guidance']['amount_cents'] is None
    else:
        assert result['collection_guidance']['amount_cents'] is None


def test_saved_payer_evidence_reprices_when_existing_contract_link_is_resolved(appointment):
    inputs, source, payer, network = appointment
    payload = response()
    case = {}

    def calculate():
        result = {'issues': [], 'service_date': inputs['service_date'],
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'benefits': api._previsit_benefits(payload, api._coverage_snapshot(inputs, source), inputs['service_date'])}
        api._finish_previsit(result, case, inputs, source, payload,
                             {'practice_id': 'clinic-a'}, payer['stedi_payer_id'])
        return result

    network['plan_matcher'] = 'Unrelated Product'
    unresolved = calculate()
    assert unresolved['collection_guidance']['amount_cents'] is None
    assert next(row for row in unresolved['evidence_resolution'] if row['field'] == 'product_mapping')['status'] == 'unresolved'

    network['plan_matcher'] = 'Choice Medical'
    resolved = calculate()  # Same saved response; no new eligibility transaction.
    assert resolved['collection_guidance']['amount_cents'] == 4000
    assert next(row for row in resolved['evidence_resolution'] if row['field'] == 'product_mapping')['status'] == 'resolved'
    assert next(row for row in resolved['evidence_resolution'] if row['field'] == 'collection_amount')['status'] == 'resolved'
    assert any(line['allowed_cents'] == 20000 for line in resolved['price_range']['lines'])


def test_saved_ambiguous_product_reconciles_through_scoped_mapping_and_rates(appointment):
    inputs, source, payer, network = appointment
    profile = api.commercial_store.list_profiles('clinic-a')[0]
    source['plan'] = network['plan_matcher'] = profile['plan_matcher'] = 'CI'
    payload = deepcopy(response())
    payload['subscriber']['groupNumber'] = 'SYNTHETIC-GROUP'
    payload['planDateInformation'] = {'service': {'start': inputs['service_date'], 'end': inputs['service_date']}}
    inputs['group_number'] = 'SYNTHETIC-GROUP'
    payload['benefitsInformation'][0]['planCoverageDescription'] = 'BASIC'
    profile['rules']['plan_product_names'] = ['Blue Choice PPO Basic']
    case = {}

    def calculate():
        result = {'issues': [], 'service_date': inputs['service_date'],
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'benefits': api._previsit_benefits(payload, api._coverage_snapshot(inputs, source), inputs['service_date'])}
        api._finish_previsit(result, case, inputs, source, payload,
                             {'practice_id': 'clinic-a'}, payer['stedi_payer_id'])
        return result

    unlinked = calculate()
    assert unlinked['collection_guidance']['amount_cents'] is None
    assert next(row for row in unlinked['evidence_resolution'] if row['field'] == 'product_mapping')['status'] == 'unresolved'

    profile['rules']['product_mappings'] = [{
        'products': ['BASIC'], 'contract_product': 'Blue Choice PPO Basic',
        'rate_plan_matcher': 'CI', 'network_id': network['id'],
        'network_reference': network['evidence_reference'],
        'reference': 'Synthetic contract and payer evidence', 'actor_id': 'synthetic-admin',
        'valid_from': '2020-01-01', 'valid_to': '2099-12-31',
        'plan_scope': {'plan_number': None, 'group_number': 'SYNTHETIC-GROUP'},
    }]
    priced = calculate()
    assert priced['network_evidence']['product_mapping_approval']['contract_product'] == 'Blue Choice PPO Basic'
    assert priced['collection_guidance']['amount_cents'] == 4000
    assert all(line['allowed_cents'] == 20000 for line in priced['price_range']['lines'])
    assert next(row for row in priced['evidence_resolution'] if row['field'] == 'product_mapping')['status'] == 'resolved'
