"""Synthetic Botox planning and financial guards; no payer or eCW calls."""
from datetime import date
from unittest.mock import patch

from app.api.routers import commercial_estimator as api
from app.insurance import amount_explanation, botox_review, commercial, cpt_suggester, visit_estimate
from app.insurance import collection_guidance


DAY = date.today().isoformat()
BOOKING = {'reason': 'Botox injection, 200 units received', 'visit_type': 'established',
           'lines': [{'id': 'ecw-em', 'code': '99214', 'units': 1,
                      'service_status': 'scheduled'}]}


def _facts(**changes):
    return {'botox': {'evidence_reference': 'Synthetic chart review',
                      'modifier_reference': 'Synthetic coding review',
                      'indication': 'Documented medical indication',
                      'anatomical_region': 'Documented facial region',
                      'encounter_status': 'planned', 'brand': 'Botox',
                      'supply_source': 'clinic', 'planned_units': 80, 'administered_units': 80,
                      'discarded_units': 0, 'separate_em_documented': False,
                      **changes},
            'confirmed_modifiers': {'J0585': [], '64612': [], '64615': []}}


def _codes(*entries):
    return [{'id': code, 'code': code, 'units': units, 'modifiers': []}
            for code, units in entries]


def test_booking_and_bedrock_candidates_never_select_billed_care():
    assert api._previsit_codes(BOOKING) == []
    assert api._previsit_service_types(BOOKING) == ['98', '1', '2', '30']
    available = _codes(('99214', 1), ('64612', 1), ('J0585', 80))
    for row in available:
        row['source'] = 'eCW scheduled service'
    with patch.object(cpt_suggester.boto3, 'client') as bedrock:
        plan = cpt_suggester.recommend_visit_plan(BOOKING, {}, available)
        assert plan['kind'] == 'botox' and plan['recommended_ids'] == []
        bedrock.assert_not_called()


def test_clinical_review_requires_documented_drug_procedure_dose_and_separate_em():
    drug = _codes(('J0585', 80))
    assert 'injection procedure' in ' '.join(botox_review.clinical_issues(drug, _facts()))
    both = _codes(('64612', 1), ('J0585', 80))
    assert botox_review.clinical_issues(both, _facts()) == []
    assert 'modifiers' in ' '.join(botox_review.clinical_issues(both, {
        **_facts(), 'confirmed_modifiers': {'J0585': []}}))
    assert 'planned Botox dose' in ' '.join(botox_review.clinical_issues(both, _facts(planned_units=200)))
    assert 'clinic supplied' in ' '.join(botox_review.clinical_issues(both, _facts(supply_source='specialty_pharmacy')))
    assert 'administered brand' in ' '.join(botox_review.clinical_issues(both, _facts(brand='other')))
    consult = _codes(('99214', 1), ('64612', 1), ('J0585', 80))
    assert 'separate office E/M' in ' '.join(botox_review.clinical_issues(consult, _facts()))
    assert botox_review.clinical_issues(consult, _facts(separate_em_documented=True)) == []
    assert botox_review.clinical_issues(both, _facts(administered_units=None, discarded_units=None)) == []
    assert 'payer waste policy' in ' '.join(botox_review.clinical_issues(
        both, _facts(encounter_status='performed', discarded_units=20)))
    assert botox_review.clinical_issues(_codes(('99214', 1)), _facts(), botox_visit=True)
    assert 'duplicate J0585' in ' '.join(botox_review.clinical_issues(
        _codes(('J0585', 80), ('J0585', 80), ('64612', 1)), _facts()))


def test_historical_completed_encounter_needs_performed_evidence():
    selected = _codes(('64612', 1), ('J0585', 80))
    assert 'performed care' in ' '.join(botox_review.clinical_issues(selected, _facts(), completed=True))
    assert botox_review.clinical_issues(selected, _facts(encounter_status='performed'), completed=True) == []


def test_office_copay_cannot_price_drug_or_injection_even_with_rates():
    raw = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'inPlanNetworkIndicatorCode': 'Y'},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '20'},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '40'},
    ]}
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    selected = _codes(('64612', 1), ('J0585', 80))
    rates = [{'code': row['code'], 'allowed_cents': 10000, 'source_reference': 'Synthetic UHC rate'}
             for row in selected]
    price = visit_estimate.price_lines(selected, rates, benefits, {}, payload=raw, service_date=DAY)
    assert not price['complete']
    for line in price['lines']:
        assert line['calculation'] is None
        assert line['benefits']['authorization_signal'] == 'unknown'
        assert any('medical-benefit cost-share' in issue for issue in line['issues'])
        assert 'office' not in str(line.get('benefit_basis', '')).lower()


def test_verified_drug_and_procedure_terms_allocate_one_deductible_and_keep_pa_separate():
    raw = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'inPlanNetworkIndicatorCode': 'Y'},
        {'code': 'C', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '50'},
        {'code': 'G', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '500'},
        {'code': 'A', 'serviceTypeCodes': ['1'], 'inPlanNetworkIndicatorCode': 'Y',
         'benefitPercent': '.20'},
        {'code': 'A', 'serviceTypeCodes': ['2'], 'inPlanNetworkIndicatorCode': 'Y',
         'benefitPercent': '.30'},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'inPlanNetworkIndicatorCode': 'Y',
         'benefitAmount': '40'},
    ]}
    benefits = commercial.normalize_stedi_271(raw, '30', True)
    selected = _codes(('J0585', 80), ('64612', 1))
    rates = [{'code': row['code'], 'allowed_cents': 100,
              'source_reference': 'Synthetic exact-product contract'} for row in selected]
    evidence = {'reference': 'Synthetic payer portal decision', 'verified_at': DAY,
                'valid_from': DAY, 'valid_to': DAY, 'units': 80, 'status': 'valid'}
    reviews = {
        'J0585': {'benefits': {**evidence, 'service_type_code': '1',
                              'selected_ids': ['benefitsInformation[3]'],
                              'cost_share': 'coinsurance', 'deductible_applies': True},
                  'authorization': evidence, 'referral': {**evidence, 'status': 'not_required'}},
        '64612': {'benefits': {**evidence, 'service_type_code': '2',
                              'selected_ids': ['benefitsInformation[4]'],
                              'cost_share': 'coinsurance', 'deductible_applies': True},
                  'authorization': {**evidence, 'status': 'not_required'},
                  'referral': {**evidence, 'status': 'not_required'}},
    }
    price = visit_estimate.price_lines(selected, rates, benefits, {}, payload=raw,
                                       service_date=DAY, reviews=reviews)
    assert price['complete'] and price['patient_max_cents'] == 5630
    assert [line['calculation']['patient_cents'] for line in price['lines']] == [5600, 30]
    assert [line['benefits']['coinsurance_percent'] for line in price['lines']] == ['20.00', '30.00']
    assert all(line['benefits']['requirements']['authorization']['cleared'] for line in price['lines'])
    assert all(line['rate_source'] == 'Synthetic exact-product contract' for line in price['lines'])
    missing_rate = visit_estimate.price_lines(selected, rates[:1], benefits, {}, payload=raw,
                                              service_date=DAY, reviews=reviews)
    assert not missing_rate['complete'] and missing_rate['lines'][1]['calculation'] is None
    no_approval = {**reviews, 'J0585': {**reviews['J0585'], 'authorization': {}}}
    pending = visit_estimate.price_lines(selected, rates, benefits, {}, payload=raw,
                                         service_date=DAY, reviews=no_approval)
    assert not pending['lines'][0]['benefits']['requirements']['authorization']['cleared']
    assert pending['lines'][1]['benefits']['requirements']['authorization']['cleared']


def test_unresolved_botox_preserves_insurance_and_authorized_deferral():
    benefits = {'active': True, 'network_status': 'unknown',
                'identity': {'status': 'review', 'binding': 'synthetic'}}
    result = {'payment_mode': 'insurance', 'benefits': benefits,
              'identity': benefits['identity'], 'botox_review': {},
              'price_range': {'complete': False, 'lines': [],
                              'service_issues': ['Confirm the documented injection procedure.']}}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert guidance['status'] == 'action_required'
    assert result['payment_mode'] == 'insurance'
    result['service_plan'] = {'kind': 'botox', 'recommended_ids': []}
    result['plan_details'] = {'stedi': {'names': ['Choice Plus']}}
    result['payer'] = 'UnitedHealthcare'
    result['codes'] = []
    sentence = amount_explanation.build(result, guidance)['sentence']
    assert 'select the documented injection, drug units' in sentence
    assert 'select the documented office CPT' not in sentence
    result['collection_policy'] = {'mode': 'defer_to_claim', 'approved_by': 'Admin',
                                   'approved_at': '2026-10-01T00:00:00Z', 'reference': 'Synthetic approval'}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0 and guidance['basis'] == 'claim_after_service'
