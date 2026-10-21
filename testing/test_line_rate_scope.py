"""Duplicate CPT lines cannot borrow each other's visit-specific quote."""
from __future__ import annotations

from app.insurance.pricing.visit import price_lines


DAY = '2026-10-14'
RULES = {'cost_share': 'payer_returned', 'provider_npi': '1234567890',
         'place_of_service': '11', 'patient_binding': 'synthetic-member',
         'product_binding': 'synthetic-product'}
BENEFITS = {'network_status': 'out_of_network', 'network_source': 'payer_participation_message',
            'review_reasons': [], 'active': True, 'identity': {
                'status': 'matched', 'match_version': 2, 'binding': 'synthetic-member'}}


def quote(line_id: str, units: int, cents: int, *, modifier: str = '') -> dict:
    return {'id': 'payer:' + line_id, 'line_id': line_id, 'code': '95886',
            'modifier': modifier, 'units': units, 'allowed_cents': cents,
            'basis': 'payer_out_of_network', 'source_reference': 'Synthetic payer quote',
            'verified_at': DAY, 'service_date': DAY, 'provider_npi': '1234567890',
            'place_of_service': '11', 'patient_binding': 'synthetic-member',
            'product_binding': 'synthetic-product'}


def test_duplicate_cpt_lines_keep_exact_line_units_and_modifiers():
    lines = [{'id': 'one', 'code': '95886', 'units': 1, 'modifiers': []},
             {'id': 'two', 'code': '95886', 'units': 2, 'modifiers': []}]
    priced = price_lines(lines, [quote('one', 1, 10000), quote('two', 2, 9000)],
                         BENEFITS, RULES, service_date=DAY)
    assert [line['unit_allowed_cents'] for line in priced['lines']] == [10000, 9000]
    assert [line['allowed_cents'] for line in priced['lines']] == [10000, 18000]
    assert all(line['rate_resolution']['status'] == 'RESOLVED' for line in priced['lines'])
    missing = price_lines(lines, [quote('one', 1, 10000)], BENEFITS, RULES, service_date=DAY)
    assert missing['lines'][1]['rate_resolution']['status'] == 'RATE_UNAVAILABLE'
    wrong_modifier = price_lines(lines[:1], [quote('one', 1, 10000, modifier='26')],
                                 BENEFITS, RULES, service_date=DAY)
    assert wrong_modifier['lines'][0]['rate_resolution']['status'] == 'RATE_UNAVAILABLE'


def test_conflicting_quotes_and_unscoped_quotes_never_pick_first():
    line = [{'id': 'one', 'code': '95886', 'units': 1, 'modifiers': []}]
    conflict = price_lines(line, [quote('one', 1, 10000),
                                  {**quote('one', 1, 11000), 'id': 'payer:other'}],
                           BENEFITS, RULES, service_date=DAY)
    assert conflict['lines'][0]['rate_resolution']['status'] == 'RATE_CONFLICT'
    assert conflict['lines'][0]['allowed_cents'] is None
    unscoped = price_lines(line, [{k: v for k, v in quote('one', 1, 10000).items()
                                   if k != 'line_id'}], BENEFITS, RULES, service_date=DAY)
    assert unscoped['lines'][0]['rate_resolution']['status'] == 'RATE_UNAVAILABLE'
