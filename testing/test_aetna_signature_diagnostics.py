"""Synthetic Aetna MEPPO pricing and source-boundary checks."""
from copy import deepcopy
from unittest.mock import patch

from app.insurance import published_plan_benefits, visit_estimate
from app.api.routers import commercial_estimator  # noqa: F401 - initialize route imports
from app.api.routers.commercial_flow.commercial_previsit_rate_reference import _attach_temporary_aetna_rates


DAY = '2026-10-28'
CODES = [{'id': 'ncs', 'code': '95908', 'units': 1, 'modifiers': []},
         {'id': 'emg', 'code': '95886', 'units': 1, 'modifiers': []}]
RATES = [{'id': 'ncs-rate', 'code': '95908', 'modifier': '', 'allowed_cents': 11879,
          'version_id': 'synthetic-version', 'source_reference': 'synthetic Aetna rate'},
         {'id': 'emg-rate', 'code': '95886', 'modifier': '', 'allowed_cents': 9997,
          'version_id': 'synthetic-version', 'source_reference': 'synthetic Aetna rate'}]


def benefits():
    return {'active': True, 'service_date_matches': True,
            'coverage_level': 'individual', 'network_status': 'in_network',
            'network_source': 'clinic_library_contract', 'has_secondary': False,
            'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
            'plan_details': {'names': ['Aetna Medicare Signature (PPO)'],
                'group_number': '000003-TX', 'plan_number': '000003-TX000006'},
            'deductible_remaining_cents': 0, 'oop_remaining_cents': 639000,
            'copay_cents': None, 'coinsurance_percent': None, 'review_reasons': []}


def test_exact_aetna_diagnostic_plan_uses_two_procedure_copays():
    priced = visit_estimate.price_lines(CODES, RATES, benefits(),
        {'cost_share': 'payer_returned', 'aetna_payer_verified': True,
         'aetna_eoc_verified': True,
         'place_of_service': '11'}, service_date=DAY)
    assert priced['complete'] is True
    assert priced['allowed_min_cents'] == 21876
    assert priced['patient_min_cents'] == 10000
    assert priced['plan_min_cents'] == 11876
    assert [line['calculation']['copay_cents'] for line in priced['lines']] == [5000, 5000]
    assert all(line['benefits']['copay_period'] == 'PROCEDURE' for line in priced['lines'])


def test_other_aetna_product_or_unverified_payer_gets_no_published_copay():
    for change in ({'plan_number': 'another-plan'}, {}):
        member = benefits()
        member['plan_details'].update(change)
        priced = visit_estimate.price_lines(CODES, RATES, member,
            {'cost_share': 'payer_returned', 'aetna_payer_verified': not not change,
             'aetna_eoc_verified': True,
             'place_of_service': '11'}, service_date=DAY)
        assert priced['complete'] is False
        assert all(line['benefits']['copay_cents'] is None for line in priced['lines'])


def test_plan_name_alone_does_not_activate_eoc_for_collection():
    priced = visit_estimate.price_lines(CODES, RATES, benefits(),
        {'cost_share': 'payer_returned', 'aetna_payer_verified': True,
         'place_of_service': '11'}, service_date=DAY)
    assert priced['complete'] is False
    assert all(line['benefits']['copay_cents'] is None for line in priced['lines'])


def test_draft_aetna_values_support_labelled_planning_amount():
    result = {'payer': 'Aetna', 'payment_mode': 'insurance', 'reference_only': True,
              'reference_benefits': benefits(), 'benefits': {},
              'codes': deepcopy(CODES)}
    draft = [{'code': '95908', 'modifier': '', 'allowed_cents': 11879,
              'source_reference': 'clinic-supplied Aetna portal table'},
             {'code': '95886', 'modifier': '', 'allowed_cents': 9997,
              'source_reference': 'clinic-supplied Aetna portal table'}]
    with patch('app.api.routers.commercial_estimator.commercial_store.list_connections',
               return_value=[{'id': 'synthetic-aetna', 'stedi_payer_id': 'HPQRS', 'status': 'draft'}]), \
         patch('app.api.routers.commercial_estimator.commercial_store.aetna_meppo_reference_rates',
               return_value=draft):
        _attach_temporary_aetna_rates(result, 'synthetic-practice',
                                      {'service_date': DAY, 'place_of_service': '11'}, {})
    assert result['aetna_diagnostic_reference']['patient_share_cents'] == 10000
    assert result['aetna_diagnostic_reference']['copay_cents'] == 10000
    assert result['aetna_diagnostic_reference']['payer_share_cents'] == 11876
    assert result['temporary_rate_reference']['complete'] is True
    assert sum(line['unit_reference_cents'] for line in result['temporary_rate_reference']['lines']) == 21876


def test_choice_pos_ii_diagnostics_show_scoped_blue_cross_reference_only(monkeypatch):
    monkeypatch.setattr(commercial_estimator.commercial_store, 'list_connections', lambda *_: [
        {'id': 'blue', 'display_name': 'Blue Cross and Blue Shield of Texas',
         'stedi_payer_id': 'QDTRP', 'status': 'staff_guidance_enabled'}])
    monkeypatch.setattr(commercial_estimator.commercial_store, 'list_networks', lambda *_: [
        {'id': 'network', 'payer_connection_id': 'blue', 'status': 'verified',
         'billing_npi': '1111111111', 'rendering_npi': '2222222222',
         'location_id': 'office', 'place_of_service': '11', 'effective_from': '2026-01-01'}])
    monkeypatch.setattr(commercial_estimator.commercial_store, 'list_profiles', lambda *_: [
        {'payer_connection_id': 'blue', 'provider_network_id': 'network',
         'status': 'staff_guidance_enabled'}])
    monkeypatch.setattr(commercial_estimator.commercial_store, 'matching_rates', lambda *_, **__: [
        {'code': '95908', 'modifier': '', 'allowed_cents': 10511,
         'source_reference': 'library:synthetic-blue-choice-ppo'},
        {'code': '95886', 'modifier': '', 'allowed_cents': 8918,
         'source_reference': 'library:synthetic-blue-choice-ppo'}])
    result = {'payer': 'Aetna', 'payment_mode': 'insurance', 'codes': deepcopy(CODES),
              'plan_details': {'stedi': {'names': ['Aetna Choice POS II']}},
              'price_range': {'complete': False, 'allowed_min_cents': None}}
    inputs = {'service_date': '2026-11-11', 'place_of_service': '11',
              'rendering_provider': {'npi': '2222222222'}}
    source = {'billing_provider': {'npi': '1111111111'}, 'location_id': 'office'}

    _attach_temporary_aetna_rates(result, 'primary', inputs, source)
    reference = result['temporary_rate_reference']
    assert reference['complete'] is True
    assert sum(row['unit_reference_cents'] for row in reference['lines']) == 19429
    assert 'not Aetna contracted allowances or a patient amount to collect' in reference['message']
    assert 'aetna_diagnostic_reference' not in result
    assert result['price_range']['allowed_min_cents'] is None

    result['plan_details']['stedi']['names'] = ['Another Aetna product']
    result.pop('temporary_rate_reference')
    _attach_temporary_aetna_rates(result, 'primary', inputs, source)
    assert 'temporary_rate_reference' not in result


def test_aetna_signature_office_fee_screen_is_only_a_scoped_reference():
    member = benefits()
    member['plan_details']['plan_number'] = '000003-TX000047'
    result = {'payer': 'Aetna', 'payment_mode': 'insurance', 'benefits': member,
              'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}]}
    _attach_temporary_aetna_rates(result, 'primary',
                                  {'service_date': '2026-11-03', 'place_of_service': '11'}, {})
    assert result['temporary_rate_reference']['lines'][0]['unit_reference_cents'] == 11960
    assert 'not an active allowed rate' in result['temporary_rate_reference']['message']
    result['benefits'] = {**member, 'service_date_matches': False, 'active': False}
    result.pop('temporary_rate_reference')
    _attach_temporary_aetna_rates(result, 'primary',
                                  {'service_date': '2026-11-04', 'place_of_service': '11'}, {})
    assert result['temporary_rate_reference']['lines'][0]['unit_reference_cents'] == 11960
    assert 'allowed_cents' not in result
    for practice, day, code in [('other-practice', '2026-11-03', '99203'),
                                ('primary', '2026-11-03', '99213')]:
        changed = {**result, 'codes': [{'id': 'office', 'code': code, 'units': 1, 'modifiers': []}]}
        changed.pop('temporary_rate_reference', None)
        _attach_temporary_aetna_rates(changed, practice,
                                      {'service_date': day, 'place_of_service': '11'}, {})
        assert 'temporary_rate_reference' not in changed


def test_aetna_signature_office_uses_published_in_network_deductible_rule():
    member = benefits()
    member['plan_details']['plan_number'] = '000003-TX000047'
    raw = {'copay_cents': 7000, 'coinsurance_percent': '0',
           'deductible_remaining_cents': 75000,
           'review_reasons': ['Confirm the remaining individual deductible that applies to this visit.']}
    rules = {'place_of_service': '11', 'provider_office_role': 'specialist'}
    office = published_plan_benefits.apply_office_terms(raw, member, rules, '2026-11-03', '99203')
    assert office['copay_cents'] == 7000
    assert office['deductible_applies'] is False
    assert office['deductible_remaining_cents'] == 0
    assert office['published_plan_rule']['office_specialist_copay_cents'] == 7000
    conflict = published_plan_benefits.apply_office_terms(
        {**raw, 'copay_cents': 9000}, member, rules, '2026-11-03', '99203')
    assert conflict['requires_review'] is True
