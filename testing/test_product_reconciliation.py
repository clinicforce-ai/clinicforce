"""Synthetic product and network evidence must not invent participation."""

import pytest
import hashlib
import json
from types import SimpleNamespace

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_pricing as pricing
from app.api.routers.commercial_flow import commercial_previsit_requirements as requirements
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_network_evidence
from app.api.routers.commercial_flow.commercial_previsit_status import _reprice_saved_new_network
from app.insurance import amount_explanation
from app.insurance import collection_guidance
from app.insurance import contract_requirements
from app.insurance import group_contract_support
from app.insurance import network_participation
from app.insurance.identity import patient_plan_applies


DAY = '2026-10-21'


def test_saved_requirements_refresh_when_network_projection_changes(monkeypatch):
    """A saved PPO decision must follow the newly supported signed network."""
    called = []
    monkeypatch.setattr(requirements, 'attach_requirements', lambda *args: called.append(args))
    api_stub = SimpleNamespace(staff_store=SimpleNamespace(list_library=lambda *_: []),
                               commercial_store=SimpleNamespace(list_connections=lambda *_: []))
    saved = {'benefits': {'network_status': 'unknown', 'network_source': None},
             'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    result = {'requirement_sources': [],
              'benefits': {'network_status': 'in_network', 'network_source': 'clinic_group_agreement'},
              'network_evidence': {'network_name': 'Blue Choice PPO'}}
    case = {'previsit_context': {'result': saved, 'inputs': {}, 'source': {}, 'payer_id': ''}}
    requirements.refresh_saved_requirements(api_stub, result, case, 'synthetic-practice')
    assert len(called) == 1


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
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [])
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


@pytest.mark.parametrize('payer_name', ['BCBS Texas', 'BCBSTX'])
def test_blue_plan_without_registered_contract_uses_out_of_network_tier(monkeypatch, payer_name):
    payer, _, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = payer_name
    source['payer_plan_names'] = ['Blue Medicare Advantage PPO']
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [])
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'plan_details': {'stedi': {'names': source['payer_plan_names']}},
              'codes': [], 'issues': [],
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['network_status'] == 'out_of_network'
    assert benefits['network_source'] == 'clinic_no_blue_contract'
    assert result['network_evidence']['decision'] == 'out_of_network_by_clinic_contract_record'
    assert network_participation.verified_out_of_network(benefits)


def test_undated_blue_contract_record_is_unresolved_not_absent(monkeypatch):
    payer, _, _, inputs, _ = setup_path(monkeypatch)
    payer['display_name'] = 'BCBSTX'
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [
        {'kind': 'contract', 'content_type': 'application/pdf', 'payer': 'BCBSTX'}])
    assert not group_contract_support.no_blue_contract('clinic', payer, inputs['service_date'])


@pytest.mark.parametrize('product', [
    'Blue Choice PPO', 'Blue Essentials HMO', 'FEP Blue Basic PSHB',
    'Blue Medicare Advantage PPO',
])
def test_current_signed_blue_agreement_covers_blue_medical_products(monkeypatch, product):
    payer, _, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'BCBS Texas'
    source['payer_plan_names'] = [product]
    document = {'id': 'blue-agreement', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'BCBS Texas', 'effective_from': '2026-01-01', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [{'text': 'Signed agreement. Attachment A: Blue Choice PPO network.'}]}})
    result = {'plan_details': {'stedi': {'names': [product]}}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown'}
    supported = group_contract_support.resolve('clinic', payer, inputs, source, result, benefits)
    assert supported['decision'] == 'in_network_contract_supported'
    assert supported['product'] == product
    assert supported['document_id'] == 'blue-agreement'


def test_saved_blue_out_of_network_result_tracks_new_clinic_contract(monkeypatch):
    payer, _, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'BCBS Texas'
    source['payer_plan_names'] = ['Blue Essentials HMO']
    document = {'id': 'blue-agreement', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'BCBS Texas', 'effective_from': '2026-01-01', 'sha256': 'digest'}
    documents = []
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: documents)
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [{'text': 'Signed agreement. Attachment A: Blue Choice PPO network.'}]}})
    result = {'plan_details': {'stedi': {'names': source['payer_plan_names']}},
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'out_of_network', 'network_source': 'clinic_no_blue_contract'},
              'network_evidence': {'decision': 'out_of_network_by_clinic_contract_record'},
              'issues': []}
    case = {'previsit_context': {'payer_id': 'payer-route', 'inputs': inputs, 'source': source}}
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['benefits']['network_status'] == 'out_of_network'
    documents.append(document)
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['benefits']['network_status'] == 'in_network'
    assert result['network_evidence']['document_id'] == 'blue-agreement'
    result['price_range'] = {'complete': True, 'patient_min_cents': 4000,
                             'patient_max_cents': 4000, 'lines': [{'allowed_cents': 10000}]}
    result['arrival_collection'] = {'amount_cents': 4000}
    documents.clear()
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['benefits']['network_status'] == 'out_of_network'
    assert result['price_range']['patient_min_cents'] is None
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    assert 'arrival_collection' not in result


def test_unbound_member_cannot_use_matching_clinic_contract(monkeypatch):
    payer, network, profile, inputs, source = setup_path(monkeypatch)
    source.update(payer_plan_bound=False, payer_plan_names=['Blue Choice PPO Basic'])
    profile['rules']['library_source']['sha256'] = 'digest'
    document = {'id': 'synthetic-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Signed example agreement', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: document)
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'codes': [], 'issues': []}
    benefits = {'active': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['decision'] == 'lacking_applicable_network_evidence'
    assert 'not linked this member product' in result['network_evidence']['reason']


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


@pytest.mark.parametrize('invalid', [None, 'missing_ecw_tin', 'missing_library_date', 'conflicting_library_date',
                                     'product', 'generic_ppo', 'reported_network', 'rendering', 'missing_rendering',
                                     'billing', 'tin', 'location', 'place_of_service', 'date', 'rule_expired',
                                     'document_hash', 'attachment'])
def test_signed_group_agreement_requires_member_network_and_provider_scope(monkeypatch, invalid):
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
    elif invalid == 'generic_ppo':
        source['payer_plan_names'] = ['MED/SURG - PPO']
    elif invalid == 'reported_network':
        source['payer_network_names'] = ['Other PPO']
    elif invalid == 'rendering':
        inputs['rendering_provider']['npi'] = '3333333333'
    elif invalid == 'missing_rendering':
        inputs['rendering_provider'].pop('npi')
    elif invalid == 'tin':
        source['billing_provider']['tin'] = '000000000'
    elif invalid == 'billing':
        source['billing_provider']['npi'] = '3333333333'
    elif invalid == 'missing_ecw_tin':
        source['billing_provider'].pop('tin', None)
        payer['display_name'] = 'Blue Cross Blue Shield Tx'
        document['payer'] = payer['display_name']
    elif invalid == 'missing_library_date':
        document.pop('effective_from')
        extracted['pages'][0]['text'] = r'Executed professional agreement. Effective Date of Agreement: \ed2\ \ed3\06/16/2026.'
        extracted['pages'].append({'text': 'ATTACHMENT A TEXAS PROVIDER NETWORKS. '
            'Authorized Signature: Blue Choice PPOSM Provider Network.'})
    elif invalid == 'conflicting_library_date':
        extracted['pages'][0]['text'] += ' Effective Date of Agreement: 06/17/2026.'
    elif invalid == 'location':
        source['location_id'] = 'other-office'
    elif invalid == 'place_of_service':
        inputs['place_of_service'] = '02'
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
    if invalid == 'date':
        assert benefits['network_status'] == 'out_of_network'
        assert benefits['network_source'] == 'clinic_no_blue_contract'
    elif invalid not in {None, 'missing_ecw_tin', 'missing_library_date',
                         'product', 'generic_ppo', 'reported_network', 'rule_expired'}:
        assert result['network_evidence']['decision'] != 'in_network_contract_supported'
        assert network_participation.outcome(benefits, result['network_evidence']) == 'UNRESOLVED'
    else:
        assert result['network_evidence']['decision'] == 'in_network_contract_supported'
        assert result['network_evidence']['network_name'] == 'Blue Choice PPO'
        assert result['network_evidence']['document_id'] == document['id']
        if invalid == 'missing_ecw_tin':
            assert result['network_evidence']['tin_source'] == 'scoped clinic network record'
        if invalid == 'missing_library_date':
            assert result['network_evidence']['agreement_effective_from'] == '2026-06-16'
            assert result['network_evidence']['agreement_effective_source'] == 'signed agreement text'
            assert result['network_evidence']['network_attachment_source'] == 'signed network page'
        assert network_participation.outcome(benefits, result['network_evidence']) == 'IN_NETWORK_CONTRACT_SUPPORTED'
    if result['network_evidence']['decision'] == 'in_network_contract_supported':
        assert benefits['network_status'] == 'in_network'
        assert benefits['network_source'] == 'clinic_group_agreement'
        assert network_participation.verified_in_network(benefits)
    elif invalid != 'date':
        assert benefits['network_status'] == 'unknown'


@pytest.mark.parametrize('missing', [None, 'local_payer_alias', 'home_plan', 'product', 'bluecard_clause',
                                     'rendering', 'tin', 'location', 'payer_network'])
def test_bluecard_ppo_group_agreement_uses_home_plan_and_exact_provider_scope(monkeypatch, missing):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'Blue Cross Blue Shield of Texas'
    source['payer_plan_names'] = ['MED/SURG - PPO']
    source['payer_verification_party'] = {'name': 'BCBS MINNESOTA', 'payer_id': 'synthetic-home',
                                          'evidence_path': 'plans[0].benefits.statuses[0].relatedEntities[0]'}
    if missing == 'home_plan':
        source.pop('payer_verification_party')
    elif missing == 'product':
        source['payer_plan_names'] = ['MED/SURG - HMO']
    elif missing == 'rendering':
        inputs['rendering_provider']['npi'] = '3333333333'
    elif missing == 'tin':
        source['billing_provider']['tin'] = '000000000'
    elif missing == 'location':
        source['location_id'] = 'other-office'
    elif missing == 'payer_network':
        source['payer_network_names'] = ['Other Network']
    attachment = ('ATTACHMENT A TEXAS PROVIDER NETWORKS\n'
                  'Authorized Signature:\nBlue Choice PPO Provider Network\n'
                  'Signed by example\n06/16/2026\nDate: signed electronically')
    clause = ('Executed professional agreement. 3.1.1 BlueCard. Provider acknowledges and agrees '
              'that Provider will provide or arrange for Covered Services to Covered Persons '
              'under the BlueCard Program.')
    if missing == 'bluecard_clause':
        clause = 'Executed professional agreement without a BlueCard participation clause.'
    document = {'id': 'bluecard-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Signed example agreement', 'payer': payer['display_name'],
                'effective_from': '2026-06-16', 'sha256': 'synthetic-digest'}
    if missing == 'local_payer_alias':
        document['payer'] = 'BCBS TX'
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'synthetic-digest', 'pages': [{'text': clause}, {'text': attachment}]}})
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'codes': [], 'issues': [],
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    if missing in {'bluecard_clause', 'rendering', 'tin', 'location'}:
        assert benefits['network_status'] == 'unknown'
        assert result['network_evidence']['decision'] == 'lacking_applicable_network_evidence'
    else:
        assert benefits['network_status'] == 'in_network'
        assert benefits['network_source'] == 'clinic_group_agreement'
        assert result['network_evidence']['product_network_source'] == (
            'clinic_blue_contract_policy' if missing == 'home_plan' else 'signed_bluecard_agreement')
        if missing != 'home_plan':
            assert result['network_evidence']['bluecard_clause_source'] == 'Page 1, BlueCard participation clause'
        assert result['network_evidence']['network_attachment_source'] == 'signed network page'
        result['benefits'] = benefits
        assert collection_guidance.build(result)['network_source'] == (
            'Clinic Blue contract policy and scoped provider-network record' if missing == 'home_plan' else
            'Signed Texas BlueCard agreement and scoped clinic provider-network record')


def test_verification_party_cannot_bind_a_different_plan_product():
    response = {'plans': [
        {'name': 'MED/SURG - PPO', 'benefits': {'statuses': [
            {'status': 'Active', 'service': {'system': 'STC', 'value': '30'}}]}},
        {'name': 'Other Medical Product', 'benefits': {'statuses': [
            {'status': 'Active', 'service': {'system': 'STC', 'value': '30'},
             'relatedEntities': [{'type': 'PARTY_PERFORMING_VERIFICATION',
                                  'name': {'organization': 'BCBS MINNESOTA'},
                                  'payerId': 'synthetic-home'}]}]}}]}
    assert api.commercial.plan_details(response)['verification_payer'] is None


@pytest.mark.parametrize('home_name', ['BCBS MINNESOTA', 'Blue Cross Blue Shield of Illinois'])
def test_blue_home_payer_route_uses_scoped_texas_bluecard_contract(monkeypatch, home_name):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = home_name
    local = {'id': 'local-texas', 'display_name': 'Blue Cross and Blue Shield of Texas',
             'stedi_payer_id': 'texas-route', 'status': 'staff_guidance_enabled'}
    network['payer_connection_id'] = local['id']
    source['payer_plan_names'] = ['MED/SURG - PPO']
    source.pop('payer_verification_party', None)
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer, local])
    document = {'id': 'texas-agreement', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'BCBS TX', 'effective_from': '2026-06-16', 'sha256': 'synthetic-digest'}
    extraction = {'sha256': 'synthetic-digest', 'pages': [
        {'text': 'Executed agreement. BlueCard. Provider acknowledges and agrees that '
                 'Provider will provide or arrange for Covered Services under the BlueCard Program.'},
        {'text': 'ATTACHMENT A TEXAS PROVIDER NETWORKS\nAuthorized Signature:\n'
                 'Blue Choice PPO Provider Network\nSigned by example\n06/16/2026\nDate: signed electronically'}]}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': extraction})
    result = {'plan_details': {'stedi': {'names': ['MED/SURG - PPO']}}}
    benefits = {'active': True, 'network_status': 'unknown'}
    supported = group_contract_support.resolve('clinic', payer, inputs, source, result, benefits)
    assert supported['decision'] == 'in_network_contract_supported'
    assert supported['home_plan'] == home_name
    assert supported['home_plan_evidence_path'] == 'Stedi eligibility payer route'
    assert supported['document_id'] == document['id']
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result.update(payer=home_name, codes=[], issues=[])
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['network_status'] == 'in_network'
    assert benefits['network_source'] == 'clinic_group_agreement'
    source['payer_plan_names'] = ['MED/SURG - HMO']
    result['plan_details']['stedi']['names'] = ['MED/SURG - HMO']
    assert group_contract_support.resolve('clinic', payer, inputs, source, result, benefits)['product'] == 'MED/SURG - HMO'


def test_blue_home_route_without_own_connection_uses_texas_agreement(monkeypatch):
    _, network, _, inputs, source = setup_path(monkeypatch)
    local = {'id': 'local-texas', 'display_name': 'BCBS Texas',
             'stedi_payer_id': 'texas-route', 'status': 'staff_guidance_enabled'}
    network['payer_connection_id'] = local['id']
    source['payer_plan_names'] = ['MED/SURG - PPO']
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [local])
    document = {'id': 'texas-agreement', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'BCBS Texas', 'effective_from': '2026-06-16', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [
            {'text': 'Executed agreement. BlueCard. Provider acknowledges and agrees that '
                     'Provider will provide or arrange for Covered Services under the BlueCard Program.'},
            {'text': 'ATTACHMENT A TEXAS PROVIDER NETWORKS\nAuthorized Signature:\n'
                     'Blue Choice PPO Provider Network\nSigned by example\n06/16/2026\nDate: signed electronically'}]}})
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': 'BCBS MINNESOTA', 'stedi_payer': {'name': 'BCBS MINNESOTA', 'id': 'home-route'},
              'plan_details': {'stedi': {'names': ['MED/SURG - PPO']}},
              'codes': [], 'issues': [],
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'home-route', benefits)
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['product_network_source'] == 'signed_bluecard_agreement'
    assert result['network_evidence']['home_plan'] == 'BCBS MINNESOTA'
    saved = {'stedi_payer': result['stedi_payer'], 'plan_details': result['plan_details'],
             'identity': result['identity'],
             'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown'},
             'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    case = {'previsit_context': {'inputs': inputs, 'source': source, 'payer_id': 'home-route'}}
    recheck_saved_network_evidence(saved, case, 'clinic', None)
    assert saved['benefits']['network_status'] == 'in_network'
    assert saved['network_evidence']['product_network_source'] == 'signed_bluecard_agreement'
    stale_route = {**result, 'stedi_payer': {'name': 'BCBS MINNESOTA', 'id': 'different-route'},
                   'issues': []}
    stale_benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(stale_route, 'clinic', inputs, source, 'home-route', stale_benefits)
    assert stale_benefits['network_status'] == 'unknown'


@pytest.mark.parametrize('invalid', ['fep', 'dental', 'wrong_home', 'wrong_network',
                                     'missing_texas_connection', 'wrong_contract', 'wrong_provider',
                                     'stale_product', 'inactive'])
def test_blue_home_route_does_not_extend_texas_ppo_agreement_beyond_scope(monkeypatch, invalid):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'BCBS MINNESOTA'
    local = {'id': 'local-texas', 'display_name': 'BCBS Texas',
             'status': 'staff_guidance_enabled'}
    network['payer_connection_id'] = local['id']
    source['payer_plan_names'] = ['MED/SURG - PPO']
    document = {'id': 'texas-agreement', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'BCBS Texas', 'effective_from': '2026-06-16', 'sha256': 'digest'}
    extraction = {'sha256': 'digest', 'pages': [
        {'text': 'Executed agreement. BlueCard. Provider acknowledges and agrees that '
                 'Provider will provide or arrange for Covered Services under the BlueCard Program.'},
        {'text': 'ATTACHMENT A TEXAS PROVIDER NETWORKS\nAuthorized Signature:\n'
                 'Blue Choice PPO Provider Network\nSigned by example\n06/16/2026\nDate: signed electronically'}]}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer, local])
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': extraction})
    if invalid == 'fep':
        source['payer_plan_names'] = ['Federal Employee PPO']
    elif invalid == 'dental':
        source['payer_plan_names'] = ['Dental PPO']
    elif invalid == 'wrong_home':
        source['payer_verification_party'] = {'name': 'BCBS Illinois', 'payer_id': 'other-home'}
    elif invalid == 'wrong_network':
        source['payer_network_names'] = ['BlueHPN']
    elif invalid == 'missing_texas_connection':
        monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer])
    elif invalid == 'wrong_contract':
        document['payer'] = 'BCBS Illinois'
    elif invalid == 'wrong_provider':
        inputs['rendering_provider']['npi'] = '3333333333'
    result = {'plan_details': {'stedi': {'names': source['payer_plan_names']}}}
    if invalid == 'stale_product':
        result['plan_details']['stedi']['identified_product'] = 'Different PPO'
    benefits = {'active': True, 'network_status': 'unknown'}
    if invalid == 'inactive':
        benefits['active'] = False
    supported = group_contract_support.resolve('clinic', payer, inputs, source, result, benefits)
    if invalid in {'fep', 'wrong_network'}:
        assert supported and supported['product_network_source'] == 'signed_bluecard_agreement'
    else:
        assert supported is None


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
    assert benefits['network_status'] == 'in_network'
    assert network_participation.verified_in_network(benefits)
    result['benefits'] = benefits
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] != 'provider_credentialing_unresolved'


def test_any_payer_contract_path_promotes_dated_member_network_to_in_network(monkeypatch):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'Example Health'
    network['network_name'] = 'Metro PPO'
    source['payer_plan_names'] = ['Example PPO Gold']
    source['payer_network_names'] = ['Metro PPO']
    document = {'id': 'signed-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                'payer': 'Example Health', 'effective_from': '2026-01-01', 'sha256': 'digest'}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': {
        'sha256': 'digest', 'pages': [{'text': 'Signed agreement. Attachment A: Metro PPO network.'}]}})
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
    benefits = {'active': True, 'network_status': 'unknown', 'service_date_matches': None,
                'coverage_evidence': {'status': 'ACTIVE', 'start': '2026-01-01', 'end': '2026-12-31'}}
    source['payer_plan_bound'] = patient_plan_applies(benefits, identity, inputs['service_date'])
    result = {'payer': payer['display_name'], 'codes': [], 'issues': [], 'identity': identity}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['network_status'] == 'in_network'
    assert benefits['network_source'] == 'clinic_group_agreement'
    assert result['network_evidence']['product_network_source'] == 'patient_bound_payer_network'
    assert network_participation.verified_in_network(benefits)
    benefits['service_date_matches'] = False
    assert not patient_plan_applies(benefits, identity, inputs['service_date'])
    benefits['service_date_matches'] = None
    benefits['coverage_evidence']['end'] = '2026-10-20'
    assert not patient_plan_applies(benefits, identity, inputs['service_date'])
    benefits['coverage_evidence']['end'] = '2026-12-31'
    source['payer_network_names'] = []
    assert group_contract_support.resolve('clinic', payer, inputs, source, result, benefits) is None


def test_reconciled_group_agreement_supplies_in_network_tier(monkeypatch):
    payer, network, profile, inputs, source = setup_path(monkeypatch)
    source['payer_plan_names'] = ['Preferred Provider Option Medical']
    network['evidence_reference'] = 'library:signed-group-contract'
    profile['rules']['library_source'] = {
        'document_id': 'signed-group-contract', 'reference': network['evidence_reference'],
        'sha256': 'synthetic-digest', 'effective_from': '2026-06-16',
        'origin': 'signed_group_agreement', 'locator': 'Page 2, signed network attachment'}
    document = {'id': 'signed-group-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                'label': 'Signed group agreement', 'payer': payer['display_name'],
                'sha256': 'synthetic-digest'}
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: document)
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *_: None)
    monkeypatch.setattr(pricing.inventory_certification, 'applicable', lambda *_: None)
    monkeypatch.setattr(api, '_previsit_range', lambda *_args, **_kwargs: ({'lines': [], 'complete': False}, []))
    result = {'payer': payer['display_name'], 'codes': [], 'issues': [],
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    benefits = {'active': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'payer-route', benefits)
    assert benefits['network_status'] == 'in_network'
    assert benefits['network_source'] == 'clinic_group_agreement'
    assert result['network_evidence']['decision'] == 'in_network_contract_supported'
    assert network_participation.outcome(benefits, result['network_evidence']) == 'IN_NETWORK_CONTRACT_SUPPORTED'
    assert network_participation.verified_in_network(benefits)
    inputs['service_date'] = '2026-06-15'
    assert not pricing._network_contract_evidence('clinic', network, profile, inputs['service_date']).get('document_id')


def test_group_agreement_allows_approved_copay_advance():
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                'network_source': 'clinic_group_agreement',
                'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    result = {'benefits': benefits, 'identity': benefits['identity'],
              'network_evidence': {'decision': 'in_network_contract_supported'},
              'service_selection_status': 'selected',
              'price_range': {'complete': True, 'patient_min_cents': 4000, 'patient_max_cents': 4000,
                              'lines': [{'code': '99213', 'units': 1,
                                         'benefits': {'active': True, 'copay_cents': 4000,
                                                      'copay_period': 'VISIT', 'deductible_applies': False,
                                                      'coinsurance_percent': '0'}}]},
              'arrival_collection': {'amount_cents': 4000, 'status': 'copay_only'},
              'collection_policy': {'mode': 'collect_verified_copay', 'reference': 'synthetic-rule',
                                    'approved_at': '2026-01-01', 'approved_by': 'synthetic-admin'}}
    guidance = collection_guidance.build(result)
    assert guidance['network'] == 'In network · clinic agreement'
    assert guidance['amount_cents'] == 4000
    result['collection_policy'] = {}
    assert collection_guidance.build(result)['amount_cents'] is None


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
              'benefits': {'active': True, 'service_date_matches': None, 'network_status': 'unknown',
                           'coverage_evidence': {'status': 'ACTIVE', 'start': '2026-01-01'}},
              'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['network_evidence']['decision'] == 'in_network_contract_supported'
    assert result['plan_details']['stedi']['identified_product'] == 'FEP Blue Basic PSHB'
    assert result['benefits']['network_status'] == 'in_network'
    assert network_participation.verified_in_network(result['benefits'])
    changed = {**case, 'previsit_context': {**case['previsit_context'], 'inputs': {
        **inputs, 'rendering_provider': {'npi': '3333333333'}}}}
    stale = {'plan_details': result['plan_details'], 'identity': result['identity'],
             'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown'},
             'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    recheck_saved_network_evidence(stale, changed, 'clinic', payer)
    assert stale['network_evidence']['decision'] != 'in_network_contract_supported'
    assert stale['benefits']['network_status'] == 'unknown'


def test_saved_bluecard_result_recovers_home_plan_from_retained_response(monkeypatch):
    payer, network, _, inputs, source = setup_path(monkeypatch)
    payer['display_name'] = 'Blue Cross Blue Shield of Texas'
    network['evidence_reference'] = 's3://synthetic/setup.json'
    document = {'id': 'synthetic-bluecard-contract', 'kind': 'contract',
                'content_type': 'application/pdf', 'label': 'Signed example agreement',
                'payer': payer['display_name'], 'effective_from': '2026-06-16', 'sha256': 'digest'}
    extraction = {'sha256': 'digest', 'pages': [
        {'text': 'Executed agreement. 3.1.1 BlueCard. Provider acknowledges and agrees '
                 'that Provider will provide or arrange for Covered Services under the BlueCard Program.'},
        {'text': 'ATTACHMENT A TEXAS PROVIDER NETWORKS\nAuthorized Signature:\n'
                 'Blue Choice PPO Provider Network\nSigned by example\n06/16/2026\nDate: signed electronically'}]}
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {**document, 'extraction': extraction})
    raw = json.dumps({'plans': [{'benefits': {'statuses': [{
        'status': 'ACTIVE', 'service': {'system': 'STC', 'value': '30'},
        'planCoverageDescription': 'MED/SURG - PPO', 'relatedEntities': [{
            'type': 'PARTY_PERFORMING_VERIFICATION', 'name': {'organization': 'BCBS MINNESOTA'},
            'payerId': 'synthetic-home'}]}]}}]}).encode()
    monkeypatch.setattr('app.api.routers.commercial_flow.commercial_previsit_network_guard.object_store.get_object_bytes',
                        lambda *_: raw)
    case = {'previsit_context': {'inputs': inputs, 'source': source},
            'previsit_raw_object_key': 'synthetic-protected-object',
            'previsit_raw_sha256': hashlib.sha256(raw).hexdigest()}
    result = {'plan_details': {'stedi': {'names': ['MED/SURG - PPO']}},
              'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'unknown'},
              'network_evidence': {'decision': 'lacking_applicable_network_evidence'}}
    recheck_saved_network_evidence(result, case, 'clinic', payer)
    assert result['plan_details']['stedi']['verification_payer']['name'] == 'BCBS MINNESOTA'
    assert result['network_evidence']['product_network_source'] == 'signed_bluecard_agreement'
    assert result['benefits']['network_status'] == 'in_network'


def test_saved_new_network_reprices_office_copay_from_protected_response(monkeypatch):
    from test_visit_estimate import payload as synthetic_payload

    raw_response = synthetic_payload()
    raw_response['benefitsInformation'].append({
        'code': 'B', 'serviceTypeCodes': ['98'], 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'Y', 'timeQualifierCode': '27', 'benefitAmount': '30'})
    raw = json.dumps(raw_response).encode()
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *_: raw)
    monkeypatch.setattr(api, '_previsit_practice_path',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            api.HTTPException(409, 'No exact CPT fee schedule')))
    identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
    benefits = {**api.commercial.normalize_stedi_271(raw_response, '98', True),
                'identity': identity, 'network_status': 'in_network',
                'network_source': 'clinic_group_agreement'}
    case = {'previsit_context': {'inputs': {'service_date': DAY,
                'place_of_service': '11', 'rendering_provider': {'npi': '2222222222'}},
            'source': {'plan': 'CI', 'billing_provider': {'npi': '1111111111'},
                       'location_id': 'office'}, 'payer_id': 'payer-route'},
            'previsit_raw_object_key': 'synthetic-protected-object',
            'previsit_raw_sha256': hashlib.sha256(raw).hexdigest()}
    result = {'benefits': benefits, 'identity': identity,
              'plan_details': {'stedi': {'names': ['Choice Medical']}},
              'codes': [{'id': 'office', 'code': '99203', 'units': 1}],
              'price_range': {'lines': []}, 'arrival_collection': {'amount_cents': None}}
    _reprice_saved_new_network(result, case, 'clinic', {'id': 'payer'})
    assert result['price_range']['lines'][0]['benefits']['copay_cents'] == 3000
    assert 'arrival_collection' not in result


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
