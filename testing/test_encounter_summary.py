"""Financial summary keeps partial evidence separate from a full visit total."""
from app.insurance import encounter_summary


def test_known_office_copay_does_not_become_a_complete_encounter():
    result = {'benefits': {'network_status': 'unknown'},
              'collection_guidance': {'amount_cents': 3000, 'basis': 'office_copay'},
              'arrival_collection': {'payments_received_cents': None},
              'price_range': {'complete': False, 'lines': [
                  {'code': '99204', 'benefits': {'copay_cents': 3000}, 'calculation': None},
                  {'code': '95886', 'benefits': {}, 'calculation': None}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] == 3000
    assert summary['components']['copay_cents'] == 3000
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['estimated_later_balance_cents'] is None
    assert summary['unpriced_codes'] == ['99204', '95886']


def test_complete_encounter_uses_shared_calculation_and_verified_payment_once():
    result = {'benefits': {'network_status': 'in_network'},
              'collection_guidance': {'amount_cents': 7500},
              'arrival_collection': {'payments_received_cents': 500},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 10000,
                              'patient_max_cents': 10000, 'lines': [
                                  {'code': '99204', 'calculation': {'copay_cents': 3000,
                                                                  'deductible_cents': 4000, 'coinsurance_cents': 0}},
                                  {'code': '95886', 'calculation': {'copay_cents': 0,
                                                                  'deductible_cents': 1000, 'coinsurance_cents': 2000}}]}}
    summary = encounter_summary.build(result)
    assert summary['complete'] is True
    assert summary['components'] == {'copay_cents': 3000, 'deductible_cents': 5000, 'coinsurance_cents': 2000}
    assert summary['estimated_later_balance_cents'] == 2000


def test_partial_copay_component_respects_collection_cap_and_prior_payment():
    result = {'benefits': {'network_status': 'unknown'},
              'collection_guidance': {'amount_cents': 1500, 'basis': 'office_copay'},
              'arrival_collection': {'payments_received_cents': 500},
              'price_range': {'complete': False, 'lines': [
                  {'code': '99204', 'benefits': {'copay_cents': 3000, 'oop_remaining_cents': 2000}, 'calculation': None}]}}
    summary = encounter_summary.build(result, {'payments': {'reference': 'receipt-1', 'amount_cents': 500}})
    assert summary['components']['copay_cents'] == 2000
    assert summary['payments_verified_cents'] == 500


def test_conditional_complete_price_has_no_known_later_balance():
    result = {'benefits': {'network_status': 'in_network'},
              'collection_guidance': {'amount_cents': 1000},
              'arrival_collection': {'payments_received_cents': 0},
              'price_range': {'complete': True, 'conditional': True, 'mode': 'services',
                              'patient_min_cents': 3000, 'patient_max_cents': 3000,
                              'lines': [{'code': '99204', 'calculation': {'copay_cents': 3000,
                                                                         'deductible_cents': 0, 'coinsurance_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['estimated_total_patient_min_cents'] == 3000
    assert summary['estimated_later_balance_cents'] is None
