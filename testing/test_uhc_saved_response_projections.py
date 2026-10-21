"""Replay deidentified benefit shapes from three saved UHC estimates.

The fixture keeps payer row scope and missing/conflicting fields; patient identifiers
and exact accumulator amounts were removed or changed.
"""
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow import commercial_previsit_pricing as pricing
from app.api.routers.commercial_flow import commercial_previsit_status as status
from app.insurance import amount_explanation, collection_guidance, commercial, encounter_summary, visit_estimate
from app.insurance.collection.requirements import requirement_blocker


FIXTURES = json.loads((Path(__file__).parent / 'fixtures' / 'uhc_saved_response_projections.json').read_text())
POLICY = {'mode': 'defer_to_claim', 'reference': 'approved clinic policy',
          'approved_by': 'administrator', 'approved_at': '2026-10-15T12:00:00Z'}


def _synthetic_choice_plus_terms(*, waive_deductible: bool = True) -> dict:
    office_notes = ['No referral required']
    if waive_deductible:
        office_notes.append('Deductible does not apply')
    return {'subscriber': {'memberId': 'SYNTHETIC', 'firstName': 'Example',
                           'lastName': 'Member', 'dateOfBirth': '1980-01-01'},
            'benefitsInformation': [
                {'code': '1', 'serviceTypeCodes': ['30'], 'planCoverageDescription': 'POS CHOICE PLUS'},
                *[{'code': 'B', 'serviceTypeCodes': ['98'], 'benefitAmount': '0',
                   'timeQualifierCode': '7', 'coverageLevelCode': 'IND',
                   'inPlanNetworkIndicatorCode': tier, 'messages': office_notes,
                   'authOrCertIndicator': 'N'} for tier in ('Y', 'N')],
                {'code': 'A', 'serviceTypeCodes': ['98'], 'benefitPercent': '.20',
                 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y'},
                {'code': 'A', 'serviceTypeCodes': ['98'], 'benefitPercent': '.40',
                 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'N'},
                {'code': 'C', 'serviceTypeCodes': ['30'], 'benefitAmount': '0',
                 'timeQualifierCode': '29', 'coverageLevelCode': 'FAM',
                 'inPlanNetworkIndicatorCode': 'Y'},
                *[{'code': 'G', 'serviceTypeCodes': ['30'], 'benefitAmount': '1000',
                   'timeQualifierCode': '29', 'coverageLevelCode': 'IND',
                   'inPlanNetworkIndicatorCode': tier} for tier in ('Y', 'N')]]}


def test_choice_plus_zero_copay_and_tier_specific_coinsurance_need_sourced_inputs():
    """The real response shape is simulated without member or contract data."""
    day = '2026-10-14'
    identity = {'status': 'matched', 'binding': 'synthetic-binding', 'match_version': 2}
    raw = _synthetic_choice_plus_terms()
    for tier, rate, expected in (('in_network', '20.00', 3000),
                                 ('out_of_network', '40.00', 6000)):
        benefits = commercial.normalize_stedi_271(raw, '98', tier == 'in_network',
            service_date=day, provider_out_of_network_verified=tier == 'out_of_network')
        benefits.update(identity=identity, network_status=tier,
            network_source='clinic_library_contract' if tier == 'in_network' else 'payer_participation_message')
        rules = {'cost_share': 'payer_returned', 'place_of_service': '11',
                 'provider_npi': '1234567890', 'patient_binding': identity['binding']}
        quote = {'id': 'synthetic-quote', 'line_id': 'office', 'code': '99203',
                 'allowed_cents': 15000, 'basis': 'payer_out_of_network',
                 'source_reference': 'synthetic payer quote', 'verified_at': day + 'T12:00:00Z',
                 'service_date': day, 'provider_npi': rules['provider_npi'],
                 'place_of_service': '11', 'patient_binding': identity['binding']}
        contracted = {'code': '99203', 'allowed_cents': 15000,
                      'source_reference': 'synthetic scoped UHC contract'}
        rates = [contracted] if tier == 'in_network' else [quote]
        price = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
            rates, benefits, rules, payload=raw, service_date=day)
        result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
                  'identity': identity, 'benefits': benefits, 'price_range': price,
                  'arrival_collection': visit_estimate.arrival_collection(price, benefits, {}),
                  'plan_details': {'stedi': {'names': ['POS CHOICE PLUS']}},
                  'service_plan': {'selected_codes': ['99203']},
                  'service_selection_status': 'approved'}
        if tier == 'out_of_network':
            unapproved = collection_guidance.build(result)
            assert unapproved['amount_cents'] is None
            assert unapproved['positive_advance_policy_missing'] is True
            assert amount_explanation.build(result, unapproved)['reason_code'] == 'collection_policy_approval_required'
            result['collection_policy'] = {**POLICY, 'allow_supported_oon_advance': True}
        guidance = collection_guidance.build(result)
        assert benefits['copay_cents'] == 0
        assert benefits['coinsurance_percent'] == rate
        assert benefits['deductible_remaining_cents'] is None  # Family balance is separate.
        assert price['complete'] and price['patient_max_cents'] == expected
        assert guidance['amount_cents'] == expected
        result['collection_guidance'] = guidance
        result['encounter_summary'] = encounter_summary.build(result)
        sentence = amount_explanation.build(result, guidance)['sentence']
        assert sentence.startswith('POS CHOICE PLUS')
        assert f'{rate.rstrip("0").rstrip(".")}% of $150.00' in sentence
        assert '$0.00 office copay' in sentence
        assert 'office deductible does not apply' in sentence
        if tier == 'out_of_network':
            missing_quote = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
                [], benefits, rules, payload=raw, service_date=day)
            result.update(price_range=missing_quote,
                          arrival_collection=visit_estimate.arrival_collection(missing_quote, benefits, {}))
            missing_guidance = collection_guidance.build(result)
            assert missing_guidance['amount_cents'] == 0
            assert missing_guidance['basis'] == 'claim_after_service'
    no_waiver = _synthetic_choice_plus_terms(waive_deductible=False)
    uncertain = commercial.normalize_stedi_271(no_waiver, '98', True, service_date=day)
    uncertain.update(identity=identity, network_status='in_network', network_source='clinic_library_contract')
    unresolved = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}],
        [contracted], uncertain, rules, payload=no_waiver, service_date=day)
    assert unresolved['complete'] is False and unresolved.get('patient_max_cents') is None
    unresolved_result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
                         'identity': identity, 'benefits': uncertain, 'price_range': unresolved,
                         'plan_details': {'stedi': {'names': ['POS CHOICE PLUS']}},
                         'service_plan': {'selected_codes': ['99203']}}
    unresolved_guidance = collection_guidance.build(unresolved_result)
    unresolved_sentence = amount_explanation.build(unresolved_result, unresolved_guidance)['sentence']
    assert unresolved_guidance['amount_cents'] is None
    assert unresolved_sentence.startswith('UHC reported active POS CHOICE PLUS medical coverage.')
    assert '$0.00 professional office copay, 20.00% in-network and 40.00% out-of-network coinsurance' in unresolved_sentence
    assert 'No insured amount is ready' in unresolved_sentence


def test_same_policy_visit_benefit_conflict_withholds_existing_positive_advance():
    result = supported_result()
    assert result['collection_guidance']['amount_cents'] is not None
    result['recovery'] = {'state': 'complete', 'outcome': 'review_needed',
                          'reason_code': 'same_policy_evidence_conflict',
                          'active_policy_count': 1}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert 'same medical policy' in guidance['reason']
    assert amount_explanation.build(result, guidance)['reason_code'] == 'same_policy_evidence_conflict'


def replay(name: str) -> tuple[dict, dict, dict]:
    case = FIXTURES[name]
    benefits = commercial.normalize_stedi_271(case['payer_response'], '98', False,
                                               service_date='2026-10-14')
    benefits['identity'] = case['identity']
    benefits['has_secondary'] = name == 'multiple_policies'
    lines = [{'code': code, 'calculation': None, 'allowed_cents': None,
              'benefits': benefits} for code in case['service_plan']['alternatives']]
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'benefit_source': 'Stedi', 'benefits': benefits, 'identity': case['identity'],
              'plan_details': {'stedi': {'names': [case['product']]}},
              'recovery': case['recovery'], 'service_plan': case['service_plan'],
              'price_range': {'complete': False,
                              'mode': 'alternatives' if lines else 'services', 'lines': lines},
              'collection_policy': POLICY}
    if name == 'regional_ppo':
        result['issues'] = ['Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.']
    guidance = collection_guidance.build(result)
    return result, guidance, amount_explanation.build(result, guidance)


def supported_result() -> dict:
    """A wholly synthetic positive UHC path through the pricing modules."""
    from testing.test_visit_estimate import DAY

    raw = _supported_payload()
    identity = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    benefits = commercial.normalize_stedi_271(raw, '98', True, service_date=DAY)
    benefits.update(identity=identity, network_source='clinic_library_contract')
    codes = [{'id': 'office', 'code': '99203', 'units': 1},
             {'id': 'test', 'code': '93000', 'units': 1}]
    rates = [{'code': code['code'], 'allowed_cents': 10000,
              'source_reference': 'synthetic-scoped-contract'} for code in codes]
    price = visit_estimate.price_lines(codes, rates, benefits, {
        'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'},
    }, payload=raw, service_date=DAY)
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'appointment_id': 'synthetic-visit', 'service_date': DAY,
              'checked_at': f'{DAY}T12:00:00Z', 'identity': identity,
              'benefits': benefits, 'codes': codes, 'selected_ids': ['office', 'test'],
              'available_codes': codes, 'price_range': price,
              'service_plan': {'kind': 'office', 'selected_codes': ['99203', '93000']}}
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    result['encounter_summary'] = encounter_summary.build(result)
    result['payment_explanation'] = amount_explanation.build(result, guidance)
    return result


def _supported_payload() -> dict:
    from testing.test_visit_estimate import payload

    raw = payload()
    raw['benefitsInformation'].append({
        'code': 'G', 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y',
        'serviceTypeCodes': ['30'], 'timeQualifierCode': '23', 'benefitAmount': '1000',
    })
    return raw


def supported_four_requirement_result() -> dict:
    """Synthetic priced visit covering every selected-CPT requirement combination."""
    from testing.test_visit_estimate import DAY

    result = supported_result()
    codes = [{'id': f'line-{code}', 'code': code, 'units': 1}
             for code in ('99203', '93000', '95907', '95886')]
    rates = [{'code': row['code'], 'allowed_cents': 10000,
              'source_reference': 'synthetic-scoped-contract'} for row in codes]
    result['price_range'] = visit_estimate.price_lines(
        codes, rates, result['benefits'],
        {'cost_share': 'payer_returned',
         'service_type_by_code': {row['code']: '30' for row in codes[1:]}},
        payload=_supported_payload(), service_date=DAY)
    result.update(codes=codes, selected_ids=[row['id'] for row in codes],
                  available_codes=codes,
                  service_plan={'kind': 'office', 'selected_codes': [row['code'] for row in codes]})
    no_rule = {'status': 'Not required', 'reason': 'Synthetic payer rule', 'sources': []}
    required = {'status': 'Required', 'reason': 'Synthetic payer rule', 'sources': []}
    decisions = [('99203', False, False), ('93000', False, True),
                 ('95907', True, False), ('95886', True, True)]
    procedures = []
    for line, (_, referral, authorization) in zip(result['price_range']['lines'], decisions):
        decision = {'id': line['id'], 'code': line['code'],
                    'referral': required if referral else no_rule,
                    'authorization': {**required, 'approval_status': 'Verified'} if authorization else no_rule}
        procedures.append(decision)
        for kind, needed in (('referral', referral), ('authorization', authorization)):
            line['benefits']['requirements'][kind] = ({
                'required': 'required', 'status': 'valid', 'cleared': True,
                'reference': f"synthetic-{kind}-{line['code']}",
                'verified_at': f'{DAY}T12:00:00Z'} if needed else
                {'required': 'not_required', 'status': 'not_applicable', 'cleared': True})
    result['requirement_evidence'] = {'referral': required,
                                      'authorization': {**required, 'approval_status': 'Verified'},
                                      'procedures': procedures}
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    result['encounter_summary'] = encounter_summary.build(result)
    result['payment_explanation'] = amount_explanation.build(result, guidance)
    return result


def test_four_selected_cpts_keep_distinct_sourced_requirement_decisions():
    result = supported_four_requirement_result()
    assert result['price_range']['complete'] is True
    assert result['collection_guidance']['amount_cents'] == 8000
    assert result['encounter_summary']['collect_today_cents'] == 8000
    assert [(row['code'], row['referral']['status'], row['authorization']['status'])
            for row in result['requirement_evidence']['procedures']] == [
                ('99203', 'Not required', 'Not required'),
                ('93000', 'Not required', 'Required'),
                ('95907', 'Required', 'Not required'),
                ('95886', 'Required', 'Required')]


def test_cpt_authorization_rule_conflicting_with_saved_line_withholds_amount():
    result = supported_four_requirement_result()
    result['requirement_evidence']['authorization'] = {}
    result['price_range']['lines'][1]['benefits']['requirements']['authorization'] = {
        'required': 'not_required', 'status': 'not_applicable', 'cleared': True,
    }
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    summary = encounter_summary.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert summary['collect_today_cents'] is None
    assert explanation['reason_code'] == 'authorization_required'
    assert 'CPT 93000' in explanation['sentence']


def test_cpt_no_referral_rule_conflicting_with_saved_required_line_withholds_amount():
    result = supported_four_requirement_result()
    result['price_range']['lines'][0]['benefits']['requirements']['referral'] = {
        'required': 'required', 'status': 'valid', 'cleared': True,
        'reference': 'synthetic-referral-99203', 'verified_at': '2026-10-14T12:00:00Z',
    }
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    assert guidance['amount_cents'] is None
    assert encounter_summary.build(result)['collect_today_cents'] is None
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'referral_required'
    assert 'CPT 99203' in explanation['sentence']


def test_same_cpt_on_two_lines_uses_each_lines_requirement_decision():
    no_rule = {'required': 'not_required', 'status': 'not_applicable', 'cleared': True}
    cleared = {'required': 'required', 'status': 'valid', 'cleared': True,
               'reference': 'synthetic-referral', 'verified_at': '2026-10-14T12:00:00Z'}
    result = {
        'payer': 'United Healthcare',
        'price_range': {'mode': 'services', 'lines': [
            {'id': 'office-a', 'code': '99203', 'benefits': {'requirements': {
                'referral': cleared, 'authorization': no_rule}}},
            {'id': 'office-b', 'code': '99203', 'benefits': {'requirements': {
                'referral': no_rule, 'authorization': no_rule}}},
        ]},
        'requirement_evidence': {
            'referral': {'status': 'Required'},
            'procedures': [
                {'id': 'office-a', 'code': '99203', 'referral': {'status': 'Required'}},
                {'id': 'office-b', 'code': '99203', 'referral': {'status': 'Not required'}},
            ],
        },
    }
    assert requirement_blocker(result) is None
    result['price_range']['lines'][0]['benefits']['requirements']['referral'] = no_rule
    assert requirement_blocker(result)[1] == 'referral_required'
    result['price_range']['lines'][0]['id'] = '99203'
    result['price_range']['lines'][0]['benefits']['requirements']['referral'] = cleared
    result['requirement_evidence']['procedures'] = [
        {'id': '99203', 'code': '99203', 'referral': {'status': 'Required'}},
    ]
    assert requirement_blocker(result)[1] == 'referral_required'


def test_two_priced_lines_sharing_cpt_keep_supported_collection_when_one_needs_referral():
    from testing.test_visit_estimate import DAY

    result = supported_result()
    codes = [{'id': 'ecg-a', 'code': '93000', 'units': 1},
             {'id': 'ecg-b', 'code': '93000', 'units': 1}]
    result['price_range'] = visit_estimate.price_lines(
        codes, [{'code': '93000', 'allowed_cents': 10000,
                 'source_reference': 'synthetic-scoped-contract'}],
        result['benefits'], {'cost_share': 'payer_returned',
                             'service_type_by_code': {'93000': '30'}},
        payload=_supported_payload(), service_date=DAY)
    result.update(codes=codes, selected_ids=[row['id'] for row in codes],
                  service_plan={'kind': 'testing', 'selected_codes': ['93000']})
    no_rule = {'required': 'not_required', 'status': 'not_applicable', 'cleared': True}
    required = {'required': 'required', 'status': 'valid', 'cleared': True,
                'reference': 'synthetic-referral', 'verified_at': f'{DAY}T12:00:00Z'}
    result['price_range']['lines'][0]['benefits']['requirements'] = {
        'referral': required, 'authorization': no_rule}
    result['price_range']['lines'][1]['benefits']['requirements'] = {
        'referral': no_rule, 'authorization': no_rule}
    result['requirement_evidence'] = {
        'referral': {'status': 'Required'},
        'procedures': [
            {'id': 'ecg-a', 'code': '93000', 'referral': {'status': 'Required'}},
            {'id': 'ecg-b', 'code': '93000', 'referral': {'status': 'Not required'}},
        ],
    }
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    assert result['price_range']['complete'] is True
    assert guidance['amount_cents'] == result['price_range']['patient_min_cents'] == 8000
    assert encounter_summary.build(result)['collect_today_cents'] == 8000
    result['price_range']['lines'][0]['benefits']['requirements']['referral']['reference'] = ''
    assert collection_guidance.build(result)['amount_cents'] is None


@pytest.mark.parametrize('name,reason_code', [
    ('regional_ppo', 'clinic_contract_missing'),
    ('multiple_policies', 'multiple_active_policies'),
    ('identity_mismatch', 'policy_identity_mismatch'),
])
def test_saved_get_rebuilds_three_uhc_results_without_inheriting_old_zero_policy(name, reason_code):
    result, _, _ = replay(name)
    result['service_date'] = '2026-10-14'
    case = {'source_mode': 'commercial_previsit', 'revision': 1,
            'previsit_context': {'source': {}, 'result': result},
            'previsit': {'request_state': 'complete'}, 'visit_review': {}}
    session = {'patient_id': 'synthetic-patient', 'share_manifest_id': None,
               'practice_id': 'synthetic-practice', 'intake_id': 'synthetic-intake',
               'staff': {'id': 'synthetic-staff', 'display_name': 'Synthetic staff'}}
    app = FastAPI()
    app.include_router(api.router)
    with patch.object(api, '_session', new=AsyncMock(return_value=session)), \
         patch.object(api.store, 'get_case', return_value=deepcopy(case)) as get_case:
        response = TestClient(app).get('/api/doctor/insurance/commercial/cases/synthetic-case/previsit')
    assert response.status_code == 200
    opened = response.json()
    assert get_case.call_args.args == ('synthetic-case', 'synthetic-patient', None)
    assert get_case.call_args.kwargs['practice_id'] == 'synthetic-practice'
    assert opened['benefits']['active'] is True
    assert opened['payment_mode'] == 'insurance'
    assert opened['collection_guidance']['amount_cents'] is None
    assert opened['payment_explanation']['reason_code'] == reason_code
    assert opened['insurance_diagnosis']['eligibilityStatus'] in {'BENEFITS_VERIFIED', 'PAYER_BENEFITS_RETURNED'}
    assert all(row['patient_cents'] is None for row in opened['collection_guidance']['services'])


def test_saved_get_uses_current_approved_clinic_zero_policy_without_zero_liability():
    result, _, _ = replay('multiple_policies')
    result['service_date'] = '2026-10-14'
    case = {'source_mode': 'commercial_previsit', 'revision': 1,
            'previsit_context': {'payer_id': 'synthetic-route', 'source': {}, 'result': result},
            'previsit': {'request_state': 'complete'}, 'visit_review': {}}
    session = {'patient_id': 'synthetic-patient', 'share_manifest_id': None,
               'practice_id': 'synthetic-practice', 'intake_id': 'synthetic-intake',
               'staff': {'id': 'synthetic-staff', 'display_name': 'Synthetic staff'}}
    connection = {'stedi_payer_id': 'synthetic-route', 'rules': {'collection_policy': POLICY}}
    app = FastAPI()
    app.include_router(api.router)
    with patch.object(api, '_session', new=AsyncMock(return_value=session)), \
         patch.object(api.store, 'get_case', return_value=deepcopy(case)), \
         patch.object(api.commercial_store, 'list_connections', return_value=[connection]):
        response = TestClient(app).get('/api/doctor/insurance/commercial/cases/synthetic-case/previsit')
    assert response.status_code == 200
    opened = response.json()
    assert opened['collection_guidance']['amount_cents'] == 0
    assert opened['collection_guidance']['basis'] == 'claim_after_service'
    assert opened['encounter_summary']['collect_today_cents'] == 0
    assert opened['encounter_summary']['estimated_total_patient_min_cents'] is None
    assert opened['benefits']['active'] is True
    assert '3 active policies' in opened['payment_explanation']['sentence']


def test_saved_get_retains_a_supported_synthetic_uhc_collection_and_breakdown():
    result = supported_four_requirement_result()
    result['network_evidence'] = {'document_id': 'synthetic-contract'}
    for line in result['price_range']['lines']:
        line['rate_version'] = 'synthetic-version'
    case = {'source_mode': 'commercial_previsit', 'revision': 1,
            'previsit_context': {'payer_id': 'synthetic-route',
                                 'inputs': {'service_date': result['service_date']},
                                 'source': {}, 'result': result},
            'previsit': {'request_state': 'complete'}, 'visit_review': {}}
    session = {'patient_id': 'synthetic-patient', 'share_manifest_id': None,
               'practice_id': 'synthetic-practice', 'intake_id': 'synthetic-intake',
               'staff': {'id': 'synthetic-staff', 'display_name': 'Synthetic staff'}}
    app = FastAPI()
    app.include_router(api.router)
    connection = {'id': 'synthetic-connection', 'stedi_payer_id': 'synthetic-route',
                  'status': 'staff_guidance_enabled', 'rules': {}}
    network = {'id': 'synthetic-network'}
    profile = {'id': 'synthetic-profile'}
    rates = [{'code': line['code'], 'modifier': '', 'allowed_cents': line['unit_allowed_cents'],
              'version_id': line['rate_version']} for line in result['price_range']['lines']]
    with patch.object(api, '_session', new=AsyncMock(return_value=session)), \
         patch.object(api.store, 'get_case', return_value=deepcopy(case)), \
         patch.object(api.commercial_store, 'list_connections', return_value=[connection]), \
         patch.object(api, '_previsit_practice_path', return_value=(connection, network, profile)), \
         patch.object(api, '_network_contract_evidence', return_value={'document_id': 'synthetic-contract'}), \
         patch.object(api.commercial_store, 'matching_rates', return_value=rates):
        response = TestClient(app).get('/api/doctor/insurance/commercial/cases/synthetic-case/previsit')
    assert response.status_code == 200
    opened = response.json()
    assert opened['benefits']['active'] is True
    assert opened['collection_guidance']['amount_cents'] == 8000
    assert opened['encounter_summary']['collect_today_cents'] == 8000
    assert opened['encounter_summary']['components']['deductible_cents'] == 5000
    assert opened['encounter_summary']['components']['coinsurance_cents'] == 3000
    assert opened['payment_explanation']['reason_code'] == 'insured_services'
    assert [row['code'] for row in opened['price_range']['lines']] == [
        '99203', '93000', '95907', '95886']
    assert opened['requirement_evidence']['procedures'][0]['referral']['status'] == 'Not required'
    assert opened['requirement_evidence']['procedures'][1]['authorization']['approval_status'] == 'Verified'
    assert opened['requirement_evidence']['procedures'][2]['referral']['status'] == 'Required'
    assert opened['requirement_evidence']['procedures'][3]['authorization']['status'] == 'Required'


@pytest.mark.parametrize('name', FIXTURES)
def test_saved_payer_benefits_remain_present_when_collection_is_deferred(name):
    result, guidance, explanation = replay(name)
    assert result['benefits']['active'] is True
    assert guidance['amount_cents'] == 0 and guidance['basis'] == 'claim_after_service'
    assert guidance['status'] == 'zero_advance'
    assert 'member protections permit it' in guidance['patient_message']
    assert explanation['sentence'].startswith('Collect $0 today:')
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None
    assert all(row['patient_cents'] is None for row in guidance['services'])
    assert collection_guidance.build({**result, 'collection_policy': {}})['amount_cents'] is None


@pytest.mark.parametrize('name,action', [
    ('regional_ppo', 'check UHC Regional PPO participation under a separate agreement'),
    ('multiple_policies', 'rerun eligibility and a UHC Stedi coordination-of-benefits check'),
    ('identity_mismatch', 'reconcile first name, surname, subscriber first name, subscriber surname'),
])
def test_unapproved_collection_policy_still_shows_the_exact_staff_action(name, action):
    result, _, _ = replay(name)
    result.pop('collection_policy')
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert guidance['status'] == 'action_required'
    assert guidance['basis'] == 'missing_collection_inputs'
    assert action in explanation['sentence']
    assert ';' not in explanation['sentence']


@pytest.mark.parametrize('name,reason_code', [
    ('regional_ppo', 'clinic_contract_missing'),
    ('multiple_policies', 'multiple_active_policies'),
    ('identity_mismatch', 'policy_identity_mismatch'),
])
def test_plan_referral_signal_does_not_hide_the_saved_case_blocker(name, reason_code):
    result, guidance, _ = replay(name)
    result['benefits']['referral_signal'] = 'required'
    assert amount_explanation.build(result, guidance)['reason_code'] == reason_code


def test_later_empty_search_does_not_relabel_an_active_uhc_response_as_no_policy():
    result, guidance, _ = replay('regional_ppo')
    result['recovery'] = {'reason_code': 'no_coverage_found', 'outcome': 'no_match'}
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'clinic_contract_missing'
    assert 'no policy' not in explanation['sentence'].lower()
    result['benefits']['service_date_matches'] = False
    assert amount_explanation.build(result, guidance)['reason_code'] == 'no_policy_returned'


def test_prior_enrollment_or_member_rejection_does_not_erase_active_uhc_benefits():
    result, _, _ = replay('regional_ppo')
    result['eligibility_failure'] = {'kind': 'provider_enrollment', 'codes': ['72']}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'clinic_contract_missing'
    assert 'enrollment' not in guidance['reason'].lower()
    assert not any('member ID' in action for action in guidance['patient_actions'])


def test_prior_enrollment_failure_is_not_a_current_blocker_after_active_uhc_271():
    result, guidance, _ = replay('regional_ppo')
    result['eligibility_failure'] = {'kind': 'provider_enrollment', 'codes': ['72']}
    enrollment = {'status': 'MISSING', 'reason_code': 'enrollment_missing'}
    case = {'previsit_context': {'source': {}}, 'previsit_discovery': {'enrollment': enrollment}}
    diagnosis = api._insurance_diagnosis(case, result, guidance)
    assert diagnosis['enrollment'] == enrollment
    assert 'ENROLLMENT_BLOCKED' not in diagnosis['states']
    assert 'ENROLLMENT_MISSING' not in diagnosis['reasonCodes']
    assert 'PROVIDER_ENROLLMENT' not in diagnosis['reasonCodes']
    assert diagnosis['eligibilityStatus'] == 'BENEFITS_VERIFIED'
    result['benefits']['active'] = None
    missing = api._insurance_diagnosis(case, result, guidance)
    assert 'ENROLLMENT_BLOCKED' in missing['states']
    assert 'ENROLLMENT_MISSING' in missing['reasonCodes']


def test_regional_ppo_same_scope_annual_oop_conflict_and_no_invented_allowance():
    result, _, explanation = replay('regional_ppo')
    terms = result['benefits']
    assert terms['coinsurance_percent'] == '20.00' and terms['copay_cents'] == 0
    assert terms['oop_annual_cents'] is None
    assert len(terms['oop_annual_conflict_ids']) == 2
    assert any('conflicting annual individual out-of-pocket maximum' in reason.lower()
               for reason in terms['review_reasons'])
    assert terms['network_status'] == 'unknown'
    assert [line['code'] for line in result['price_range']['lines']] == ['99202', '99203', '99204', '99205']
    assert all(line['allowed_cents'] is None for line in result['price_range']['lines'])
    assert explanation['reason_code'] == 'clinic_contract_missing'
    assert 'check UHC Regional PPO participation under a separate agreement' in explanation['sentence']
    assert 'allowed amount from the applicable agreement or UHC out-of-network quote' in explanation['sentence']
    assert 'which annual individual out-of-pocket maximum applies' in explanation['sentence']

    reversed_rows = json.loads(json.dumps(FIXTURES['regional_ppo']['payer_response']))
    reversed_rows['plans'][0]['benefits']['outOfPocket'].reverse()
    reordered = commercial.normalize_stedi_271(reversed_rows, '98', False, service_date='2026-10-14')
    assert reordered['oop_annual_cents'] is None
    assert len(reordered['oop_annual_conflict_ids']) == 2


def test_regional_ppo_conflict_preserves_plan_and_benefit_schedule_dates():
    response = deepcopy(FIXTURES['regional_ppo']['payer_response'])
    response['plans'][0]['dates'] = {'planBegin': '2026-01-01'}
    response['plans'][0]['benefits']['outOfPocket'][1]['benefitsDateInformation'] = {
        'benefit': {'start': '2026-06-01', 'end': '2026-12-31'}}
    terms = commercial.normalize_stedi_271(response, '98', False, service_date='2026-10-14')
    options = {row['id']: row for row in terms['reported_options']}
    dates = [options[row_id]['effective_dates'] for row_id in terms['oop_annual_conflict_ids']]
    assert all(row['plan.planBegin'] == '2026-01-01' for row in dates)
    assert any(row.get('benefit schedule.benefit') == '2026-06-01 to 2026-12-31' for row in dates)


def test_regional_ppo_amount_action_only_names_current_cpt_and_oop_gaps():
    result, _, _ = replay('regional_ppo')
    result['service_plan']['selected_codes'] = ['99203']
    result['benefits']['oop_annual_conflict_ids'] = []
    result['benefits']['oop_remaining_conflict_ids'] = []
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'clinic_contract_missing'
    assert 'select the documented E/M CPT' not in explanation['sentence']
    assert 'out-of-pocket maximum applies' not in explanation['sentence']
    assert 'out-of-pocket balance applies' not in explanation['sentence']


def test_same_scope_remaining_oop_conflict_is_distinct_from_annual_maximum():
    response = json.loads(json.dumps(FIXTURES['regional_ppo']['payer_response']))
    rows = response['plans'][0]['benefits']['outOfPocket']
    rows[1]['amount'] = rows[0]['amount']
    rows.append({**rows[2], 'amount': '7400'})
    terms = commercial.normalize_stedi_271(response, '98', False, service_date='2026-10-14')
    assert terms['oop_annual_cents'] == 900000
    assert terms['oop_annual_conflict_ids'] == []
    assert terms['oop_remaining_cents'] is None
    assert len(terms['oop_remaining_conflict_ids']) == 2
    assert any('conflicting remaining individual out-of-pocket balances' in reason.lower()
               for reason in terms['review_reasons'])


def test_annual_oop_conflict_names_distinct_plan_scope_without_selecting_a_limit():
    response = deepcopy(FIXTURES['regional_ppo']['payer_response'])
    first = response['plans'][0]
    first['name'] = 'Synthetic first plan'
    first['benefits'] = {'statuses': first['benefits']['statuses'],
                         'outOfPocket': [first['benefits']['outOfPocket'][0]]}
    second = deepcopy(first)
    second['name'] = 'Synthetic second plan'
    second['benefits']['outOfPocket'][0]['amount'] = '8000'
    response['plans'].append(second)
    terms = commercial.normalize_stedi_271(response, '98', False, service_date='2026-10-14')
    assert terms['oop_annual_cents'] is None
    assert len(terms['oop_annual_conflict_ids']) == 2
    assert any('across payer plans' in reason for reason in terms['review_reasons'])
    assert not any('for the same plan and network tier' in reason for reason in terms['review_reasons'])


@pytest.mark.parametrize('change,scope', [
    ({'network': {'indicator': 'IN_NETWORK'}}, 'benefit network tiers'),
    ({'dates': {'benefitBegin': '2026-06-01'}}, 'effective periods'),
])
def test_annual_oop_conflict_names_varying_tier_or_overlapping_dates(change, scope):
    response = deepcopy(FIXTURES['regional_ppo']['payer_response'])
    rows = response['plans'][0]['benefits']['outOfPocket']
    rows[1].update(change)
    if scope == 'effective periods':
        rows[0]['dates'] = {'benefitBegin': '2026-01-01'}
    terms = commercial.normalize_stedi_271(response, '98', False, service_date='2026-10-14')
    assert terms['oop_annual_cents'] is None
    assert any('across ' + scope in reason for reason in terms['review_reasons'])
    if scope == 'effective periods':
        options = {row['id']: row for row in terms['reported_options']}
        assert options[terms['oop_annual_conflict_ids'][0]]['effective_dates']['benefit row.benefitBegin'] == '2026-01-01'
        assert options[terms['oop_annual_conflict_ids'][1]]['effective_dates']['benefit row.benefitBegin'] == '2026-06-01'


def test_family_oop_row_does_not_conflict_with_individual_annual_limit():
    response = deepcopy(FIXTURES['regional_ppo']['payer_response'])
    response['plans'][0]['benefits']['outOfPocket'][1]['coverageLevel'] = 'FAMILY'
    terms = commercial.normalize_stedi_271(response, '98', False, service_date='2026-10-14')
    assert terms['oop_annual_cents'] == 900000
    assert terms['oop_annual_conflict_ids'] == []


def test_three_active_policies_do_not_acquire_discovery_order_or_family_deductible():
    result, _, explanation = replay('multiple_policies')
    terms = result['benefits']
    assert terms['coinsurance_percent'] == '20.00'
    assert terms['deductible_remaining_cents'] is None
    assert all(candidate['priority'] is None for candidate in result['recovery']['candidates'])
    assert explanation['reason_code'] == 'multiple_active_policies'
    assert '3 active policies' in explanation['sentence']
    assert ('reconcile subscriber first name, subscriber relationship with UHC against eCW, '
            'then rerun eligibility and a UHC Stedi coordination-of-benefits check for primary, '
            'secondary and tertiary order') in explanation['sentence']
    assert 'before selecting the documented office CPT' in explanation['sentence']
    assert ';' not in explanation['sentence']


def test_saved_uhc_result_clears_unbound_legacy_provider_network_label_on_read():
    result, _, _ = replay('regional_ppo')
    result.pop('collection_policy', None)
    result.update(service_date='2026-10-14', payment_mode='insurance',
                  codes=[{'id': 'office', 'code': '99203', 'units': 1}],
                  checked_at='2026-10-14T12:00:00Z')
    result['benefits'].update(network_status='out_of_network',
                              network_source='payer_participation_message')
    case = {'previsit_context': {'result': result, 'payer_id': 'synthetic-uhc-route',
        'source': {'rendering_provider': {'npi': '2222222222',
                                         'first_name': 'Synthetic', 'last_name': 'Clinician'}},
        'inputs': {'rendering_provider': {'npi': '2222222222'},
                   'service_date': '2026-10-14'}},
        'previsit': {'request_state': 'complete'}, 'visit_review': {}}
    opened = status._previsit_result(case)
    assert opened['benefits']['active'] is True
    assert opened['benefits']['network_status'] == 'unknown'
    assert opened['collection_guidance']['amount_cents'] is None
    assert any('not bound to this rendering provider NPI' in issue for issue in opened['issues'])


@pytest.mark.parametrize('coordination,action', [
    ({'state': 'indeterminate'}, 'check the submitted Stedi COB transaction outcome before retrying'),
    ({'state': 'complete', 'status': 'primacy_undetermined'}, 'ask UHC and the other payers to establish'),
    ({'state': 'complete', 'status': 'primacy_reported'}, 'match the payer-reported roles to all active policies'),
    ({'state': 'complete', 'status': 'primacy_conflict'}, 'reconcile conflicting medical payer roles'),
    ({'state': 'complete', 'status': 'no_cob_found'}, 'Stedi COB found no other plan'),
    ({'state': 'complete', 'status': 'no_benefit_overlap'}, 'Stedi COB found no overlapping benefits'),
    ({'state': 'complete', 'status': 'cob_not_required'}, 'Stedi says COB is not required'),
    ({'state': 'complete', 'status': 'rejected'}, 'reconcile the member details and UHC COB route'),
    ({'state': 'not_ready', 'reason_code': 'cob_payer_route_unsupported'}, 'ask UHC directly to establish'),
    ({'state': 'not_ready', 'reason_code': 'cob_credentials_missing'}, 'clinic administrator: connect the Stedi COB account'),
    ({'state': 'not_ready', 'reason_code': 'cob_subscriber_fields_missing',
      'missing_fields': ['subscriber date of birth']}, 'obtain subscriber date of birth from UHC'),
    ({'state': 'not_ready', 'reason_code': 'cob_future_visit_date'}, 'reopen this visit on its date to run UHC COB'),
])
def test_three_policy_staff_action_reflects_the_actual_cob_outcome(coordination, action):
    result, guidance, _ = replay('multiple_policies')
    result['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    result['coordination'] = coordination
    explanation = amount_explanation.build(result, guidance)
    assert action in explanation['sentence']
    assert 'run a UHC Stedi coordination-of-benefits check' not in explanation['sentence']
    if coordination.get('status') == 'primacy_conflict':
        assert 'UHC COB reported conflicting medical payer roles' in explanation['sentence']
        assert 'did not identify which pays first' not in explanation['sentence']


def test_three_policy_stale_cob_roles_cannot_outrank_current_identity_mismatch():
    result, guidance, _ = replay('multiple_policies')
    result['coordination'] = {'state': 'complete', 'status': 'primacy_reported'}
    explanation = amount_explanation.build(result, guidance)
    assert 'payer-reported roles are not yet tied to this patient and all active policies' in explanation['sentence']
    assert 'did not identify which pays first' not in explanation['sentence']
    assert 'reconcile subscriber first name, subscriber relationship with UHC against eCW' in explanation['sentence']
    assert 'rerun eligibility and a UHC Stedi coordination-of-benefits check' in explanation['sentence']
    assert 'match the payer-reported roles' not in explanation['sentence']


def test_three_policy_indeterminate_cob_keeps_identity_reconciliation_after_outcome_check():
    result, guidance, _ = replay('multiple_policies')
    result['coordination'] = {'state': 'indeterminate'}
    explanation = amount_explanation.build(result, guidance)
    assert 'check the submitted Stedi COB transaction outcome before retrying' in explanation['sentence']
    assert 'reconcile subscriber first name, subscriber relationship with UHC against eCW' in explanation['sentence']
    assert 'then rerun eligibility and COB' in explanation['sentence']


def test_earlier_uhc_271_other_coverage_gets_cob_action_without_discovery():
    result, _, _ = replay('multiple_policies')
    result['recovery'] = {}
    result['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    result['benefits']['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] == 0  # Synthetic approved clinic deferral policy.
    assert explanation['reason_code'] == 'multiple_active_policies'
    assert 'run a UHC Stedi coordination-of-benefits check' in explanation['sentence']


def test_identity_mismatch_beats_empty_followup_search_and_blocks_accumulators():
    result, _, explanation = replay('identity_mismatch')
    assert result['benefits']['deductible_remaining_cents'] is not None
    assert result['benefits']['copay_cents'] is None
    scoped = commercial.normalize_stedi_271(FIXTURES['identity_mismatch']['payer_response'],
                                             '98', True, place_of_service='11', service_date='2026-10-14')
    assert scoped['copay_cents'] is None  # POS 11 matches both rows; UHC did not identify which applies.
    assert explanation['reason_code'] == 'policy_identity_mismatch'
    assert 'reconcile first name, surname, subscriber first name, subscriber surname' in explanation['sentence']
    assert 'select the documented office CPT' in explanation['sentence']
    assert 'ask UHC which office copay applies at this place of service' in explanation['sentence']
    assert not result['service_plan']['selected_codes']


@pytest.mark.parametrize('reverse_rows', [False, True])
def test_conflicting_office_copays_stay_unresolved_in_either_payer_row_order(reverse_rows):
    response = deepcopy(FIXTURES['identity_mismatch']['payer_response'])
    rows = response['plans'][0]['benefits']['coPayment']
    if reverse_rows:
        rows.reverse()

    terms = commercial.normalize_stedi_271(
        response, '98', True, service_date='2026-10-14',
        place_of_service='11', office_visit=True,
    )

    assert terms['active'] is True
    assert terms['copay_cents'] is None
    assert 'which copay or coinsurance term applies' in terms['review_reasons'][0]


def test_empty_followup_search_does_not_hide_earlier_active_benefits():
    result, _, _ = replay('identity_mismatch')
    result.update(service_date='2026-10-14', checked_at='2026-10-14T12:00:00Z')
    case = {'revision': 1, 'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': None, 'source': {}, 'result': result},
            'previsit_discovery': {'request_state': 'complete', 'outcome': 'no_match',
                                   'reason_code': 'no_coverage_found', 'exhausted': True}}
    reopened = api._previsit_result(case)
    assert reopened['benefits']['active'] is True
    assert reopened['coverage_status'] == 'REVIEW_REQUIRED'
    assert 'earlier payer response still contains active benefits' in reopened['recovery']['message']
    assert reopened['insurance_diagnosis']['eligibilityStatus'] == 'PAYER_BENEFITS_RETURNED'


def test_returned_benefits_are_not_labelled_missing_or_an_estimate_ready():
    for name in FIXTURES:
        result, guidance, _ = replay(name)
        case = {'previsit_context': {'source': {}}, 'previsit_discovery': {}}
        diagnosis = api._insurance_diagnosis(case, result, guidance)
        assert diagnosis['pricingStatus'] == 'NOT_CALCULATED'
        assert diagnosis['eligibilityStatus'] == ('BENEFITS_VERIFIED' if name == 'regional_ppo'
                                                  else 'PAYER_BENEFITS_RETURNED')
        if name == 'multiple_policies':
            assert diagnosis['identityStatus'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize(('network_status', 'network_source', 'expected_rate_reason', 'network_unverified'), [
    ('out_of_network', 'payer_participation_message', 'PAYER_OUT_OF_NETWORK_ALLOWANCE_MISSING', False),
    ('in_network', 'clinic_library_contract', 'CONTRACTED_RATE_MISSING', False),
    ('in_network', None, None, True),
])
def test_uhc_diagnosis_names_the_correct_network_and_allowance_source(
        network_status, network_source, expected_rate_reason, network_unverified):
    result = {'payer': 'United Healthcare', 'benefit_source': 'Stedi',
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': network_status,
                           'network_source': network_source},
              'price_range': {'complete': False, 'lines': [{'code': '99203', 'allowed_cents': None}]}}
    diagnosis = api._insurance_diagnosis({'previsit_context': {'source': {}}}, result,
                                         {'amount_cents': None, 'reason': 'Allowance is needed.'})
    reasons = diagnosis['reasonCodes']
    assert ('PROVIDER_NETWORK_NOT_VERIFIED' in reasons) is network_unverified
    assert ('CONTRACTED_RATE_MISSING' in reasons) is (expected_rate_reason == 'CONTRACTED_RATE_MISSING')
    assert ('PAYER_OUT_OF_NETWORK_ALLOWANCE_MISSING' in reasons) is (
        expected_rate_reason == 'PAYER_OUT_OF_NETWORK_ALLOWANCE_MISSING')
    assert diagnosis['eligibilityStatus'] == 'BENEFITS_VERIFIED'


def test_discovered_patient_match_does_not_override_payer_subscriber_mismatch():
    result, guidance, explanation = replay('multiple_policies')
    result['recovery']['raw_candidate_count'] = 4
    result['recovery']['candidates'].append({
        'active_medical': False, 'identity_status': 'MATCHED',
        'verification_status': 'VERIFIED', 'priority': None})
    case = {'previsit_context': {'source': {}}, 'previsit_discovery': {}}
    diagnosis = api._insurance_diagnosis(case, result, guidance)
    assert diagnosis['identityStatus'] == 'REVIEW_REQUIRED'
    assert diagnosis['eligibilityStatus'] == 'PAYER_BENEFITS_RETURNED'
    assert '3 active policies' in amount_explanation.build(result, guidance)['sentence']


def test_inactive_discovery_hit_does_not_create_a_tertiary_payer():
    result, guidance, _ = replay('multiple_policies')
    result['recovery']['candidates'][1]['active_medical'] = False
    explanation = amount_explanation.build(result, guidance)['sentence']
    assert '2 active policies' in explanation
    assert 'check for payer order' in explanation
    assert 'tertiary order' not in explanation


@pytest.mark.parametrize('name', FIXTURES)
@pytest.mark.parametrize('payer_label,directory_name', [
    ('UnitedHealthcare', None), ('United Health Care', None), ('UHC', None),
    ('AARP Regional PPO', 'UnitedHealthcare'),
])
def test_saved_automatic_cash_fallback_reopens_as_insurance_with_clinic_policy(name, payer_label, directory_name, monkeypatch):
    result, _, _ = replay(name)
    result.update(payer=payer_label, service_date='2026-10-14', payment_mode='cash',
                  cash_fallback={'reason': 'old automatic fallback'})
    if directory_name:
        result['stedi_payer'] = {'name': directory_name}
    case = {'id': 'synthetic', 'revision': 1,
            'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'uhc-route', 'source': {}, 'result': result}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [
        {'stedi_payer_id': 'uhc-route', 'rules': {'collection_policy': POLICY}}])
    reopened = api._previsit_result(case, 'clinic')
    assert reopened['payment_mode'] == 'insurance'
    assert 'cash_fallback' not in reopened
    assert reopened['collection_guidance']['amount_cents'] == 0
    assert reopened['payment_explanation']['sentence'].startswith('Collect $0 today:')
    case['visit_review'] = {'payment_mode': 'cash'}
    selected_cash = api._previsit_result(case, 'clinic')
    assert selected_cash['payment_mode'] == 'cash'


def test_uhc_directory_name_retains_temporary_rate_reference_for_product_alias():
    result = {'payer': 'AARP Regional PPO', 'stedi_payer': {'name': 'UnitedHealthcare'},
              'codes': [{'id': 'office', 'code': '99203', 'units': 1}], 'issues': []}
    with patch.object(api.commercial_store, 'list_connections', return_value=[]), \
         patch.object(api, '_office_service_types', return_value=['98']), \
         patch.object(api.visit_estimate, 'price_lines', return_value={'lines': [], 'complete': False}), \
         patch.object(pricing, '_attach_temporary_uhc_rates') as attach:
        pricing._attach_previsit_pricing(result, 'synthetic-clinic', {'service_date': '2026-10-15'},
                                         {}, 'synthetic-payer-route', {'network_status': 'unknown'})
    attach.assert_called_once()


def test_exact_clinic_uhc_route_retains_insurance_when_saved_name_is_only_product(monkeypatch):
    result, _, _ = replay('regional_ppo')
    result.update(payer='AARP Regional PPO', service_date='2026-10-14', payment_mode='cash',
                  cash_fallback={'reason': 'old automatic fallback'})
    result.pop('stedi_payer', None)
    case = {'id': 'synthetic', 'revision': 1, 'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'uhc-route', 'source': {}, 'result': result}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [
        {'stedi_payer_id': 'uhc-route', 'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
         'rules': {'collection_policy': POLICY}}])
    reopened = api._previsit_result(case, 'clinic')
    assert reopened['payment_mode'] == 'insurance'
    assert 'cash_fallback' not in reopened
    assert reopened['collection_guidance']['amount_cents'] == 0


def test_exact_clinic_uhc_route_keeps_temporary_rate_reference_without_directory_name():
    result = {'payer': 'AARP Regional PPO', 'codes': [{'id': 'office', 'code': '99203', 'units': 1}],
              'issues': []}
    with patch.object(api.commercial_store, 'list_connections', return_value=[
            {'stedi_payer_id': 'uhc-route', 'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
             'status': 'draft'}]), \
         patch.object(api, '_office_service_types', return_value=['98']), \
         patch.object(api.visit_estimate, 'price_lines', return_value={'lines': [], 'complete': False}), \
         patch.object(pricing, '_attach_temporary_uhc_rates') as attach:
        pricing._attach_previsit_pricing(result, 'synthetic-clinic', {'service_date': '2026-10-15'},
                                         {}, 'uhc-route', {'network_status': 'unknown'})
    attach.assert_called_once()


def test_exact_non_uhc_route_does_not_inherit_stale_uhc_name(monkeypatch):
    result, _, _ = replay('regional_ppo')
    result.update(payer='UnitedHealthcare', service_date='2026-10-14', payment_mode='cash',
                  cash_fallback={'reason': 'old automatic fallback'},
                  stedi_payer={'name': 'UnitedHealthcare'})
    case = {'id': 'synthetic', 'revision': 1, 'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'other-route', 'source': {}, 'result': result}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [
        {'stedi_payer_id': 'other-route', 'display_name': 'Example Health', 'rules': {}}])
    reopened = api._previsit_result(case, 'clinic')
    assert reopened['payment_mode'] == 'cash'


def test_product_alias_alone_does_not_identify_united_healthcare():
    assert not api.commercial.is_united_healthcare_payer({'payer': 'AARP Regional PPO'})
    assert api.commercial.is_united_healthcare_payer(
        {'payer': 'AARP Regional PPO'}, {'displayName': 'UnitedHealthcare'})
    assert not api.commercial.is_united_healthcare_payer(
        {'payer': 'UnitedHealthcare', 'stedi_payer': {'name': 'Example Health'}},
        {'displayName': 'Example Health'})


def test_legacy_recovered_result_does_not_reuse_prior_uhc_directory_route(monkeypatch):
    result, _, _ = replay('multiple_policies')
    result.update(payer='Example Health', stedi_payer={'name': 'UnitedHealthcare', 'id': 'old-route'},
                  payment_mode='cash', cash_fallback={'reason': 'old result'})
    case = {'id': 'synthetic', 'revision': 1, 'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'new-route', 'source': {}, 'result': result}}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [])
    reopened = api._previsit_result(case, 'clinic')
    assert reopened.get('stedi_payer') is None
    assert reopened['payment_mode'] == 'cash'
