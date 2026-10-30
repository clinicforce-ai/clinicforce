"""Synthetic Aetna MEPPO pricing and source-boundary checks."""
from copy import deepcopy
from unittest.mock import patch

from app.insurance import visit_estimate
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
            'network_source': 'clinic_nmct_policy', 'has_secondary': False,
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
