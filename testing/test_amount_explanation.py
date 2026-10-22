"""Synthetic visit amounts; payer and patient data are never used here."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.insurance import commercial, collection_guidance, visit_estimate, amount_explanation
from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_estimator import _compare_card_hints
from test_visit_estimate import DAY, payload
from testing.synthetic_policy import ADVANCE_POLICY, COPAY_POLICY


def matched_terms(*args, **kwargs):
    kwargs.setdefault('provider_npi', '1234567890')
    terms = commercial.normalize_stedi_271(*args, **kwargs)
    terms['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}  # Synthetic tests isolate benefit pricing.
    return terms


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
    terms = matched_terms(raw, '98', False)
    assert terms['network_status'] == 'out_of_network'
    assert terms['coinsurance_percent'] == '40.00'
    assert terms['deductible_remaining_cents'] == 10000
    assert terms['selected_benefit_ids'] == ['benefitsInformation[4]']
    assert terms['coinsurance_id'] == 'benefitsInformation[4]'
    assert terms['deductible_id'] == 'benefitsInformation[5]'
    raw['benefitsInformation'][4]['additionalInformation'] = []
    unknown = matched_terms(raw, '98', False)
    assert unknown['network_status'] == 'unknown'
    assert unknown['coinsurance_percent'] != '40.00'


def test_conflicting_clinic_and_payer_network_evidence_blocks_a_tier():
    raw = payload()
    raw['benefitsInformation'].append({'code': 'A', 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'N', 'serviceTypeCodes': ['98'], 'benefitPercent': '.40',
        'additionalInformation': [{'description': 'Provider ID you entered is not participating in this patient network'}]})
    terms = matched_terms(raw, '98', True)
    assert terms['network_status'] == 'unknown'
    assert terms['requires_review']
    assert any('Provider-network evidence' in item for item in terms['review_reasons'])


def test_expired_provider_nonparticipation_note_cannot_select_out_of_network():
    raw = payload()
    raw['benefitsInformation'].append({'code': 'A', 'coverageLevelCode': 'IND',
        'inPlanNetworkIndicatorCode': 'N', 'serviceTypeCodes': ['98'], 'benefitPercent': '.40',
        'benefitsDateInformation': {'benefitEnd': '2020-12-31'},
        'additionalInformation': [{'description': 'Provider ID you entered is not participating in this patient network'}]})
    terms = matched_terms(raw, '98', False, service_date=DAY)
    assert terms['network_status'] == 'unknown'
    assert terms['coinsurance_percent'] == '20.00'


def test_verified_office_copay_can_stand_beside_unpriced_testing_and_deduct_payment_once():
    raw = payload()
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27')
    codes = [{'id': 'office', 'code': '99203', 'units': 1}, {'id': 'test', 'code': '93000', 'units': 1}]
    terms = matched_terms(raw, '98', True)
    terms['network_source'] = 'clinic_library_contract'
    priced = visit_estimate.price_lines(codes, [], terms,
        {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}}, payload=raw, service_date=DAY)
    result = {'benefits': terms, 'price_range': priced,
              'payment_mode': 'insurance', 'arrival_collection': {}, 'collection_policy': COPAY_POLICY}
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
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'clinic_library_absent'},
              'price_range': {'lines': [{'code': '99213', 'calculation': None}]},
              'payment_mode': 'cash', 'cash_fallback': {'reason': 'Clinic price selected after insured pricing failed.'}}
    assert amount_explanation.fallback_cause(result) == (
        'the Clinical Library contract is not linked to this plan and provider')
    sentence = amount_explanation.build(result, {'basis': 'practice_cash_price',
        'amount_cents': 15000})['sentence']
    assert 'Clinical Library contract is not linked' in sentence


def test_active_uhc_benefits_with_unknown_network_name_the_clinic_linkage_step():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None, 'benefits': {}}]}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'provider_network_unresolved'
    assert 'exact plan and provider' in explanation['sentence']
    assert 'payer did not return a patient share' not in explanation['sentence']
    result['collection_policy'] = {'mode': 'defer_to_claim', 'approved_by': 'administrator',
                                   'approved_at': '2026-10-15T12:00:00Z', 'reference': 'synthetic-policy'}
    deferred = amount_explanation.build(result, collection_guidance.build(result))
    assert deferred['sentence'].startswith('Collect $0 today:')
    assert 'link the exact plan and rendering provider' in deferred['sentence']


def test_legacy_active_uhc_result_without_service_plan_asks_for_the_documented_cpt():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'price_range': {'mode': 'services', 'lines': []}}
    explanation = amount_explanation.build(result, {'basis': 'missing_collection_inputs', 'amount_cents': None})
    assert explanation['reason_code'] == 'planned_cpt_missing'
    assert 'document the planned CPT in eCW' in explanation['sentence']


def test_uhc_service_selection_issue_names_the_clinical_action_without_semicolon():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'service_issues': [
                  'Add the anticipated nerve-conduction service (95907–95913) for these EMG add-ons. Choose its level from the planned study count; the add-ons alone are not a complete visit estimate.'
              ], 'lines': [{'code': '95885', 'allowed_cents': 7000,
                            'calculation': {'patient_cents': 1400}}]}}
    explanation = amount_explanation.build(result, {'basis': 'missing_collection_inputs', 'amount_cents': None})
    assert explanation['reason_code'] == 'service_selection_invalid'
    assert 'add the anticipated nerve-conduction service (95907–95913)' in explanation['sentence']
    assert 'rerun the insured estimate' in explanation['sentence']
    assert ';' not in explanation['sentence']


def test_uhc_priced_line_without_a_calculation_names_the_engine_gap():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'service_issues': [], 'lines': [
                  {'code': '99203', 'allowed_cents': 10000, 'calculation': None, 'issues': []}]}}
    explanation = amount_explanation.build(result, {'basis': 'missing_collection_inputs', 'amount_cents': None})
    assert explanation['reason_code'] == 'calculation_unavailable'
    assert 'pricing result and rerun the insured estimate' in explanation['sentence']


def test_uhc_priced_line_with_an_issue_names_that_issue_before_engine_gap():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'service_issues': [], 'lines': [
                  {'code': '95907', 'allowed_cents': 10000, 'calculation': None,
                   'issues': ['Map this service to a payer-supported benefit category in the clinic estimate profile.']}]}}
    explanation = amount_explanation.build(result, {'basis': 'missing_collection_inputs', 'amount_cents': None})
    assert explanation['reason_code'] == 'service_pricing_issue'
    assert 'address CPT 95907: Map this service to a payer-supported benefit category' in explanation['sentence']
    assert 'pricing result' not in explanation['sentence']


def test_saved_uhc_in_network_label_without_source_names_participation_gap():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network'},
              'price_range': {'mode': 'services', 'complete': True,
                              'patient_min_cents': 3800, 'patient_max_cents': 3800,
                              'lines': [{'code': '99203', 'calculation': {'patient_cents': 3800}}]}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert explanation['reason_code'] == 'provider_network_unresolved'
    assert explanation['network_tier'] == 'unknown'
    assert 'link the exact plan and rendering provider' in explanation['sentence']


def test_conflicting_uhc_provider_participation_names_the_payer_check():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract', 'network_conflict': True},
              'price_range': {'mode': 'services', 'complete': True,
                              'patient_min_cents': 3800, 'patient_max_cents': 3800,
                              'lines': [{'code': '99203', 'calculation': {'patient_cents': 3800}}]}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert explanation['reason_code'] == 'provider_network_conflict'
    assert 'ask UHC whether the rendering provider participates in this exact product at this location' in explanation['sentence']


def test_out_of_network_benefit_percent_names_the_missing_allowed_amount_action():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'payer_participation_message'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None,
                   'benefits': {'coinsurance_percent': '30.00'}}]}}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'out_of_network_allowance_missing'
    assert 'ask UHC for the CPT-specific out-of-network allowed amount for this member' in explanation['sentence']
    assert 'record the payer reference under Services' in explanation['sentence']


def test_scoped_clinic_nonparticipation_names_out_of_network_allowance_without_pricing_it():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'out_of_network',
                           'network_source': 'clinic_nonparticipation_rule'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None,
                   'benefits': {'coinsurance_percent': '30.00'}}]}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert explanation['network_tier'] == 'out_of_network'
    assert explanation['reason_code'] == 'out_of_network_allowance_missing'
    assert 'ask UHC for the CPT-specific out-of-network allowed amount' in explanation['sentence']


def test_insured_amount_names_allowed_and_out_of_pocket_reductions_separately():
    result = {'benefits': {'network_status': 'in_network', 'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'complete': True, 'lines': [{
                  'code': '99203', 'calculation': {'copay_cents': 3000, 'deductible_cents': 0,
                      'coinsurance_cents': 0, 'allowed_cap_adjustment_cents': 1000,
                      'oop_adjustment_cents': 500}}]}}
    sentence = amount_explanation.build(result, {'basis': 'anticipated_services',
        'amount_cents': 1500})['sentence']
    assert '$10.00 to the payer allowed amount' in sentence
    assert '$5.00 under the out-of-pocket limit' in sentence


def test_out_of_network_terms_without_provider_statement_name_network_link_first():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'out_of_network'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None,
                   'benefits': {'coinsurance_percent': '30.00'}}]}}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'provider_network_unresolved'
    assert explanation['network_tier'] == 'unknown'
    assert 'link the exact plan and rendering provider' in explanation['sentence']


def test_in_network_missing_rate_names_the_exact_contract_lookup():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None, 'allowed_cents': None,
                   'rate_resolution': {'status': 'RATE_UNAVAILABLE'}, 'benefits': {}}]}}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'contract_rate_missing'
    assert 'load the active UHC contract rate for CPT 99203, the rendering provider, visit date and location' in explanation['sentence']
    named = {**result, 'plan_details': {'stedi': {'names': ['POS CHOICE PLUS']}}}
    assert 'load the active UHC contract rate for CPT 99203 under POS CHOICE PLUS' in (
        amount_explanation.build(named, collection_guidance.build(named))['sentence'])
    alias = {**result, 'payer': 'AARP Regional PPO',
             'stedi_payer': {'name': 'UnitedHealthcare', 'id': 'synthetic-uhc-route'}}
    assert 'load the active UHC contract rate for CPT 99203' in (
        amount_explanation.build(alias, collection_guidance.build(alias))['sentence'])


def test_in_network_rate_conflict_does_not_claim_the_rate_is_missing():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'in_network',
                           'network_source': 'clinic_library_contract'},
              'price_range': {'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'calculation': None, 'allowed_cents': None,
                   'rate_resolution': {'status': 'RATE_CONFLICT', 'matched_candidate_count': 2},
                   'benefits': {}}]}}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'contract_rate_conflict'
    assert 'choose the applicable UHC contract rate for CPT 99203' in explanation['sentence']
    assert 'no contracted rate' not in explanation['sentence']


def test_priced_uhc_cpt_names_the_missing_payer_cost_share_field():
    base = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
            'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
            'benefits': {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract'},
            'price_range': {'mode': 'services', 'lines': [
                {'code': '99203', 'units': 1, 'calculation': None, 'allowed_cents': 20000,
                 'rate_resolution': {'status': 'RESOLVED'},
                 'issues': ['Confirm the remaining individual deductible that applies to this visit.'],
                 'benefits': {}}]}}
    guidance = collection_guidance.build(base)
    assert guidance['amount_cents'] is None
    deductible = amount_explanation.build(base, guidance)
    assert deductible['reason_code'] == 'individual_deductible_unresolved'
    assert deductible['sentence'] == (
        'No insured amount is ready because the applicable individual deductible balance for CPT 99203 is unresolved, '
        'so obtain that balance from UHC for CPT 99203 and rerun the insured estimate.')
    conflict = {**base, 'price_range': {**base['price_range'], 'lines': [
        {**base['price_range']['lines'][0], 'issues': [
            'Confirm which copay or coinsurance term applies, including any visit-count conditions.']}]}}
    selected = amount_explanation.build(conflict, collection_guidance.build(conflict))
    assert selected['reason_code'] == 'cost_share_term_unresolved'
    assert selected['sentence'] == (
        'No insured amount is ready because the applicable copay or coinsurance term for CPT 99203 is unresolved, '
        'so ask UHC which term applies to CPT 99203 at this visit and rerun the insured estimate.')
    oop = {**base, 'price_range': {**base['price_range'], 'lines': [
        {**base['price_range']['lines'][0], 'issues': [
            'Confirm the remaining individual out-of-pocket maximum.']}]}}
    assert amount_explanation.build(oop, collection_guidance.build(oop))['reason_code'] == 'individual_oop_unresolved'
    approved = {**base, 'collection_policy': {'mode': 'defer_to_claim', 'reference': 'synthetic policy',
                'approved_by': 'clinic-admin', 'approved_at': '2026-10-15T12:00:00Z'}}
    deferred = amount_explanation.build(approved, collection_guidance.build(approved))
    assert deferred['sentence'] == (
        'Collect $0 today: the applicable individual deductible balance for CPT 99203 is unresolved, '
        'so obtain that balance from UHC for CPT 99203 and rerun the insured estimate.')


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
    terms = matched_terms(raw, '98', True)
    terms['network_source'] = 'clinic_library_contract'
    priced = visit_estimate.price_lines(codes, rates, terms,
        {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}}, payload=raw, service_date=DAY)
    assert priced['complete']
    assert [row['calculation']['patient_cents'] for row in priced['lines']] == [3000, 2000]
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}, 'collection_policy': ADVANCE_POLICY}, {})
    assert guidance['amount_cents'] == 3000
    sentence = amount_explanation.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'encounter_summary': {
            'coinsurance_percent': '20', 'coinsurance_base_cents': 10000}}, guidance)['sentence']
    assert 'Collect $30.00 for the consultation' in sentence
    assert 'other estimated patient costs follow the claim' in sentence


def test_saved_ecw_benefits_are_not_labelled_stedi_on_each_service():
    raw = payload()
    raw['benefit_source'] = 'eCW saved eligibility'
    terms = matched_terms(raw, '98', True)
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
    terms = matched_terms(raw, '98', True)
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


@pytest.mark.parametrize('network_source', ['payer_participation_message', 'clinic_nonparticipation_rule'])
def test_out_of_network_percentage_without_allowance_does_not_become_dollar_total(network_source):
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][3]['benefitPercent'] = '.40'
    raw['benefitsInformation'][3]['additionalInformation'].append(
        {'description': 'Provider ID you entered is not participating in this patient network'})
    terms = matched_terms(raw, '98', False)
    terms['network_source'] = network_source
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], terms,
        {'cost_share': 'payer_returned', 'provider_npi': '1234567890'}, payload=raw, service_date=DAY)
    contract_cannot_replace_payer = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
        [{'code': '99203', 'allowed_cents': 10000, 'source_reference': 'In-network contract'}], terms,
        {'cost_share': 'payer_returned', 'provider_npi': '1234567890'}, payload=raw, service_date=DAY)
    assert contract_cannot_replace_payer['lines'][0]['allowed_cents'] is None
    guidance = collection_guidance.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance', 'arrival_collection': {}}, {})
    assert guidance['network'] == 'Out of network'
    assert guidance['amount_cents'] is None
    assert guidance['services'][0]['coinsurance_percent'] == '40.00'


@pytest.mark.parametrize('network_source', ['payer_participation_message', 'clinic_nonparticipation_rule'])
def test_out_of_network_known_payer_allowance_prices_only_the_allowed_share(network_source):
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3]['benefitPercent'] = '.40'
    raw['benefitsInformation'][3]['additionalInformation'].append(
        {'description': 'Provider ID you entered is not participating in this patient network'})
    terms = matched_terms(raw, '98', False)
    terms['network_source'] = network_source
    payer_rate = {'id': 'verified-payer-quote', 'line_id': 'office', 'units': 1,
                  'code': '99203', 'allowed_cents': 10000,
                  'basis': 'payer_out_of_network', 'source_reference': 'Payer quote for this visit',
                  'verified_at': DAY, 'service_date': DAY, 'provider_npi': '1234567890',
                  'place_of_service': '11', 'patient_binding': 'synthetic-visit-binding'}
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [payer_rate], terms,
        {'cost_share': 'payer_returned', 'provider_npi': '1234567890', 'place_of_service': '11',
         'patient_binding': 'synthetic-visit-binding'}, payload=raw, service_date=DAY)
    assert priced['complete']
    assert priced['patient_max_cents'] == 4000
    assert priced['lines'][0]['calculation']['possible_additional_out_of_network_charges_cents'] is None
    result = {'benefits': terms, 'price_range': priced,
              'payment_mode': 'insurance', 'arrival_collection': {}}
    assert collection_guidance.build(result, {})['amount_cents'] is None
    result['collection_policy'] = {'mode': 'defer_to_claim', 'approved_by': 'synthetic-admin',
                                   'approved_at': DAY, 'reference': 'Synthetic approval',
                                   'allow_supported_oon_advance': True}
    guidance = collection_guidance.build(result, {})
    assert guidance['amount_cents'] == 4000
    assert guidance['network'] == 'Out of network'


@pytest.mark.parametrize('decision, phrase', [
    ('out_of_network_confirmed_by_payer', 'payer confirmed this provider'),
    ('out_of_network_confirmed_by_scoped_nonparticipation_record', 'scoped nonparticipation record'),
])
def test_oon_headline_preserves_the_actual_network_source(decision, phrase):
    result = {'payer': 'UnitedHealthcare', 'payment_mode': 'insurance',
              'plan_details': {'stedi': {'names': ['Synthetic Medicare Advantage PPO']}},
              'network_evidence': {'decision': decision},
              'benefits': {'network_status': 'out_of_network',
                           'network_source': ('payer_participation_message' if decision.endswith('by_payer')
                                              else 'clinic_nonparticipation_rule')},
              'price_range': {'mode': 'services', 'complete': True, 'lines': [{
                  'id': 'office', 'code': '99204', 'allowed_basis': 'payer_out_of_network',
                  'calculation': {'copay_cents': 4000, 'deductible_cents': 0,
                                  'coinsurance_cents': 0, 'allowed_cap_adjustment_cents': 0,
                                  'oop_adjustment_cents': 0}}]}}
    sentence = amount_explanation.build(result, {'amount_cents': 4000,
        'basis': 'anticipated_services'})['sentence']
    assert phrase in sentence
    assert 'collect $40.00 now' in sentence


@pytest.mark.parametrize('network_source', ['payer_participation_message', 'clinic_nonparticipation_rule'])
def test_out_of_network_fixed_office_copay_is_only_a_known_partial_amount(network_source):
    raw = payload()
    for row in raw['benefitsInformation']:
        row['inPlanNetworkIndicatorCode'] = 'N'
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27',
        additionalInformation=[{'description': 'Provider ID you entered is not participating in this patient network'},
                               {'description': 'No referral required'}])
    terms = matched_terms(raw, '98', False)
    terms['network_source'] = network_source
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], terms,
        {'cost_share': 'payer_returned', 'provider_npi': '1234567890'}, payload=raw, service_date=DAY)
    result = {'benefits': terms, 'price_range': priced,
              'payment_mode': 'insurance', 'arrival_collection': {}}
    assert collection_guidance.build(result, {})['amount_cents'] is None
    result['collection_policy'] = {'mode': 'defer_to_claim', 'approved_by': 'synthetic-admin',
                                   'approved_at': DAY, 'reference': 'Synthetic approval',
                                   'allow_supported_oon_advance': True}
    guidance = collection_guidance.build(result, {})
    assert guidance['amount_cents'] == 3000
    sentence = amount_explanation.build({'benefits': terms, 'price_range': priced,
        'payment_mode': 'insurance'}, guidance)['sentence']
    assert sentence == ('Collect $30.00 for the consultation: the out-of-network plan office copay, '
                        'and the office deductible is already met, while any extra doctor charge remains unknown.')


def test_short_explanation_says_which_amount_is_known_and_why_cash_was_used():
    partial = {'payment_mode': 'insurance', 'benefits': {'network_status': 'in_network'},
               'price_range': {'lines': [{'code': '99203', 'calculation': None},
                                         {'code': '95907', 'calculation': None}]}}
    sentence = amount_explanation.build(partial, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence']
    assert sentence == ('Collect $30.00 for the consultation: the plan office copay, while '
                        'nerve testing is excluded because its patient cost is unknown.')
    office_only = {**partial, 'price_range': {'lines': [{'code': '99203', 'calculation': None}]}}
    assert amount_explanation.build(office_only, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == 'Collect $30.00 for the consultation: the plan office copay.'
    uncertain = {**office_only, 'price_range': {'lines': [{'code': '99203', 'calculation': None,
        'benefits': {'deductible_applicability': 'UNKNOWN', 'deductible_remaining_cents': 130000}}]}}
    assert amount_explanation.build(uncertain, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == (
        'Collect $30.00 for the consultation: the plan office copay, while the office deductible remains unknown.')
    waived = deepcopy(uncertain)
    waived['price_range']['lines'][0]['benefits']['deductible_applicability'] = 'DOES_NOT_APPLY'
    assert amount_explanation.build(waived, {'amount_cents': 3000, 'basis': 'office_copay'})['sentence'] == (
        'Collect $30.00 for the consultation: the plan office copay, and the office deductible does not apply.')
    cash = {'payment_mode': 'cash', 'cash_fallback': {'reason': 'completed checks'},
            'benefits': {'network_status': 'out_of_network',
                         'network_source': 'payer_participation_message'}, 'price_range': {'lines': []}}
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
    unordered_referral = {**multiple, 'benefits': {'active': True, 'referral_signal': 'required'}}
    assert amount_explanation.build(unordered_referral,
        {'amount_cents': 25000, 'basis': 'practice_cash_price'})['reason_code'] == 'multiple_active_policies'
    mismatched_referral = {**base, 'benefits': {'active': True, 'referral_signal': 'required'},
        'identity': {'status': 'review', 'differences': [{'field': 'last_name'}]}}
    assert amount_explanation.build(mismatched_referral,
        {'amount_cents': 25000, 'basis': 'practice_cash_price'})['reason_code'] == 'policy_identity_mismatch'
    enrollment = {**multiple, 'eligibility_failure': {'kind': 'provider_enrollment'}}
    assert amount_explanation.build(enrollment, {'amount_cents': 25000, 'basis': 'practice_cash_price'})['reason_code'] == 'provider_enrollment'


def test_cash_fallback_names_verified_benefits_without_matching_clinic_prices():
    base = {'payment_mode': 'cash', 'cash_fallback': {'reason': 'completed checks'},
            'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
            'benefits': {'active': True, 'network_status': 'unknown'},
            'price_range': {'lines': [{'code': '99203', 'calculation': None}]}}
    product = {**base, 'issues': ['The clinic fee schedule is not matched to the exact payer product.']}
    explanation = amount_explanation.build(product, {'amount_cents': 25000, 'basis': 'practice_cash_price'})
    assert explanation['reason_code'] == 'clinic_product_mismatch'
    assert 'exact payer product has no approved link to the clinic agreement' in explanation['sentence']
    contract = {**base, 'issues': ['Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.']}
    explanation = amount_explanation.build(contract, {'amount_cents': 25000, 'basis': 'practice_cash_price'})
    assert explanation['reason_code'] == 'clinic_contract_missing'
    assert 'clinic pricing is not linked to this plan and provider' in explanation['sentence']


def test_draft_uhc_pricing_connection_keeps_payer_benefits_and_names_setup_action(monkeypatch):
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda _practice: [
        {'stedi_payer_id': 'synthetic-uhc-route', 'status': 'draft',
         'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)'}])
    benefits = {'active': True, 'network_status': 'unknown'}
    result = {'payer': 'UnitedHealthcare', 'issues': [], 'codes': [], 'benefits': benefits,
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}
    api._attach_previsit_pricing(result, 'synthetic-clinic', {'service_date': '2026-10-16'},
                                 {}, 'synthetic-uhc-route', benefits)
    assert benefits['active'] is True
    assert result['issues'] == ["Clinic pricing: this payer's clinic estimator connection is still a draft."]
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] == 'clinic_payer_connection_draft'
    assert 'verified network for this exact UHC product and provider' in explanation['sentence']
    assert 'enable its payer connection and estimate profile' in explanation['sentence']


def test_deferred_uhc_explanation_names_missing_visit_code_and_financial_source_error():
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True},
              'service_plan': {'selected_codes': [], 'alternatives': []},
              'price_range': {'mode': 'services', 'lines': []}}
    guidance = {'basis': 'claim_after_service', 'amount_cents': 0}
    missing_code = amount_explanation.build(result, guidance)
    assert missing_code['reason_code'] == 'planned_cpt_missing'
    assert missing_code['sentence'] == (
        'Collect $0 today: no billable CPT is documented for this visit, so document the planned CPT in eCW and rerun the insured estimate.')
    guidance['source_errors'] = [{'path': 'price_range.patient_min_cents', 'kind': 'invalid_calculation'}]
    invalid = amount_explanation.build(result, guidance)
    assert invalid['reason_code'] == 'financial_source_error'
    assert invalid['sentence'] == (
        'Collect $0 today: the calculated visit amount is invalid, so have the clinic administrator correct the price calculation and rerun the insured estimate.')
    guidance['source_errors'] = [{'path': 'benefits.deductible', 'kind': 'invalid_amount'}]
    payer_field = amount_explanation.build(result, guidance)
    assert payer_field['reason_code'] == 'financial_source_error'
    assert payer_field['sentence'] == (
        'Collect $0 today: the payer benefit response contains an invalid financial field, so verify the flagged benefit field with the payer and rerun the insured estimate.')


def test_identity_mismatch_precedes_empty_follow_up_discovery():
    result = {'payment_mode': 'insurance', 'benefits': {'active': True,
              'identity': {'status': 'review'}, 'network_status': 'unknown'},
              'recovery': {'reason_code': 'no_coverage_found'},
              'price_range': {'lines': []}}
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'deferred_billing'})
    assert explanation['reason_code'] == 'policy_identity_mismatch'
    assert 'payer response did not match' in explanation['sentence']
    assert 'returned no policy' not in explanation['sentence']


@pytest.mark.parametrize(('state', 'expected'), [
    ('pending', 'check_pending'), ('complete', 'payer_route_ambiguous'),
])
def test_unchecked_uhc_route_does_not_claim_ecw_and_payer_identifiers_are_missing(state, expected):
    result = {'payer': 'UHC Medicare', 'payment_mode': 'insurance',
              'identity': {'status': 'review', 'missing_fields': ['member_id', 'patient_demographics']},
              'benefits': {'active': None, 'network_status': 'unknown'},
              'recovery': {'state': state, 'reason_code': 'discovered_payer_not_routable'},
              'price_range': {'mode': 'services', 'lines': []}}
    explanation = amount_explanation.build(result, {'amount_cents': None, 'basis': 'missing_collection_inputs'})
    assert explanation['reason_code'] == expected
    assert 'identifiers' not in explanation['sentence']


def test_current_patient_mismatch_precedes_stale_benefit_identity_in_staff_line():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'identity': {'status': 'review', 'differences': [{'field': 'first_name'}],
                           'policy_differences': [{'field': 'subscriber_first'}]},
              'benefits': {'active': True, 'network_status': 'unknown',
                           'identity': {'status': 'matched', 'binding': 'old-member-binding', 'match_version': 2}},
              'price_range': {'mode': 'services', 'lines': []}}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'policy_identity_mismatch'
    assert 'reconcile first name, subscriber first name with UHC' in explanation['sentence']


def test_uhc_zero_advance_explains_collection_policy_and_actual_gap():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'collection_policy': {'mode': 'defer_to_claim', 'reference': 'clinic policy 2026',
                                    'approved_by': 'administrator', 'approved_at': '2026-10-15T12:00:00Z'},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'recovery': {'reason_code': 'multiple_active_policies'},
              'price_range': {'lines': []}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] == 0
    assert explanation['reason_code'] == 'multiple_active_policies'
    assert explanation['sentence'] == ('Collect $0 today: the insurance search returned more than one active policy '
                                       'and did not identify which pays first, so confirm payer order with the payers before billing.')


def test_uncoded_office_alternatives_name_the_missing_clinical_selection():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'price_range': {'mode': 'alternatives', 'lines': [
                  {'code': code, 'calculation': None} for code in ('99202', '99203', '99204', '99205')]}}
    unresolved = amount_explanation.build(result, {'amount_cents': None, 'basis': 'deferred_billing'})
    assert unresolved['reason_code'] == 'planned_cpt_unselected'
    assert 'alternative E/M CPT levels' in unresolved['sentence']
    result['collection_policy'] = {'mode': 'defer_to_claim', 'reference': 'clinic policy 2026',
                                   'approved_by': 'administrator', 'approved_at': '2026-10-15T12:00:00Z'}
    guidance = collection_guidance.build(result)
    deferred = amount_explanation.build(result, guidance)
    assert deferred['sentence'].startswith('Collect $0 today:')
    assert 'price the booked service alternatives' in deferred['sentence']


def test_zero_advance_identity_line_names_only_mismatched_fields():
    result = {'payment_mode': 'insurance', 'collection_policy': {'mode': 'defer_to_claim',
              'reference': 'clinic policy 2026', 'approved_by': 'administrator',
              'approved_at': '2026-10-15T12:00:00Z'},
              'benefits': {'active': True, 'identity': {'status': 'review',
                  'differences': [{'field': 'birth_date', 'ecw': 'synthetic', 'payer': 'synthetic'}]},
                  'network_status': 'unknown'}, 'recovery': {'reason_code': 'no_coverage_found'},
              'price_range': {'lines': []}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'policy_identity_mismatch'
    assert 'reconcile date of birth with UHC' in explanation['sentence']
    assert 'synthetic' not in explanation['sentence']


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
        'This is NMCT’s nerve-testing package price for the planned care, after the recorded payment, with the focused visit included.')
    selected['cash_fallback'] = {'reason': 'completed checks'}
    selected['benefits'] = {'network_status': 'out_of_network',
                            'network_source': 'payer_participation_message'}
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
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [])
    current = api._previsit_result(case)
    saved = asyncio.run(api.saved_previsit('synthetic', MagicMock()))
    assert saved['payment_explanation'] == current['payment_explanation']
    assert saved['payment_explanation']['sentence'] == 'This is NMCT’s cash price for the planned care.'


def test_saved_case_uses_current_approved_deferral_and_removes_it_when_disabled(monkeypatch):
    case = {'revision': 1, 'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'uhc-route', 'source': {}, 'result': {
                'service_date': DAY, 'payment_mode': 'insurance', 'payer': 'United Healthcare',
                'benefits': {'active': True, 'network_status': 'unknown'},
                'recovery': {'reason_code': 'multiple_active_policies'},
                'price_range': {'complete': False, 'lines': []}}}}
    policy = {'mode': 'defer_to_claim', 'reference': 'clinic policy 2026',
              'approved_by': 'administrator', 'approved_at': '2026-10-15T12:00:00Z'}
    monkeypatch.setattr(api.commercial_store, 'list_connections',
                        lambda *_: [{'stedi_payer_id': 'uhc-route', 'rules': {'collection_policy': policy}}])
    saved = api._previsit_result(case, 'clinic')
    assert saved['collection_guidance']['amount_cents'] == 0
    assert saved['encounter_summary']['collect_today_cents'] == 0
    assert saved['encounter_summary']['estimated_total_patient_min_cents'] is None
    monkeypatch.setattr(api.commercial_store, 'list_connections',
                        lambda *_: [{'stedi_payer_id': 'uhc-route', 'rules': {}}])
    disabled = api._previsit_result(case, 'clinic')
    assert disabled['collection_guidance']['amount_cents'] is None
    assert disabled['encounter_summary']['collect_today_cents'] is None


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
