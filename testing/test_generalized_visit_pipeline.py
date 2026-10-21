"""Unseen synthetic payer/service combination through the production calculators."""
from datetime import date

from app.api.schemas.commercial import VisitVerification
from app.insurance import amount_explanation, collection_guidance, commercial, visit_estimate
from testing.synthetic_policy import ADVANCE_POLICY


def test_blue_plan_consultation_plus_nerve_study_uses_shared_pipeline():
    day = date.today().isoformat()
    raw = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y'},
        {'code': 'C', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '50'},
        {'code': 'G', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '500'},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '27', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '30'},
        {'code': 'A', 'serviceTypeCodes': ['73'], 'coverageLevelCode': 'IND',
         'inPlanNetworkIndicatorCode': 'Y', 'benefitPercent': '.20'},
    ]}
    identity = {'status': 'matched', 'binding': 'synthetic-member', 'match_version': 2}
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    benefits.update(network_status='in_network', network_source='clinic_library_contract', identity=identity)
    codes = [{'id': 'consult', 'code': '99214', 'units': 1},
             {'id': 'study', 'code': '95907', 'units': 1}]
    rates = [{'code': row['code'], 'allowed_cents': cents,
              'source_reference': 'Synthetic exact-product Blue plan contract'}
             for row, cents in zip(codes, (12000, 8000))]
    evidence = {'reference': 'Synthetic member-specific decision', 'verified_at': day,
                'valid_from': day, 'valid_to': day, 'units': 1, 'status': 'not_required'}
    reviews = {line: {'benefits': {**evidence, 'status': 'valid', 'service_type_code': category,
                                  'selected_ids': [row_id], 'cost_share': share,
                                  'deductible_applies': deductible},
                      'referral': evidence, 'authorization': evidence}
               for line, category, row_id, share, deductible in (
                   ('consult', '98', 'benefitsInformation[3]', 'copay', False),
                   ('study', '73', 'benefitsInformation[4]', 'coinsurance', True))}
    price = visit_estimate.price_lines(codes, rates, benefits, {'cost_share': 'payer_returned'},
                                       payload=raw, service_date=day, reviews=reviews)
    assert price['complete'] and price['patient_max_cents'] == 8600
    assert [line['calculation']['patient_cents'] for line in price['lines']] == [3000, 5600]
    assert sum(line['calculation']['deductible_cents'] for line in price['lines']) == 5000
    assert sum(line['calculation']['patient_cents'] + line['calculation']['plan_cents']
               for line in price['lines']) == price['allowed_max_cents'] == 20000
    result = {'payment_mode': 'insurance', 'payer': 'Synthetic Blue Plan',
              'collection_policy': ADVANCE_POLICY,
              'service_selection_status': 'approved', 'selected_ids': [row['id'] for row in codes],
              'codes': codes, 'benefits': benefits, 'identity': identity, 'price_range': price,
              'plan_details': {'stedi': {'names': ['Synthetic Blue PPO']}}}
    result['arrival_collection'] = visit_estimate.arrival_collection(price, benefits, {})
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 8600
    sentence = amount_explanation.build(result, guidance)['sentence']
    assert '$30.00 copay' in sentence and '$50.00 deductible' in sentence
    assert '$6.00 coinsurance' in sentence and 'contracted CPT prices' in sentence

    # Suggestions may be priced for preview, but cannot authorize collection.
    result.update(service_selection_status='preview', selected_ids=[])
    preview = collection_guidance.build(result)
    assert preview['amount_cents'] is None
    assert 'approve the intended services' in preview['reason']


def test_drug_benefit_rejects_unrelated_category_even_when_row_is_selected():
    day = date.today().isoformat()
    verification = VisitVerification(line_id='drug', kind='benefits', reference='Synthetic payer ref',
        notes='Synthetic drug-specific confirmation', valid_from=day, valid_to=day,
        units=80, service_type_code='33', selected_ids=['benefitsInformation[3]'],
        cost_share='coinsurance')
    raw = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y'},
        {'code': 'C', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '0'},
        {'code': 'G', 'serviceTypeCodes': ['30'], 'coverageLevelCode': 'IND',
         'timeQualifierCode': '29', 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '500'},
        {'code': 'A', 'serviceTypeCodes': ['33'], 'coverageLevelCode': 'IND',
         'inPlanNetworkIndicatorCode': 'Y', 'benefitPercent': '.20'},
        {'code': 'A', 'serviceTypeCodes': ['1'], 'coverageLevelCode': 'IND',
            'inPlanNetworkIndicatorCode': 'Y', 'benefitPercent': '.20'},
    ]}
    terms = commercial.normalize_stedi_271(raw, '33', True)
    decision = {'reference': 'Synthetic member decision', 'verified_at': day,
                'valid_from': day, 'valid_to': day, 'units': 80, 'status': 'not_required'}
    reviews = {'drug': {'benefits': {**decision, **verification.model_dump(mode='json'),
                                     'verified_at': day, 'status': 'valid'},
                        'referral': decision, 'authorization': decision}}
    price = visit_estimate.price_lines([{'id': 'drug', 'code': 'J0585', 'units': 80}],
        [{'code': 'J0585', 'allowed_cents': 100, 'source_reference': 'Synthetic exact-product rate'}],
        terms, {'cost_share': 'payer_returned'}, payload=raw, service_date=day, reviews=reviews)
    assert not price['complete']
    assert price['lines'][0]['calculation'] is None
    assert any('category' in issue.lower() for issue in price['lines'][0]['issues'])
    reviews['drug']['benefits'].update(service_type_code='1', selected_ids=['benefitsInformation[4]'])
    correct = visit_estimate.price_lines([{'id': 'drug', 'code': 'J0585', 'units': 80}],
        [{'code': 'J0585', 'allowed_cents': 100, 'source_reference': 'Synthetic exact-product rate'}],
        terms, {'cost_share': 'payer_returned'}, payload=raw, service_date=day, reviews=reviews)
    assert correct['complete'] and correct['patient_max_cents'] == 1600
    assert correct['lines'][0]['benefit_basis'] == 'Service category 1'
