"""Synthetic checks for the database-backed payer fee lookup."""
from datetime import date
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from app.insurance import payer_fees
from app.insurance import collection_guidance
from app.api.routers import commercial_estimator  # noqa: F401
from app.api.routers.commercial_flow.commercial_previsit_pricing import _attach_previsit_pricing, _previsit_range


def test_sql_import_contains_approved_payers_and_only_verified_bcbstx_fallbacks():
    sql = (Path(__file__).resolve().parents[1] / 'scripts/sql/2026-11-13-payer-fee-schedules.sql').read_text()
    db = sqlite3.connect(':memory:')
    db.executescript(sql)
    rows = db.execute('SELECT payer_key, count(*) FROM payer_fee_schedule GROUP BY payer_key').fetchall()
    assert rows == [('cigna', 11), ('humana', 15), ('multiplan', 14), ('uhc', 17), ('wellpoint', 15)]
    for payer, code, expected in [
        ('cigna', '99203', 10117), ('cigna', '99204', 15113),
        ('wellpoint', '99204', 18049), ('uhc', '64611', 12013),
        ('humana', '99204', 17147), ('multiplan', '95912', 23123),
    ]:
        assert db.execute('SELECT allowed_cents FROM payer_fee_schedule WHERE payer_key=? AND code=?',
                          (payer, code)).fetchone() == (expected,)
    for payer, code in [('cigna', 'J0585'), ('cigna', '98006'),
                        ('multiplan', '64611'), ('multiplan', '95983')]:
        assert db.execute('SELECT allowed_cents FROM payer_fee_schedule WHERE payer_key=? AND code=?',
                          (payer, code)).fetchone() is None


class FakeDatabase:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _query, params):
        self.params = params
        return self

    def fetchall(self):
        return self.rows


@pytest.mark.parametrize(('payer', 'plan', 'expected'), [
    ('Cigna', 'Cigna Open Access Plus', ('cigna', 'commercial')),
    ('Wellpoint', 'Wellpoint Medicare Advantage PPO', ('wellpoint', 'advantage')),
    ('UnitedHealthcare', 'UnitedHealthcare Choice Plus', ('uhc', 'commercial')),
    ('Humana', 'Humana Medicare Advantage HMO', ('humana', 'medicare')),
    ('MultiPlan', 'MultiPlan PHCS PPO', ('multiplan', 'phcs')),
])
def test_selected_payer_categories(payer, plan, expected):
    key = payer_fees.payer_key(payer)
    assert (key, payer_fees.category(key, [plan])) == expected


def test_missing_or_mixed_product_never_chooses_a_schedule():
    assert payer_fees.category('cigna', []) is None
    assert payer_fees.category('uhc', ['UHC Choice Plus', 'UHC Medicare Advantage']) is None
    assert payer_fees.category('wellpoint', ['Wellpoint commercial PPO']) is None
    assert payer_fees.category('multiplan', ['MultiPlan PPO']) is None


def test_member_bound_uhc_choice_plus_network_can_select_commercial_fee_category():
    plan = {'names': [], 'network_names': ['UNITEDHEALTHCARE CHOICE PLUS'],
            'group_number': 'SYNTHETIC-GROUP', 'group_name': 'Synthetic employer benefit plan'}
    assert payer_fees.fee_schedule_products('United Healthcare', plan) == ['UnitedHealthcare Choice Plus']
    assert payer_fees.fee_schedule_products('United Healthcare',
        {**plan, 'group_name': 'Synthetic Medicare plan'}) == []
    assert payer_fees.fee_schedule_products('United Healthcare',
        {**plan, 'network_names': ['UNITEDHEALTHCARE CHOICE PLUS', 'Other network']}) == []
    assert payer_fees.fee_schedule_products('Humana', plan) == []


def test_uhc_network_only_product_prices_selected_televisit_from_database():
    line = {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}
    benefits = {'plan_details': {'names': [], 'network_names': ['UNITEDHEALTHCARE CHOICE PLUS'],
        'group_number': 'SYNTHETIC-GROUP', 'group_name': 'Synthetic employer benefit plan'},
        'identity': {'binding': 'synthetic-person'}, 'network_status': 'in_network'}
    inputs = {'service_date': '2026-11-14', 'place_of_service': '10',
              'rendering_provider': {'npi': '1234567890'}}
    rate = {'id': 'db:99213', 'line_id': 'office', 'code': '99213', 'modifier': '',
            'allowed_cents': 8178, 'basis': 'payer_fee_schedule'}
    with patch.object(payer_fees, 'active_schedule', return_value=True), \
         patch.object(payer_fees, 'matching_rates', return_value=[rate]) as lookup, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing._api._previsit_practice_path',
               side_effect=AssertionError('No clinic profile should be read')):
        priced, issues = _previsit_range('synthetic-practice', inputs,
            {'rendering_provider': {'npi': '1234567890'}, 'payer_plan_bound': True},
            'synthetic-route', [line], benefits, payer_name='United Healthcare')
    assert lookup.called
    assert priced['lines'][0]['allowed_cents'] == 8178
    assert not issues


def test_current_database_fee_and_fixed_uhc_office_copay_set_checkin_amount():
    identity = {'status': 'matched', 'match_version': 2, 'binding': 'synthetic-person'}
    benefits = {'active': True, 'service_date_matches': True, 'identity': identity,
        'network_status': 'in_network', 'network_source': 'clinic_library_contract',
        'copay_cents': 5000, 'copay_period': 'VISIT', 'deductible_applies': False,
        'coinsurance_percent': '0', 'oop_remaining_cents': 337064}
    line = {'id': 'suggested:99213', 'code': '99213', 'units': 1, 'modifiers': [],
            'allowed_cents': 8178, 'allowed_basis': 'payer_fee_schedule',
            'rate_resolution': {'status': 'RESOLVED'}, 'benefits': benefits,
            'calculation': {'patient_cents': 5000, 'plan_cents': 3178}}
    result = {'payer': 'United Healthcare', 'benefits': benefits, 'identity': identity,
        'service_selection_status': 'preview',
        'service_plan': {'recommended_ids': ['suggested:99213']},
        'codes': [{'id': 'suggested:99213', 'code': '99213', 'units': 1}],
        'price_range': {'mode': 'services', 'complete': True, 'conditional': False,
            'patient_min_cents': 5000, 'patient_max_cents': 5000, 'lines': [line]}}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 5000
    assert guidance['basis'] == 'office_copay'
    line['rate_resolution']['status'] = 'RATE_UNAVAILABLE'
    assert collection_guidance.build(result)['amount_cents'] is None
    line['rate_resolution']['status'] = 'RESOLVED'
    benefits['network_source'] = None
    assert collection_guidance.build(result)['amount_cents'] is None


def test_lookup_uses_database_amount_and_leaves_missing_code_unpriced():
    db = FakeDatabase([{'code': '99203', 'allowed_cents': 10117,
                        'source_reference': 'BCBSTX synthetic fee row',
                        'effective_from': date(2026, 11, 13)}])
    lines = [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []},
             {'id': 'missing', 'code': 'J0585', 'units': 1, 'modifiers': []},
             {'id': 'modified', 'code': '99203', 'units': 1, 'modifiers': ['25']}]
    with patch.object(payer_fees, '_connection', return_value=db):
        rates = payer_fees.matching_rates('cigna', 'commercial', '2026-11-14', lines,
            place_of_service='11', provider_npi='1234567890',
            patient_binding='synthetic-person', product_binding='synthetic-product')
    assert db.params[0:2] == ('cigna', 'commercial')
    assert set(db.params[2]) == {'99203', 'J0585'}
    assert len(rates) == 1
    assert rates[0]['allowed_cents'] == 10117
    assert rates[0]['basis'] == 'payer_fee_schedule'
    with patch.object(payer_fees, '_connection', return_value=db):
        assert payer_fees.matching_rates('cigna', 'commercial', '2026-11-14', lines,
            place_of_service='10', provider_npi='1234567890',
            patient_binding='synthetic-person', product_binding='synthetic-product') == []


@pytest.mark.parametrize(('payer', 'plan'), [
    ('Cigna', 'Cigna Open Access Plus'),
    ('Wellpoint', 'Wellpoint Medicare Advantage PPO'),
    ('UnitedHealthcare', 'UnitedHealthcare Choice Plus'),
    ('Humana', 'Humana Medicare Advantage HMO'),
    ('MultiPlan', 'MultiPlan PHCS PPO'),
])
def test_previsit_uses_database_schedule_without_contract_profile(payer, plan):
    line = {'id': 'office', 'code': '99204', 'units': 1, 'modifiers': []}
    benefits = {'plan_details': {'names': [plan]}, 'identity': {'binding': 'synthetic-person'},
                'network_status': 'in_network'}
    inputs = {'service_date': '2026-11-14', 'place_of_service': '11',
              'rendering_provider': {'npi': '1234567890'}}
    rate = {'id': 'db:99204', 'line_id': 'office', 'code': '99204', 'modifier': '',
            'allowed_cents': 15113, 'basis': 'payer_fee_schedule'}
    with patch.object(payer_fees, 'active_schedule', return_value=True), \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing.payer_fees.matching_rates',
               return_value=[rate]) as lookup, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing._api.visit_estimate.price_lines',
               return_value={'complete': False, 'lines': []}) as price:
        _, issues = _previsit_range('synthetic-practice', inputs,
            {'rendering_provider': {'npi': '1234567890'}, 'payer_plan_bound': True},
            'synthetic-route', [line], benefits, payer_name=payer)
    assert lookup.called
    assert price.call_args.args[1] == [rate]
    assert not issues


@pytest.mark.parametrize(('payer', 'plan', 'code', 'expected'), [
    ('Cigna', 'Cigna Open Access Plus', '99204', 15113),
    ('Wellpoint', 'Wellpoint Medicare Advantage PPO', '99204', 18049),
    ('UnitedHealthcare', 'UnitedHealthcare Choice Plus', '64611', 12013),
    ('Humana', 'Humana Medicare Advantage HMO', '99204', 17147),
    ('MultiPlan', 'MultiPlan PHCS PPO', '95912', 23123),
])
def test_selected_visit_prices_from_imported_database_row(payer, plan, code, expected):
    sql = (Path(__file__).resolve().parents[1] / 'scripts/sql/2026-11-13-payer-fee-schedules.sql').read_text()
    imported = sqlite3.connect(':memory:')
    imported.executescript(sql)
    key = payer_fees.payer_key(payer)
    row = imported.execute('''SELECT code, allowed_cents, source_reference, effective_from
        FROM payer_fee_schedule WHERE payer_key=? AND code=?''', (key, code)).fetchone()
    db = FakeDatabase([dict(zip(('code', 'allowed_cents', 'source_reference', 'effective_from'), row))])
    benefits = {'plan_details': {'names': [plan]}, 'identity': {'binding': 'synthetic-person'},
                'network_status': 'in_network'}
    inputs = {'service_date': '2026-11-14', 'place_of_service': '11',
              'rendering_provider': {'npi': '1234567890'}}
    line = {'id': 'selected', 'code': code, 'units': 1, 'modifiers': []}
    with patch.object(payer_fees, 'active_schedule', return_value=True), \
         patch.object(payer_fees, '_connection', return_value=db):
        priced, issues = _previsit_range('synthetic-practice', inputs,
            {'rendering_provider': {'npi': '1234567890'}, 'payer_plan_bound': True},
            'synthetic-route', [line], benefits, payer_name=payer)
    assert priced['lines'][0]['allowed_cents'] == expected
    assert priced['lines'][0]['allowed_basis'] == 'payer_fee_schedule'
    assert not issues


def test_uhc_choice_epo_fee_schedule_keeps_clinic_network_decision(monkeypatch):
    from app.api.routers.commercial_flow import commercial_previsit_pricing as pricing

    agreement = {'id': 'synthetic-uhc-agreement', 'kind': 'contract',
                 'payer': 'UnitedHealthcare', 'effective_from': '2026-01-01'}
    connection = {'id': 'synthetic-uhc-route', 'stedi_payer_id': '87726',
                  'display_name': 'UnitedHealthcare', 'status': 'staff_guidance_enabled'}
    monkeypatch.setattr(pricing._api.staff_store, 'list_library', lambda _: [agreement])
    monkeypatch.setattr(pricing._api.commercial_store, 'list_connections', lambda _: [connection])
    monkeypatch.setattr(pricing.library_network_exclusions, 'applicable_exclusion', lambda *args: None)
    monkeypatch.setattr(pricing.payer_fees, 'schedule_for_service', lambda *args: ('uhc', 'commercial'))
    monkeypatch.setattr(pricing, '_attach_temporary_uhc_rates', lambda *args: None)
    monkeypatch.setattr(pricing.payer_fees, 'matching_rates', lambda *args, **kwargs: [{
        'id': 'synthetic-fee', 'line_id': 'office', 'code': '99203', 'modifier': '',
        'units': 1, 'allowed_cents': 10036, 'basis': 'payer_fee_schedule',
        'source_reference': 'Synthetic clinic fee'}])
    result = {'payer': 'UnitedHealthcare', 'codes': [{'id': 'office', 'code': '99203', 'units': 1}],
              'issues': [], 'service_date': '2026-11-14', 'service_selection_status': 'selected',
              'selected_ids': ['office']}
    identity = {'status': 'matched', 'binding': 'synthetic-person', 'match_version': 2}
    benefits = {'network_status': 'unknown', 'active': True, 'service_date_matches': True,
                'identity': identity, 'coverage_level': 'individual',
                'plan_details': {'names': ['CHOICE EPO']}, 'copay_cents': 3000,
                'copay_period': 'VISIT', 'deductible_applies': False,
                'deductible_remaining_cents': 0, 'coinsurance_percent': '0',
                'oop_remaining_cents': 0, 'oop_annual_cents': 240000,
                'has_secondary': False, 'cost_share': 'copay'}
    _attach_previsit_pricing(result, 'primary',
        {'service_date': '2026-11-14', 'place_of_service': '11',
         'rendering_provider': {'npi': '1234567890'}},
        {'plan': 'CHOICE EPO', 'payer_plan_bound': True,
         'payer_plan_names': ['CHOICE EPO'],
         'rendering_provider': {'npi': '1234567890'}}, '87726', benefits)
    result.update(benefits=benefits, identity=identity)
    guidance = collection_guidance.build(result)
    assert benefits['network_source'] == 'clinic_library_contract'
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['document_id'] == agreement['id']
    assert result['price_range']['complete'] is True
    assert result['price_range']['allowed_min_cents'] == 10036
    assert result['price_range']['patient_min_cents'] == 0
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'out_of_pocket_limit'
