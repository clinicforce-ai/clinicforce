"""Synthetic visit amounts; payer and patient data are never used here."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

from app.insurance import commercial, collection_guidance, visit_estimate, amount_explanation
from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_estimator import _compare_card_hints
from test_visit_estimate import DAY, payload


def test_out_of_network_terms_are_selected_only_with_provider_evidence():
    raw = payload()
    raw['benefitsInformation'][3]['benefitPercent'] = '.10'
    raw['benefitsInformation'] += [
        {'code': 'A', 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N',
         'serviceTypeCodes': ['98'], 'benefitPercent': '.40', 'authOrCertIndicator': 'N',
         'additionalInformation': [{'description': 'Provider ID you entered is not participating in this patient network'}]},
        {'code': 'C', 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N',
         'serviceTypeCodes': ['30'], 'timeQualifierCode': '29', 'benefitAmount': '100'},
    ]
    terms = commercial.normalize_stedi_271(raw, '98', False)
    assert terms['network_status'] == 'out_of_network'
    assert terms['coinsurance_percent'] == '40.00'
    assert terms['deductible_remaining_cents'] == 10000
    assert terms['selected_benefit_ids'] == ['benefitsInformation[4]']
    assert terms['coinsurance_id'] == 'benefitsInformation[4]'
    assert terms['deductible_id'] == 'benefitsInformation[5]'
    raw['benefitsInformation'][4]['additionalInformation'] = []
    unknown = commercial.normalize_stedi_271(raw, '98', False)
    assert unknown['network_status'] == 'unknown'
    assert unknown['coinsurance_percent'] != '40.00'


def test_conflicting_clinic_and_payer_network_evidence_blocks_a_tier():
    raw = payload()
    raw['benefitsInformation'].append({'code': 'A', 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'N', 'serviceTypeCodes': ['98'], 'benefitPercent': '.40',
        'additionalInformation': [{'description': 'Provider ID you entered is not participating in this patient network'}]})
    terms = commercial.normalize_stedi_271(raw, '98', True)
    assert terms['network_status'] == 'unknown'
    assert terms['requires_review']
    assert any('Provider-network evidence' in item for item in terms['review_reasons'])


def test_expired_provider_nonparticipation_note_cannot_select_out_of_network():
    raw = payload()
    raw['benefitsInformation'].append({'code': 'A', 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'N', 'serviceTypeCodes': ['98'], 'benefitPercent': '.40',
        'benefitsDateInformation': {'benefitEnd': '2020-12-31'},
        'additionalInformation': [{'description': 'Provider ID you entered is not participating in this patient network'}]})
    terms = commercial.normalize_stedi_271(raw, '98', False, service_date=DAY)
    assert terms['network_status'] == 'unknown'
    assert terms['coinsurance_percent'] == '20.00'


def test_verified_office_copay_can_stand_beside_unpriced_testing_and_deduct_payment_once():
    raw = payload()
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27')
    codes = [{'id': 'office', 'code': '99203', 'units': 1}, {'id': 'test', 'code': '93000', 'units': 1}]
    priced = visit_estimate.price_lines(codes, [], commercial.normalize_stedi_271(raw, '98', True),
        {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}}, payload=raw, service_date=DAY)
    result = {'benefits': commercial.normalize_stedi_271(raw, '98', True), 'price_range': priced,
              'payment_mode': 'insurance', 'arrival_collection': {}}
    guidance = collection_guidance.build(result, {'payments': {'reference': 'synthetic receipt', 'amount_cents': 1000}})
    assert guidance['amount_cents'] == 2000
    assert guidance['basis'] == 'office_copay'
    assert any(row['code'] == '93000' and row['patient_cents'] is None for row in guidance['services'])


def test_unknown_provider_network_qualifies_payer_office_copay():
    result = {'benefits': {'network_status': 'unknown'}, 'price_range': {'lines': [
        {'code': '99203', 'calculation': None, 'benefits': {'network_independent': False}}]}}
    explanation = amount_explanation.build(result, {'basis': 'office_copay', 'amount_cents': 3000})
    assert explanation['sentence'] == 'Collect $30.00 for the consultation: the plan office copay.'
    assert explanation['network_tier'] == 'unknown'


def test_missing_clinic_contract_names_the_cash_fallback_cause():
    result = {'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'clinic_library_absent'},
              'price_range': {'lines': [{'code': '99213', 'calculation': None}]},
              'payment_mode': 'cash', 'cash_fallback': {'reason': 'Clinic price selected after insured pricing failed.'}}
    assert amount_explanation.fallback_cause(result) == (
        'the Clinical Library has no current contract for this payer, plan, provider and visit')
    sentence = amount_explanation.build(result, {'basis': 'practice_cash_price',
        'amount_cents': 15000})['sentence']
    assert 'Clinical Library has no current contract' in sentence


def test_procedure_specific_coinsurance_adds_to_office_copay():
    raw = payload()
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27', serviceTypeCodes=['98'])
    raw['benefitsInformation'].append({'code': 'A', 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'Y', 'serviceTypeCodes': ['30'],
        'compositeMedicalProcedureIdentifier': {'procedureCode': '93000'},
        'benefitPercent': '.20', 'authOrCertIndicator': 'N',
        'additionalInformation': [{'description': 'No referral required'}]})
    codes = [{'id': 'office', 'code': '99203', 'units': 1}, {'id': 'test', 'code': '93000', 'units': 1}]
    rates = [{'code': code['code'], 'allowed_cents': 10000} for code in codes]
    terms = commercial.normalize_stedi_271(raw, '98', True)
    priced = visit_estimate.price_lines(codes, rates, terms,
        {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}}, payload=raw, service_date=DAY)
    assert priced['complete']
    assert [row['calculation']['patient_cents'] for row in priced['lines']] == [3000, 2000]
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['amount_cents'] == 5000
    assert amount_explanation.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance'}, guidance)['sentence'] == (
        'This $50.00 advance includes $30.00 copay and $20.00 coinsurance based on the clinic’s contracted CPT prices.')


def test_saved_ecw_benefits_are_not_labelled_stedi_on_each_service():
    raw = payload()
    raw['benefit_source'] = 'eCW saved eligibility'
    terms = commercial.normalize_stedi_271(raw, '98', True)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
        [{'code': '99203', 'allowed_cents': 10000}], terms, {'cost_share': 'payer_returned'},
        payload=raw, service_date=DAY)
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'benefit_source': 'eCW saved eligibility'}, {})
    assert priced['lines'][0]['benefit_source'] == 'eCW saved eligibility'
    assert guidance['services'][0]['benefit_source'] == 'eCW saved eligibility'


def test_explicit_after_deductible_copay_is_not_collected_as_full_known_amount():
    raw = payload()
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27',
        additionalInformation=[{'description': 'Copay applies after deductible'},
                               {'description': 'No referral required'}])
    terms = commercial.normalize_stedi_271(raw, '98', True)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], terms,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['amount_cents'] is None
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
        [{'code': '99203', 'allowed_cents': 10000}], terms, {'cost_share': 'payer_returned'},
        payload=raw, service_date=DAY)
    assert priced['complete']
    assert priced['patient_max_cents'] == 8000  # $50 deductible, then $30 copay.


def test_out_of_network_percentage_without_allowance_does_not_become_dollar_total():
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][3]['benefitPercent'] = '.40'
    raw['benefitsInformation'][3]['additionalInformation'].append(
        {'description': 'Provider ID you entered is not participating in this patient network'})
    terms = commercial.normalize_stedi_271(raw, '98', False)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], terms,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    contract_cannot_replace_payer = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
        [{'code': '99203', 'allowed_cents': 10000, 'source_reference': 'In-network contract'}], terms,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    assert contract_cannot_replace_payer['lines'][0]['allowed_cents'] is None
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['network'] == 'Out of network'
    assert guidance['amount_cents'] is None
    assert guidance['services'][0]['coinsurance_percent'] == '40.00'


def test_out_of_network_known_payer_allowance_prices_only_the_allowed_share():
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3]['benefitPercent'] = '.40'
    raw['benefitsInformation'][3]['additionalInformation'].append(
        {'description': 'Provider ID you entered is not participating in this patient network'})
    terms = commercial.normalize_stedi_271(raw, '98', False)
    payer_rate = {'id': 'verified-payer-quote', 'code': '99203', 'allowed_cents': 10000,
                  'basis': 'payer_out_of_network', 'source_reference': 'Payer quote for this visit',
                  'verified_at': DAY, 'service_date': DAY, 'provider_npi': '1234567890',
                  'place_of_service': '11', 'patient_binding': 'synthetic-visit-binding'}
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [payer_rate], terms,
        {'cost_share': 'payer_returned', 'provider_npi': '1234567890', 'place_of_service': '11',
         'patient_binding': 'synthetic-visit-binding'}, payload=raw, service_date=DAY)
    assert priced['complete']
    assert priced['patient_max_cents'] == 4000
    assert priced['lines'][0]['calculation']['possible_additional_out_of_network_charges_cents'] is None
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['amount_cents'] == 4000
    assert guidance['network'] == 'Out of network'


def test_out_of_network_fixed_office_copay_is_only_a_known_partial_amount():
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27',
        additionalInformation=[{'description': 'Provider ID you entered is not participating in this patient network'},
                               {'description': 'No referral required'}])
    terms = commercial.normalize_stedi_271(raw, '98', False)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], terms,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['amount_cents'] == 3000
    sentence = amount_explanation.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance'}, guidance)['sentence']
    assert sentence == ('Collect $30.00 for the consultation: the out-of-network plan office copay; '
                        'the office deductible is already met; any extra doctor charge is unknown.')


def test_short_explanation_says_which_amount_is_known_and_why_cash_was_used():
    partial = {'payment_mode': 'insurance', 'benefits': {'network_status': 'in_network'},
               'price_range': {'lines': [{'code': '99203', 'calculation': None},
                                         {'code': '95907', 'calculation': None}]}}
    sentence = amount_explanation.build(partial, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence']
    assert sentence == ('Collect $30.00 for the consultation: the plan office copay; '
                        'nerve testing is excluded because its patient cost is unknown.')
    office_only = {**partial, 'price_range': {'lines': [{'code': '99203', 'calculation': None}]}}
    assert amount_explanation.build(office_only, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == 'Collect $30.00 for the consultation: the plan office copay.'
    uncertain = {**office_only, 'price_range': {'lines': [{'code': '99203', 'calculation': None,
        'benefits': {'deductible_applicability': 'UNKNOWN', 'deductible_remaining_cents': 130000}}]}}
    assert amount_explanation.build(uncertain, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == (
        'Collect $30.00 for the consultation: the plan office copay; any office deductible is still unknown.')
    waived = deepcopy(uncertain)
    waived['price_range']['lines'][0]['benefits']['deductible_applicability'] = 'DOES_NOT_APPLY'
    assert amount_explanation.build(waived, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == (
        'Collect $30.00 for the consultation: the plan office copay; the office deductible does not apply.')
    cash = {'payment_mode': 'cash', 'cash_fallback': {'reason': 'completed checks'},
            'benefits': {'network_status': 'out_of_network'}, 'price_range': {'lines': []}}
    explanation = amount_explanation.build(cash, {'amount_cents': 16000, 'basis': 'practice_cash_price'})
    assert explanation['reason_code'] == 'out_of_network_allowance_missing'
    assert explanation['sentence'] == 'This is NMCT’s cash price because the plan did not give an allowed price for out-of-network care.'
    cigna = {**partial, 'identity': {'reconciliation_rule': 'cigna_member_suffix'},
             'benefits': {'active': True, 'network_status': 'in_network'}}
    assert 'two-digit suffix' in amount_explanation.build(cigna, {'amount_cents': 3000, 'basis': 'office_copay'})['notice']


def test_cash_fallback_names_the_actual_policy_or_clinic_price_blocker():
    base = {'payment_mode': 'cash', 'cash_fallback': {'reason': 'completed checks'},
            'benefits': {}, 'price_range': {'lines': []}}
    multiple = {**base, 'recovery': {'reason_code': 'multiple_active_policies'}}
    message = amount_explanation.build(multiple, {'amount_cents': 25000, 'basis': 'practice_cash_price'})
    assert message['reason_code'] == 'multiple_active_policies'
    assert 'more than one active policy' in message['sentence']
    missing_rate = {**base, 'price_range': {'lines': [
        {'code': '95907', 'calculation': None}, {'code': '95886', 'calculation': None}]}}
    message = amount_explanation.build(missing_rate, {'amount_cents': None, 'basis': 'practice_cash_price'})
    assert 'no cash rate for 95907, 95886' in message['sentence']
    assert 'evidence could not support' not in message['sentence']
    referral = {**base, 'benefits': {'referral_signal': 'required'}}
    assert amount_explanation.build(referral, {'amount_cents': 25000, 'basis': 'practice_cash_price'})['reason_code'] == 'referral_required'
    enrollment = {**multiple, 'eligibility_failure': {'kind': 'provider_enrollment'}}
    assert amount_explanation.build(enrollment, {'amount_cents': 25000, 'basis': 'practice_cash_price'})['reason_code'] == 'provider_enrollment'


def test_doctor_diagnosis_marks_copay_with_unpriced_testing_as_partial():
    result = {'benefits': {'active': True, 'network_status': 'in_network'},
              'price_range': {'lines': [{'code': '99203', 'calculation': None, 'issues': []},
                                        {'code': '95907', 'calculation': None, 'issues': ['Payer allowance missing']}],
                              'issues': [], 'service_issues': []}, 'issues': []}
    diagnosis = api._insurance_diagnosis({'previsit_context': {'source': {}}}, result,
        {'amount_cents': 3000, 'basis': 'office_copay'})
    assert diagnosis['pricingStatus'] == 'PARTIAL_AMOUNT'
    assert 'ADDITIONAL_SERVICE_PRICE_UNRESOLVED' in diagnosis['reasonCodes']
    assert diagnosis['pricingReasons'] == ['Payer allowance missing']


def test_cash_quote_deducts_verified_payment_once():
    result = {'payment_mode': 'cash', 'price_range': {'cash_complete': True,
        'cash_min_cents': 16000, 'cash_max_cents': 16000, 'lines': []}}
    review = {'payments': {'reference': 'synthetic ledger', 'amount_cents': 2500}}
    guidance = collection_guidance.build(result, review)
    assert guidance['amount_cents'] == guidance['range_min_cents'] == guidance['range_max_cents'] == 13500
    assert 'after the recorded payment' in amount_explanation.build(result, guidance, review)['sentence']
    paid = collection_guidance.build(result, {'payments': {'reference': 'synthetic ledger', 'amount_cents': 16000}})
    assert paid['amount_cents'] == 0
    assert amount_explanation.build(result, paid, {'payments': {'reference': 'synthetic ledger'}})['sentence'] == (
        'No payment is due now because the recorded payment covers NMCT’s cash price.')
    selected = {**result, 'cash_quote': {'selection': 'named:example', 'complete': True}}
    assert amount_explanation.build(selected, guidance, review)['sentence'] == (
        'This is NMCT’s nerve-testing package price for the planned care, after the recorded payment; the focused visit is included.')
    selected['cash_fallback'] = {'reason': 'completed checks'}
    selected['benefits'] = {'network_status': 'out_of_network'}
    assert 'because the plan did not give an allowed price for out-of-network care' in (
        amount_explanation.build(selected, guidance, review)['sentence'])


def test_saved_previsit_rebuilds_the_same_short_sentence(monkeypatch):
    case = {'id': 'synthetic', 'revision': 2, 'source_mode': 'commercial_previsit',
        'previsit': {'request_state': 'complete'}, 'previsit_context': {'source': {},
            'result': {'service_date': DAY, 'payment_mode': 'cash', 'benefits': {}, 'issues': [],
                'price_range': {'cash_complete': True, 'cash_min_cents': 16000,
                                'cash_max_cents': 16000, 'lines': []}}}}
    session = {'patient_id': 'synthetic', 'practice_id': 'clinic', 'intake_id': 'intake',
               'staff': {'id': 'doctor', 'display_name': 'Doctor'}, 'share_manifest_id': ''}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    monkeypatch.setattr(api.store, 'get_case', lambda *args, **kwargs: case)
    current = api._previsit_result(case)
    saved = asyncio.run(api.saved_previsit('synthetic', MagicMock()))
    assert saved['payment_explanation'] == current['payment_explanation']
    assert saved['payment_explanation']['sentence'] == 'This is NMCT’s cash price for the planned care.'


def test_card_route_needs_a_patient_bound_card_and_matching_identity():
    inputs = {'member_id': 'M123', 'group_number': 'G7', 'relationship': '18',
              'patient': {'patient_first_name': 'Pat', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01'},
              'subscriber': {'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'}}
    source = {'_patient_id': 'ecw-patient', 'coverage_id': 'ecw-policy', 'active_coverages': 1, 'payer': 'BCBS Texas'}
    card = {'status': 'read', 'count': 1, 'fields': {'payer': 'BCBS Illinois', 'member_id': 'M123 01',
            'group_number': 'G7', 'member_name': 'Pat Example', 'birth_date': '1980-01-01'}}
    result = _compare_card_hints(card, deepcopy(inputs), source)
    assert result['status'] == 'conflict'
    assert result['alternative_supported'] is True
    assert result['differences'] == ['payer_name']
    unbound = _compare_card_hints(card, deepcopy(inputs), {**source, '_patient_id': None})
    assert unbound['alternative_supported'] is False
    bad_dob = _compare_card_hints({**card, 'fields': {**card['fields'], 'birth_date': '1990-01-01'}}, deepcopy(inputs), source)
    assert bad_dob['alternative_supported'] is False
