"""Synthetic checks for the database-backed Aetna fee schedule."""
from datetime import date
from unittest.mock import patch
import pytest

from app.insurance import aetna_fees
from app.api.routers import commercial_estimator  # noqa: F401 - resolve route imports
from app.api.routers.commercial_flow.commercial_previsit_pricing import _previsit_range


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


def test_aetna_category_requires_one_identified_medical_product():
    assert aetna_fees.category('Aetna', ['Aetna Medicare Signature (PPO)']) == 'advantage'
    assert aetna_fees.category('Aetna', ['Aetna Choice POS II']) == 'commercial'
    assert aetna_fees.category('Aetna', ['Aetna Medicare Supplement']) is None
    assert aetna_fees.category('Aetna', ['Aetna Choice POS II', 'Aetna Medicare PPO']) is None
    assert aetna_fees.category('Other payer', ['Aetna Choice POS II']) is None


def test_matching_rates_reads_current_database_amount_and_rejects_modifiers():
    db = FakeDatabase([{'code': '99213', 'allowed_cents': 4321,
                        'source_reference': 'synthetic approved schedule',
                        'effective_from': date(2026, 11, 13)}])
    lines = [{'id': 'office', 'code': '99213', 'units': 2, 'modifiers': []},
             {'id': 'modified', 'code': '99213', 'units': 1, 'modifiers': ['25']}]
    with patch.object(aetna_fees, '_connection', return_value=db):
        rates = aetna_fees.matching_rates('commercial', '2026-11-14', lines,
            place_of_service='11', provider_npi='1234567890',
            patient_binding='synthetic-person', product_binding='synthetic-product')
    assert db.params == ('commercial', ['99213'], date(2026, 11, 14))
    assert len(rates) == 1
    assert rates[0]['allowed_cents'] == 4321
    assert rates[0]['line_id'] == 'office'
    assert rates[0]['basis'] == 'aetna_fee_schedule'


@pytest.mark.parametrize(('plan', 'category'), [
    ('Aetna Choice POS II', 'commercial'),
    ('Aetna Medicare Signature (PPO)', 'advantage'),
])
def test_previsit_uses_aetna_schedule_without_a_contract_profile(plan, category):
    line = {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}
    benefits = {'plan_details': {'names': [plan]},
                'identity': {'binding': 'synthetic-person'},
                'network_status': 'in_network'}
    inputs = {'service_date': '2026-11-14', 'place_of_service': '11',
              'rendering_provider': {'npi': '1234567890'}}
    rate = {'id': 'synthetic-rate', 'line_id': 'office', 'code': '99213',
            'modifier': '', 'allowed_cents': 4321, 'basis': 'aetna_fee_schedule'}
    with patch('app.api.routers.commercial_flow.commercial_previsit_pricing.aetna_fees.matching_rates',
               return_value=[rate]) as lookup, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing._api.visit_estimate.price_lines',
               return_value={'complete': False, 'lines': []}) as price:
        _, issues = _previsit_range('other-practice', inputs,
            {'rendering_provider': {'npi': '1234567890'}}, 'synthetic-route',
            [line], benefits, payer_name='Aetna')
    assert lookup.call_args.args[:3] == (category, '2026-11-14', [line])
    assert price.call_args.args[1] == [rate]
    assert 'clinic contract' not in ' '.join(issues).lower()


def test_ambiguous_aetna_product_never_falls_back_to_a_different_schedule():
    benefits = {'plan_details': {'names': ['Aetna Medicare PPO', 'Aetna Choice POS II']},
                'identity': {'binding': 'synthetic-person'},
                'network_status': 'in_network'}
    line = {'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []}
    with patch('app.api.routers.commercial_flow.commercial_previsit_pricing.aetna_fees.matching_rates') as lookup, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing.medicare_fees.estimate_rate') as medicare, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing._api.commercial_store.matching_rates') as contract, \
         patch('app.api.routers.commercial_flow.commercial_previsit_pricing._api.visit_estimate.price_lines',
               return_value={'complete': False, 'lines': []}) as price:
        _, issues = _previsit_range('synthetic-practice',
            {'service_date': '2026-11-14', 'place_of_service': '11',
             'rendering_provider': {'npi': '1234567890'}}, {}, 'synthetic-route',
            [line], benefits, payer_name='Aetna')
    lookup.assert_not_called()
    medicare.assert_not_called()
    contract.assert_not_called()
    assert price.call_args.args[1] == []
    assert any('Identify one Aetna' in issue for issue in issues)
