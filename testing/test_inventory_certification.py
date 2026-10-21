"""Synthetic scoped UHC inventory approvals; no protected contract or patient data."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.api.routers import commercial_estimator  # Register the workflow imports.
from app.api.routers.commercial_flow import commercial_previsit_pricing
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_inventory
from app.insurance import amount_explanation, commercial_store, inventory_certification, library_network_exclusions
from app.insurance.visit_estimate import is_emg_visit
from app.insurance.collection_guidance import build as collection_guidance
from app.insurance.collection.amount_actions import _collection_action
from testing.test_uhc_saved_response_projections import replay


DOC = {'id': 'synthetic-general-agreement', 'kind': 'contract', 'payer': 'UnitedHealthcare',
       'sha256': 'synthetic-sha256'}
SCOPE = {'rendering_npi': '1234567890', 'billing_npi': '9876543210', 'tin': '123456789',
         'location_id': 'office', 'place_of_service': '11',
         'effective_from': '2026-01-01', 'effective_to': '2026-12-31'}
EXCLUSION = {'decision': 'excluded_under_available_agreement',
             'document_id': DOC['id'], 'page': 7,
             'product_kind': 'regional_medicare_advantage_ppo'}
CERTIFICATE = {'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01T12:00:00Z',
               'reference': 'Synthetic clinic inventory review',
               'document_hashes': {DOC['id']: DOC['sha256']}, 'scopes': [SCOPE]}
PAYER = {'id': 'synthetic-uhc-route', 'stedi_payer_id': '87726',
         'display_name': 'UnitedHealthcare', 'status': 'draft',
         'rules': {'agreement_inventory': CERTIFICATE}}
INPUTS = {'service_date': '2026-10-14', 'rendering_provider': {'npi': '1234567890'},
          'place_of_service': '11'}
SOURCE = {'plan': 'RPPO-UHC Complete Care Support TX-1A', 'payer_plan_bound': True,
          'payer_plan_names': ['RPPO-UHC Complete Care Support TX-1A'],
          'billing_provider': {'npi': '9876543210', 'tin': '123456789'},
          'location_id': 'office'}


def test_certification_matches_exact_scope_and_invalidates_when_inventory_changes(monkeypatch):
    docs = [DOC]
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: docs)
    assert inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS, SOURCE, EXCLUSION)['decision'] == (
        'out_of_network_by_clinic_contract_inventory')
    assert inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS,
        {**SOURCE, 'billing_provider': {**SOURCE['billing_provider'], 'tin': '999999999'}}, EXCLUSION) is None
    assert inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS,
        {**SOURCE, 'payer_plan_bound': False}, EXCLUSION) is None
    assert inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS,
        {**SOURCE, 'payer_plan_names': ['Choice Plus']}, EXCLUSION) is None
    docs.append({**DOC, 'id': 'new-separate-agreement'})
    assert inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS, SOURCE, EXCLUSION) is None


def test_certified_regional_inventory_does_not_request_another_agreement(monkeypatch):
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: [DOC])
    certified = inventory_certification.applicable('synthetic-clinic', PAYER, INPUTS, SOURCE, EXCLUSION)
    assert certified and certified['decision'] == 'out_of_network_by_clinic_contract_inventory'
    result, _, _ = replay('regional_ppo')
    result['network_evidence'] = certified
    result['benefits'].update(network_status='out_of_network', network_source='clinic_contract_inventory')
    explanation = amount_explanation.build(result, collection_guidance(result))
    assert explanation['reason_code'] == 'planned_cpt_unselected'
    assert 'separate agreement' not in explanation['sentence']
    assert 'service alternatives from the applicable plan and clinic evidence' in explanation['sentence']
    for reason in ('clinic_agreement_exclusion', 'clinic_contract_missing'):
        assert 'separate agreement' not in _collection_action(reason, result)


def test_separate_executed_product_agreement_blocks_inventory_oon(monkeypatch):
    separate = {**DOC, 'id': 'synthetic-separate-regional-agreement', 'sha256': 'separate-sha'}
    documents = [DOC, separate]
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: documents)
    network = {'values': {'participation': 'participating',
                          'plan': 'Regional Medicare Advantage PPO',
                          'renderingnpi': SCOPE['rendering_npi'],
                          'billingnpi': SCOPE['billing_npi'], 'tin': SCOPE['tin'],
                          'locationid': SCOPE['location_id'], 'pos': SCOPE['place_of_service'],
                          'effectivefrom': '2026-01-01', 'effectiveto': '2026-12-31'}}
    monkeypatch.setattr(inventory_certification.staff_store, 'get_library',
                        lambda document_id, _: {**separate, 'extraction': {'compiled':
                            {'networks': [network]}}} if document_id == separate['id'] else DOC)
    certified = {**CERTIFICATE, 'document_hashes': {
        DOC['id']: DOC['sha256'], separate['id']: separate['sha256']}}
    payer = {**PAYER, 'rules': {'agreement_inventory': certified}}
    assert inventory_certification.applicable('synthetic-clinic', payer, INPUTS,
                                              SOURCE, EXCLUSION) is None
    network['values']['locationid'] = 'another-office'
    assert inventory_certification.applicable('synthetic-clinic', payer, INPUTS,
                                              SOURCE, EXCLUSION) is not None


def test_draft_connection_can_use_certified_inventory_without_becoming_active(monkeypatch):
    api = commercial_previsit_pricing._api
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: [DOC])
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda _: [PAYER])
    monkeypatch.setattr(library_network_exclusions, 'applicable_exclusion', lambda *args: EXCLUSION)
    monkeypatch.setattr(commercial_previsit_pricing, '_attach_temporary_uhc_rates', lambda *args: None)
    seen = {}

    def price(*args, **kwargs):
        seen['tier'] = args[5]['network_status']
        return {'mode': 'services', 'lines': []}, []

    monkeypatch.setattr(api, '_previsit_range', price)
    result = {'payer': 'UnitedHealthcare', 'codes': [], 'issues': []}
    benefits = {'network_status': 'unknown'}
    commercial_previsit_pricing._attach_previsit_pricing(
        result, 'synthetic-clinic', INPUTS, SOURCE, '87726', benefits)
    assert seen['tier'] == 'out_of_network'
    assert benefits['network_source'] == 'clinic_contract_inventory'
    assert result['network_evidence']['decision'] == 'out_of_network_by_clinic_contract_inventory'
    assert result['agreement_exclusion']['document_id'] == DOC['id']
    assert PAYER['status'] == 'draft'


def test_enabled_connection_keeps_inventory_exclusion_not_payer_confirmation(monkeypatch):
    api = commercial_previsit_pricing._api
    enabled = {**PAYER, 'status': 'staff_guidance_enabled'}
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: [DOC])
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda _: [enabled])
    monkeypatch.setattr(library_network_exclusions, 'applicable_exclusion', lambda *args: EXCLUSION)
    monkeypatch.setattr(commercial_previsit_pricing, '_attach_temporary_uhc_rates', lambda *args: None)
    monkeypatch.setattr(api, '_previsit_practice_path', lambda *args: (_ for _ in ()).throw(
        api.HTTPException(422, 'No exact contract route for this excluded product.')))
    monkeypatch.setattr(api, '_previsit_range', lambda *args, **kwargs: ({'mode': 'services', 'lines': []}, []))
    result = {'payer': 'UnitedHealthcare', 'codes': [], 'issues': []}
    benefits = {'network_status': 'unknown'}
    commercial_previsit_pricing._attach_previsit_pricing(
        result, 'synthetic-clinic', INPUTS, SOURCE, '87726', benefits)
    assert benefits['network_source'] == 'clinic_contract_inventory'
    evidence = result['network_evidence']
    assert evidence['decision'] == 'out_of_network_by_clinic_contract_inventory'
    assert evidence['document_id'] == DOC['id']
    assert evidence['page'] == EXCLUSION['page']
    assert 'without claiming payer confirmation' in evidence['reason']


def test_payer_participation_conflict_prevents_inventory_collection(monkeypatch):
    api = commercial_previsit_pricing._api
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: [DOC])
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda _: [PAYER])
    monkeypatch.setattr(library_network_exclusions, 'applicable_exclusion', lambda *args: EXCLUSION)
    monkeypatch.setattr(commercial_previsit_pricing, '_attach_temporary_uhc_rates', lambda *args: None)
    result = {'payer': 'UnitedHealthcare', 'codes': [], 'issues': []}
    benefits = {'network_status': 'in_network', 'network_source': 'payer_participation_message'}
    commercial_previsit_pricing._attach_previsit_pricing(
        result, 'synthetic-clinic', INPUTS, SOURCE, '87726', benefits)
    assert benefits['network_conflict'] is True
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['decision'] == 'conflicting_network_evidence'


def test_inventory_route_stamps_approval_and_can_revoke_without_activating_connection():
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    db.execute.return_value.fetchone.return_value = {
        'display_name': 'UnitedHealthcare', 'rules_json': json.dumps({'other_rule': True})}
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value=PAYER), \
         patch.object(inventory_certification.staff_store, 'list_library', return_value=[DOC]):
        commercial_store.set_agreement_inventory('synthetic-clinic', PAYER['id'], 'certify',
            'Synthetic clinic inventory review', [SCOPE], 'synthetic-admin')
        with pytest.raises(commercial_store.CommercialError):
            commercial_store.set_agreement_inventory('synthetic-clinic', PAYER['id'], 'certify',
                'Synthetic clinic inventory review', [{**SCOPE, 'effective_to': '2025-01-01'}], 'synthetic-admin')
    stored = json.loads(db.execute.call_args_list[1].args[1][0])
    assert stored['agreement_inventory']['document_hashes'] == {DOC['id']: DOC['sha256']}
    assert stored['agreement_inventory']['approved_by'] == 'synthetic-admin'
    assert stored['other_rule'] is True


def test_provisional_service_price_cannot_become_a_positive_advance():
    result = {'payment_mode': 'insurance', 'service_selection_status': 'preview',
        'benefits': {'active': True, 'network_status': 'out_of_network',
                     'network_source': 'clinic_contract_inventory', 'service_date_matches': True},
        'identity': {'status': 'warning', 'binding': 'synthetic-binding', 'match_version': 2},
        'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 4000,
                        'patient_max_cents': 4000, 'lines': []},
        'arrival_collection': {'amount_cents': 4000}}
    guidance = collection_guidance(result)
    assert guidance['amount_cents'] is None
    assert guidance['status'] == 'action_required'
    assert 'doctor must approve' in guidance['reason']


def test_certified_inventory_positive_advance_requires_separate_collection_approval():
    result = {'payment_mode': 'insurance', 'service_selection_status': 'approved',
        'network_evidence': {'decision': 'out_of_network_by_clinic_contract_inventory',
                             'document_id': DOC['id']},
        'benefits': {'active': True, 'network_status': 'out_of_network',
                     'network_source': 'clinic_contract_inventory', 'service_date_matches': True},
        'identity': {'status': 'warning', 'binding': 'synthetic-binding', 'match_version': 2},
        'plan_details': {'stedi': {'names': ['RPPO-UHC Complete Care Support TX-1A']}},
        'billing_protection': {'status': 'not_qmb', 'identity_binding': 'synthetic-binding',
                               'reference': 'synthetic-member-check', 'verified_at': '2026-10-14T12:00:00Z'},
        'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 4000,
                        'patient_max_cents': 4000, 'lines': [{'id': 'office', 'code': '99203',
                        'allowed_basis': 'payer_out_of_network', 'units': 1,
                        'calculation': {'patient_cents': 4000},
                        'benefits': {'active': True, 'network_status': 'out_of_network',
                                     'requirements': {kind: {'required': 'unknown', 'cleared': False}
                                                      for kind in ('referral', 'authorization')}}}]}}
    policy = {'mode': 'defer_to_claim', 'reference': 'Synthetic collection approval',
              'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01T12:00:00Z'}
    deferred = collection_guidance({**result, 'collection_policy': policy})
    assert deferred['amount_cents'] == 0
    assert deferred['basis'] == 'claim_after_service'
    explanation = amount_explanation.build({**result, 'collection_policy': policy}, deferred)
    assert explanation['sentence'].startswith(
        'The patient has RPPO-UHC Complete Care Support TX-1A, which is excluded from NMCT’s UHC agreement')
    assert 'collect $0 today' in explanation['sentence'].lower()
    approved = collection_guidance({**result, 'collection_policy':
        {**policy, 'allow_supported_oon_advance': True}})
    assert approved['amount_cents'] == 4000


def test_reopened_inventory_estimate_loses_amount_after_certification_revocation(monkeypatch):
    monkeypatch.setattr(inventory_certification.staff_store, 'list_library', lambda _: [DOC])
    monkeypatch.setattr(library_network_exclusions, 'applicable_exclusion', lambda *args: EXCLUSION)
    result = {'benefits': {'network_status': 'out_of_network',
                          'network_source': 'clinic_contract_inventory',
                          'active': True, 'service_date_matches': True},
              'identity': {'status': 'warning', 'binding': 'synthetic-binding', 'match_version': 2},
              'plan_details': {'stedi': {'names': SOURCE['payer_plan_names']}},
              'network_evidence': {'approved_at': CERTIFICATE['approved_at'],
                                   'document_hashes': CERTIFICATE['document_hashes']},
              'price_range': {'complete': True, 'patient_min_cents': 4000,
                              'patient_max_cents': 4000,
                              'lines': [{'id': 'office', 'code': '99203', 'allowed_cents': 18000,
                                         'unit_allowed_cents': 18000,
                                         'calculation': {'patient_cents': 4000}}]},
              'arrival_collection': {'amount_cents': 4000}}
    case = {'previsit_context': {'inputs': INPUTS, 'source': SOURCE}}
    recheck_saved_inventory(result, case, 'synthetic-clinic',
                            {**PAYER, 'rules': {}})
    assert result['benefits']['network_status'] == 'unknown'
    assert result['price_range']['patient_min_cents'] is None
    assert result['price_range']['lines'][0]['calculation'] is None
    assert 'arrival_collection' not in result


def test_doctors_emg_choice_overrides_a_scheduled_code_or_reason_for_new_suggestions():
    booked = {'reason': 'EMG and nerve testing', 'lines': [
        {'code': '95907', 'service_status': 'scheduled'}]}
    assert is_emg_visit(booked) is True
    assert is_emg_visit({**booked, 'emg_required': False}) is False
    assert is_emg_visit({'reason': 'Office consultation', 'emg_required': True}) is True
