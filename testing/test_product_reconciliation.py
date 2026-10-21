"""Synthetic product and network evidence must not invent participation."""

import pytest
from types import SimpleNamespace

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_pricing as pricing
from app.api.routers.commercial_flow import commercial_previsit_requirements as requirements
from app.insurance import amount_explanation
from app.insurance import contract_requirements


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
