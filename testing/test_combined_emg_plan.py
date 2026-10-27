"""Synthetic cost planning for an established office visit plus EMG/NCS."""

from copy import deepcopy

from app.insurance.visit_estimate import combined_emg_plan
from app.insurance.cpt_suggester import established_visit_for_planning


def case():
    terms = {'active': True, 'network_status': 'in_network', 'service_date_matches': True}
    return {'visit_type': 'established', 'established_for_planning': True, 'separate_em_planned': True,
            'service_date': '2026-10-28', 'payment_mode': 'insurance',
            'identity': {'match_version': 2, 'status': 'matched', 'binding': 'synthetic'},
            'benefits': {'active': True, 'service_date_matches': True,
                         'network_status': 'in_network', 'network_source': 'clinic_group_agreement'},
            'selected_ids': ['nerve', 'muscle', 'office'],
            'price_range': {'mode': 'services', 'complete': False, 'lines': [
                {'id': 'nerve', 'code': '95908', 'units': 1, 'allowed_cents': 10511,
                 'allowed_basis': 'clinic_contract', 'rate_resolution': {'status': 'RESOLVED'},
                 'benefits': {**terms, 'coinsurance_percent': '20.0', 'coinsurance_id': 'payer:nerve'}},
                {'id': 'muscle', 'code': '95886', 'units': 1, 'allowed_cents': 8918,
                 'allowed_basis': 'clinic_contract', 'rate_resolution': {'status': 'RESOLVED'},
                 'benefits': {**terms, 'coinsurance_percent': '20.0', 'coinsurance_id': 'payer:muscle'}},
                {'id': 'office', 'code': '99213', 'units': 1, 'allowed_cents': 7615,
                 'allowed_basis': 'clinic_contract', 'rate_resolution': {'status': 'RESOLVED'},
                 'benefits': {**terms, 'copay_cents': 5500, 'copay_id': 'payer:office',
                              'coinsurance_percent': '0'}}]}}


def test_combined_emg_plan_uses_office_copay_and_diagnostic_coinsurance_once():
    result = combined_emg_plan(case())
    assert result['office_copay_cents'] == 5500
    assert result['diagnostic_allowed_cents'] == 19429
    assert result['diagnostic_coinsurance_cents'] == 3886
    assert result['total_allowed_cents'] == 27044
    assert result['amount_cents'] == result['patient_share_cents'] == 9386
    assert combined_emg_plan(case(), {'payments': {'reference': 'synthetic-ledger', 'amount_cents': 1000}})['amount_cents'] == 8386


def test_prior_office_visit_supports_checkbox_only_when_status_is_not_new():
    source = {'recent_completed_appointments': [{'coded_services': ['99204']}]}
    assert established_visit_for_planning({'visit_type': None}, source)
    assert established_visit_for_planning({'visit_type': 'established'}, {})
    assert not established_visit_for_planning({'visit_type': 'new'}, source)


def test_combined_emg_plan_requires_established_selected_and_scoped_payer_terms():
    for change in ('new', 'unchecked', 'missing_office', 'different_member', 'oop_met', 'oop_known', 'deductible_due', 'unpriced'):
        result = deepcopy(case())
        if change == 'new':
            result['visit_type'] = 'new'
            result['established_for_planning'] = False
        elif change == 'unchecked':
            result['separate_em_planned'] = False
        elif change == 'missing_office':
            result['price_range']['lines'][2]['benefits']['copay_id'] = None
        elif change == 'different_member':
            result['identity']['status'] = 'review'
        elif change == 'oop_met':
            result['price_range']['lines'][0]['benefits']['oop_remaining_cents'] = 0
        elif change == 'oop_known':
            result['price_range']['lines'][0]['benefits']['oop_remaining_cents'] = 2000
        elif change == 'deductible_due':
            result['price_range']['lines'][0]['benefits']['deductible_remaining_cents'] = 35000
        elif change == 'unpriced':
            result['price_range']['lines'][0]['rate_resolution']['status'] = 'MISSING'
        assert combined_emg_plan(result) is None, change
