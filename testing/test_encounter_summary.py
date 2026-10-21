"""Financial summary keeps partial evidence separate from a full visit total."""
from copy import deepcopy

import pytest

from app.insurance import encounter_summary


def test_known_office_copay_does_not_become_a_complete_encounter():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True, 'network_status': 'unknown'},
              'collection_guidance': {'amount_cents': 3000, 'basis': 'office_copay'},
              'arrival_collection': {'payments_received_cents': None},
              'price_range': {'complete': False, 'lines': [
                  {'code': '99204', 'benefits': {'copay_cents': 3000, 'network_independent': True}, 'calculation': None},
                  {'code': '95886', 'benefits': {}, 'calculation': None}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] == 3000
    assert summary['components']['copay_cents'] == 3000
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['estimated_later_balance_cents'] is None
    assert summary['unpriced_codes'] == ['99204', '95886']


def test_zero_advance_policy_does_not_become_zero_patient_liability():
    result = {'benefits': {'network_status': 'unknown'},
              'collection_guidance': {'amount_cents': 0, 'basis': 'claim_after_service'},
              'price_range': {'complete': False, 'lines': [{'code': '99203', 'calculation': None}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['estimated_total_patient_max_cents'] is None
    assert summary['estimated_later_balance_cents'] is None


def test_payer_scoped_out_of_network_share_keeps_advance_without_full_patient_total():
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'payer_participation_message'},
              'collection_guidance': {'amount_cents': 4000, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 4000,
                              'patient_max_cents': 4000, 'lines': [{
                                  'code': '99203', 'allowed_basis': 'payer_out_of_network',
                                  'allowed_cents': 10000, 'rate_resolution': {'status': 'RESOLVED'},
                                  'calculation': {'patient_cents': 4000, 'copay_cents': 0,
                                                  'deductible_cents': 0, 'coinsurance_cents': 4000,
                                                  'coinsurance_base_cents': 10000,
                                                  'coinsurance_percent': '40',
                                                  'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] == 4000
    assert summary['components']['coinsurance_cents'] == 4000
    assert summary['coinsurance_base_cents'] == 10000
    assert summary['complete'] is False
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['estimated_later_balance_cents'] is None

    without_provider_statement = deepcopy(result)
    without_provider_statement['benefits'].pop('network_source')
    assert encounter_summary.build(without_provider_statement)['collect_today_cents'] is None
    wrong_price_source = deepcopy(result)
    wrong_price_source['price_range']['lines'][0]['allowed_basis'] = 'clinic_contract'
    assert encounter_summary.build(wrong_price_source)['collect_today_cents'] is None


def test_provider_specific_out_of_network_office_copay_is_a_partial_advance():
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'payer_participation_message'},
              'collection_guidance': {'amount_cents': 3000, 'basis': 'office_copay'},
              'price_range': {'complete': False, 'mode': 'services', 'lines': [
                  {'code': '99203', 'benefits': {'copay_cents': 3000}, 'calculation': None}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] == 3000
    assert summary['components']['copay_cents'] == 3000
    assert summary['estimated_total_patient_min_cents'] is None
    result['benefits'].pop('network_source')
    assert encounter_summary.build(result)['collect_today_cents'] is None


def test_discovery_order_or_identity_conflict_hides_saved_insured_total():
    price = {'complete': True, 'mode': 'services', 'patient_min_cents': 10000,
             'patient_max_cents': 10000, 'lines': [{'code': '99203',
                 'calculation': {'copay_cents': 10000, 'deductible_cents': 0,
                                 'coinsurance_cents': 0, 'oop_adjustment_cents': 0}}]}
    for extra in ({'recovery': {'reason_code': 'multiple_active_policies'}},
                  {'identity': {'status': 'review'}},
                  {'benefits': {'network_status': 'in_network', 'has_secondary': True}}):
        result = {'payment_mode': 'insurance', 'benefits': {'network_status': 'in_network'},
                  'collection_guidance': {'amount_cents': 10000}, 'price_range': price, **extra}
        summary = encounter_summary.build(result)
        assert summary['collect_today_cents'] is None
        assert summary['estimated_total_patient_min_cents'] is None
        assert summary['components']['copay_cents'] is None


def test_unsourced_in_network_label_cannot_restore_a_saved_collection_or_total():
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network'},
              'collection_guidance': {'amount_cents': 3800, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3800,
                              'patient_max_cents': 3800, 'lines': [{'code': '99203',
                                  'calculation': {'copay_cents': 3800, 'deductible_cents': 0,
                                                  'coinsurance_cents': 0, 'oop_adjustment_cents': 0}}]}}
    unsupported = encounter_summary.build(result)
    assert unsupported['collect_today_cents'] is None
    assert unsupported['estimated_total_patient_min_cents'] is None
    result['benefits']['network_source'] = 'clinic_library_contract'
    supported = encounter_summary.build(result)
    assert supported['collect_today_cents'] == 3800
    assert supported['estimated_total_patient_min_cents'] == 3800


def test_uncleared_documented_authorization_hides_cached_advance_and_full_total():
    result = {'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'requirement_evidence': {'authorization': {'status': 'Required', 'approval_status': 'Not verified'}},
              'collection_guidance': {'amount_cents': 3800, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3800,
                              'patient_max_cents': 3800, 'lines': [{
                                  'code': '99203', 'calculation': {'patient_cents': 3800,
                                      'copay_cents': 3800, 'deductible_cents': 0,
                                      'coinsurance_cents': 0, 'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None
    assert summary['complete'] is False and summary['conditional'] is True


@pytest.mark.parametrize('field', ['coverage_status_conflict', 'coverage_dates_invalid'])
@pytest.mark.parametrize('scope', ['plan', 'cpt'])
def test_structured_coverage_conflict_hides_saved_insured_total(field, scope):
    result = {'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 3800, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3800,
                              'patient_max_cents': 3800, 'lines': [{'code': '99203', 'benefits': {},
                                  'calculation': {'patient_cents': 3800, 'copay_cents': 3800,
                                                  'deductible_cents': 0, 'coinsurance_cents': 0,
                                                  'oop_adjustment_cents': 0}}]}}
    target = result['benefits'] if scope == 'plan' else result['price_range']['lines'][0]['benefits']
    target[field] = True
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None


@pytest.mark.parametrize('line_change', [
    {'active': False}, {'service_date_matches': False}, {'network_conflict': True},
    {'has_secondary': True}, {'historical_only': True},
    {'identity': {'status': 'review'}},
    {'identity': {'status': 'matched', 'binding': 'another-member', 'match_version': 2}},
    {'network_status': 'out_of_network'},
])
def test_explicit_selected_cpt_benefit_conflict_hides_saved_total(line_change):
    result = {'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 3800, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3800,
                              'patient_max_cents': 3800, 'lines': [{'code': '99203',
                                  'benefits': line_change,
                                  'calculation': {'patient_cents': 3800, 'copay_cents': 3800,
                                                  'deductible_cents': 0, 'coinsurance_cents': 0,
                                                  'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None


@pytest.mark.parametrize('change', [
    {'active': False}, {'service_date_matches': False}, {'network_conflict': True},
    {'source_errors': [{'path': 'benefits.copay', 'kind': 'invalid_amount'}]},
    {'review_reasons': ['Provider-network evidence reports nonparticipation or contradicts the in-network assertion.']},
])
def test_current_payer_contradiction_hides_saved_insured_collection_and_total(change):
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True,
              'network_status': 'in_network', 'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 3800, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3800,
                              'patient_max_cents': 3800, 'lines': [{'code': '99203',
                                  'calculation': {'copay_cents': 3800, 'deductible_cents': 0,
                                                  'coinsurance_cents': 0, 'oop_adjustment_cents': 0}}]}}
    supported = encounter_summary.build(result)
    assert supported['collect_today_cents'] == 3800
    changed = deepcopy(result)
    changed['benefits'].update(change)
    unsupported = encounter_summary.build(changed)
    assert unsupported['collect_today_cents'] is None
    assert unsupported['estimated_total_patient_min_cents'] is None
    assert unsupported['components']['copay_cents'] is None
    changed['collection_guidance'] = {'amount_cents': 0, 'basis': 'out_of_pocket_limit'}
    assert encounter_summary.build(changed)['collect_today_cents'] is None
    changed['collection_guidance'] = {'amount_cents': 0, 'basis': 'claim_after_service'}
    assert encounter_summary.build(changed)['collect_today_cents'] == 0


def test_complete_encounter_uses_shared_calculation_and_verified_payment_once():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 7500},
              'arrival_collection': {'payments_received_cents': 500},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 10000,
                              'patient_max_cents': 10000, 'lines': [
                                  {'code': '99204', 'calculation': {'copay_cents': 3000,
                                                                  'deductible_cents': 4000, 'coinsurance_cents': 0,
                                                                  'coinsurance_base_cents': 0, 'coinsurance_percent': '20',
                                                                  'oop_adjustment_cents': 0}},
                                  {'code': '95886', 'calculation': {'copay_cents': 0,
                                                                  'deductible_cents': 1000, 'coinsurance_cents': 2000,
                                                                  'coinsurance_base_cents': 10000, 'coinsurance_percent': '20',
                                                                  'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['complete'] is True
    assert summary['components'] == {'copay_cents': 3000, 'deductible_cents': 5000,
                                     'coinsurance_cents': 2000, 'allowed_cap_adjustment_cents': None,
                                     'oop_adjustment_cents': 0}
    assert summary['coinsurance_base_cents'] == 10000
    assert summary['coinsurance_percent'] == '20'
    assert summary['estimated_later_balance_cents'] == 2000


def test_saved_coinsurance_formula_must_match_its_line_amount():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 3000, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3000,
                              'patient_max_cents': 3000, 'lines': [{'code': '99203',
                                  'calculation': {'patient_cents': 3000, 'copay_cents': 0,
                                                  'deductible_cents': 0, 'coinsurance_cents': 3000,
                                                  'coinsurance_base_cents': 10000,
                                                  'coinsurance_percent': '20',
                                                  'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['integrity_error'] == 'line_amount_mismatch'
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None


def test_per_cpt_rounding_does_not_create_a_false_aggregate_formula():
    line = {'calculation': {'patient_cents': 1, 'copay_cents': 0,
                            'deductible_cents': 0, 'coinsurance_cents': 1,
                            'coinsurance_base_cents': 1, 'coinsurance_percent': '50',
                            'oop_adjustment_cents': 0}}
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 2, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 2,
                              'patient_max_cents': 2, 'lines': [
                                  {'code': '99203', **deepcopy(line)},
                                  {'code': '95886', **deepcopy(line)}]}}
    summary = encounter_summary.build(result)
    assert summary['integrity_error'] is None
    assert summary['collect_today_cents'] == 2
    assert summary['components']['coinsurance_cents'] == 2
    assert summary['coinsurance_percent'] == '50'
    assert summary['coinsurance_base_cents'] is None
    assert summary['coinsurance_rounded_per_line'] is True


def test_equivalent_coinsurance_rate_formats_keep_one_valid_formula():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 40, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 40,
                              'patient_max_cents': 40, 'lines': [
                                  {'code': '99203', 'calculation': {'patient_cents': 20,
                                      'copay_cents': 0, 'deductible_cents': 0,
                                      'coinsurance_cents': 20, 'coinsurance_base_cents': 100,
                                      'coinsurance_percent': '20', 'oop_adjustment_cents': 0}},
                                  {'code': '95886', 'calculation': {'patient_cents': 20,
                                      'copay_cents': 0, 'deductible_cents': 0,
                                      'coinsurance_cents': 20, 'coinsurance_base_cents': 100,
                                      'coinsurance_percent': '20.0', 'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['integrity_error'] is None
    assert summary['coinsurance_percent'] == '20'
    assert summary['coinsurance_base_cents'] == 200


def test_saved_complete_insured_amount_must_reconcile_with_selected_cpt_and_payment():
    result = {'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 2500, 'basis': 'anticipated_services'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 3000,
                              'patient_max_cents': 3000, 'lines': [{'code': '99203',
                                  'calculation': {'patient_cents': 2000, 'copay_cents': 2000,
                                                  'deductible_cents': 0, 'coinsurance_cents': 0,
                                                  'allowed_cap_adjustment_cents': 0,
                                                  'oop_adjustment_cents': 0}}]}}
    summary = encounter_summary.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                              'amount_cents': 500}})
    assert summary['integrity_error'] == 'patient_total_mismatch'
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None

    result['price_range']['patient_min_cents'] = 2000
    result['price_range']['patient_max_cents'] = 2000
    summary = encounter_summary.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                              'amount_cents': 500}})
    assert summary['integrity_error'] == 'collection_amount_mismatch'
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] == 2000

    result['collection_guidance']['amount_cents'] = 1500
    summary = encounter_summary.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                              'amount_cents': 500}})
    assert summary['integrity_error'] is None
    assert summary['collect_today_cents'] == 1500

    result['price_range']['lines'][0]['calculation']['copay_cents'] = 3000
    summary = encounter_summary.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                              'amount_cents': 500}})
    assert summary['integrity_error'] == 'line_amount_mismatch'
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None

    result['collection_guidance'] = {'amount_cents': 1500, 'basis': 'office_copay'}
    summary = encounter_summary.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                              'amount_cents': 500}})
    assert summary['collect_today_cents'] == 1500
    assert summary['estimated_total_patient_min_cents'] is None


def test_partial_copay_component_respects_collection_cap_and_prior_payment():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True, 'network_status': 'unknown'},
              'collection_guidance': {'amount_cents': 1500, 'basis': 'office_copay'},
              'arrival_collection': {'payments_received_cents': 500},
              'price_range': {'complete': False, 'lines': [
                  {'code': '99204', 'benefits': {'copay_cents': 3000, 'oop_remaining_cents': 2000,
                                               'network_independent': True}, 'calculation': None}]}}
    summary = encounter_summary.build(result, {'payments': {'reference': 'receipt-1', 'amount_cents': 500}})
    assert summary['components']['copay_cents'] == 2000
    assert summary['payments_verified_cents'] == 500


def test_conditional_complete_price_has_no_known_later_balance():
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'collection_guidance': {'amount_cents': 1000},
              'arrival_collection': {'payments_received_cents': 0},
              'price_range': {'complete': True, 'conditional': True, 'mode': 'services',
                              'patient_min_cents': 3000, 'patient_max_cents': 3000,
                              'lines': [{'code': '99204', 'calculation': {'copay_cents': 3000,
                                                                         'deductible_cents': 0, 'coinsurance_cents': 0}}]}}
    summary = encounter_summary.build(result)
    assert summary['estimated_total_patient_min_cents'] == 3000
    assert summary['estimated_later_balance_cents'] is None
