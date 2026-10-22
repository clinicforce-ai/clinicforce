"""Synthetic regressions for the result-first workflow; no live payer traffic."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from app.api.routers import commercial_estimator as api, staff
from app.api.routers.commercial_flow.commercial_previsit_catalog import build_catalog
from app.api.routers.commercial_flow.commercial_previsit_finish import _payer_plan_pricing_source
from app.api.routers.commercial_flow.commercial_previsit_pricing import _previsit_range
from app.insurance import commercial, ecw_backend, identity, visit_estimate

DAY = date.today().isoformat()


def policy():
    return {'coverage_id': 'policy-a', 'payer_id': 'payer-a', 'member_id': 'ABC123', 'active_coverages': 1,
            'patient_first': 'Alex', 'patient_last': 'Morgan', 'patient_birth_date': '2001-02-03',
            'subscriber_first': 'Sam', 'subscriber_last': 'Other', 'relationship': '19'}


def test_repeated_cash_schedule_rows_offer_one_choice_per_cpt(monkeypatch):
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *_: {'rows': [
        {'code': '99203', 'description': 'Office visit', 'price_kind': 'unit'},
        {'code': '99203', 'description': 'Office visit', 'price_kind': 'unit'},
    ]})
    _, _, _, available, _ = build_catalog(
        api, {}, {'practice_id': 'synthetic-clinic'},
        {'service_date': DAY, 'reason': 'consultation'}, {}, None, {}, '', None,
    )
    assert [row['code'] for row in available].count('99203') == 1


def test_skin_biopsy_catalog_offers_first_and_additional_site_codes(monkeypatch):
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *_: None)
    _, _, _, available, _ = build_catalog(
        api, {}, {'practice_id': 'synthetic-clinic'},
        {'service_date': DAY, 'reason': 'Skin Biopsy', 'visit_type': 'established'},
        {}, None, {}, '', None,
    )
    assert {row['code'] for row in available} >= {'11104', '11105'}
    assert next(row for row in available if row['code'] == '11105')['service_group'] == 'other'


def response(last='Morgan'):
    base = {'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y', 'serviceTypeCodes': ['30']}
    return {'subscriber': {'memberId': 'ABC123', 'firstName': 'Sam', 'lastName': 'Other'},
            'dependent': {'firstName': 'Alex', 'lastName': last, 'dateOfBirth': '2001-02-03', 'relationToSubscriber': 'CHILD'},
            'benefitsInformation': [{**base, 'code': '1', 'planCoverageDescription': 'Choice Medical'},
                {**base, 'code': 'C', 'timeQualifierCode': '29', 'benefitAmount': '0'},
                {**base, 'code': 'G', 'timeQualifierCode': '29', 'benefitAmount': '1000'},
                {**base, 'code': 'B', 'timeQualifierCode': '7', 'benefitAmount': '40',
                 'authOrCertIndicator': 'N', 'additionalInformation': [{'description': 'No referral required'}]}]}


def test_dependent_patient_is_compared_separately_from_policyholder():
    result = identity.compare(response(), policy())
    assert result['status'] == 'matched' and not result['policy_differences']
    assert not any('identity' in r for r in api._previsit_benefits(response(), policy(), DAY)['review_reasons'])
    raw = response('Mór-gan')
    raw['dependent']['dateOfBirth'] = '20010203'
    assert identity.compare(raw, policy())['status'] == 'matched'


def test_missing_payer_member_or_patient_anchors_cannot_bind_benefits():
    raw = response()
    raw['subscriber'].pop('memberId')
    check = identity.compare(raw, policy())
    assert check['status'] == 'review' and check['missing_fields'] == ['member_id']
    raw = response()
    raw['dependent'].pop('firstName')
    raw['dependent'].pop('dateOfBirth')
    check = identity.compare(raw, policy())
    assert check['status'] == 'review' and check['missing_fields'] == ['patient_demographics']
    raw = response()
    raw['dependent'].pop('relationToSubscriber')
    check = identity.compare(raw, policy())
    assert check['status'] == 'review' and check['missing_fields'] == ['relationship']


def test_validated_ecw_saved_benefits_bind_by_patient_and_coverage_reference():
    from test_ecw_benefits import saved_response
    raw = {'_ecw_response': saved_response()}
    coverage = {'patient_id': 'p', 'coverage_id': 'coverage-1'}
    assert identity.compare(raw, coverage)['status'] == 'matched'
    assert identity.compare(raw, {**coverage, 'patient_id': 'other'})['status'] == 'review'
    assert identity.compare(raw, {**coverage, 'coverage_id': 'other'})['status'] == 'review'


def test_name_resolution_is_bound_to_patient_policy_and_payer_not_visit():
    raw = response('Former')
    current = identity.compare(raw, policy())
    assert current['status'] == 'warning' and current['can_confirm']
    evidence = {'binding': current['binding'], 'reference': 'Payer call 123', 'actor_id': 'reviewer', 'verified_at': DAY}
    assert identity.compare(raw, {**policy(), 'appointment_id': 'next'}, evidence)['status'] == 'reconciled'
    assert identity.compare(raw, {**policy(), 'patient_first': ' ALEX '}, evidence)['status'] == 'reconciled'
    for key, value in [('member_id', 'DIFFERENT'), ('patient_birth_date', '2002-02-03'), ('payer_id', 'other')]:
        changed = identity.compare(raw, {**policy(), key: value}, evidence)
        assert changed['status'] != 'reconciled'
    assert not identity.compare(raw, {**policy(), 'member_id': ''})['can_confirm']
    public = staff.public({'patient': {'id': 'p', 'insurance_identities': {'private': evidence}}})
    assert public['patient'] == {'id': 'p'}


def test_true_duplicates_resolve_but_hidden_restrictions_remain_distinct():
    raw = response()
    raw['benefitsInformation'].append(deepcopy(raw['benefitsInformation'][-1]))
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY)
    assert benefits['copay_cents'] == 4000 and len(benefits['selected_benefit_ids']) == 2
    raw['benefitsInformation'][-1]['placeOfService'] = '22'
    assert commercial.normalize_stedi_271(raw, '98', True)['copay_cents'] is None


def test_payer_policy_referral_sentence_is_a_required_signal():
    raw = response()
    raw['benefitsInformation'][-1]['additionalInformation'] = []
    raw['benefitsInformation'].append({'code': 'D', 'serviceTypeCodes': ['98'],
        'coverageLevelCode': 'IND',
        'messages': ['This policy requires that services be referred by a primary care physician.']})
    terms = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203', service_date=DAY)
    assert terms['referral_signal'] == 'required'


def test_office_service_hierarchy_respects_notes_and_dates():
    raw = response()
    base = raw['benefitsInformation'].pop()
    raw['benefitsInformation'] += [
        {**base, 'serviceTypeCodes': ['98']},
        {**base, 'serviceTypeCodes': ['BY'], 'benefitAmount': '80', 'messages': [{'message': 'INJECTIONS'}]},
        {**base, 'serviceTypeCodes': ['5'], 'benefitAmount': '25'},
        {**base, 'serviceTypeCodes': ['BY'], 'benefitAmount': '50', 'benefitsDateInformation': {'planEnd': '20000101'}},
    ]
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY)
    assert benefits['copay_cents'] == 4000 and benefits['resolved_service_type'] == '98'
    raw['benefitsInformation'].append({**base, 'serviceTypeCodes': ['BQ'], 'benefitAmount': '30'})
    neuro = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY, office_service_types=['BQ', 'BY', '98'])
    assert neuro['copay_cents'] == 3000 and neuro['resolved_service_type'] == 'BQ'
    assert len(neuro['reported_options']) == 7  # Full payer evidence remains available.


def test_daily_copay_is_allocated_once_and_zero_percent_is_not_an_extra_charge():
    raw = response()
    raw['benefitsInformation'].append({**raw['benefitsInformation'][-1], 'code': 'A', 'benefitPercent': '0'})
    codes = [{'id': 'office', 'code': '99204', 'units': 1}, {'id': 'test', 'code': '93000', 'units': 1}]
    rates = [{'code': row['code'], 'allowed_cents': 15000} for row in codes]
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    result = visit_estimate.price_lines(codes, rates, benefits, {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '98'}}, payload=raw, service_date=DAY)
    assert result['complete'] and result['patient_max_cents'] == 4000
    assert [row['calculation']['copay_cents'] for row in result['lines']] == [4000, 0]


def test_zero_oop_gives_conditional_result_without_clearing_identity_or_collecting():
    raw = response('Former')
    raw['benefitsInformation'][2]['benefitAmount'] = '0'
    benefits = api._previsit_benefits(raw, policy(), DAY)
    price = visit_estimate.price_lines([{'id': 'office', 'code': '99204', 'units': 1}], [], benefits, {}, payload=raw, service_date=DAY)
    assert visit_estimate.planning_estimate(price, benefits, benefits['identity']) is None
    confirmed = {**benefits, 'network_status': 'in_network'}  # Synthetic provider participation.
    planning = visit_estimate.planning_estimate(price, confirmed, benefits['identity'])
    assert planning['amount_cents'] == 0 and not planning['collectable']
    assert len(planning['assumptions']) == 1
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] is None
    for changes in [{'active': False}, {'network_status': 'out_of_network'}, {'has_secondary': True}, {'service_date_matches': False}, {'review_reasons': ['Verify payer dates']}]:
        assert visit_estimate.planning_estimate(price, {**confirmed, **changes}, benefits['identity']) is None
    assert visit_estimate.planning_estimate(price, confirmed, {'status': 'review', 'can_confirm': False}) is None
    price['lines'][0]['benefits']['requirements']['authorization']['status'] = 'denied'
    assert visit_estimate.planning_estimate(price, confirmed, benefits['identity']) is None


def test_ecw_context_preserves_comments_and_financial_fields_without_coding_them():
    comment = 'Patient wrote: headaches; please explain treatment.\nDo not change my words.'
    visit = {'resourceType': 'Encounter', 'id': 'v', 'comment': comment,
             'extension': [{'url': 'https://ehr.test/General_Notes', 'valueString': 'Payment preference: insurance'},
                           {'url': 'https://ehr.test/visit-copay', 'valueMoney': {'value': 250, 'currency': 'USD'}}],
             'text': {'div': '<div><p>Visit context</p><script>hidden()</script></div>'}}
    fields = ecw_backend.appointment_fields(visit)
    assert fields[0]['text'] == comment
    assert any(row['text'] == '250 USD' for row in fields)
    assert fields[-1]['text'] == 'Visit context'
    assert not ecw_backend._visit_context(visit, [], [], [])


def test_general_notes_fill_only_missing_policy_fields_from_labeled_header():
    header = 'Patient payment preference : Example Health - Texas, Subscriber No. : ( ABC123 )'
    fields = [{'label': 'General Notes', 'text': header + '\nPatient entered comments on healow : symptoms and preferences', 'source': 'Encounter/v/GeneralNotes'}]
    fallback = ecw_backend.appointment_insurance_fallback
    assert fallback(fields, None, None, 1) == {'payer': 'Example Health - Texas', 'member_id': 'ABC123', 'source': 'Encounter/v/GeneralNotes'}
    assert fallback(fields, 'ABC123', None, 1)['payer'] == 'Example Health - Texas'
    assert fallback(fields, None, 'Example Health - Texas', 1)['member_id'] == 'ABC123'
    assert fallback(fields, 'ABC123', 'Example Health - Texas', 1) == {}
    assert fallback(fields, 'OTHER', None, 1) == {}
    assert fallback(fields, None, 'Different Health', 1) == {}
    assert fallback(fields, None, None, 0) == fallback(fields, None, None, 1)
    assert fallback(fields, None, None, 2) == {}
    assert len(ecw_backend.appointment_insurance_candidates(fields)) == 1
    assert len(ecw_backend.appointment_insurance_candidates([
        *fields, {**fields[0], 'text': header.replace('ABC123', 'OTHER')}])) == 2
    assert fallback([*fields, {**fields[0], 'text': header.replace('ABC123', 'OTHER')}], None, None, 1) == {}
    assert fallback([{**fields[0], 'text': 'Patient entered comments on healow : ' + header}], None, None, 1) == {}
    assert fallback([{**fields[0], 'label': 'Patient comments'}], None, None, 1) == {}


def test_product_mapping_is_scoped_to_current_network_evidence_dates_and_plan(monkeypatch):
    payer = {'id': 'payer-a', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'network', 'payer_connection_id': 'payer-a', 'status': 'verified', 'network_name': 'Choice PPO', 'billing_npi': '1',
               'rendering_npi': '2', 'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'CI',
               'effective_from': '2020-01-01', 'evidence_reference': 'contract-v1'}
    mapping = {'products': ['Choice Medical'], 'reference': 'Contract p2', 'actor_id': 'admin',
               'valid_from': DAY, 'valid_to': DAY, 'network_id': 'network', 'network_reference': 'contract-v1',
               'plan_scope': {'group_number': 'group-a', 'plan_number': None}}
    profile = {'payer_connection_id': 'payer-a', 'provider_network_id': 'network', 'status': 'staff_guidance_enabled',
               'plan_matcher': 'CI', 'rules': {'product_mappings': [mapping],
                   'plan_product_names': ['Choice Medical'],
                   'library_source': {'document_id': 'synthetic-contract', 'reference': 'contract-v1'}}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *a: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *a: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *a: [profile])
    inputs = {'payer_id': 'payer-a', 'service_date': DAY, 'rendering_provider': {'npi': '2'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office', 'plan': 'CI',
              'payer_plan_names': ['Choice Medical'], 'payer_plan_scope': mapping['plan_scope']}
    assert api._practice_path('clinic', inputs, source)[2] == profile
    payer_network = {**source, 'payer_plan_names': ['Preferred Provider Option Plus Medical'],
                     'payer_network_names': ['Choice PPO']}
    assert api._practice_path('clinic', inputs, payer_network)[2] == profile
    for changes in [{'payer_network_names': ['Other PPO']},
                    {'payer_plan_names': ['A different named PPO']}]:
        with pytest.raises(HTTPException): api._practice_path('clinic', inputs, {**payer_network, **changes})
    for changes in [{'payer_plan_scope': {'group_number': 'another'}}, {'payer_plan_names': ['Other Medical']}]:
        with pytest.raises(HTTPException): api._practice_path('clinic', inputs, {**source, **changes})
    network['evidence_reference'] = 'contract-v2'
    with pytest.raises(HTTPException): api._practice_path('clinic', inputs, source)


def test_regional_ppo_cannot_inherit_a_general_uhc_contract_through_product_mapping(monkeypatch):
    product = 'UHC Complete Regional PPO Medicare Advantage'
    payer = {'id': 'uhc', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    general = {'id': 'general', 'payer_connection_id': 'uhc', 'status': 'verified',
               'network_name': 'Choice Plus', 'billing_npi': '1', 'rendering_npi': '2',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'Choice Plus',
               'effective_from': '2020-01-01', 'evidence_reference': 'library:general'}
    mapping = {'products': [product], 'reference': 'Admin claim', 'actor_id': 'admin',
               'valid_from': DAY, 'valid_to': DAY, 'network_id': 'general',
               'network_reference': 'library:general',
               'plan_scope': {'plan_number': 'plan-a', 'group_number': None}}
    general_profile = {'payer_connection_id': 'uhc', 'provider_network_id': 'general',
                       'status': 'staff_guidance_enabled', 'plan_matcher': 'Choice Plus',
                       'rules': {'product_mappings': [mapping]}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [general])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [general_profile])
    inputs = {'payer_id': 'uhc', 'service_date': DAY, 'rendering_provider': {'npi': '2'},
              'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office', 'plan': 'Choice Plus',
              'payer_plan_names': [product], 'payer_plan_scope': mapping['plan_scope']}
    with pytest.raises(HTTPException, match='own exact-product provider contract'):
        api._practice_path('clinic', inputs, source)

    regional = {**general, 'id': 'regional', 'plan_matcher': product,
                'network_name': 'Regional PPO', 'evidence_reference': 'library:regional'}
    regional_profile = {**general_profile, 'provider_network_id': 'regional',
                        'plan_matcher': product, 'rules': {'plan_product_names': [product]}}
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [general, regional])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [general_profile, regional_profile])
    assert api._practice_path('clinic', inputs, {**source, 'plan': product})[1] == regional


@pytest.mark.parametrize('product', [
    'UHC Complete Regional PPO Medicare Advantage',
    'UHC Regional Medicare Advantage PPO',
    'UHC Complete Local PPO Medicare Advantage',
    'UnitedHealthcare Local Medicare Advantage PPO',
])
def test_excluded_medicare_advantage_ppo_names_require_their_own_network(product):
    payer = {'display_name': 'UnitedHealthcare'}
    assert api._excluded_ma_ppo_contract_mismatch([product], {'plan_matcher': 'Choice Plus'}, payer)
    assert not api._excluded_ma_ppo_contract_mismatch([product], {'plan_matcher': product}, payer)


def test_general_medicare_advantage_ppo_does_not_inherit_a_local_regional_exclusion():
    assert not api._excluded_ma_ppo_contract_mismatch(
        ['UnitedHealthcare Group Medicare Advantage (PPO)'], {'plan_matcher': 'Group PPO'},
        {'display_name': 'UnitedHealthcare'})


def test_renamed_uhc_connection_cannot_hide_a_payer_named_local_ppo():
    assert api._excluded_ma_ppo_contract_mismatch(
        ['UHC Complete Local PPO'], {'plan_matcher': 'Choice Plus'},
        {'id': 'opaque-connection', 'display_name': 'Choice Plus'})


def test_renamed_uhc_connection_alias_keeps_generic_local_ppo_out_of_general_contract():
    assert api._excluded_ma_ppo_contract_mismatch(
        ['Local PPO'], {'plan_matcher': 'Choice Plus'},
        {'id': 'opaque-connection', 'display_name': 'Choice Plus', 'aliases': ['United Healthcare']})


def test_uhc_local_ppo_appendix_does_not_set_another_payers_contract_scope():
    assert not api._excluded_ma_ppo_contract_mismatch(
        ['Blue Cross Local Medicare Advantage PPO'], {'plan_matcher': 'General PPO'},
        {'display_name': 'Blue Cross Blue Shield'})


def test_other_payer_can_use_its_sourced_local_ppo_product_mapping(monkeypatch):
    product = 'Blue Cross Local Medicare Advantage PPO'
    payer = {'id': 'bcbs', 'display_name': 'Blue Cross Blue Shield',
             'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'bcbs-network', 'payer_connection_id': 'bcbs', 'status': 'verified',
               'network_name': 'General PPO', 'billing_npi': '1', 'rendering_npi': '2',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'General PPO',
               'effective_from': '2020-01-01', 'evidence_reference': 'library:bcbs-contract'}
    scope = {'group_number': 'GROUP-A', 'plan_number': None}
    mapping = {'products': [product], 'reference': 'BCBS contract appendix', 'actor_id': 'admin',
               'valid_from': DAY, 'valid_to': DAY, 'network_id': 'bcbs-network',
               'network_reference': 'library:bcbs-contract', 'plan_scope': scope}
    profile = {'payer_connection_id': 'bcbs', 'provider_network_id': 'bcbs-network',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'General PPO',
               'rules': {'product_mappings': [mapping], 'plan_product_names': [product],
                         'library_source': {'document_id': 'bcbs-contract',
                                            'reference': 'library:bcbs-contract'}}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [profile])
    inputs = {'payer_id': 'bcbs', 'service_date': DAY, 'rendering_provider': {'npi': '2'},
              'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office',
              'plan': product, 'payer_plan_names': [product], 'payer_plan_scope': scope}
    assert api._practice_path('clinic', inputs, source)[1] == network


def test_scoped_product_alias_can_select_the_network_before_product_validation(monkeypatch):
    payer = {'id': 'uhc', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'choice', 'payer_connection_id': 'uhc', 'status': 'verified',
               'network_name': 'Choice Plus', 'billing_npi': '1', 'rendering_npi': '2',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'Choice Plus',
               'effective_from': '2020-01-01', 'evidence_reference': 'library:choice-contract'}
    scope = {'group_number': 'GROUP-A', 'plan_number': None}
    mapping = {'products': ['POS CHOICE PLUS'], 'reference': 'Contract product appendix',
               'actor_id': 'clinic-admin', 'valid_from': DAY, 'valid_to': DAY,
               'network_id': 'choice', 'network_reference': 'library:choice-contract',
               'plan_scope': scope}
    profile = {'payer_connection_id': 'uhc', 'provider_network_id': 'choice',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'Choice Plus',
               'rules': {'product_mappings': [mapping], 'plan_product_names': ['POS CHOICE PLUS'],
                         'library_source': {'document_id': 'choice-contract',
                                            'reference': 'library:choice-contract'}}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *a: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *a: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *a: [profile])
    inputs = {'payer_id': 'uhc', 'service_date': DAY,
              'rendering_provider': {'npi': '2'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office',
              'plan': 'POS CHOICE PLUS', 'payer_plan_names': ['POS CHOICE PLUS'],
              'payer_plan_scope': scope}
    _, selected_network, selected_profile = api._practice_path('clinic', inputs, source)
    assert selected_network == network
    assert selected_profile['rate_plan_matcher'] == 'POS CHOICE PLUS'
    assert selected_profile['require_exact_rate_plan'] is True
    payer_named = {**source, 'plan': 'Choice Plus', 'payer_plan_bound': True}
    assert api._practice_path('clinic', inputs, payer_named)[2]['rate_plan_matcher'] == 'POS CHOICE PLUS'
    same_product_repeated = {**payer_named, 'payer_plan_names': ['POS CHOICE PLUS', 'pos choice plus']}
    assert api._practice_path('clinic', inputs, same_product_repeated)[2]['require_exact_rate_plan'] is True
    not_bound = {**payer_named, 'payer_plan_bound': False}
    assert 'rate_plan_matcher' not in api._practice_path('clinic', inputs, not_bound)[2]
    rate_profiles = []
    monkeypatch.setattr(api.commercial_store, 'matching_rates',
                        lambda _practice, rate_profile, *_: rate_profiles.append(rate_profile) or [])
    monkeypatch.setattr(api, '_network_contract_evidence', lambda *_: {'document_id': 'synthetic-contract'})
    api._previsit_range('clinic', inputs, source, 'uhc',
                        [{'id': 'office', 'code': '99203', 'source': 'eCW scheduled service', 'units': 1}],
                        {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract'})
    assert rate_profiles[0]['rate_plan_matcher'] == 'POS CHOICE PLUS'
    profile['rules']['plan_product_names'] = ['Choice Plus']
    with pytest.raises(HTTPException):
        api._practice_path('clinic', inputs, source)
    profile['rules']['plan_product_names'] = ['POS CHOICE PLUS']
    for changed in ({'payer_plan_scope': {'group_number': 'OTHER', 'plan_number': None}},
                    {'plan': 'OTHER PRODUCT'},
                    {'payer_plan_names': ['OTHER PRODUCT']}):
        with pytest.raises(HTTPException):
            api._practice_path('clinic', inputs, {**source, **changed})
    network['evidence_reference'] = 'library:replacement'
    with pytest.raises(HTTPException):
        api._practice_path('clinic', inputs, source)


def test_single_matched_stedi_plan_can_supply_missing_ecw_contract_label(monkeypatch):
    payer = {'id': 'payer-a', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled',
             'stedi_payer_id': 'uhc-route', 'display_name': 'United Healthcare'}
    network = {'id': 'network-a', 'payer_connection_id': 'payer-a', 'status': 'verified',
               'network_name': 'Choice Plus', 'billing_npi': '1', 'rendering_npi': '2',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'Choice Plus',
               'effective_from': '2020-01-01', 'evidence_reference': 'library:synthetic-contract'}
    profile = {'payer_connection_id': 'payer-a', 'provider_network_id': 'network-a',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'Choice Plus',
               'rules': {'plan_product_names': ['Choice Plus'], 'library_source': {
                   'document_id': 'synthetic-contract', 'reference': 'library:synthetic-contract',
                   'sha256': 'synthetic-hash'}}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [profile])
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda *_: {
        'id': 'synthetic-contract', 'kind': 'contract', 'content_type': 'application/pdf',
        'sha256': 'synthetic-hash', 'label': 'Synthetic Choice Plus agreement'})
    monkeypatch.setattr(api, '_previsit_range', lambda *_, **__: ({'lines': []}, []))
    inputs = {'payer_id': 'payer-a', 'service_date': DAY,
              'rendering_provider': {'npi': '2'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office',
              'payer_plan_names': ['Choice Plus']}
    benefits = {'active': True, 'service_date_matches': True}
    comparison = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    with pytest.raises(HTTPException):
        api._previsit_practice_path('clinic', inputs, source)
    filled = _payer_plan_pricing_source(source, {'names': ['Choice Plus']}, benefits, comparison)
    assert filled['plan'] == 'Choice Plus'
    assert filled['pricing_plan_source'] == 'Stedi eligibility plan name'
    generic_ecw = {**source, 'plan': 'United Healthcare'}
    exact_payer_product = _payer_plan_pricing_source(generic_ecw, {'names': ['Choice Plus']}, benefits, comparison)
    assert exact_payer_product['plan'] == 'Choice Plus'
    assert exact_payer_product['pricing_plan_source'] == 'Stedi eligibility plan name'
    assert _payer_plan_pricing_source({**source, 'plan': 'PPO'}, {'names': ['Choice Plus']},
                                      benefits, comparison)['plan'] == 'Choice Plus'
    assert _payer_plan_pricing_source(generic_ecw, {'names': ['Choice Plus', 'choice plus']},
                                      benefits, comparison)['plan'] == 'Choice Plus'
    assert _payer_plan_pricing_source(generic_ecw, {'names': ['United Healthcare', 'Choice Plus']},
                                      benefits, comparison)['plan'] == 'Choice Plus'
    assert api._previsit_practice_path('clinic', inputs, filled)[1] == network
    assert api._previsit_practice_path('clinic', inputs, exact_payer_product)[1] == network
    monkeypatch.setattr(api.commercial_store, 'matching_rates', lambda *_: [
        {'code': '99203', 'modifier': '', 'allowed_cents': 12000,
         'source_reference': 'library:synthetic-rate'}])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *_: None)
    _, _, _, available, _ = build_catalog(api, {}, {'practice_id': 'clinic'}, inputs,
                                           source, None, {**filled, 'payer_plan_names': ['Choice Plus']},
                                           'uhc-route', {})
    assert any(row['code'] == '99203' and row.get('unit_allowed_cents') == 12000 for row in available)
    with monkeypatch.context() as missing_rates:
        missing_rates.setattr(api.commercial_store, 'matching_rates', lambda *_: [])
        missing_rates.setattr(api, '_previsit_range', _previsit_range)
        network_only = {'payer': 'United Healthcare',
                        'codes': [{'id': 'office', 'code': '99203', 'units': 1}], 'issues': []}
        network_benefits = {'active': True, 'service_date_matches': True, 'identity': comparison}
        api._attach_previsit_pricing(network_only, 'clinic', inputs, filled,
                                     'uhc-route', network_benefits, payload={})
        assert network_benefits['network_status'] == 'in_network'
        assert network_benefits['network_source'] == 'clinic_library_contract'
        assert network_only['network_evidence']['document_id'] == 'synthetic-contract'
        assert network_only['price_range']['lines'][0]['allowed_cents'] is None
    with monkeypatch.context() as no_contract:
        no_contract.setattr(api.staff_store, 'get_library', lambda *_: None)
        no_contract.setattr(api.staff_store, 'list_library', lambda *_: [])
        _, _, _, unlinked, _ = build_catalog(api, {}, {'practice_id': 'clinic'}, inputs,
                                               source, None, {**filled, 'payer_plan_names': ['Choice Plus']},
                                               'uhc-route', {})
        assert not any(row.get('unit_allowed_cents') is not None for row in unlinked)
        priced, issues = _previsit_range('clinic', inputs,
            {**filled, 'payer_plan_names': ['Choice Plus']}, 'payer-a',
            [{'id': 'office', 'code': '99203', 'units': 1}],
            {'active': True, 'network_status': 'unknown'}, payload={})
        assert priced['lines'][0]['allowed_cents'] is None
        assert any('contract linked' in issue.lower() for issue in issues)
    mixed_names = {**exact_payer_product, 'payer_plan_names': ['United Healthcare', 'Choice Plus'],
                   'payer_plan_bound': True}
    assert api._previsit_practice_path('clinic', inputs, mixed_names)[1] == network
    assert api._previsit_practice_path('clinic', inputs, mixed_names)[2].get('require_exact_rate_plan') is not True
    with pytest.raises(HTTPException):
        api._previsit_practice_path('clinic', inputs, {**mixed_names,
            'payer_plan_names': ['Choice Plus', 'Regional PPO']})
    assert _payer_plan_pricing_source(generic_ecw, {'names': ['Choice Plus', 'Regional PPO']},
                                      benefits, comparison) is generic_ecw
    assert _payer_plan_pricing_source(generic_ecw, {'names': ['Choice Plus']},
                                      benefits, {'status': 'review'}) is generic_ecw
    result = {'payer': 'United Healthcare', 'codes': [], 'issues': []}
    pricing_benefits = {**benefits, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, filled, 'uhc-route', pricing_benefits)
    assert pricing_benefits['network_status'] == 'in_network'
    assert result['network_evidence']['document_id'] == 'synthetic-contract'
    assert result['network_evidence']['product_source'] == 'Stedi eligibility plan name'
    assert _payer_plan_pricing_source(source, {'names': ['Choice Plus']}, benefits,
                                      {'status': 'matched', 'binding': 'legacy-binding'}) is source
    warned = _payer_plan_pricing_source(source, {'names': ['Choice Plus']}, benefits,
                                        {'status': 'warning', 'binding': 'corroborated-binding',
                                         'match_version': 2})
    assert warned['plan'] == 'Choice Plus'
    for plan, terms, identity_status in [
        ({'names': ['Choice Plus', 'Regional PPO']}, benefits, 'matched'),
        ({'names': ['Choice Plus']}, benefits, 'review'),
        ({'names': ['Choice Plus']}, {**benefits, 'service_date_matches': False}, 'matched'),
        ({'names': ['Choice Plus']}, {**benefits, 'active': False}, 'matched'),
        ({'names': ['PPO']}, benefits, 'matched'),
        ({'names': ['Preferred Provider Organization Plus Medical']}, benefits, 'matched'),
    ]:
        assert _payer_plan_pricing_source(source, plan, terms, {'status': identity_status}) is source
    assert _payer_plan_pricing_source({**source, 'plan': 'eCW product'},
                                      {'names': ['Choice Plus']}, benefits, comparison)['plan'] == 'eCW product'


def test_previsit_uses_the_unique_active_contract_when_payer_product_label_is_generic(monkeypatch):
    payer = {'id': 'payer-a', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'network', 'payer_connection_id': 'payer-a', 'status': 'verified',
               'network_name': 'Blue Choice PPO', 'billing_npi': '1', 'rendering_npi': '2',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'CI',
               'effective_from': '2020-01-01', 'evidence_reference': 'contract-v1'}
    profile = {'payer_connection_id': 'payer-a', 'provider_network_id': 'network',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'CI', 'rules': {}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *a: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *a: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *a: [profile])
    inputs = {'payer_id': 'payer-a', 'service_date': DAY,
              'rendering_provider': {'npi': '2'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office', 'plan': 'CI',
              'payer_plan_names': ['Preferred Provider Organization Plus Medical']}
    assert api._previsit_practice_path('clinic', inputs, source)[1] == network
    with pytest.raises(HTTPException):
        api._practice_path('clinic', inputs, source)


def test_network_pdf_points_only_to_its_uploaded_contract(monkeypatch):
    library_source = {'document_id': 'contract-a', 'reference': 'library:contract-a',
                      'sha256': 'source-hash', 'locator': 'Page 3, row 7'}
    network = {'network_name': 'Choice PPO', 'evidence_reference': 'library:contract-a'}
    profile = {'rules': {'library_source': library_source}}
    document = {'kind': 'contract', 'content_type': 'application/pdf', 'sha256': 'source-hash',
                'label': 'Signed Choice PPO contract'}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [{'id': 'payer-a', 'stedi_payer_id': 'route-a', 'status': 'staff_guidance_enabled'}])
    monkeypatch.setattr(api, '_previsit_practice_path', lambda *_: ({}, network, profile))
    monkeypatch.setattr(api, '_previsit_range', lambda *_, **__: ({'lines': []}, []))
    lookup = MagicMock(return_value=document)
    monkeypatch.setattr(api.staff_store, 'get_library', lookup)

    def estimate(source=None):
        result = {'codes': [], 'issues': []}
        api._attach_previsit_pricing(result, 'clinic', {'service_date': DAY}, source or {}, 'route-a', {})
        return result.get('network_evidence') or {}

    assert estimate()['document_id'] == 'contract-a'
    assert estimate()['locator'] == 'Page 3, row 7'
    assert estimate({'pricing_plan_source': 'Stedi eligibility plan name'})['product_source'] == 'Stedi eligibility plan name'
    lookup.assert_called_with('contract-a', 'clinic')
    document['content_type'] = 'text/csv'
    assert 'document_id' not in estimate()
    document['content_type'] = 'application/pdf'
    profile['rules']['library_source']['reference'] = 'library:other-contract'
    assert 'document_id' not in estimate()


def test_payer_only_library_match_is_related_document_not_contract_rule(monkeypatch):
    network = {'network_name': 'Choice PPO', 'payer_connection_id': 'payer-a',
               'evidence_reference': 'contract-config'}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: {
        'id': 'payer-a', 'display_name': 'Example Health'})
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: [
        {'id': 'payer-pdf', 'kind': 'contract', 'content_type': 'application/pdf',
         'payer': 'Example Health', 'label': 'Example Health agreement'}])
    evidence = api._network_contract_evidence('clinic', network, {'rules': {}})
    assert evidence['related_document_id'] == 'payer-pdf'
    assert 'document_id' not in evidence


def test_current_library_contract_decides_network_independently_of_price(monkeypatch):
    payer = {'id': 'payer-a', 'stedi_payer_id': 'route-a', 'display_name': 'Example Health',
             'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'network-a', 'payer_connection_id': payer['id'], 'status': 'verified',
               'network_name': 'Choice PPO', 'billing_npi': '1111111111', 'rendering_npi': '2222222222',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'CI',
               'effective_from': '2020-01-01', 'evidence_reference': 'signed-scope'}
    profile = {'payer_connection_id': payer['id'], 'provider_network_id': network['id'],
               'status': 'staff_guidance_enabled', 'plan_matcher': 'CI', 'rules': {}}
    inputs = {'service_date': DAY, 'rendering_provider': {'npi': '2222222222'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1111111111'}, 'location_id': 'office', 'plan': 'CI',
              'payer_plan_names': ['Preferred Provider Organization Plus Medical']}
    documents = [{'id': 'contract-a', 'kind': 'contract', 'content_type': 'application/pdf',
                  'payer': 'Example Health', 'label': 'Synthetic current agreement',
                  'effective_from': '2020-01-01'}]
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer])
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *_: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [profile])
    monkeypatch.setattr(api.staff_store, 'list_library', lambda *_: documents)
    monkeypatch.setattr(api, '_previsit_range', lambda *_, **__: ({'lines': []}, []))

    def estimate():
        result, benefits = {'codes': [], 'issues': []}, {'network_status': 'unknown'}
        api._attach_previsit_pricing(result, 'clinic', inputs, source, 'route-a', benefits)
        return result, benefits

    result, benefits = estimate()
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['related_document_id'] == 'contract-a'
    documents[0]['effective_to'] = '2020-12-31'
    result, benefits = estimate()
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['reason'] == 'The Clinical Library has no contract linked to this exact payer product and provider.'
    documents[0].pop('effective_to')
    documents.append({**documents[0], 'id': 'second-contract'})
    result, benefits = estimate()
    assert benefits['network_status'] == 'unknown'
    assert 'Multiple current payer contracts' in result['network_evidence']['reason']
    documents.clear()
    result, benefits = estimate()
    assert benefits['network_status'] == 'unknown'
    network['status'] = 'nonparticipating'
    result, benefits = estimate()
    assert benefits['network_status'] == 'out_of_network'
    assert benefits['network_source'] == 'clinic_nonparticipation_rule'
    network['status'] = 'verified'
    del source['location_id']
    result, benefits = estimate()
    assert benefits['network_status'] == 'unknown'


@pytest.mark.parametrize('payer_label', ['United Healthcare', 'United Health Care'])
def test_uhc_uses_scoped_blue_cross_library_rate_only_as_temporary_reference(monkeypatch, payer_label):
    payer = {'id': 'bcbs', 'display_name': 'Blue Cross and Blue Shield of Texas',
             'stedi_payer_id': 'bcbs-route', 'status': 'staff_guidance_enabled'}
    network = {'id': 'bcbs-network', 'payer_connection_id': 'bcbs', 'status': 'verified',
               'billing_npi': '1111111111', 'rendering_npi': '2222222222',
               'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'CI',
               'effective_from': '2020-01-01', 'effective_to': None}
    profile = {'payer_connection_id': 'bcbs', 'provider_network_id': 'bcbs-network',
               'status': 'staff_guidance_enabled', 'plan_matcher': 'CI'}
    inputs = {'service_date': DAY, 'rendering_provider': {'npi': '2222222222'},
              'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1111111111'}, 'location_id': 'office'}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [payer])
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *_: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *_: [profile])
    monkeypatch.setattr(api.commercial_store, 'matching_rates', lambda *_, **__: [
        {'code': '99203', 'modifier': '', 'allowed_cents': 10117,
         'source_reference': 'library:synthetic-fee-schedule'}])
    result = {'payer': payer_label, 'codes': [{'code': '99203', 'units': 1}], 'issues': []}
    benefits = {'active': True, 'network_status': 'unknown'}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'uhc-route', benefits)
    assert result['temporary_rate_reference']['lines'][0]['unit_reference_cents'] == 10117
    assert result['temporary_rate_reference']['complete'] is True
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    assert benefits['network_status'] == 'unknown'
    source['location_id'] = 'different-office'
    result = {'payer': payer_label, 'codes': [{'code': '99203', 'units': 1}], 'issues': []}
    api._attach_previsit_pricing(result, 'clinic', inputs, source, 'uhc-route', benefits)
    assert 'temporary_rate_reference' not in result


def test_requirement_candidates_include_suggested_cpts_without_expanding_the_selected_visit():
    rows = [{'id': 'office', 'code': '99204'}, {'id': 'test', 'code': '95911'},
            {'id': 'alternative', 'code': '99205'}, {'id': 'catalog', 'code': '95886'}]
    result = {'codes': [rows[0]], 'available_codes': rows,
              'suggestions': {'groups': [{'codes': [{'code': '95911'}]}]},
              'service_plan': {'recommended_ids': ['office', 'alternative']}}
    assert [row['code'] for row in api._requirement_candidates(result)] == ['95911', '99205']


def test_identity_review_persists_across_visits_and_rejects_stale_or_unauthorized_edits(monkeypatch):
    library_document = {'id': 'signed-contract', 'kind': 'contract', 'content_type': 'application/pdf',
                        'payer': 'Example Health', 'label': 'Synthetic contract',
                        'filename': 'synthetic-contract.pdf', 'sha256': 'digest',
                        'extraction': {'sha256': 'digest', 'pages': [{'number': 9,
                            'text': 'Provider will comply with all Referral and Preauthorization requirements set forth in the Policies and Procedures.'}],
                            'tables': []}}
    monkeypatch.setattr(api.library_pricing, 'prepare', lambda practice: [library_document])
    monkeypatch.setattr(api.staff_store, 'list_library', lambda practice: [library_document])
    monkeypatch.setattr(api.staff_store, 'get_library', lambda document_id, practice: library_document)
    inputs = {'service_date': DAY, 'payer_id': 'payer-a', 'member_id': 'ABC123', 'relationship': '19',
              'subscriber': {'firstName': 'Sam', 'lastName': 'Other'},
              'patient': {'patient_first_name': 'Alex', 'patient_last_name': 'Morgan', 'patient_birth_date': '2001-02-03'},
              'lines': [{'code': '99204', 'units': 1, 'service_status': 'scheduled'}], 'visit_type': 'new'}
    source = {'appointment_id': 'visit-a', 'coverage_id': 'policy-a', 'active_coverages': 1,
              'plan': 'CI', 'payer': 'Example Health'}
    session = {'practice_id': 'clinic', 'patient_id': 'patient', 'share_manifest_id': '', 'intake_id': 'intake',
               'staff': {'id': 'staff', 'role': 'staff', 'display_name': 'Reviewer'}, 'intake': {'fields': {}, 'patient': {'id': 'patient', 'given_name': 'Alex', 'family_name': 'Morgan', 'birth_date': '2001-02-03'}}}
    raw = response('Former'); raw['benefitsInformation'][2]['benefitAmount'] = '0'
    checked = datetime.now(timezone.utc).isoformat()
    result = {'checked_at': checked, 'check_id': 'case', 'issues': [], 'suggestions': None,
              'codes': api._previsit_codes(inputs), 'benefits': api._case_benefits(raw, inputs, source, {})}
    case = {'id': 'case', 'revision': 1, 'source_mode': 'commercial_previsit',
            'library_pricing': [{'document_id': 'signed-contract'}],
            'previsit': {'checked_at': checked}, 'previsit_raw_object_key': 'protected'}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    monkeypatch.setattr(api, '_ecw_practice_config', lambda *a: {})
    monkeypatch.setattr(api.ecw_backend, 'fetch_inputs', AsyncMock(return_value=(inputs, [], source)))
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *a: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *a: None)
    monkeypatch.setattr(api.cpt_suggester, 'recommend_visit_plan', lambda i, s, rows: {
        'version': 2, 'recommended_ids': [rows[0]['id']], 'recommended_units': {rows[0]['id']: 1}})
    monkeypatch.setattr(api.store, 'get_case', lambda *a, **kw: case)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *a: json.dumps(raw).encode())
    monkeypatch.setattr(api, '_save', lambda row, *a: {**row, 'revision': row['revision'] + 1})
    persist = MagicMock(side_effect=lambda patient, practice, proof: session['intake']['patient'].update(insurance_identities={proof['binding']: proof}))
    monkeypatch.setattr(api.staff_store, 'save_insurance_identity', persist)
    api._finish_previsit(result, case, inputs, source, raw, session, 'payer-a')
    assert result['requirement_evidence']['referral']['status'] == 'Verification required'
    assert result['requirement_evidence']['referral']['sources'][0]['page'] == 9
    token = result['context_token']
    emg = asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, emg_required=True), None))
    assert emg['emg_option']['requested'] is True
    assert emg['selected_ids'] == result['selected_ids']
    assert emg['checked_at'] == result['checked_at']
    confirm = {'binding': result['identity']['binding'], 'reference': 'Patient card 123', 'notes': 'Patient verified former surname on this insurance.'}
    updated = asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, identity_confirmation=confirm), None))
    assert updated['identity']['status'] == 'reconciled'
    assert not any('identity' in r for r in updated['benefits']['review_reasons'])
    assert persist.call_args.args[:2] == ('patient', 'clinic')
    later_source = {**source, 'appointment_id': 'visit-b'}
    later = {**result, 'benefits': api._case_benefits(raw, inputs, later_source, {})}
    api._finish_previsit(later, {}, inputs, later_source, raw, session, 'payer-a')
    assert later['identity']['status'] == 'reconciled'
    with pytest.raises(HTTPException) as stale:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=0, identity_confirmation=confirm), None))
    assert stale.value.status_code == 409 and persist.call_count == 1
    with pytest.raises(HTTPException) as unauthorized:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, product_mapping={
            'reference': 'Contract', 'notes': 'Verified contract', 'valid_from': DAY, 'valid_to': DAY}), None))
    assert unauthorized.value.status_code == 403
    session['staff']['role'] = 'admin'
    with patch.object(api.commercial, 'plan_details', return_value={
            'names': ['UHC Complete Regional PPO Medicare Advantage']}), \
         patch.object(api, '_practice_path', return_value=({}, {'plan_matcher': 'Choice Plus'}, {'id': 'general'})), \
         patch.object(api.commercial_store, 'save_product_mapping') as saved_mapping:
        with pytest.raises(HTTPException, match='separate exact-product local or regional PPO agreement') as excluded:
            asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1,
                product_mapping={'reference': 'General UHC agreement', 'notes': 'This is only a general PPO agreement.',
                                 'valid_from': DAY, 'valid_to': DAY}), None))
        assert excluded.value.status_code == 422
        saved_mapping.assert_not_called()
    with patch.object(api.commercial, 'plan_details', return_value={'names': ['UHC Charter']}), \
         patch.object(api, '_practice_path', return_value=({}, {'plan_matcher': 'Choice Plus'},
             {'id': 'general', 'rules': {'plan_product_names': ['Choice Plus']}})), \
         patch.object(api.commercial_store, 'save_product_mapping') as saved_mapping:
        with pytest.raises(HTTPException, match='sourced exact-product profile') as excluded:
            asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1,
                product_mapping={'reference': 'General UHC agreement', 'notes': 'The member uses another product.',
                                 'valid_from': DAY, 'valid_to': DAY}), None))
        assert excluded.value.status_code == 422
        saved_mapping.assert_not_called()
    with pytest.raises(HTTPException) as mismatch:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, identity_confirmation={**confirm, 'binding': 'f' * 64}), None))
    assert mismatch.value.status_code == 409 and persist.call_count == 1


def test_name_difference_does_not_block_calculating_the_returned_policy():
    raw = response('Former')
    benefits = api._previsit_benefits(raw, policy(), DAY)
    benefits['network_status'] = 'in_network'
    benefits['network_source'] = 'clinic_library_contract'
    price = visit_estimate.price_lines([{'id': 'office', 'code': '99204', 'units': 1}],
        [{'code': '99204', 'allowed_cents': 15000}], benefits, {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    assert price['complete'] and not price['conditional']
    result = visit_estimate.planning_estimate(price, benefits, benefits['identity'])
    assert result['amount_cents'] == 4000 and not result['collectable']
    assert benefits['identity']['status'] == 'warning'  # Neither EHR nor payer identity is overwritten.
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] == 4000


def test_surname_notice_requires_first_name_dob_and_member_corroboration():
    raw = response('Former')
    assert identity.compare(raw, policy())['status'] == 'warning'
    for target, key, value in [(raw['dependent'], 'dateOfBirth', '2002-02-03'),
                               (raw['dependent'], 'firstName', 'Other'),
                               (raw['subscriber'], 'memberId', 'different')]:
        original = target[key]
        target[key] = value
        benefits = api._previsit_benefits(raw, policy(), DAY)
        assert benefits['identity']['status'] == 'review'
        assert any('identity' in reason for reason in benefits['review_reasons'])
        assert visit_estimate.arrival_collection({'complete': True, 'patient_max_cents': 4000},
            {**benefits, 'network_status': 'in_network'}, {})['amount_cents'] is None
        target[key] = original
        del target[key]
        assert identity.compare(raw, policy())['status'] == 'review'
        target[key] = original


def test_optional_test_mentions_do_not_replace_the_office_visit():
    for reason in ['Neurology consultation; consider EMG', 'New patient, possible EMG/NCS',
                   'Neurology consultation and EMG if needed', 'No EMG planned']:
        inputs = {'visit_type': 'new', 'reason': reason, 'lines': []}
        assert not visit_estimate.is_emg_visit(inputs)
        assert [row['code'] for row in api._previsit_codes(inputs)] == ['99202', '99203', '99204', '99205']
    for reason in ['EMG/NCS both arms', 'EMG for possible radiculopathy', 'EMG, no separate office visit']:
        assert visit_estimate.is_emg_visit({'reason': reason})
    assert visit_estimate.is_emg_visit({'reason': 'Consider EMG', 'lines': [{'code': '95909', 'service_status': 'scheduled'}]})


def test_saved_payer_response_is_scoped_and_never_cached(monkeypatch):
    session = {'patient_id': 'patient', 'share_manifest_id': '', 'practice_id': 'clinic', 'intake_id': 'intake', 'staff': {'id': 'staff', 'display_name': 'Clinician'}}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    case = {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'complete'}, 'previsit_raw_object_key': 'protected'}
    read_case = MagicMock(return_value=case)
    read_object = MagicMock(return_value=b'{"id":"payer-reference","benefitsInformation":[]}')
    monkeypatch.setattr(api.store, 'get_case', read_case)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', read_object)
    result = asyncio.run(api.saved_payer_response('case', None))
    assert result.headers['Cache-Control'] == 'no-store'
    assert json.loads(result.body)['response']['id'] == 'payer-reference'
    assert read_case.call_args.kwargs == {'practice_id': 'clinic', 'intake_id': 'intake', 'staff_actor': {'id': 'staff', 'display_name': 'Clinician'}}
    read_object.reset_mock()
    read_case.return_value = None
    with pytest.raises(HTTPException) as missing:
        asyncio.run(api.saved_payer_response('foreign-case', None))
    assert missing.value.status_code == 404
    read_object.assert_not_called()
    for state in ['pending', 'indeterminate']:
        read_case.return_value = {**case, 'previsit': {'request_state': state}}
        with pytest.raises(HTTPException): asyncio.run(api.saved_payer_response('case', None))
    read_object.assert_not_called()


def test_shared_accumulator_with_office_and_injection_labels_is_not_discarded():
    raw = response('Former')
    del raw['benefitsInformation']
    common = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}}
    raw['plans'] = [{'name': 'Example PPO', 'benefits': {
        'statuses': [{'status': 'ACTIVE', 'service': {'system': 'STC', 'value': '30'}}],
        'coPayment': [
            {**common, 'service': {'system': 'STC', 'value': 'BY'}, 'amount': '40', 'timePeriod': 'DAY', 'messages': ['INJECTIONS - PROFESSIONAL']},
            {**common, 'service': {'system': 'STC', 'value': '98'}, 'amount': '40', 'timePeriod': 'DAY', 'messages': ['OFFICE VISIT - PROFESSIONAL']}],
        'outOfPocket': [{**common, 'service': {'system': 'STC', 'value': '30'}, 'amount': '0', 'timePeriod': 'REMAINING',
            'messages': ['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL;INJECTIONS - PROFESSIONAL;LABORATORY- PROFESSIONAL;OFFICE VISIT - PROFESSIONAL;SURGERY- PROFESSIONAL;XRAY - PROFESSIONAL']}]
    }}]
    benefits = api._previsit_benefits(raw, policy(), DAY)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], benefits, {}, payload=raw, service_date=DAY)
    terms = priced['lines'][0]['benefits']
    assert terms['copay_cents'] == 4000 and terms['oop_remaining_cents'] == 0
    assert terms['resolved_service_type'] == '98'
    assert visit_estimate.planning_estimate(priced, benefits, benefits['identity']) is None
    confirmed = {**benefits, 'network_status': 'in_network'}  # Synthetic provider participation.
    assert visit_estimate.planning_estimate(priced, confirmed, benefits['identity'])['amount_cents'] == 0
    # Mentioning injections within an office benefit must not remove it either.
    raw['plans'][0]['benefits']['coPayment'][1]['messages'] = ['Office visit including injections']
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203', service_date=DAY)['copay_cents'] == 4000
