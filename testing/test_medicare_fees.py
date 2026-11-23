"""Synthetic checks for the clinic-approved Medicare fee schedule."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

from app.core import patient_store
from app.core.patient_schema_insurance import initialize_insurance
from app.insurance import arrival_zero_default, collection_guidance, commercial, encounter_summary, medicare_fees
from app.api.routers.commercial_flow.commercial_previsit_network_guard import apply_nmct_contract_absence
from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_pricing as previsit_pricing
from app.api.routers.commercial_flow import commercial_previsit_saved_pricing as saved_pricing


def test_schema_defines_fee_table_without_seeding_amounts():
    cursor = MagicMock()
    initialize_insurance(cursor)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any('CREATE TABLE IF NOT EXISTS medicare_fee_schedule' in sql for sql in statements)
    assert not any('INSERT INTO medicare_fee_schedule' in sql for sql in statements)
    assert not any('INSERT INTO medicare_fee_schedule' in call.args[0]
                   for call in cursor.executemany.call_args_list)


def test_lookup_reads_database_value_and_rejects_unpriced_scope(monkeypatch):
    db = MagicMock()
    db.__enter__.return_value = db
    db.execute.return_value.fetchone.return_value = {
        'code': '99204', 'allowed_cents': 18050,
        'source_reference': 'Synthetic adjusted database row', 'effective_from': '2026-11-13'}
    monkeypatch.setattr(patient_store, '_connection', lambda: db)
    args = dict(service_date='2026-11-13', place_of_service='11',
                provider_npi='1234567890', patient_binding='synthetic-binding')
    rate = medicare_fees.estimate_rate('99204', [], 1, **args)
    assert rate['allowed_cents'] == 18050
    assert rate['source_reference'] == 'Synthetic adjusted database row'
    assert medicare_fees.estimate_rate('99204', ['25'], 1, **args) is None
    assert medicare_fees.estimate_rate('99204', [], 1,
        **{**args, 'service_date': '2026-11-12'}) is None
    assert medicare_fees.estimate_rate('99204', [], 1,
        **{**args, 'patient_binding': ''}) is None


def test_live_visit_uses_medicare_database_fee_for_original_and_advantage(monkeypatch):
    def lookup(code, modifiers, units, *, service_date, place_of_service, provider_npi, patient_binding):
        if code != '99204' or modifiers or not patient_binding:
            return None
        return {'id': 'medicare:99204', 'code': code, 'modifier': '',
                'allowed_cents': 18049, 'basis': 'medicare_fee_schedule',
                'source_reference': 'Synthetic approved database row',
                'effective_from': '2026-11-13', 'service_date': service_date,
                'place_of_service': place_of_service, 'provider_npi': provider_npi,
                'patient_binding': patient_binding}
    monkeypatch.setattr(medicare_fees, 'estimate_rate', lookup)
    inputs = {'service_date': '2026-11-13', 'place_of_service': '11'}
    source = {'rendering_provider': {'npi': '1234567890'}}
    codes = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': []}]
    base = {'active': True, 'coverage_level': 'individual', 'service_date_matches': True,
            'identity': {'binding': 'synthetic-binding', 'status': 'matched', 'match_version': 2},
            'cost_share': 'coinsurance', 'coinsurance_percent': '20',
            'deductible_remaining_cents': 0, 'oop_remaining_cents': 100000,
            'referral_signal': 'not_required', 'authorization_signal': 'not_required',
            'review_reasons': [], 'requires_review': False}
    for payer_id, name, network in [
        ('CMSMED', 'Original Medicare', 'in_network'),
        ('87726', 'Regional Medicare Advantage PPO', 'out_of_network'),
    ]:
        benefits = {**base, 'network_status': network,
                    'network_source': 'clinic_contract_inventory',
                    'plan_details': {'names': [name]}}
        priced, issues = api._previsit_range('synthetic-clinic', inputs, source, payer_id,
            codes, benefits, review={'selected_ids': ['office']})
        assert not issues
        assert priced['lines'][0]['allowed_basis'] == 'medicare_fee_schedule'
        assert priced['lines'][0]['allowed_cents'] == 18049
        assert priced['complete'] and priced['patient_max_cents'] == 3610


def test_original_medicare_office_without_network_or_oop_returns_twenty_percent(monkeypatch):
    def lookup(code, modifiers, units, *, service_date, place_of_service, provider_npi, patient_binding):
        return {'id': 'synthetic-medicare-fee', 'code': code, 'modifier': '',
                'allowed_cents': 11960, 'basis': 'medicare_fee_schedule',
                'source_reference': 'Synthetic approved fee', 'effective_from': '2026-11-13',
                'service_date': service_date, 'place_of_service': place_of_service,
                'provider_npi': provider_npi, 'patient_binding': patient_binding}
    monkeypatch.setattr(medicare_fees, 'estimate_rate', lookup)
    identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
    benefits = {'active': True, 'original_medicare': True, 'has_secondary': False,
        'service_date_matches': True, 'coverage_level': 'individual', 'identity': identity,
        'network_status': 'unknown', 'deductible_remaining_cents': 0,
        'coinsurance_percent': '20.0', 'copay_cents': None, 'oop_remaining_cents': None,
        'referral_signal': 'unknown', 'authorization_signal': 'unknown',
        'review_reasons': ['Confirm the remaining individual out-of-pocket maximum.',
                           "Confirm the plan's referral requirement and, if required, that a referral is on file."],
        'requires_review': True}
    code = {'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}
    price, issues = api._previsit_range('synthetic-clinic',
        {'service_date': '2026-11-19', 'place_of_service': '11'},
        {'rendering_provider': {'npi': '1234567890'}}, 'JDNSN', [code], benefits,
        review={'selected_ids': ['office']})
    assert not issues and price['complete']
    assert price['lines'][0]['calculation']['patient_cents'] == 2392
    result = {'payer': 'Original Medicare', 'stedi_payer': {'id': 'JDNSN'},
              'benefits': benefits, 'identity': identity, 'price_range': price,
              'codes': [code], 'selected_ids': ['office']}
    guidance = collection_guidance.build(result)
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert guidance['amount_cents'] == 2392
    assert summary['collect_today_cents'] == 2392


def test_original_medicare_secondary_uses_provisional_primary_share(monkeypatch):
    def lookup(code, modifiers, units, *, service_date, place_of_service, provider_npi, patient_binding):
        return {'id': f'synthetic-medicare-{code}', 'code': code, 'modifier': '',
                'allowed_cents': 11960 if code == '99203' else 9633,
                'basis': 'medicare_fee_schedule', 'source_reference': 'Synthetic approved fee',
                'effective_from': '2026-11-13', 'service_date': service_date,
                'place_of_service': place_of_service, 'provider_npi': provider_npi,
                'patient_binding': patient_binding}
    monkeypatch.setattr(medicare_fees, 'estimate_rate', lookup)
    for code, expected in [('99203', 2392), ('99213', 1927)]:
        identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
        benefits = {'active': True, 'original_medicare': True, 'has_secondary': True,
            'service_date_matches': True, 'coverage_level': 'individual', 'identity': identity,
            'network_status': 'unknown', 'deductible_remaining_cents': 0,
            'coinsurance_percent': '20', 'copay_cents': None, 'oop_remaining_cents': None,
            'referral_signal': 'not_required', 'authorization_signal': 'not_required',
            'review_reasons': ['Confirm which plan pays first and how additional coverage applies.'],
            'requires_review': True}
        selected = {'id': 'office', 'code': code, 'units': 1, 'modifiers': []}
        price, issues = api._previsit_range('synthetic-clinic',
            {'service_date': '2026-11-23', 'place_of_service': '11'},
            {'rendering_provider': {'npi': '1234567890'}}, 'JDNSN', [selected], benefits,
            review={'selected_ids': ['office']})
        assert not issues and price['lines'][0]['allowed_cents'] in {9633, 11960}
        result = {'payer': 'Original Medicare', 'stedi_payer': {'id': 'JDNSN'},
            'service_date': '2026-11-23', 'benefits': benefits, 'identity': identity,
            'price_range': price, 'codes': [selected], 'selected_ids': ['office'],
            'service_selection_status': 'approved', 'payment_mode': 'insurance',
            'insurance_details': {'coverage_id': 'primary'},
            'coverage_records': [
                {'id': 'primary', 'status': 'active', 'order': 1, 'payer': ['Medicare of Texas']},
                {'id': 'secondary', 'status': 'active', 'order': 2, 'payer': ['Supplement']},
            ], 'recovery': {'reason_code': 'multiple_active_policies', 'active_policy_count': 2}}
        guidance = collection_guidance.build(result)
        assert guidance['amount_cents'] == expected
        assert guidance['collection_rule']['mode'] == 'original_medicare_office_coinsurance'
        assert guidance['primary_medicare_share_cents'] == expected
        approved_policy = {**result, 'collection_policy': {
            'mode': 'collect_supported_advance', 'approved_by': 'synthetic-user',
            'approved_at': '2026-11-01', 'reference': 'synthetic-approval'}}
        assert collection_guidance.build(approved_policy)['collection_rule']['mode'] == 'original_medicare_office_coinsurance'
        assert encounter_summary.build({**result, 'collection_guidance': guidance})['collect_today_cents'] == expected
        preview = {**result, 'service_selection_status': 'preview',
                   'service_plan': {'recommended_ids': ['office']}}
        assert collection_guidance.build(preview)['amount_cents'] == expected
        paid = collection_guidance.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                               'amount_cents': 500}})
        assert paid['amount_cents'] == max(0, expected - 500)
        assert encounter_summary.build({**result, 'collection_guidance': paid},
            {'payments': {'reference': 'synthetic-receipt', 'amount_cents': 500}})['collect_today_cents'] == max(0, expected - 500)
        protected = {**result, 'benefits': {**benefits, 'qmb_status': 'protected'}}
        assert collection_guidance.build(protected)['amount_cents'] != expected
        msp = {**result, 'benefits': {**benefits, 'medicare_part_b_secondary': {'status': 'reported'}}}
        assert collection_guidance.build(msp)['amount_cents'] != expected
        reversed_order = {**result, 'coverage_records': [
            {**result['coverage_records'][0], 'order': 2},
            {**result['coverage_records'][1], 'order': 1}]}
        assert collection_guidance.build(reversed_order)['amount_cents'] != expected
        conflicting_cob = {**result, 'coordination': {'status': 'primacy_reported',
            'payer_roles': [{'role': 'Primary Payer', 'payer_name': 'Other Plan'}]}}
        assert collection_guidance.build(conflicting_cob)['amount_cents'] != expected
        advantage = {**result, 'benefits': {**benefits,
            'plan_details': {'names': ['Example Medicare Advantage PPO']}}}
        assert collection_guidance.build(advantage)['amount_cents'] != expected


def test_original_medicare_not_marked_out_of_network_by_missing_commercial_contract():
    result = {'payer': 'Medicare of Texas'}
    benefits = {'active': True, 'original_medicare': True, 'network_status': 'unknown'}
    assert not apply_nmct_contract_absence(result, benefits, 'Medicare of Texas', None,
                                           '2026-11-23', True)
    assert benefits['network_status'] == 'unknown'


def test_pending_medicare_eligibility_has_no_saved_zero_amount():
    result = {'nmct_fixed_office_copay_advance_enabled': True,
              'eligibility_request_state': 'pending', 'payment_mode': 'insurance'}
    guidance = arrival_zero_default.apply(result, collection_guidance.build(result))
    assert guidance['amount_cents'] is None


def test_original_medicare_prices_without_commercial_connection(monkeypatch):
    monkeypatch.setattr(previsit_pricing._api.commercial_store, 'list_connections', lambda _: [])
    monkeypatch.setattr(previsit_pricing._api.network_participation, 'is_nmct_practice', lambda _: True)
    monkeypatch.setattr(previsit_pricing._api.staff_store, 'list_library', lambda _: [])
    monkeypatch.setattr(previsit_pricing._api.network_participation, 'nmct_library_contract', lambda *args: None)
    monkeypatch.setattr(previsit_pricing.library_network_exclusions, 'applicable_exclusion', lambda *args: None)
    monkeypatch.setattr(previsit_pricing, 'apply_nmct_product_attestation', lambda *args: False)
    monkeypatch.setattr(medicare_fees, 'estimate_rate', lambda code, modifiers, units, **scope: {
        'id': 'synthetic-medicare-fee', 'code': code, 'modifier': '', 'allowed_cents': 9633,
        'basis': 'medicare_fee_schedule', 'source_reference': 'Synthetic clinic fee',
        'effective_from': '2026-11-13', **scope})
    identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
    benefits = {'active': True, 'original_medicare': True, 'has_secondary': False,
        'service_date_matches': True, 'coverage_level': 'individual', 'identity': identity,
        'network_status': 'unknown', 'deductible_remaining_cents': 0,
        'coinsurance_percent': '20', 'copay_cents': None, 'oop_remaining_cents': None,
        'referral_signal': 'not_required', 'authorization_signal': 'not_required',
        'review_reasons': [], 'requires_review': False}
    result = {'payer': 'Medicare of Texas', 'codes': [
        {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}], 'issues': []}
    previsit_pricing._attach_previsit_pricing(result, 'synthetic-clinic',
        {'service_date': '2026-11-23', 'place_of_service': '11'},
        {'rendering_provider': {'npi': '1234567890'}}, 'JDNSN', benefits,
        review={'selected_ids': ['office']})
    assert result['price_range']['lines'][0]['allowed_cents'] == 9633
    assert result['price_range']['patient_min_cents'] == 1927
    assert benefits['network_status'] == 'unknown'
    assert 'temporary_rate_reference' not in result
    result.update(stedi_payer={'id': 'JDNSN'}, benefits=benefits, identity=identity,
                  selected_ids=['office'], service_selection_status='approved')
    assert collection_guidance.build(result)['amount_cents'] == 1927


def test_structured_qmb_period_blocks_medicare_cost_sharing():
    response = {'payerId': 'CMS', 'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'insuranceTypeCode': 'MB'},
        {'code': '1', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'insuranceTypeCode': 'QM',
         'dates': {'eligibility': {'start': '2026-01-01', 'end': '2026-12-31'}}},
    ]}
    active = commercial.normalize_stedi_271(response, '98', False,
                                              service_date='2026-11-23')
    assert active['qmb_status'] == 'protected'
    later = commercial.normalize_stedi_271(response, '98', False,
                                             service_date='2027-01-01')
    assert later['qmb_status'] == 'unknown'


def test_saved_medicare_reprices_from_verified_271_and_current_fee(monkeypatch):
    payload = {'payerId': 'CMS', 'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'insuranceTypeCode': 'MB'},
        {'code': 'C', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'benefitAmount': '0'},
        {'code': 'A', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'benefitPercent': '.20'},
    ]}
    raw = json.dumps(payload).encode()
    monkeypatch.setattr(saved_pricing._api.object_store, 'get_object_bytes', lambda _: raw)
    identity = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}
    fresh = {'active': True, 'original_medicare': True, 'has_secondary': True,
        'service_date_matches': True, 'coverage_level': 'individual', 'identity': identity,
        'network_status': 'unknown', 'deductible_remaining_cents': 0,
        'coinsurance_percent': '20', 'copay_cents': None, 'oop_remaining_cents': None,
        'referral_signal': 'not_required', 'authorization_signal': 'not_required',
        'review_reasons': ['Confirm which plan pays first and how additional coverage applies.'],
        'requires_review': True}
    monkeypatch.setattr(saved_pricing._api, '_case_benefits', lambda *args: fresh)
    monkeypatch.setattr(medicare_fees, 'estimate_rate', lambda code, modifiers, units, **scope: {
        'id': 'synthetic-medicare-fee', 'code': code, 'modifier': '', 'allowed_cents': 9633,
        'basis': 'medicare_fee_schedule', 'source_reference': 'Synthetic clinic fee',
        'effective_from': '2026-11-13', **scope})
    selected = {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}
    case = {'previsit': {'request_state': 'complete'}, 'previsit_raw_object_key': 'synthetic-key',
        'previsit_raw_sha256': hashlib.sha256(raw).hexdigest(),
        'previsit_context': {'payer_id': 'JDNSN',
            'inputs': {'service_date': '2026-11-23', 'place_of_service': '11'},
            'source': {'rendering_provider': {'npi': '1234567890'}}},
        'visit_review': {'selected_ids': ['office']}}
    result = {'payer': 'Medicare of Texas', 'benefits': {**fresh, 'network_status': 'out_of_network',
        'network_source': 'clinic_no_insurer_contract'},
        'identity': identity, 'codes': [selected], 'price_range': {'lines': []},
        'service_date': '2026-11-23', 'service_selection_status': 'approved',
        'selected_ids': ['office'], 'insurance_details': {'coverage_id': 'primary'},
        'coverage_records': [
            {'id': 'primary', 'status': 'active', 'order': 1, 'payer': ['Medicare of Texas']},
            {'id': 'secondary', 'status': 'active', 'order': 2, 'payer': ['Supplement']}],
        'temporary_rate_reference': {'source': 'old BCBS proxy'}}
    saved_pricing.refresh_saved_network_pricing(result, case, 'synthetic-clinic', None,
                                                ('out_of_network', 'clinic_no_insurer_contract'))
    saved_pricing.refresh_saved_cross_payer_planning(result, case, 'synthetic-clinic', None)
    assert result['price_range']['lines'][0]['allowed_cents'] == 9633
    assert result['price_range']['lines'][0]['allowed_basis'] == 'medicare_fee_schedule'
    assert 'temporary_rate_reference' not in result
    assert collection_guidance.build(result)['amount_cents'] == 1927
