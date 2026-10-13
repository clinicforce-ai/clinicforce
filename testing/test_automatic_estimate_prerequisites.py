"""Automatic office planning and existing contract reuse, with synthetic evidence only."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from app.api.routers import commercial_estimator as api
from test_result_first_estimate import response


@pytest.fixture
def appointment(monkeypatch):
    day = date.today().isoformat()
    payer = {'id': 'connection-a', 'stedi_payer_id': 'route-a', 'category': 'commercial_medical',
             'status': 'staff_guidance_enabled', 'rules': {'service_type_code': '98'}}
    network = {'id': 'network-a', 'payer_connection_id': payer['id'], 'status': 'verified',
               'billing_npi': '1111111111', 'rendering_npi': '2222222222', 'location_id': 'office',
               'place_of_service': '11', 'plan_matcher': 'Choice Medical', 'effective_from': '2020-01-01',
               'evidence_reference': 'Synthetic approved contract', 'network_name': 'Example network'}
    profile = {'id': 'profile-a', 'payer_connection_id': payer['id'], 'provider_network_id': network['id'],
               'status': 'staff_guidance_enabled', 'plan_matcher': 'Choice Medical', 'rules': {}}
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
    assert [(row['code'], row['units'], row['confirmed']) for row in result['codes']] == [
        (code, 1, False) for code in ('99212', '99213', '99214', '99215')]
    assert result['service_plan']['selection_source'] == 'suggested'
    assert not case['visit_review'].get('selected_ids')
    if mode in {'deferred', 'verified'}:
        assert not result['issues'], result['issues']
        assert next(row for row in result['price_range']['lines'] if row['code'] == '99213')['allowed_cents'] == 20000
        assert case['previsit_context']['payer_id'] == payer['stedi_payer_id']
        diagnosis = api._insurance_diagnosis(case, result, result['collection_guidance'])
        assert 'PROVIDER_NETWORK_NOT_VERIFIED' not in diagnosis['reasonCodes']
        assert result['price_range']['complete'] is False  # Other E/M levels remain unpriced.
        if mode == 'deferred':
            assert diagnosis['eligibilityStatus'] == 'NOT_VERIFIED'
    else:
        assert all(row['allowed_cents'] is None for row in result['price_range']['lines'])
        assert result['issues']
    if mode in {'verified', 'wrong_product'}:
        assert result['collection_guidance']['amount_cents'] == 4000
        if mode == 'wrong_product':
            # Existing fixed-copay planning can stand alone without a contract rate.
            assert result['collection_guidance']['basis'] == 'office_copay'
            assert result['collection_guidance']['assumptions']
    else:
        assert result['collection_guidance']['amount_cents'] is None
