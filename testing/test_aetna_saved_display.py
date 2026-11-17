"""A saved Aetna office check must use its protected payer identity on read."""
import hashlib
import json
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_saved_pricing as saved_pricing
from app.api.routers.commercial_flow import commercial_previsit_status as status
from app.api.routers.commercial_flow import commercial_previsit_requirements as requirements
from app.insurance import aetna_office_planning, identity
from app.insurance.requirements.published import _published_rule


def _case():
    inputs = {
        'service_date': '2026-11-17', 'place_of_service': '11', 'payer_id': '60054',
        'member_id': 'SYN123', 'relationship': '18',
        'patient': {'patient_first_name': 'Taylor Morgan', 'patient_last_name': 'Rivera',
                    'patient_birth_date': '1950-01-10'},
        'subscriber': {'firstName': 'Taylor Morgan', 'lastName': 'Rivera',
                       'birthDate': '1950-01-10'},
        'rendering_provider': {'npi': '1234567890'},
    }
    source = {'_patient_id': 'synthetic-patient', 'coverage_id': 'synthetic-policy',
              'plan': 'CI',
              'active_coverages': 1, 'subscriber_evidence': {'patient_linked': True}}
    payload = {'payerId': '60054', 'subscriber': {'memberId': 'SYN123',
        'dateOfBirth': '1950-01-19', 'name': {'person': {
            'firstName': 'TAYLOR', 'middleName': 'M', 'lastName': 'RIVERA'}}}}
    raw = json.dumps(payload).encode()
    matched = identity.compare(payload, api._coverage_snapshot(inputs, source))
    assert matched['reconciliation_rule'] == 'aetna_payer_demographics'
    old = {**matched, 'status': 'review', 'reconciliation_rule': None,
           'missing_fields': ['patient_demographics']}
    benefits = {
        'active': True, 'service_date_matches': False,
        'returned_service_dates': {'start': '2026-11-16'},
        'network_status': 'unknown', 'deductible_remaining_cents': 0,
        'oop_remaining_cents': 530265, 'qmb_status': 'unknown', 'identity': old,
        'plan_details': {'names': ['Aetna Medicare Value Plus (PPO)'],
                         'group_number': '000003-TX', 'plan_number': '000003-TX000083'},
        'reported_options': [
            {'kind': 'copay', 'service_type_code': '98', 'network': 'IN_NETWORK',
             'places_of_service': ['11'], 'amount_cents': 5000,
             'payer_note': 'Specialist Visit or Evaluation'},
            {'kind': 'coinsurance', 'service_type_code': '98', 'network': 'IN_NETWORK',
             'places_of_service': ['11'], 'percent': '0',
             'payer_note': 'Telemedicine Specialist Visit or Evaluation'},
        ],
    }
    result = {'service_date': '2026-11-17', 'payer': 'Aetna', 'payment_mode': 'insurance',
              'identity': old, 'benefits': benefits,
              'plan_details': {'stedi': benefits['plan_details']},
              'codes': [{'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}],
              'benefit_source': 'Stedi', 'insurance_details': {'member_id': 'SYN123'}}
    case = {'previsit': {'request_state': 'complete'},
            'previsit_raw_object_key': 'synthetic-protected-object',
            'previsit_raw_sha256': hashlib.sha256(raw).hexdigest(),
            'previsit_context': {'payer_id': '60054', 'inputs': inputs,
                                 'source': source, 'result': result}}
    return case, raw


def test_saved_identity_rebinds_only_from_hash_verified_aetna_response():
    case, raw = _case()
    result = deepcopy(case['previsit_context']['result'])
    with patch.object(api.object_store, 'get_object_bytes', return_value=raw):
        saved_pricing.refresh_saved_aetna_identity(result, case)
    assert result['identity']['reconciliation_rule'] == 'aetna_payer_demographics'
    assert result['benefits']['identity'] == result['identity']
    assert case['previsit_context']['result']['identity']['status'] == 'review'

    changed = deepcopy(case)
    changed['previsit_raw_sha256'] = '0' * 64
    result = deepcopy(changed['previsit_context']['result'])
    with patch.object(api.object_store, 'get_object_bytes', return_value=raw):
        saved_pricing.refresh_saved_aetna_identity(result, changed)
    assert result['identity']['status'] == 'review'


def test_saved_office_split_uses_reported_copay_when_only_zero_row_is_telemedicine():
    case, _ = _case()
    result = case['previsit_context']['result']
    result['identity'] = {**result['identity'], 'status': 'warning',
                          'reconciliation_rule': 'aetna_payer_demographics'}
    result['benefits']['identity'] = result['identity']
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 9633}]):
        planning = aetna_office_planning.estimate(result, case['previsit_context']['inputs'])
    assert (planning['copay_cents'], planning['plan_share_cents']) == (5000, 4633)
    assert planning['coinsurance_percent'] is None
    assert planning['collectable'] is False


def test_saved_value_plus_requirement_is_conditional_when_provider_tier_unknown():
    case, _ = _case()
    result = case['previsit_context']['result']
    result['identity'] = {**result['identity'], 'status': 'warning',
                          'reconciliation_rule': 'aetna_payer_demographics'}
    result['benefits']['identity'] = result['identity']
    context = {'service_date': '2026-11-17', 'payer_names': {'aetna', '60054'}, 'pos': '11',
               'member_id': 'SYN123'}
    for kind in ('referral', 'authorization'):
        decision = _published_rule(kind, result, context,
                                   ['CI', 'Aetna Legacy (PPO)', 'Aetna Medicare Value Plus (PPO)'], {'99213'})
        assert decision['status'] == 'Not required'
        assert decision['member_specific_check'] is True


def test_saved_previsit_read_reprojects_patient_copay_and_plan_share(monkeypatch):
    case, raw = _case()
    result = case['previsit_context']['result']
    result['price_range'] = {'mode': 'services', 'complete': False, 'lines': []}
    result['checked_at'] = '2026-11-16T17:36:00Z'
    result['requirement_sources'] = []
    result['published_requirements_version'] = 'stale'
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *_: raw)
    monkeypatch.setattr(status._api.commercial_store, 'list_connections', lambda *_: [
        {'id': 'synthetic-aetna-route', 'display_name': 'Aetna',
         'stedi_payer_id': '60054', 'aliases': ['Aetna Medicare'], 'rules': {}}])
    monkeypatch.setattr(status._api.commercial_store, 'list_cpt_requirements', lambda *_: [])
    monkeypatch.setattr(status._api.staff_store, 'list_library', lambda *_: [])
    for name in ('_recheck_saved_clinic_pricing', 'recheck_saved_network_evidence',
                 'refresh_saved_network_pricing', '_recheck_saved_payer_allowance',
                 'refresh_saved_cross_payer_planning'):
        monkeypatch.setattr(status, name, lambda *_: None)
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 9633}]):
        projected = status._previsit_result(case, 'synthetic-practice')
    assert projected['identity']['reconciliation_rule'] == 'aetna_payer_demographics'
    assert projected['aetna_office_planning']['copay_cents'] == 5000
    assert projected['aetna_office_planning']['plan_share_cents'] == 4633
    assert projected['aetna_office_planning']['collectable'] is False
    procedure = projected['requirement_evidence']['procedures'][0]
    assert procedure['code'] == '99213'
    assert procedure['referral']['status'] == 'Not required'
    assert procedure['authorization']['status'] == 'Not required'
    assert case['previsit_context']['result']['identity']['status'] == 'review'


def test_saved_requirement_refreshes_when_only_identity_binding_changes(monkeypatch):
    case, _ = _case()
    result = deepcopy(case['previsit_context']['result'])
    result['identity'] = {**result['identity'], 'status': 'warning',
                          'reconciliation_rule': 'aetna_payer_demographics'}
    result['requirement_sources'] = []
    result['public_source_checked_day'] = date.today().isoformat()
    case['previsit_context']['result']['published_requirements_version'] = requirements._rules_version([])
    calls = []
    monkeypatch.setattr(requirements, 'attach_requirements', lambda *args: calls.append(args))
    store = SimpleNamespace(list_library=lambda *_: [])
    payer = SimpleNamespace(list_cpt_requirements=lambda *_: [], list_connections=lambda *_: [])
    requirements.refresh_saved_requirements(SimpleNamespace(staff_store=store,
        commercial_store=payer), result, case, 'synthetic-practice')
    assert len(calls) == 1
