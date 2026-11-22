"""Clinic roster resolves exact member products without claiming payer confirmation."""
from copy import deepcopy

from app.api.routers.commercial_flow import commercial_previsit_network_guard as guard
from app.api.routers.commercial_flow import commercial_practice
from app.api.routers.commercial_flow.commercial_previsit_network_guard import apply_nmct_product_attestation
from app.insurance import commercial_store, network_participation, network_product_roster


DAY = '2026-11-18'
NPI = '1234567893'


def case():
    result = {'payer': 'Blue Cross and Blue Shield of Texas', 'issues': []}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'unknown',
                'network_source': None, 'identity': {'status': 'matched', 'binding': 'test', 'match_version': 2}}
    inputs = {'service_date': DAY, 'place_of_service': '11', 'rendering_provider': {'npi': NPI}}
    source = {'payer_plan_bound': True, 'payer_plan_names': ['BlueChoice PPO'],
              'payer_network_names': [], 'rendering_provider': {'npi': NPI},
              'location_id': 'office-1', 'billing_provider': {}}
    return result, benefits, inputs, source


def test_user_list_product_matches_current_member_and_reports_clinic_source(monkeypatch):
    row = {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'}
    seen = []
    def matching(practice, payer, product, day):
        seen.append((practice, payer, product, day))
        return row if payer == 'bcbstx' and product == 'bluechoiceppo' else None
    monkeypatch.setattr(commercial_store, 'matching_network_product', matching)
    result, benefits, inputs, source = case()
    assert apply_nmct_product_attestation(result, benefits, network_participation.NMCT_PRACTICE_ID,
        result['payer'], None, inputs, source, {'id': 'synthetic-current-contract'})
    assert benefits['network_status'] == 'in_network'
    assert benefits['network_source'] == 'clinic_product_roster'
    assert network_participation.verified_in_network(benefits)
    assert result['network_evidence']['reference'] == row['source_reference']
    assert 'insurer did not separately confirm' in result['network_evidence']['reason']
    assert seen == [(network_participation.NMCT_PRACTICE_ID, 'bcbstx', 'bluechoiceppo', DAY)]


def test_roster_requires_identified_active_product_and_selected_nmct_context(monkeypatch):
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_:
        {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'})
    base = case()
    for change in ('other_product', 'conflicting_products', 'inactive', 'unbound', 'wrong_practice', 'wrong_provider', 'no_office'):
        result, benefits, inputs, source = deepcopy(base)
        practice = network_participation.NMCT_PRACTICE_ID
        if change == 'other_product':
            source['payer_plan_names'] = ['Blue Choice PPO Premium']
            monkeypatch.setattr(commercial_store, 'matching_network_product', lambda _p, _k, product, _d:
                {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'}
                if product == 'bluechoiceppo' else None)
        elif change == 'conflicting_products': source['payer_plan_names'].append('Blue Advantage HMO')
        elif change == 'inactive': benefits['active'] = False
        elif change == 'unbound': source['payer_plan_bound'] = False
        elif change == 'wrong_practice': practice = 'other-clinic'
        elif change == 'wrong_provider': source['rendering_provider']['npi'] = '9876543210'
        elif change == 'no_office': source['location_id'] = ''
        assert not network_product_roster.apply(result, benefits, practice, result['payer'], inputs, source), change
        assert benefits['network_status'] != 'in_network'


def test_opposing_provider_statement_is_shown_as_a_conflict(monkeypatch):
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_:
        {'product_name': 'Blue Choice PPO℠ [BCA]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'})
    result, benefits, inputs, source = case()
    benefits.update(network_status='out_of_network', network_source='payer_participation_message')
    assert not network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
                                            result['payer'], inputs, source)
    assert benefits['network_status'] == 'unknown'
    assert benefits['network_conflict'] is True
    assert result['network_evidence']['decision'] == 'conflicting_network_evidence'


def test_roster_requires_returned_provider_to_match_selected_billing_or_rendering_npi(monkeypatch):
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_:
        {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'})
    result, benefits, inputs, source = case()
    assert not network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
        result['payer'], inputs, source, {'provider': {'npi': '9876543210'}})
    source['billing_provider']['npi'] = '9876543210'
    assert network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
        result['payer'], inputs, source, {'provider': {'npi': '9876543210'}})


def test_exact_roster_keys_cover_all_payer_families_and_card_logo_forms():
    names = {'Cigna': 'cigna', 'Aetna': 'aetna', 'Blue Cross and Blue Shield of Texas': 'bcbstx',
             'UnitedHealthcare': 'uhc', 'Wellpoint TX': 'wellpoint', 'HealthSmart': 'healthsmart',
             'MultiPlan Network': 'multiplan', 'Humana Medicare': 'humana'}
    assert {name: network_product_roster.payer_key(name) for name in names} == names
    assert network_product_roster.payer_key('Health Plans Incorporated', ['PHCS']) == 'multiplan'
    assert network_product_roster.product_key('Blue Choice PPO℠ [BCS]') == 'bluechoiceppo'
    assert network_product_roster.product_key('Blue Choice PPO℠ [BCA]') == 'bluechoiceppo'
    assert network_product_roster.product_key('POS CHOICE PLUS') == 'choiceplus'
    assert network_product_roster.product_key('PHCS (Logo on front of card)') == 'phcs'
    assert network_product_roster.product_key('PHCS (Logo on back of card)') == 'phcs'


def test_roster_can_use_any_shared_product_word_except_insurer_name(monkeypatch):
    rows = [
        {'product_name': 'Aetna Gold Plus', 'source_reference': 'clinic-roster'},
        {'product_name': 'Aetna Choice POS II (Open Access)', 'source_reference': 'clinic-roster'},
    ]
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_: None)
    monkeypatch.setattr(commercial_store, 'list_network_products', lambda *_: rows)
    result, benefits, inputs, source = case()
    result['payer'] = 'Aetna'
    source['payer_plan_names'] = ['Aetna Gold Select']
    source['payer_plan_bound'] = False  # A payer date echo must not erase the patient-bound product.
    benefits['service_date_matches'] = False
    benefits['coverage_evidence'] = {'status': 'ACTIVE', 'start': '2026-01-01'}
    assert network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
                                        'Aetna', inputs, source)
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['match_kind'] == 'shared_product_word'
    assert result['network_evidence']['matched_words'] == ['gold']
    assert not network_product_roster.matches_contract_network(
        {'practice_id': network_participation.NMCT_PRACTICE_ID, 'network_name': 'Aetna Gold Plus',
         'status': 'verified', 'plan_matcher': 'Gold Plus'}, source, 'Aetna', DAY)


def test_roster_shared_word_does_not_match_insurer_only_or_unbound_patient(monkeypatch):
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_: None)
    monkeypatch.setattr(commercial_store, 'list_network_products', lambda *_:
                        [{'product_name': 'Aetna Gold Plus', 'source_reference': 'clinic-roster'}])
    result, benefits, inputs, source = case()
    result['payer'] = 'Aetna'
    source['payer_plan_names'] = ['Aetna Silver HMO']
    assert not network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
                                            'Aetna', inputs, source)
    source['payer_plan_names'] = ['Aetna Gold Select']
    benefits['identity'] = {'status': 'review', 'match_version': 2}
    assert not network_product_roster.apply(result, benefits, network_participation.NMCT_PRACTICE_ID,
                                            'Aetna', inputs, source)


def test_saved_aetna_plan_uses_shared_word_even_with_payer_date_echo(monkeypatch):
    monkeypatch.setattr(guard.staff_store, 'list_library', lambda *_: [])
    monkeypatch.setattr(guard, 'scoped_admin_nonparticipation', lambda *_: None)
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_: None)
    monkeypatch.setattr(commercial_store, 'list_network_products', lambda *_:
                        [{'product_name': 'Open Choice PPO', 'source_reference': 'clinic-roster'}])
    result, benefits, inputs, source = case()
    result['payer'] = 'Aetna'
    benefits.update(service_date_matches=False,
                    coverage_evidence={'status': 'ACTIVE', 'start': '2026-01-01'})
    source.update(payer_plan_bound=False, payer_plan_names=['BP Medicare PPO ESA'])
    result.update(benefits=benefits, plan_details={'stedi': {'names': source['payer_plan_names']}},
                  price_range={'complete': False, 'lines': []})
    saved = {'previsit_context': {'inputs': inputs, 'source': source}}
    guard.recheck_saved_network_evidence(result, saved, network_participation.NMCT_PRACTICE_ID, None)
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['match_kind'] == 'shared_product_word'
    assert result['network_evidence']['matched_words'] == ['ppo']
    benefits['coverage_evidence']['end'] = '2026-11-17'
    guard.recheck_saved_network_evidence(result, saved, network_participation.NMCT_PRACTICE_ID, None)
    assert benefits['network_status'] == 'unknown'


def test_saved_roster_tier_is_cleared_when_product_is_revoked(monkeypatch):
    monkeypatch.setattr(guard.staff_store, 'list_library', lambda *_: [])
    monkeypatch.setattr(guard, 'scoped_admin_nonparticipation', lambda *_: None)
    result, benefits, inputs, source = case()
    benefits.update(network_status='in_network', network_source='clinic_product_roster')
    result.update(benefits=benefits, plan_details={'stedi': {'names': ['BlueChoice PPO']}},
                  price_range={'complete': True, 'patient_min_cents': 1000, 'lines': []})
    saved = {'previsit_context': {'inputs': inputs, 'source': source}}
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_:
        {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'})
    guard.recheck_saved_network_evidence(result, saved, network_participation.NMCT_PRACTICE_ID, None)
    assert benefits['network_status'] == 'in_network'
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_: None)
    guard.recheck_saved_network_evidence(result, saved, network_participation.NMCT_PRACTICE_ID, None)
    assert benefits['network_status'] == 'unknown'
    assert result['price_range']['patient_min_cents'] is None
    assert 'no longer lists' in result['network_evidence']['reason']


def test_confirmed_product_uses_only_same_named_provider_contract_rate_scope(monkeypatch):
    payer = {'id': 'bcbs-route', 'display_name': 'Blue Cross and Blue Shield of Texas',
             'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'network', 'practice_id': network_participation.NMCT_PRACTICE_ID,
               'payer_connection_id': payer['id'], 'status': 'verified', 'network_name': 'Blue Choice PPO',
               'billing_npi': '1111111111', 'rendering_npi': NPI, 'location_id': 'office-1',
               'place_of_service': '11', 'plan_matcher': 'CI', 'effective_from': '2026-01-01'}
    profile = {'payer_connection_id': payer['id'], 'provider_network_id': network['id'],
               'status': 'staff_guidance_enabled', 'plan_matcher': 'CI', 'rules': {}}
    monkeypatch.setattr(commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(commercial_store, 'list_profiles', lambda *_: [profile])
    monkeypatch.setattr(commercial_store, 'matching_network_product', lambda *_:
        {'product_name': 'Blue Choice PPO℠ [BCS]', 'source_reference': 'clinic-owner-confirmed-2026-11-18'})
    inputs = {'payer_id': payer['id'], 'service_date': DAY, 'rendering_provider': {'npi': NPI},
              'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1111111111'}, 'rendering_provider': {'npi': NPI},
              'location_id': 'office-1', 'plan': 'BlueChoice PPO', 'payer_plan_bound': True,
              'payer_plan_names': ['BlueChoice PPO']}
    assert commercial_practice._practice_path(network_participation.NMCT_PRACTICE_ID, inputs, source)[2]['rate_plan_matcher'] == 'CI'
    source['payer_plan_names'] = ['Blue Choice PPO Premium']
    source['plan'] = source['payer_plan_names'][0]
    try:
        commercial_practice._practice_path(network_participation.NMCT_PRACTICE_ID, inputs, source)
    except Exception as exc:
        assert 'no single verified provider-network' in str(exc)
    else:
        raise AssertionError('An unlisted product used the clinic rate scope')
