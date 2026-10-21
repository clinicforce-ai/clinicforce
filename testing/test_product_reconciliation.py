"""Synthetic product and network evidence must not invent participation."""

import pytest
import hashlib
import json
from types import SimpleNamespace

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_pricing as pricing
from app.api.routers.commercial_flow import commercial_previsit_requirements as requirements
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_network_evidence
from app.insurance import amount_explanation
from app.insurance import contract_requirements
from app.insurance import network_participation


DAY = '2026-10-21'


def setup_path(monkeypatch, mappings=None):
    payer = {'id': 'payer', 'display_name': 'Example Blue', 'category': 'commercial_medical',
             'status': 'staff_guidance_enabled', 'stedi_payer_id': 'payer-route'}
    network = {'id': 'network', 'payer_connection_id': 'payer', 'status': 'verified',
               'network_name': 'Blue Choice PPO', 'billing_npi': '1111111111',
               'rendering_npi': '2222222222', 'location_id': 'office', 'place_of_service': '11',
               'tin': '123456789', 'plan_matcher': 'CI', 'effective_from': '2026-01-01',
               'evidence_reference': 'library:synthetic-contract'}
    profile = {'id': 'profile', 'payer_connection_id': 'payer', 'provider_network_id': 'network',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'CI',
               'rules': {'plan_product_names': ['Blue Choice PPO Basic'],
                         'library_source': {'document_id': 'synthetic-contract',
                                            'reference': 'library:synthetic-contract'},
                         'product_mappings': mappings or []}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer])
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [profile])
    inputs = {'payer_id': 'payer', 'service_date': DAY, 'rendering_provider': {'npi': '2222222222'},
              'place_of_service': '11'}
    scope = {'plan_number': None, 'group_number': 'SYNTHETIC-GROUP'}
    source = {'billing_provider': {'npi': '1111111111', 'tin': '123456789'},
              'location_id': 'office', 'plan': 'CI', 'payer_plan_names': ['BASIC'],
              'payer_plan_bound': True, 'payer_plan_scope': scope}
    return payer, network, profile, inputs, source


def approved_mapping(**changes):
    return {'products': ['BASIC'], 'contract_product': 'Blue Choice PPO Basic',
            'rate_plan_matcher': 'CI', 'network_identifiers': ['BCA'],
            'reference': 'Synthetic payer and contract confirmation', 'actor_id': 'synthetic-admin',
            'valid_from': '2026-01-01', 'valid_to': '2026-12-31',
            'network_id': 'network', 'network_reference': 'library:synthetic-contract',
            'plan_scope': {'plan_number': None, 'group_number': 'SYNTHETIC-GROUP'},
            **changes}


def test_unmapped_basic_keeps_coverage_without_false_participation_conflict(monkeypatch):
    _, _, _, inputs, source = setup_path(monkeypatch)
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': 'BCBS Texas', 'codes': [], 'issues': [], 'service_selection_status': 'preview',
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown',
                'network_conflict': False}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['active'] is True
    assert benefits['network_conflict'] is False
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['decision'] == 'lacking_applicable_network_evidence'
    assert result['network_evidence']['reason_code'] == 'product_link_missing'
    result['benefits'] = benefits
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] == 'clinic_product_mismatch'
    assert explanation['sentence'] == (
        "BCBS Texas coverage is active, but ClinicForce has not yet linked this member's exact plan to "
        "NMCT's verified provider network. The insurance amount cannot be finalized until the "
        'plan/network match and applicable service benefits are established.')


def test_approved_basic_alias_keeps_contract_rate_scope_and_accepts_bca(monkeypatch):
    _, network, profile, inputs, source = setup_path(monkeypatch, [approved_mapping()])
    source['payer_network_names'] = ['BCA']
    _, selected, priced = api._practice_path('clinic', inputs, source)
    assert selected == network
    assert priced['rate_plan_matcher'] == 'CI'
    assert priced['require_exact_rate_plan'] is True
    assert pricing._product_mapping_evidence(profile, network, source, DAY)['reference'] == approved_mapping()['reference']


@pytest.mark.parametrize('change', [
    {'valid_to': '2026-10-20'},
    {'plan_scope': {'plan_number': None, 'group_number': 'OTHER-GROUP'}},
    {'network_reference': 'another-contract'},
    {'contract_product': 'Another Product'},
])
def test_basic_alias_requires_valid_contract_and_member_scope(monkeypatch, change):
    _, _, _, inputs, source = setup_path(monkeypatch, [approved_mapping(**change)])
    with pytest.raises(api.HTTPException):
        api._practice_path('clinic', inputs, source)


def test_ambiguous_basic_aliases_do_not_select_a_network(monkeypatch):
    second = approved_mapping(contract_product='Another Basic Product')
    _, _, profile, inputs, source = setup_path(monkeypatch, [approved_mapping(), second])
    profile['rules']['plan_product_names'].append('Another Basic Product')
    with pytest.raises(api.HTTPException):
        api._practice_path('clinic', inputs, source)


def test_out_of_state_or_mismatched_network_identifier_does_not_inherit_texas_rate(monkeypatch):
    _, _, _, inputs, source = setup_path(monkeypatch, [approved_mapping()])
    for network_name in ('OTHER', 'BlueCard Out-of-State'):
        with pytest.raises(api.HTTPException):
            api._practice_path('clinic', inputs, {**source, 'payer_network_names': [network_name]})
    _, _, _, inputs, source = setup_path(monkeypatch, [approved_mapping(
        network_identifiers=['BlueCard Out-of-State'])])
    with pytest.raises(api.HTTPException, match='home-plan'):
        api._practice_path('clinic', inputs,
                           {**source, 'payer_network_names': ['BlueCard Out-of-State']})


def test_actual_provider_participation_conflict_keeps_its_own_reason():
    result = {'payer': 'BCBS Texas', 'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown',
                           'network_conflict': True}, 'issues': [], 'codes': []}
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] == 'provider_network_conflict'


def test_alias_does_not_bypass_contract_tin(monkeypatch):
    _, _, _, inputs, source = setup_path(monkeypatch, [approved_mapping()])
    source['billing_provider']['tin'] = '000000000'
    with pytest.raises(api.HTTPException):
        api._practice_path('clinic', inputs, source)


def test_identified_fep_basic_cannot_use_blue_choice_basic_ci_contract(monkeypatch):
    _, _, _, inputs, source = setup_path(monkeypatch, [approved_mapping()])
    source['payer_plan_names'] = ['FEP Blue Basic PSHB']
    source['payer_plan_scope'] = {'plan_number': None, 'group_number': '33A'}
    with pytest.raises(api.HTTPException, match='exact payer product'):
        api._practice_path('clinic', inputs, source)


@pytest.mark.parametrize('invalid', [None, 'product', 'reported_network', 'rendering', 'tin',
                                     'location', 'date', 'rule_expired', 'document_hash', 'attachment'])
def test_fep_group_agreement_is_visible_without_claiming_clinician_participation(monkeypatch, invalid):
    payer, network, profile, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'Blue Cross Blue Shield of Texas'
    network['evidence_reference'] = 's3://synthetic/clinic-setup.json'
    profile['rules']['library_source'] = {}
    source['payer_plan_names'] = ['FEP Blue Basic PSHB']
    source['payer_plan_scope'] = {'plan_number': None, 'group_number': '33A'}
    document = {'id': 'signed-group-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Signed professional provider agreement', 'payer': payer['display_name'],
                'effective_from': '2026-06-16', 'effective_to': None, 'sha256': 'synthetic-digest'}
    extracted = {'sha256': 'synthetic-digest', 'pages': [
        {'text': 'Executed group professional provider agreement. Attachment A: Blue Choice PPO network. '
                 'Group provider arrangement and individual credentialing requirements.'}]}
    if invalid == 'product':
        source['payer_plan_names'] = ['Different Basic Product']
    elif invalid == 'reported_network':
        source['payer_network_names'] = ['Other PPO']
    elif invalid == 'rendering':
        inputs['rendering_provider']['npi'] = '3333333333'
    elif invalid == 'tin':
        source['billing_provider']['tin'] = '000000000'
    elif invalid == 'location':
        source['location_id'] = 'other-office'
    elif invalid == 'date':
        document['effective_to'] = '2026-10-20'
    elif invalid == 'rule_expired':
        inputs['service_date'] = '2027-01-01'
    elif invalid == 'document_hash':
        extracted['sha256'] = 'changed'
    elif invalid == 'attachment':
        extracted['pages'][0]['text'] = 'Executed agreement. Attachment A: Another PPO network.'
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': extracted})
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'plan_details': {'stedi': {
                  'identified_product': source['payer_plan_names'][0]}},
              'codes': [], 'issues': [], 'service_selection_status': 'preview',
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown',
                'network_conflict': False}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    if invalid:
        assert result['network_evidence']['decision'] != 'in_network_contract_supported'
        assert network_participation.outcome(benefits, result['network_evidence']) == 'UNRESOLVED'
    else:
        assert result['network_evidence']['decision'] == 'in_network_contract_supported'
        assert result['network_evidence']['network_name'] == 'Blue Choice PPO'
        assert result['network_evidence']['document_id'] == document['id']
        assert network_participation.outcome(benefits, result['network_evidence']) == 'IN_NETWORK_CONTRACT_SUPPORTED'
    assert benefits['network_status'] == 'unknown'
    assert benefits.get('network_source') != 'clinic_library_contract'


def test_patient_bound_network_name_supports_group_contract_without_rate_match(monkeypatch):
    payer, network, profile, inputs, source = setup_path(monkeypatch)
    network['evidence_reference'] = 's3://synthetic/group-setup.json'
    profile['rules']['library_source'] = {}
    source['payer_plan_names'] = ['Example PPO Gold']
    source['payer_network_names'] = ['Blue Choice PPO']
    document = {'id': 'group-document', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Example group agreement', 'payer': payer['display_name'],
                'effective_from': '2026-01-01', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [{'text': 'Signed group agreement. Attachment A: Blue Choice PPO.'}]}})
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'codes': [], 'issues': [],
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert result['network_evidence']['decision'] == 'in_network_contract_supported'
    assert result['network_evidence']['product_network_source'] == 'patient_bound_payer_network'
    assert benefits['network_status'] == 'unknown'
    result['benefits'] = benefits
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] == 'provider_credentialing_unresolved'


def test_saved_result_reuses_current_group_contract_without_payer_transaction(monkeypatch):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'BCBS Texas'
    network['evidence_reference'] = 's3://synthetic/setup.json'
    document = {'id': 'group-document', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Signed group agreement', 'payer': payer['display_name'],
                'effective_from': '2026-01-01', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [{'text': 'Executed agreement. Attachment A: Blue Choice PPO.'}]}})
    raw = json.dumps({'plans': [{'name': 'BASIC'}],
                      'subscriber': {'memberId': 'R-SYNTHETIC', 'groupNumber': '33A'},
                      'x12': 'fepblue.org'}).encode()
    monkeypatch.setattr('app.api.routers.commercial_flow.commercial_previsit_network_guard.object_store.get_object_bytes',
                        lambda *_: raw)
    case = {'previsit_context': {'inputs': inputs, 'source': source},
            'previsit_raw_object_key': 'synthetic-protected-object',
            'previsit_raw_sha256': hashlib.sha256(raw).hexdigest()}
    result = {'plan_details': {'stedi': {'names': ['BASIC']}},
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown'},
              'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['network_evidence']['decision'] == 'in_network_contract_supported'
    assert result['plan_details']['stedi']['identified_product'] == 'FEP Blue Basic PSHB'
    assert result['benefits']['network_status'] == 'unknown'
    changed = {**case, 'previsit_context': {**case['previsit_context'], 'inputs': {
        **inputs, 'rendering_provider': {'npi': '3333333333'}}}}
    stale = {'plan_details': result['plan_details'], 'identity': result['identity'],
             'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown'},
             'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    recheck_saved_network_evidence(stale, changed, 'clinic', payer)
    assert stale['network_evidence']['decision'] == 'lacking_applicable_network_evidence'


def test_requirement_context_uses_only_approved_contract_product():
    result = {'payer': 'BCBS Texas', 'benefit_source': 'Stedi',
              'codes': [{'id': 'office', 'code': '99213', 'units': 1}],
              'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}},
              'network_evidence': {'network_name': 'Blue Choice PPO',
                                   'product_mapping_approval': {'contract_product': 'Blue Choice PPO Basic'}},
              'insurance_details': {'group_number': 'SYNTHETIC', 'member_id': 'SYNTHETIC'},
              'plan_details': {'stedi': {'names': ['BASIC'], 'group_number': 'SYNTHETIC'}}}
    service = SimpleNamespace(staff_store=SimpleNamespace(get_library=lambda *_: None),
                              contract_requirements=contract_requirements,
                              _requirement_candidates=lambda *_: [])
    source = {'plan': 'CI', 'plan_details': {'ecw': ['CI']}, 'billing_provider': {'npi': '1111111111'}}
    inputs = {'service_date': DAY, 'rendering_provider': {'npi': '2222222222'},
              'place_of_service': '11'}
    session = {'practice_id': 'clinic'}
    connections = [{'stedi_payer_id': 'route', 'display_name': 'BCBS Texas'}]
    requirements.attach_requirements(service, result, {}, inputs, source, session, connections, 'route')
    assert result['requirement_evidence']['referral']['status'] == 'Not required'
    assert result['requirement_evidence']['authorization']['status'] == 'Verification required'
    result['network_evidence'].pop('product_mapping_approval')
    requirements.attach_requirements(service, result, {}, inputs, source, session, connections, 'route')
    assert result['requirement_evidence']['referral']['status'] == 'Verification required'
