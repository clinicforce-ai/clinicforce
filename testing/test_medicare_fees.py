"""Synthetic checks for the clinic-approved Medicare fee schedule."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.core import patient_store
from app.core.patient_schema_insurance import initialize_insurance
from app.insurance import medicare_fees
from app.api.routers import commercial_estimator as api


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
