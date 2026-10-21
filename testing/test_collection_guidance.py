from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from app.insurance import amount_explanation, collection_guidance, commercial, encounter_summary, evidence_resolution, identity, visit_estimate
from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow.commercial_previsit_reuse import saved_benefits_for_review
from test_visit_estimate import DAY, payload as synthetic_271
from testing.synthetic_policy import ADVANCE_POLICY, COPAY_POLICY


def scenario(amount=3800):
    return {'collection_policy': ADVANCE_POLICY,
            'benefits': {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract'},
            'identity': {'status': 'warning', 'binding': 'synthetic-current-binding', 'match_version': 2},
            'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': amount, 'patient_max_cents': amount,
                'lines': [{'id': 'office', 'code': '99203', 'units': 1,
                    'calculation': {'patient_cents': amount}, 'benefits': {'active': True,
                    'requirements': {kind: {'required': 'unknown', 'cleared': False} for kind in ('referral', 'authorization')}}}]}}


def test_patient_share_and_collection_policy_are_independent():
    result = scenario()
    result.pop('collection_policy')
    guidance = collection_guidance.build(result)
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['estimated_total_patient_min_cents'] == 3800
    assert summary['collect_today_cents'] is None
    assert guidance['positive_advance_policy_missing'] is True
    trace = evidence_resolution.build({**result, 'collection_guidance': guidance,
                                       'encounter_summary': summary}, {})
    assert next(row for row in trace if row['field'] == 'patient_share')['status'] == 'resolved'
    assert next(row for row in trace if row['field'] == 'collection_amount')['status'] == 'unresolved'
    result['collection_policy'] = {'mode': 'collect_verified_copay',
        'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01T12:00:00Z',
        'reference': 'Synthetic copay-only policy'}
    assert collection_guidance.build(result)['amount_cents'] is None
    result['collection_policy']['mode'] = 'defer_to_claim'
    deferred = collection_guidance.build(result)
    assert deferred['amount_cents'] == 0
    assert deferred['deferred_supported_share'] is True
    assert encounter_summary.build({**result, 'collection_guidance': deferred})['estimated_total_patient_min_cents'] == 3800


@pytest.mark.parametrize('result', [{}, {'eligibility_failure': {'title': 'Timeout'}},
    {'benefits': {'active': False}}, {'benefits': {'active': True, 'network_status': 'out_of_network'}},
    {'benefits': {'active': True}, 'price_range': {'lines': [], 'mode': 'options'}}])
def test_missing_evidence_requires_action_without_authorizing_deferred_billing(result):
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'action_required'
    assert value['basis'] == 'missing_collection_inputs'
    assert 'estimate is not available yet' in value['patient_message']
    assert 'No insurance advance is collected' not in value['patient_message']


def test_approved_clinic_policy_gives_zero_advance_without_zero_patient_liability():
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'collection_policy': {'mode': 'defer_to_claim', 'reference': 'clinic policy 2026',
                                    'approved_by': 'administrator', 'approved_at': '2026-10-15T12:00:00Z'},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'recovery': {'reason_code': 'multiple_active_policies'},
              'price_range': {'complete': False, 'lines': [{'code': '99203', 'calculation': None,
                              'benefits': {'copay_cents': 0}}]}}
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0
    assert value['status'] == 'zero_advance' and value['basis'] == 'claim_after_service'
    assert value['services'][0]['patient_cents'] is None
    assert 'Submit the insurance claim' in value['patient_message']
    assert collection_guidance.build({**result, 'collection_policy': {}})['amount_cents'] is None


def test_unresolved_coverage_explains_the_actual_recovery_step():
    result = {'benefits': {}, 'price_range': {'lines': []},
              'eligibility_failure': {'kind': 'coverage_identity_missing', 'detail': 'eCW lacks policyholder details.'},
              'recovery': {'outcome': 'review_needed'}}
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'policy selection' in value['reason']
    result.pop('recovery')
    assert 'eCW lacks policyholder details.' in collection_guidance.build(result)['reason']


def test_secondary_coverage_retains_provider_enrollment_blocker():
    result = {'benefits': {'has_secondary': True}, 'eligibility_failure': {'kind': 'provider_enrollment'}}
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'coordinate benefits' in value['reason']
    assert 'provider enrollment' in value['reason']


def test_failed_enrollment_lookup_does_not_claim_missing_enrollment():
    result = {'payer': 'United Healthcare', 'benefits': {}, 'price_range': {'lines': []},
              'eligibility_failure': {'kind': 'provider_enrollment',
                                      'reason_code': 'enrollment_lookup_failed',
                                      'detail': 'The provider enrollment status could not be read. Retry the enrollment lookup before eligibility.'}}
    guidance = collection_guidance.build(result)
    assert 'could not read' in guidance['reason']
    assert 'not verified' not in guidance['reason']
    explanation = amount_explanation.build(result, guidance)
    assert 'could not read' in explanation['sentence']
    assert 'retry the Stedi enrollment lookup' in explanation['sentence']
    assert 'not enrolled' not in explanation['sentence']
    assert 'complete the required Stedi provider enrollment' not in explanation['sentence']


@pytest.mark.parametrize('reason_code,cause,action', [
    ('enrollment_missing', 'returned no eligibility enrollment', 'enroll this provider NPI'),
    ('enrollment_rejected', 'enrollment was rejected', 'review the rejection'),
    ('enrollment_pending', 'enrollment is pending', 'check the enrollment status'),
])
def test_enrollment_status_gives_its_own_action(reason_code, cause, action):
    result = {'payer': 'United Healthcare', 'benefits': {}, 'price_range': {'lines': []},
              'eligibility_failure': {'kind': 'provider_enrollment', 'reason_code': reason_code}}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert cause in explanation['sentence']
    assert action in explanation['sentence']
    assert 'not enrolled' not in explanation['sentence']


def test_family_only_accumulators_do_not_become_individual_collection():
    result = scenario()
    result['price_range']['complete'] = False
    result['benefits']['review_reasons'] = ['Confirm the remaining individual deductible that applies to this visit.']
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'individual deductible' in value['reason']


def test_case_specific_amount_changes_and_payments_are_deducted_once():
    result = scenario()
    assert collection_guidance.build(result)['amount_cents'] == 3800
    assert collection_guidance.build(scenario(9123))['amount_cents'] == 9123
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000
    result['arrival_collection'] = {'amount_cents': 3000, 'payments_received_cents': 800}
    assert collection_guidance.build(result, {'payments': {'reference': 'receipt', 'amount_cents': 800}})['amount_cents'] == 3000


def test_unreadable_saved_payer_response_retains_terms_without_reusing_positive_collection():
    result = scenario()
    result['identity'] = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    result['benefit_source'] = 'Stedi'
    result['benefits'] = saved_benefits_for_review(result['benefits'])
    assert result['benefits']['active'] is True
    assert result['benefits']['source_errors'][0]['kind'] == 'saved_raw_unavailable'
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert 'protected saved payer response could not be reopened' in guidance['reason']
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'saved_payer_response_unavailable'
    assert 'rerun the payer check before collecting' in explanation['sentence']
    diagnosis = api._insurance_diagnosis({'previsit_context': {'source': {}}}, result, guidance)
    assert diagnosis['eligibilityStatus'] == 'PAYER_BENEFITS_RETURNED'
    assert 'SAVED_PAYER_RESPONSE_UNAVAILABLE' in diagnosis['reasonCodes']
    marker_only = deepcopy(result)
    marker_only['benefits']['source_errors'] = []
    marker_only['benefits']['review_reasons'] = []
    assert collection_guidance.build(marker_only)['amount_cents'] is None
    assert encounter_summary.build(marker_only)['estimated_total_patient_min_cents'] is None
    result['arrival_collection'] = {'amount_cents': 3800}
    assert collection_guidance.build(result)['amount_cents'] is None


def test_saved_collection_mismatch_is_not_returned_as_api_amount():
    result = scenario(2000)
    result['arrival_collection'] = {'amount_cents': 2500, 'payments_received_cents': 500}
    guidance = collection_guidance.build(result, {'payments': {'reference': 'synthetic-receipt',
                                                                'amount_cents': 500}})
    assert guidance['amount_cents'] is None
    assert guidance['basis'] == 'calculation_conflict'
    assert 'recorded payment' in guidance['reason']
    assert guidance['services'][0]['patient_cents'] is None
    summary = encounter_summary.build({**result, 'collection_guidance': guidance},
        {'payments': {'reference': 'synthetic-receipt', 'amount_cents': 500}})
    assert summary['collect_today_cents'] is None
    assert summary['integrity_error'] == 'collection_amount_mismatch'
    assert summary['estimated_total_patient_min_cents'] == 2000
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'calculation_conflict'
    assert 'recorded payment' in explanation['sentence']

    result.pop('arrival_collection')
    result['price_range']['patient_min_cents'] = 3000
    result['price_range']['patient_max_cents'] = 3000
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert guidance['integrity_error'] == 'patient_total_mismatch'
    assert 'selected CPT calculations' in guidance['reason']


def test_incorrect_saved_coinsurance_formula_withholds_the_api_amount():
    result = scenario(3000)
    result['price_range']['lines'][0]['calculation'].update(
        copay_cents=0, deductible_cents=0, coinsurance_cents=3000,
        coinsurance_base_cents=10000, coinsurance_percent='20', oop_adjustment_cents=0)
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert guidance['status'] == 'action_required'
    assert guidance['basis'] == 'calculation_conflict'
    assert guidance['integrity_error'] == 'line_amount_mismatch'
    assert guidance['services'][0]['patient_cents'] is None


def test_missing_payer_identity_decision_cannot_reuse_a_positive_saved_amount():
    result = scenario()
    result.pop('identity')
    result['arrival_collection'] = {'amount_cents': 3800}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] is None
    assert summary['estimated_total_patient_min_cents'] is None
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == 'payer_identity_match_missing'
    assert 'compare the saved payer patient, subscriber and member ID with eCW' in explanation['sentence']


@pytest.mark.parametrize('status', ['matched', 'warning', 'reconciled'])
def test_legacy_name_decision_cannot_bind_member_money(status):
    result = scenario()
    result['identity'] = {'status': status, 'binding': 'legacy-binding'}
    assert collection_guidance.build(result)['amount_cents'] is None
    assert collection_guidance.build({**result, 'identity': {'status': status}})['amount_cents'] is None
    assert collection_guidance.build({**result, 'identity': {
        'status': status, 'binding': 'current-binding', 'match_version': 2}})['amount_cents'] == 3800


def test_only_current_ecw_patient_coverage_reference_can_match_without_hash():
    assert identity.patient_benefits_bound({'status': 'matched', 'match_version': 2,
        'binding': None, 'reconciliation_rule': 'ecw_patient_coverage_reference'})
    assert not identity.patient_benefits_bound({'status': 'matched', 'match_version': 2,
        'binding': None, 'reconciliation_rule': 'unknown'})


def test_legacy_weak_matched_decision_cannot_restore_saved_amount():
    result = scenario()
    result['identity'] = {'status': 'matched', 'binding': 'legacy-binding'}
    result['arrival_collection'] = {'amount_cents': 3800}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert encounter_summary.build({**result, 'collection_guidance': guidance})['collect_today_cents'] is None
    result['identity']['match_version'] = 2
    assert collection_guidance.build(result)['amount_cents'] == 3800


def test_conditional_calculation_is_for_planning_and_does_not_authorize_collection():
    result = scenario()
    result['price_range']['conditional'] = True
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    assert 'unresolved' in value['reason']


def test_conditional_total_can_preserve_independent_fixed_office_copay():
    result = scenario(9000)
    result['price_range']['conditional'] = True
    result['price_range']['lines'][0]['benefits'].update(
        copay_cents=3000, copay_period='DAY', deductible_applies=False,
        oop_remaining_cents=100000, coinsurance_percent=None)
    value = collection_guidance.build(result)
    assert value['basis'] == 'office_copay'
    assert value['amount_cents'] == 3000


def test_supported_copay_advance_does_not_promise_later_member_billing():
    result = scenario(3000)
    result['arrival_collection'] = {'amount_cents': 3000, 'status': 'copay_only'}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 3000
    assert 'member protections' in guidance['reason']
    assert 'will be billed' not in guidance['reason']


@pytest.mark.parametrize('change', [{'active': False}, {'has_secondary': True}, {'service_date_matches': False},
                                  {'network_status': 'out_of_network'}, {'review_reasons': ['Eligibility evidence conflicts']}])
def test_contradictions_override_cached_numeric_amount(change):
    result = scenario()
    result['arrival_collection'] = {'amount_cents': 3800}
    result['benefits'].update(change)
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'action_required'


@pytest.mark.parametrize('kind', ['referral', 'authorization'])
@pytest.mark.parametrize('requirement', [{'required': 'required', 'cleared': False}, {'status': 'denied'}, {'status': 'expired'}, {'status': 'pending'}])
def test_required_or_adverse_approval_does_not_become_an_assumed_approval(kind, requirement):
    result = scenario()
    result['price_range']['lines'][0]['benefits']['requirements'][kind] = requirement
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['status'] == 'action_required'
    assert value['patient_actions']


@pytest.mark.parametrize('kind,reason_code,action', [
    ('referral', 'referral_required', 'obtain the referring clinician’s referral for CPT 99203'),
    ('authorization', 'authorization_required', 'obtain payer authorization approval and record its reference for CPT 99203'),
])
def test_documented_cpt_requirement_blocks_collection_until_cleared(kind, reason_code, action):
    result = scenario()
    result['payer'] = 'United Healthcare'
    result['requirement_evidence'] = {kind: {'status': 'Required', 'approval_status': 'Not verified'}}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert guidance['status'] == 'action_required'
    assert explanation['reason_code'] == reason_code
    assert action in explanation['sentence']


def test_documented_approved_authorization_keeps_supported_collection():
    result = scenario()
    result['requirement_evidence'] = {'authorization': {'status': 'Required', 'approval_status': 'Verified'}}
    result['price_range']['lines'][0]['benefits']['requirements']['authorization'] = {
        'required': 'required', 'status': 'valid', 'cleared': True,
        'reference': 'synthetic-approval', 'verified_at': '2026-10-15T12:00:00Z'}
    assert collection_guidance.build(result)['amount_cents'] == 3800


def test_cleared_referral_for_one_cpt_does_not_block_an_unrelated_cpt():
    result = scenario()
    result['service_plan'] = {'selected_codes': ['99203', '93000']}
    result['price_range']['lines'].append({'id': 'ecg', 'code': '93000', 'units': 1,
                                           'calculation': {'patient_cents': 0},
                                           'benefits': {'requirements': {'referral': {'required': 'not_required', 'cleared': True}}}})
    result['requirement_evidence'] = {
        'referral': {'status': 'Required'},
        'procedures': [{'code': '99203', 'referral': {'status': 'Required'}},
                       {'code': '93000', 'referral': {'status': 'Not required'}}]}
    office = result['price_range']['lines'][0]['benefits']['requirements']['referral']
    office.update(required='required', status='valid', cleared=True,
                  reference='synthetic-referral', verified_at='2026-10-15T12:00:00Z')
    assert collection_guidance.build(result)['amount_cents'] == 3800
    office['cleared'] = False
    assert collection_guidance.build(result)['amount_cents'] is None


def test_uncorroborated_authorization_clearance_cannot_override_unverified_approval():
    result = scenario()
    result['requirement_evidence'] = {'authorization': {'status': 'Required', 'approval_status': 'Not verified'}}
    result['price_range']['lines'][0]['benefits']['requirements']['authorization'] = {
        'required': 'required', 'status': 'valid', 'cleared': True}
    assert collection_guidance.build(result)['amount_cents'] is None


def test_saved_verified_authorization_needs_the_cpt_approval_reference_and_time():
    result = scenario()
    result['requirement_evidence'] = {'authorization': {'status': 'Required', 'approval_status': 'Verified'}}
    approval = result['price_range']['lines'][0]['benefits']['requirements']['authorization']
    approval.update(required='required', status='valid', cleared=True)
    assert collection_guidance.build(result)['amount_cents'] is None
    approval['reference'] = 'synthetic-approval'
    assert collection_guidance.build(result)['amount_cents'] is None
    approval['verified_at'] = '2026-10-15T12:00:00Z'
    assert collection_guidance.build(result)['amount_cents'] == 3800


def test_conflicting_cpt_approval_status_cannot_restore_saved_advance():
    result = scenario()
    result['payer'] = 'United Healthcare'
    result['requirement_evidence'] = {
        'authorization': {'status': 'Required', 'approval_status': 'Verified'},
        'procedures': [{'id': result['price_range']['lines'][0]['id'], 'code': '99203',
                        'authorization': {'status': 'Required', 'approval_status': 'Not verified'}}],
    }
    approval = result['price_range']['lines'][0]['benefits']['requirements']['authorization']
    approval.update(required='required', status='valid', cleared=True,
                    reference='synthetic-approval', verified_at='2026-10-15T12:00:00Z')
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert explanation['reason_code'] == 'authorization_required'
    assert 'CPT 99203' in explanation['sentence']
    assert 'approval status' in explanation['sentence']


def test_saved_cleared_referral_needs_its_cpt_reference_and_time():
    result = scenario()
    result['requirement_evidence'] = {'referral': {'status': 'Required'}}
    referral = result['price_range']['lines'][0]['benefits']['requirements']['referral']
    referral.update(required='required', status='valid', cleared=True)
    assert collection_guidance.build(result)['amount_cents'] is None
    referral.update(reference='synthetic-referral', verified_at='2026-10-15T12:00:00Z')
    assert collection_guidance.build(result)['amount_cents'] == 3800


@pytest.mark.parametrize('kind', ['referral', 'authorization'])
def test_cpt_requirement_without_sourced_clearance_blocks_even_when_visit_summary_omits_it(kind):
    result = scenario()
    result['payer'] = 'United Healthcare'
    result['service_plan'] = {'selected_codes': ['99203', '93000']}
    result['price_range']['lines'].append({'id': 'ecg', 'code': '93000', 'units': 1,
                                           'calculation': {'patient_cents': 0}, 'benefits': {}})
    result['requirement_evidence'] = {}
    requirement = result['price_range']['lines'][0]['benefits']['requirements'][kind]
    requirement.update(required='required', status='valid', cleared=True)
    for missing in ('reference', 'verified_at'):
        requirement.update(reference='synthetic-reference', verified_at='2026-10-15T12:00:00Z')
        requirement.pop(missing)
        guidance = collection_guidance.build(result)
        explanation = amount_explanation.build(result, guidance)
        assert guidance['amount_cents'] is None
        assert guidance['status'] == 'action_required'
        assert explanation['reason_code'] == f'{kind}_required'
        assert 'CPT 99203' in explanation['sentence']
        assert 'CPT 93000' not in explanation['sentence']
        assert 'lacks a valid reference or verification time' in explanation['sentence']
        assert 'record its reference and verification time' in explanation['sentence']


@pytest.mark.parametrize('kind', ['referral', 'authorization'])
def test_conflicting_visit_and_cpt_requirement_does_not_show_positive_amount(kind):
    result = scenario()
    result['payer'] = 'United Healthcare'
    result['requirement_evidence'] = {kind: {'status': 'Not required'}}
    result['price_range']['lines'][0]['benefits']['requirements'][kind] = {
        'required': 'required', 'status': 'valid', 'cleared': True,
        'reference': 'synthetic-reference', 'verified_at': '2026-10-15T12:00:00Z'}
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert guidance['requirements'][kind] == 'Conflicting CPT rule · clinic action in amount reason'
    assert not guidance['patient_actions']
    assert explanation['reason_code'] == f'{kind}_required'
    assert 'conflicts with the CPT-level' in explanation['sentence']
    assert 'CPT 99203' in explanation['sentence']
    assert 'ask United Healthcare which' in explanation['sentence']


def test_selected_specialist_visit_with_payer_referral_signal_waits_for_referral():
    result = scenario()
    result['benefits']['referral_signal'] = 'required'
    guidance = collection_guidance.build(result)
    explanation = amount_explanation.build(result, guidance)
    assert guidance['amount_cents'] is None
    assert explanation['reason_code'] == 'referral_required'
    assert 'CPT 99203' in explanation['sentence']


def test_documented_authorization_action_names_only_the_affected_selected_cpt():
    result = scenario()
    result['service_plan'] = {'selected_codes': ['99203', '93000']}
    result['price_range']['lines'].append({'id': 'test', 'code': '93000', 'units': 1,
                                           'calculation': {'patient_cents': 0}, 'benefits': {}})
    result['requirement_evidence'] = {
        'authorization': {'status': 'Required', 'approval_status': 'Not verified'},
        'procedures': [{'code': '99203', 'authorization': {'status': 'Not required'}},
                       {'code': '93000', 'authorization': {'status': 'Required'}}]}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'authorization_required'
    assert 'approval and record its reference for CPT 93000' in explanation['sentence']
    assert 'CPT 99203' not in explanation['sentence']


def test_both_documented_requirements_name_both_actions():
    result = scenario()
    result['requirement_evidence'] = {kind: {'status': 'Required', 'approval_status': 'Not verified'}
                                      for kind in ('referral', 'authorization')}
    explanation = amount_explanation.build(result, collection_guidance.build(result))
    assert explanation['reason_code'] == 'referral_authorization_required'
    assert 'obtain the referral and payer authorization approval for CPT 99203' in explanation['sentence']


def test_missing_network_defers_positive_amount_even_with_a_pricing_assumption():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    assert collection_guidance.build(result)['status'] == 'action_required'
    result['pricing_assumptions'] = ['Exact product participation is assumed for the scoped provider contract.']
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None and value['network'] == 'Not reported'
    result['benefits']['network_status'] = 'in_network'  # Synthetic product/provider participation.
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 3800
    assert value['requirements']['authorization'] == 'No requirement reported'


def test_saved_arrival_amount_cannot_bypass_current_network_evidence():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    result['price_range']['complete'] = False
    result['arrival_collection'] = {'amount_cents': 3800, 'status': 'estimate'}
    assert collection_guidance.build(result)['amount_cents'] is None


def test_uhc_in_network_label_without_participation_source_cannot_authorize_collection():
    result = scenario()
    result['payer'] = 'United Healthcare'
    result['benefits'].pop('network_source')
    result['arrival_collection'] = {'amount_cents': 3800, 'status': 'estimate'}
    unsupported = collection_guidance.build(result)
    assert unsupported['amount_cents'] is None
    assert unsupported['network'] == 'Not reported'
    result['benefits']['network_source'] = 'clinic_library_contract'
    supported = collection_guidance.build(result)
    assert supported['amount_cents'] == 3800
    assert supported['network'] == 'In network'


def test_discovered_multiple_policies_block_collection_even_without_271_secondary_flag():
    result = scenario()
    result['benefits']['has_secondary'] = False
    result['recovery'] = {'reason_code': 'multiple_active_policies'}
    assert collection_guidance.build(result)['amount_cents'] is None


def test_oop_zero_is_case_evidence_not_a_universal_free_care_default():
    result = scenario(0)
    result['benefits']['network_status'] = 'unknown'
    result['price_range']['complete'] = False
    result['planning_estimate'] = {'basis': 'out_of_pocket_limit', 'amount_cents': 0, 'assumptions': ['Covered in-network services.']}
    value = collection_guidance.build(result)
    assert value['amount_cents'] is None
    result['benefits']['network_status'] = 'in_network'
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 0 and value['basis'] == 'out_of_pocket_limit'
    assert value['services'][0]['patient_cents'] == 0
    result['identity']['status'] = 'review'
    assert collection_guidance.build(result)['status'] == 'action_required'


def test_new_patient_alternatives_collect_the_lowest_estimate_and_explain_final_complexity():
    result = scenario()
    result['price_range'].update(mode='alternatives', patient_max_cents=12000)
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 3800
    assert 'Final complexity' in ' '.join(value['assumptions'])


def office_payload():
    base = {'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
            'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'DAY', 'priorAuthIndicator': 'U'}
    return {'plans': [{'benefits': {'statuses': [{'status': 'Active'}], 'coPayment': [
        {**base, 'amount': '40', 'placesOfService': [{'code': '11'}]},
        {**base, 'amount': '20', 'placesOfService': [{'code': '10'}]}]}}]}


def test_office_and_telehealth_benefits_are_scoped_to_actual_visit_location():
    payload = office_payload()
    args = {'procedure_code': '99203', 'service_date': '2026-09-29'}
    office = commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)
    remote = commercial.normalize_stedi_271(payload, '98', True, place_of_service='10', **args)
    assert office['copay_cents'] == 4000 and remote['copay_cents'] == 2000
    assert office['selected_benefit_ids'] == ['plans[0].benefits.coPayment[0]']
    assert commercial.normalize_stedi_271(payload, '98', True, **args)['copay_cents'] is None
    assert office['authorization_signal'] == 'unknown'  # Explicit U is not an absent indicator.
    del payload['plans'][0]['benefits']['coPayment'][0]['priorAuthIndicator']
    absent = commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)
    assert absent['authorization_signal'] == 'not_required'
    payload['plans'][0]['benefits']['coverageBasis'] = [{'service': {'system': 'CPT', 'value': '99203'}, 'priorAuthIndicator': 'REQUIRED'}]
    assert commercial.normalize_stedi_271(payload, '98', True, place_of_service='11', **args)['authorization_signal'] == 'required'


def test_product_mismatch_never_falls_back_to_another_contract():
    mismatch = HTTPException(409, 'The clinic fee schedule is not matched to the exact payer product.')
    with patch.object(api, '_practice_path', side_effect=mismatch) as path:
        with pytest.raises(HTTPException, match='exact payer product'):
            api._previsit_practice_path('clinic', {'place_of_service': '11'}, {'payer_plan_names': ['Product']})
        assert path.call_count == 1
        assert path.call_args.args[1] == {'place_of_service': '11'}
        assert path.call_args.args[2]['payer_plan_names'] == ['Product']
    with patch.object(api, '_practice_path', side_effect=HTTPException(409, 'Provider record missing')) as path:
        with pytest.raises(HTTPException):
            api._previsit_practice_path('clinic', {}, {})
        assert path.call_count == 1


def test_standalone_office_copay_is_one_per_visit_and_requires_unambiguous_terms():
    result = scenario()
    result['price_range'].update(complete=False, mode='alternatives')
    line = result['price_range']['lines'][0]
    line['benefits'].update(copay_cents=4700, copay_period='DAY', deductible_applies=False, oop_remaining_cents=100000)
    result['price_range']['lines'] = [{**deepcopy(line), 'code': str(code)} for code in range(99202, 99206)]
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 4700 and value['basis'] == 'office_copay'
    result['price_range']['lines'][0]['benefits']['copay_cents'] = 6200
    assert collection_guidance.build(result)['status'] == 'action_required'


@pytest.mark.parametrize('limited_index', [0, 3])
@pytest.mark.parametrize('limited_field', ['oop_remaining_cents', 'allowed_cents'])
def test_unselected_office_alternatives_need_the_same_payable_copay(limited_index, limited_field):
    result = scenario()
    result['price_range'].update(complete=False, mode='alternatives')
    line = result['price_range']['lines'][0]
    line['benefits'].update(copay_cents=4700, copay_period='DAY', deductible_applies=False,
                            oop_remaining_cents=100000)
    result['price_range']['lines'] = [{**deepcopy(line), 'code': str(code)} for code in range(99202, 99206)]
    target = (result['price_range']['lines'][limited_index]['benefits'] if limited_field == 'oop_remaining_cents'
              else result['price_range']['lines'][limited_index])
    target[limited_field] = 2000
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert guidance['basis'] == 'missing_collection_inputs'
    assert amount_explanation.build(result, guidance)['reason_code'] == 'planned_cpt_unselected'


def test_conflicting_annual_oop_maximum_does_not_erase_independent_office_copay():
    raw = synthetic_271()
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='47', timeQualifierCode='27')
    annual = {'code': 'G', 'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y',
              'serviceTypeCodes': ['30'], 'timeQualifierCode': '23'}
    raw['benefitsInformation'].extend({**annual, 'benefitAmount': str(value)} for value in (1000, 1200))
    benefits = commercial.normalize_stedi_271(raw, '98', True, service_date=DAY, procedure_code='99203')
    benefits['network_source'] = 'clinic_library_contract'
    identity = {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}
    benefits['identity'] = identity
    codes = [{'id': 'office', 'code': '99203', 'units': 1}]
    price = visit_estimate.price_lines(codes, [], benefits, {'cost_share': 'payer_returned'},
                                       payload=raw, service_date=DAY)
    result = {'benefits': benefits, 'identity': identity, 'price_range': price,
              'codes': codes, 'payment_mode': 'insurance', 'collection_policy': COPAY_POLICY}
    assert benefits['oop_annual_cents'] is None and benefits['oop_annual_conflict_ids']
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 4700
    assert guidance['basis'] == 'office_copay'
    result['benefits'].pop('oop_remaining_cents')
    assert collection_guidance.build(result)['amount_cents'] is None


@pytest.mark.parametrize('field', ['coverage_status_conflict', 'coverage_dates_invalid'])
@pytest.mark.parametrize('scope', ['plan', 'cpt'])
def test_structured_coverage_conflict_blocks_a_cached_insured_amount(field, scope):
    result = scenario()
    target = result['benefits'] if scope == 'plan' else result['price_range']['lines'][0]['benefits']
    target[field] = True
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    explanation = amount_explanation.build(result, guidance)
    assert explanation['reason_code'] == ('coverage_status_conflict' if field == 'coverage_status_conflict'
                                          else 'coverage_date_invalid')
    assert 'rerun the insured estimate' in explanation['sentence']
    if scope == 'cpt':
        assert 'CPT 99203' in guidance['reason']
        assert 'CPT 99203' in explanation['sentence']


@pytest.mark.parametrize('line_change', [
    {'active': False}, {'service_date_matches': False}, {'network_conflict': True},
    {'has_secondary': True}, {'historical_only': True},
    {'identity': {'status': 'review'}},
    {'identity': {'status': 'matched', 'binding': 'another-member', 'match_version': 2}},
    {'network_status': 'out_of_network'},
])
def test_explicit_selected_cpt_benefit_conflict_blocks_saved_advance(line_change):
    result = scenario()
    result['price_range']['lines'][0]['benefits'].update(line_change)
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert 'CPT 99203' in guidance['reason']
    explanation = amount_explanation.build(result, guidance)
    assert 'CPT 99203' in explanation['sentence']
    assert 'so ' in explanation['sentence'] and ';' not in explanation['sentence']


def test_unknown_network_does_not_collect_a_tier_specific_office_copay():
    terms = {'active': True, 'copay_cents': 3000, 'copay_period': 'VISIT',
             'deductible_applies': False, 'coinsurance_percent': None,
             'network_independent': False}
    result = {'payment_mode': 'insurance', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'network_status': 'unknown'},
              'collection_policy': COPAY_POLICY,
              'price_range': {'complete': False, 'mode': 'services', 'lines': [
                  {'code': '99203', 'units': 1, 'benefits': terms, 'calculation': None}]}}
    assert collection_guidance.build(result)['amount_cents'] is None
    terms['network_independent'] = True
    assert collection_guidance.build(result)['amount_cents'] == 3000


def test_unmapped_service_cannot_inherit_office_zero_cost_benefit():
    payload = office_payload()
    payload['plans'][0]['benefits']['outOfPocket'] = [{'service': {'system': 'STC', 'value': '30'},
        'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'REMAINING', 'amount': '0'}]
    benefits = commercial.normalize_stedi_271(payload, '98', True)
    priced = visit_estimate.price_lines([{'id': 'unknown', 'code': '99999', 'units': 1}], [], benefits,
        {'place_of_service': '11'}, payload=payload, service_date='2026-09-29')
    assert 'needs mapping' in priced['lines'][0]['benefit_basis']
    assert visit_estimate.planning_estimate(priced, benefits, {}) is None
    value = collection_guidance.build({'benefits': benefits, 'price_range': priced})
    assert value['services'][0]['copay_cents'] is None
    assert value['services'][0]['selected_benefit_ids'] == []


def test_plain_office_description_is_not_a_visit_count_restriction():
    payload = office_payload()
    row = payload['plans'][0]['benefits']['coPayment'][0]
    row['messages'] = ['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL;OFFICE VISIT - PROFESSIONAL']
    def normalized():
        return commercial.normalize_stedi_271(payload, '98', True, procedure_code='99203', place_of_service='11')
    assert not any('conditional benefit' in r for r in normalized()['review_reasons'])
    row['messages'] = ['OFFICE VISIT - PROFESSIONAL;FIRST 10 VISITS ONLY']
    assert any('conditional benefit' in r for r in normalized()['review_reasons'])


def office_coinsurance_payload():
    common = {'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
              'network': {'indicator': 'IN_NETWORK'}}
    return {'subscriber': {'dates': {'service': {'start': '2026-10-07'}}}, 'plans': [{'benefits': {
        'statuses': [{'status': 'Active Coverage'}],
        'coPayment': [{**common, 'amount': '0', 'messages': ['Specialist Visit or Evaluation', 'Walk In Clinic Visit']}],
        'coInsurance': [{**common, 'percent': '0.20', 'messages': ['All Other In-Network Providers',
            'Specialist Visit or Evaluation,COINS APPLIES TO OUT OF POCKET',
            'Walk In Clinic Visit,COINS APPLIES TO OUT OF POCKET',
            'Telemedicine General Medicine Visit,COINS APPLIES TO OUT OF POCKET',
            'Telemedicine Specialist Visit,COINS APPLIES TO OUT OF POCKET']}],
        'deductible': [{**common, 'amount': '0', 'timePeriod': 'REMAINING'}],
        'outOfPocket': [{**common, 'amount': '1500', 'timePeriod': 'REMAINING'}],
        'limitations': [{**common, 'messages': [
            "Our records indicate the provider ID you entered is participating in this patient's network."]}]}}]}


@pytest.mark.parametrize(('copay', 'coinsurance', 'fee', 'deductible', 'expected'), [
    ('0', '0.20', 25000, '0', None), ('0', '0.20', 17321, '0', None),
    ('0', '0.20', 17321, '20', None), ('0', '0', 25000, '0', 0),
    ('50', '0', 25000, '0', 5000), ('60', '0', 25000, '0', 6000)])
def test_office_collection_uses_actual_cost_share_and_active_schedule(copay, coinsurance, fee, deductible, expected):
    payload = office_coinsurance_payload()
    terms = payload['plans'][0]['benefits']
    terms['coPayment'][0]['amount'] = copay
    terms['coInsurance'][0]['percent'] = coinsurance
    terms['deductible'][0]['amount'] = deductible
    benefits = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07',
                                              provider_npi='1234567890')
    result = {'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': benefits, 'issues': [],
              'codes': [{'id': 'office', 'code': '99203', 'units': 1}], 'collection_policy': COPAY_POLICY}
    with patch.object(api.commercial_store, 'list_connections', return_value=[]):
        api._attach_previsit_pricing(result, 'synthetic-clinic', {'service_date': '2026-10-07', 'place_of_service': '11'},
            {'rendering_provider': {'npi': '1234567890', 'first_name': 'Synthetic',
                                    'last_name': 'Clinician'}}, 'synthetic-payer', benefits, payload=payload)
    result['available_codes'] = result['codes']
    api.practice_prices.apply(result, {'id': 'active-version', 'filename': 'approved.csv', 'effective_from': '2026-01-01',
        'rows': [{'code': '99203', 'cash_cents': fee}]})
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == expected
    assert guidance['network_source'] == 'Payer statement about the requesting provider'
    assert all(line['allowed_cents'] is None for line in result['price_range']['lines'])
    assert result['price_range']['lines'][0]['benefits']['network_source'] == 'payer_participation_message'
    if coinsurance == '0.20':
        assert guidance['basis'] == 'missing_collection_inputs'
        assert guidance.get('range_max_cents') is None


@pytest.mark.parametrize('change', ['absent', 'generic', 'negated', 'contradictory', 'out_of_network_conflict', 'date', 'inactive', 'conditional'])
def test_payer_participation_and_cost_share_guards_remain(change):
    payload = office_coinsurance_payload()
    terms = payload['plans'][0]['benefits']
    if change == 'absent': terms['limitations'] = []
    if change == 'generic': terms['limitations'][0]['messages'] = ['In-network specialist office benefit.']
    if change == 'negated': terms['limitations'][0]['messages'] = ["Our records indicate the provider ID you entered is not participating in this patient's network."]
    if change == 'contradictory': terms['limitations'][0]['messages'].append('The provider is out-of-network.')
    if change == 'out_of_network_conflict':
        terms['limitations'].append({**terms['limitations'][0], 'network': {'indicator': 'OUT_OF_NETWORK'},
            'messages': ["Our records indicate the provider ID you entered is not participating in this patient's network."]})
    if change == 'date': payload['subscriber']['dates']['service']['start'] = '2026-10-06'
    if change == 'inactive': terms['statuses'][0]['status'] = 'Inactive Coverage'
    if change == 'conditional': terms['coInsurance'][0]['messages'].append('FIRST 10 VISITS ONLY')
    benefits = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07',
                                               procedure_code='99203', provider_npi='1234567890')
    if change == 'conditional':
        assert any('conditional benefit' in reason for reason in benefits['review_reasons'])
    elif change == 'negated':
        assert benefits['network_status'] == 'out_of_network'
        assert benefits['network_evidence_ids']
    else:
        assert benefits['network_status'] == 'unknown'
        assert benefits['network_evidence_ids'] == []
    if change in {'contradictory', 'out_of_network_conflict'}:
        assert benefits['network_conflict'] is True


@pytest.mark.parametrize('participating', [True, False])
def test_provider_message_needs_the_rendering_npi_bound_to_the_payer_request(participating):
    payload = office_coinsurance_payload()
    if not participating:
        payload['plans'][0]['benefits']['limitations'][0]['messages'] = [
            "Our records indicate the provider ID you entered is not participating in this patient's network."]
    missing = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07')
    assert missing['network_status'] == 'unknown'
    assert missing['network_evidence_ids'] == []
    assert missing['network_provider_npi'] is None
    payload['provider'] = {'npi': '9999999999'}
    wrong = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07',
                                            provider_npi='1234567890')
    assert wrong['network_status'] == 'unknown'
    assert wrong['network_evidence_ids'] == []
    assert wrong['network_provider_npi'] is None
    payload['provider']['npi'] = '1234567890'
    matched = commercial.normalize_stedi_271(payload, '98', False, service_date='2026-10-07',
                                              provider_npi='1234567890')
    assert matched['network_status'] == ('in_network' if participating else 'out_of_network')
    assert matched['network_source'] == 'payer_participation_message'
    assert matched['network_evidence_ids']
    assert matched['network_provider_npi'] == '1234567890'


def test_legacy_collection_policy_cannot_supply_a_zero_without_benefits():
    result = {'collection_policy': 'defer_original_medicare_secondary', 'benefits': {'has_secondary': True},
              'eligibility_failure': {'kind': 'provider_enrollment'}}
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] is None
    assert 'provider enrollment' in guidance['reason']


def test_ppo_label_does_not_invent_a_member_referral_requirement():
    result = scenario()
    result['plan_details'] = {'stedi': {'names': ['PREFERRED PROVIDER OPTION MEDICAL']}}
    assert collection_guidance.build(result)['requirements']['referral'] == 'No requirement reported'
    result['price_range']['lines'][0]['benefits']['requirements']['referral']['required'] = 'required'
    assert collection_guidance.build(result)['status'] == 'action_required'


def test_source_statuses_are_not_replaced_with_calculation_assumptions():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    result['pricing_assumptions'] = ['Use the scoped clinic contract for this estimate.']
    result['network_evidence'] = {'network_name': 'Blue Choice PPO', 'product_assumed': True}
    result['price_range']['lines'][0]['benefits']['authorization_basis'] = 'Payer indicator'
    value = collection_guidance.build(result)
    assert value['network'] == 'Network not established'
    assert value['network_source'] == 'Participation for this product and rendering provider is not established by the Blue Choice PPO agreement'
    assert value['requirements']['authorization'] == 'Unknown in payer response'
    assert value['requirements']['referral'] == 'No requirement reported'
    assert 'assumed' not in ' '.join([value['network'], *value['requirements'].values()])
    result['referral_evidence'] = [{'reference': 'ServiceRequest/example'}]
    assert collection_guidance.build(result)['requirements']['referral'] == 'Referral record on file'


def test_unmatched_patient_product_displays_scoped_contract_candidate_without_network_claim():
    result = scenario()
    result['benefits']['network_status'] = 'unknown'
    result['mapping_candidate'] = {'network': 'Blue Choice PPO', 'products': ['Preferred Provider Option Plus Medical']}
    value = collection_guidance.build(result)
    assert value['network'] == 'Network not established'
    assert value['network_source'] == 'No exact product and rendering-provider link to the Blue Choice PPO agreement'


def test_out_of_network_benefit_row_does_not_establish_provider_tier():
    result = scenario()
    result['benefits'].update(network_status='out_of_network', network_source=None)
    terms_only = collection_guidance.build(result)
    assert terms_only['network'] == 'Not reported'
    assert terms_only['network_source'] == 'The plan returned out-of-network benefit terms without establishing this provider tier'
    result['benefits']['network_source'] = 'payer_participation_message'
    provider_specific = collection_guidance.build(result)
    assert provider_specific['network'] == 'Out of network'
    assert provider_specific['network_source'] == 'Payer statement about the requesting provider'
    result['benefits']['network_source'] = 'clinic_nonparticipation_rule'
    clinic_rule = collection_guidance.build(result)
    assert clinic_rule['network'] == 'Out of network'
    assert clinic_rule['network_source'] == 'Current scoped clinic nonparticipation rule'
    assert clinic_rule['amount_cents'] is None


def test_missing_deductible_can_use_the_actual_copay_as_partial_advance_with_explanation():
    result = scenario()
    result['price_range'].update(complete=False)
    terms = result['price_range']['lines'][0]['benefits']
    terms.update(copay_cents=6300, copay_period='DAY', coinsurance_percent='0', oop_remaining_cents=100000)
    value = collection_guidance.build(result)
    assert value['amount_cents'] == 6300 and value['basis'] == 'office_copay'
    assert any('did not establish whether the deductible applies' in text for text in value['assumptions'])
    terms['copay_cents'] = 2900
    assert collection_guidance.build(result)['amount_cents'] == 2900
    result['price_range']['lines'][0]['allowed_cents'] = 2100
    assert collection_guidance.build(result)['amount_cents'] == 2100
    terms['deductible_applies'] = True
    assert collection_guidance.build(result)['basis'] == 'missing_collection_inputs'


@pytest.mark.parametrize('legacy_cash_fallback', [False, True])
def test_saved_collection_rechecks_new_library_requirement_before_display(legacy_cash_fallback):
    result = scenario()
    result.update(payer='UnitedHealthcare', service_date='2026-10-15',
                  codes=[{'id': 'office', 'code': '99203', 'units': 1}],
                  plan_details={'stedi': {'names': ['Choice Plus']}},
                  insurance_details={'member_id': 'synthetic-member'}, requirement_sources=[])
    if legacy_cash_fallback:
        result.update(payment_mode='cash', cash_fallback={'reason': 'older automatic choice'})
    case = {'previsit_context': {'result': result, 'payer_id': 'synthetic-payer',
                                 'inputs': {'service_date': '2026-10-15'}, 'source': {}}}
    document = {'id': 'new-matrix', 'kind': 'plan_matrix', 'sha256': 'synthetic-hash',
                'extraction_summary': {'status': 'usable'}}
    decision = {'referral': {'status': 'Required'},
                'authorization': {'status': 'Not required'},
                'procedures': [{'code': '99203', 'referral': {'status': 'Required'}}]}
    with patch('app.api.routers.commercial_flow.commercial_previsit_status._recheck_saved_clinic_pricing'), \
         patch.object(api.staff_store, 'list_library', return_value=[document]), \
         patch.object(api.staff_store, 'get_library', return_value=document), \
         patch.object(api.commercial_store, 'list_connections', return_value=[]), \
         patch.object(api.contract_requirements, 'evaluate', return_value=decision) as evaluate, \
         patch.object(api, '_requirement_candidates', return_value=[]):
        reopened = api._previsit_result(case, 'clinic')
    assert evaluate.called
    assert reopened.get('payment_mode') != 'cash'
    assert reopened['benefits']['active'] is True
    assert reopened['collection_guidance']['amount_cents'] is None
    assert reopened['payment_explanation']['reason_code'] == 'referral_required'
    assert 'CPT 99203' in reopened['payment_explanation']['sentence']
    assert 'requirement_evidence' not in result


def test_saved_legacy_default_zero_is_rebuilt_from_evidence():
    result = {'benefits': {}, 'collection_guidance': {'amount_cents': 0, 'status': 'deferred'}}
    value = api._previsit_result({'previsit_context': {'result': result}})
    assert value['collection_guidance']['amount_cents'] is None
    assert value['collection_guidance']['basis'] == 'missing_collection_inputs'


@pytest.mark.parametrize('change', ['unchanged', 'contract_removed', 'rate_replaced', 'profile_changed'])
def test_reopening_saved_estimate_rechecks_current_clinic_pricing(change):
    result = scenario(4800)
    result.update(payer='United Healthcare', payment_mode='insurance', service_date='2026-10-15',
                  calculated_at='2026-10-15T12:00:00+00:00',
                  codes=[{'id': 'office', 'code': '99203', 'units': 1}],
                  plan_details={'stedi': {'names': ['Choice Plus'], 'network_names': ['Choice Plus']}},
                  network_evidence={'document_id': 'original-contract'})
    result['price_range']['lines'][0].update(allowed_basis='clinic_contract', allowed_cents=10000,
                                            unit_allowed_cents=10000, rate_version='original-rate')
    context = {'result': result, 'payer_id': 'uhc-route',
               'inputs': {'service_date': '2026-10-15'}, 'source': {}}
    connection = {'id': 'clinic-uhc', 'stedi_payer_id': 'uhc-route',
                  'status': 'staff_guidance_enabled', 'rules': {'collection_policy': ADVANCE_POLICY}}
    contract_id = 'replacement-contract' if change == 'contract_removed' else 'original-contract'
    rate_id = 'replacement-rate' if change == 'rate_replaced' else 'original-rate'
    with patch.object(api.commercial_store, 'list_connections', return_value=[connection]), \
         patch.object(api, '_previsit_practice_path', return_value=({}, {}, {
             'updated_at': '2026-10-16T12:00:00+00:00' if change == 'profile_changed'
                           else '2026-10-14T12:00:00+00:00'})) as path, \
         patch.object(api, '_network_contract_evidence', return_value={'document_id': contract_id}), \
         patch.object(api.commercial_store, 'matching_rates', return_value=[
             {'code': '99203', 'modifier': '', 'allowed_cents': 10000, 'version_id': rate_id}]):
        reopened = api._previsit_result({'previsit_context': context}, 'clinic')
    assert path.call_args.args[2]['payer_plan_names'] == ['Choice Plus']
    if change == 'unchanged':
        assert reopened['collection_guidance']['amount_cents'] == 4800
        assert reopened['encounter_summary']['estimated_total_patient_min_cents'] == 4800
    else:
        assert reopened['collection_guidance']['amount_cents'] is None
        assert reopened['benefits']['active'] is True
        assert reopened['encounter_summary']['estimated_total_patient_min_cents'] is None
        assert reopened['price_range']['collection']['amount_cents'] is None
        assert 'rerun' in reopened['payment_explanation']['sentence'].lower()


def test_saved_contract_recheck_uses_the_same_patient_bound_payer_product_as_initial_pricing():
    result = scenario(4800)
    result['benefits']['service_date_matches'] = True
    result.update(payer='United Healthcare', payment_mode='insurance',
                  calculated_at='2026-10-15T12:00:00+00:00',
                  codes=[{'id': 'office', 'code': '99203', 'units': 1}],
                  plan_details={'stedi': {'names': ['UHC Complete Regional PPO Medicare Advantage']}},
                  network_evidence={'document_id': 'regional-contract'},
                  billing_protection={'status': 'not_qmb', 'reference': 'synthetic member verification',
                      'verified_at': '2026-10-15T11:00:00Z', 'identity_binding': 'synthetic-current-binding'})
    result['price_range']['lines'][0].update(allowed_basis='clinic_contract', allowed_cents=10000,
                                            unit_allowed_cents=10000, rate_version='regional-rate')
    case = {'previsit_context': {'result': result, 'payer_id': 'uhc-route',
            'inputs': {'service_date': '2026-10-15'},
            'source': {'plan': 'United Healthcare'}}}
    connection = {'id': 'clinic-uhc', 'stedi_payer_id': 'uhc-route',
                  'status': 'staff_guidance_enabled', 'rules': {'collection_policy': ADVANCE_POLICY}}
    with patch.object(api.commercial_store, 'list_connections', return_value=[connection]), \
         patch.object(api, '_previsit_practice_path', return_value=({}, {}, {})) as path, \
         patch.object(api, '_network_contract_evidence', return_value={'document_id': 'regional-contract'}), \
         patch.object(api.commercial_store, 'matching_rates', return_value=[
             {'code': '99203', 'modifier': '', 'allowed_cents': 10000, 'version_id': 'regional-rate'}]):
        reopened = api._previsit_result(case, 'clinic')
    assert path.call_args.args[2]['plan'] == 'UHC Complete Regional PPO Medicare Advantage'
    assert reopened['collection_guidance']['amount_cents'] == 4800


def test_saved_emg_cost_is_withheld_when_old_calculation_or_new_payer_order_invalidates_it():
    result = scenario(5000)
    result['emg_option'] = {'requested': True, 'included': False, 'total_min_cents': 0,
                            'total_max_cents': 0, 'additional_min_cents': 0, 'additional_max_cents': 0,
                            'scenario_count': 1, 'message': 'Old collection-based testing total.'}
    reopened = api._previsit_result({'previsit_context': {'result': result}})
    assert reopened['emg_option']['requested'] is True
    assert reopened['emg_option']['total_min_cents'] is None
    result['emg_option']['calculation_version'] = 2
    result['recovery'] = {'reason_code': 'multiple_active_policies'}
    reopened = api._previsit_result({'previsit_context': {'result': result}})
    assert reopened['emg_option']['total_min_cents'] is None
    result.pop('recovery')
    result['emg_option']['total_min_cents'] = 7000
    result['emg_option']['total_max_cents'] = 7000
    reopened = api._previsit_result({'previsit_context': {'result': result}})
    assert reopened['emg_option']['total_min_cents'] == 7000


def test_surname_warning_does_not_hide_returned_benefits_or_block_amount():
    result = scenario(4800)
    result['identity'] = {'status': 'warning', 'binding': 'synthetic-current-binding', 'match_version': 2,
                          'message': 'Payer surname differs; first name, date of birth and member ID match.'}
    assert collection_guidance.build(result)['amount_cents'] == 4800
    assert result['benefits']['active'] is True


def test_emg_comparison_uses_shared_balances_and_never_adds_a_second_visit_copay():
    from test_visit_estimate import payload, DAY
    from app.insurance import cpt_rules
    raw = payload()
    codes = [{'id': 'office', 'code': '99203', 'units': 1}]
    rates = [{'code': code, 'allowed_cents': 10000} for code in ['99203', *cpt_rules.NCS_CODES, '95885', '95886']]
    result = {'codes': codes, 'benefits': commercial.normalize_stedi_271(raw, '98', True),
              'identity': {'status': 'warning', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'collection_policy': ADVANCE_POLICY}
    result['benefits']['network_source'] = 'clinic_library_contract'
    result['price_range'] = visit_estimate.price_lines(codes, rates, result['benefits'], {}, payload=raw, service_date=DAY)
    option = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {'emg_required': True})
    assert option['scenario_count'] == 56
    assert option['additional_min_cents'] == option['additional_max_cents'] == 2000
    assert option['total_min_cents'] == option['total_max_cents'] == 8000
    assert not option['included']
    with_payment = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY,
        {'emg_required': True, 'payments': {'reference': 'synthetic-receipt', 'amount_cents': 1500}})
    assert with_payment['additional_min_cents'] == 2000
    assert with_payment['total_min_cents'] == 8000
    assert collection_guidance.emg_option(result, [], {'cost_share': 'payer_returned'}, raw, DAY, {})['scenario_count'] == 0
    assert collection_guidance.emg_option(result, [], {'cost_share': 'payer_returned'}, raw, DAY, {'emg_required': True})['total_min_cents'] is None
    # Explicit testing is priced once, with its actual quantity.
    result['codes'] += [{'id': code, 'code': code, 'units': 1} for code in ['95907', '95886']]
    exact = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {})
    assert exact['included'] and exact['scenario_count'] == 1 and exact['total_max_cents'] == 8000
    # Zero is allowed only when the payer actually reports an exhausted balance.
    raw['benefitsInformation'][2]['benefitAmount'] = '0'
    result['benefits'] = commercial.normalize_stedi_271(raw, '98', True)
    result['benefits']['network_source'] = 'clinic_library_contract'
    zero = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY, {})
    assert zero['additional_max_cents'] == zero['total_max_cents'] == 0


def test_emg_scenario_does_not_treat_deferred_checkin_as_zero_patient_cost():
    from test_visit_estimate import payload, DAY
    from app.insurance import cpt_rules
    raw = payload()
    codes = [{'id': 'office', 'code': '99203', 'units': 1}]
    rates = [{'code': code, 'allowed_cents': 10000} for code in ['99203', *cpt_rules.NCS_CODES, '95885', '95886']]
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    result = {'codes': codes, 'benefits': benefits, 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'recovery': {'reason_code': 'multiple_active_policies'},
              'collection_policy': {'mode': 'defer_to_claim', 'approved_by': 'clinic-admin',
                                    'approved_at': '2026-10-01T12:00:00Z', 'reference': 'policy-1'}}
    result['price_range'] = visit_estimate.price_lines(codes, rates, benefits, {}, payload=raw, service_date=DAY)
    option = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY,
                                            {'emg_required': True})
    assert option['total_min_cents'] is None
    assert option['additional_min_cents'] is None
    result.pop('collection_policy')
    result['recovery'] = {}
    result['billing_note'] = {'kind': 'no_collection'}
    note_option = collection_guidance.emg_option(result, rates, {'cost_share': 'payer_returned'}, raw, DAY,
                                                 {'emg_required': True})
    assert note_option['total_min_cents'] is None
