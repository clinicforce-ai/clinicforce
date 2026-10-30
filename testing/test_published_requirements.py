"""Synthetic product, service and approval boundaries for verified workbook research."""
from copy import deepcopy

import pytest

from app.insurance.contract_requirements import evaluate
from app.insurance import published_plan_benefits


def test_humana_plan_requires_card_and_payer_match_before_publishing_office_rule():
    payer_plan = {'names': ['Humana Value Choice']}
    card = {'status': 'matched', 'cms_plan_id': 'H7617-057', 'plan_name': 'Humana Value Choice (PPO)'}
    identified = published_plan_benefits.identify_card_plan(payer_plan, card, 'Humana Health Plan', True)
    assert identified['identified_product'] == 'Humana Value Choice PPO H7617-057'
    for wrong in ({**card, 'cms_plan_id': 'H7617-064'}, {**card, 'status': 'unverified'},
                  {**card, 'plan_name': 'Humana Value Choice (HMO)'}):
        assert published_plan_benefits.identify_card_plan(payer_plan, wrong, 'Humana', True) == payer_plan
    assert published_plan_benefits.identify_card_plan(payer_plan, card, 'Humana', False) == payer_plan
    assert published_plan_benefits.identify_card_plan({'names': ['Humana Gold Plus']}, card, 'Humana', True) == {'names': ['Humana Gold Plus']}
    benefits = {'active': True, 'service_date_matches': True, 'deductible_remaining_cents': None}
    published_plan_benefits.apply_plan_terms(benefits, identified, '2026-10-29', True)
    assert benefits['published_preferred_office_copay_cents'] == 3000
    assert benefits['deductible_applies'] is False
    outside = {'active': True, 'service_date_matches': True}
    published_plan_benefits.apply_plan_terms(outside, identified, '2027-01-01', True)
    assert 'published_plan_rule' not in outside
    case_benefits = {**benefits, 'plan_details': identified, 'network_status': 'in_network',
                     'network_source': 'clinic_group_agreement',
                     'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}
    rules = {'place_of_service': '11', 'provider_office_role': 'specialist'}
    office = published_plan_benefits.apply_office_terms({}, case_benefits, rules, '2026-10-29', '99214')
    assert (office['copay_cents'], office['deductible_applies'], office['coinsurance_percent']) == (3000, False, '0')
    assert published_plan_benefits.apply_office_terms({}, {**case_benefits, 'network_status': 'out_of_network'},
                                                      rules, '2026-10-29', '99214') == {}
    assert published_plan_benefits.apply_office_terms({'copay_cents': 2000}, case_benefits,
                                                      rules, '2026-10-29', '99214')['requires_review'] is True
    assert published_plan_benefits.apply_office_terms({}, case_benefits, rules, '2026-10-29', '99203')['copay_cents'] == 3000


def test_humana_card_member_mismatch_is_reference_only():
    plan = {'names': ['Humana Value Choice']}
    card = {'status': 'conflict', 'conflicts': ['member_id'], 'differences': [],
            'patient_name_matches': True, 'cms_plan_id': 'H7617-057',
            'plan_name': 'Humana Value Choice (PPO)'}
    benefits = {'active': True, 'has_secondary': False}
    reference = published_plan_benefits.conflicting_card_plan_reference(
        plan, card, 'Humana Health Plan', benefits, True, '2026-10-30')
    assert reference == 3000
    assert published_plan_benefits.identify_card_plan(plan, card, 'Humana Health Plan', True) == plan
    for changed in ({'patient_name_matches': False}, {'cms_plan_id': 'H7617-064'},
                    {'conflicts': ['member_id', 'group_number']}, {'differences': ['payer_name']}):
        assert published_plan_benefits.conflicting_card_plan_reference(
            plan, {**card, **changed}, 'Humana Health Plan', benefits, True, '2026-10-30') is None
    assert published_plan_benefits.conflicting_card_plan_reference(
        plan, card, 'Humana Health Plan', benefits, False, '2026-10-30') is None


def test_humana_exact_ppo_office_visit_has_scoped_referral_and_authorization_rules():
    result, context = case('Humana', 'Humana Value Choice PPO H7617-057', '99214', day='2026-10-29')
    decisions = evaluate(result, context, [])
    assert decisions['referral']['status'] == 'Not required'
    assert decisions['authorization']['status'] == 'Not required'
    new_visit, new_context = case('Humana', 'Humana Value Choice PPO H7617-057', '99203', day='2026-10-30')
    new_decisions = evaluate(new_visit, new_context, [])
    assert new_decisions['referral']['status'] == 'Not required'
    assert new_decisions['authorization']['status'] == 'Not required'
    for other_plan in ['Humana Value Choice', 'Humana Value Choice PPO H7617-064', 'Humana HMO']:
        assert evaluate(result, {**context, 'plans': [other_plan]}, [])['authorization']['status'] != 'Not required'
    assert evaluate(result, {**context, 'pos': '22'}, [])['authorization']['status'] != 'Not required'


def case(payer, product, *codes, pos='11', day='2026-10-14', indication=None):
    result = {'codes': [{'id': str(index), 'code': code, 'units': 1}
                        for index, code in enumerate(codes)], 'benefit_source': 'Stedi',
              'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}}
    context = {'service_date': day, 'payer_names': [payer], 'plans': [product],
               'member_id': 'SYNTHETIC', 'group_number': 'SYNTHETIC',
               'rendering_npi': '1111111111', 'billing_npi': '2222222222',
               'pos': pos, 'indication': indication}
    return result, context


@pytest.mark.parametrize('payer,product,code,referral,authorization', [
    ('UnitedHealthcare', 'Choice Plus', '99203', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Choice Plus', 'J0585', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Choice Plus', '64615', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Regional Medicare Advantage PPO', '99203', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Regional Medicare Advantage PPO', 'J0585', 'Not required', 'Required'),
    ('UnitedHealthcare', 'Regional Medicare Advantage PPO', '64615', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Navigate', '99203', 'Required', 'Verification required'),
    ('UnitedHealthcare', 'Navigate Balanced', '99203', 'Verification required', 'Verification required'),
    ('UnitedHealthcare', 'NexusACO OA', '99203', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'NexusACO R', '99203', 'Required', 'Verification required'),
    ('UnitedHealthcare', 'NexusACO RB', '99203', 'Verification required', 'Verification required'),
    ('Cigna', 'Cigna PPO', '99203', 'Not required', 'Verification required'),
    ('Cigna', 'Cigna HMO', '99203', 'Required', 'Verification required'),
    ('Cigna', 'Cigna PPO', 'J0585', 'Not required', 'Required'),
    ('Cigna', 'Cigna PPO', '64612', 'Not required', 'Required'),
    ('BCBSTX', 'Blue Choice PPO', '99203', 'Not required', 'Verification required'),
    ('BCBSTX', 'Blue Choice PPO Basic', '99213', 'Not required', 'Verification required'),
    ('BCBSTX', 'BASIC', '99213', 'Verification required', 'Verification required'),
    ('BCBSTX', 'Blue Advantage HMO', '99203', 'Required', 'Verification required'),
    ('BCBSTX', 'Blue Choice PPO', 'J0585', 'Not required', 'Verification required'),
    ('BCBSTX', 'Participating out-of-state BlueCard/BCBS networks', '99203', 'Verification required', 'Verification required'),
    ('Traditional Medicare', 'Original Medicare', '99203', 'Not required', 'Verification required'),
    ('Traditional Medicare', 'Original Medicare', '95910', 'Not required', 'Verification required'),
    ('UnitedHealthcare', 'Choice Plus', '95910', 'Not required', 'Verification required'),
])
def test_published_scope_stays_separate_from_member_approval(payer, product, code, referral, authorization):
    result, context = case(payer, product, code)
    decisions = evaluate(result, context, [])
    line = decisions['procedures'][0]
    assert (line['referral']['status'], line['authorization']['status']) == (referral, authorization)
    assert line['referral']['referral_obtained'] == ('NOT APPLICABLE' if referral == 'Not required' else 'UNKNOWN')
    assert line['authorization']['pa_approval'] == ('UNVERIFIED' if authorization != 'Not required' else 'NOT APPLICABLE')
    if authorization == 'Required':
        assert any(source.get('verified_at') for source in line['authorization']['sources'])


def test_original_medicare_opd_pa_does_not_leak_to_physician_office():
    result, context = case('Traditional Medicare', 'Original Medicare', 'J0585', '64615', pos='22')
    opd = evaluate(result, context, [])
    assert [line['authorization']['status'] for line in opd['procedures']] == ['Required', 'Required']
    office = evaluate(result, {**context, 'pos': '11'}, [])
    assert all(line['authorization']['status'] == 'Verification required' for line in office['procedures'])


def test_aetna_open_access_managed_choice_selected_office_and_ncs_rules():
    result, context = case('Aetna', 'Open Access MC', '99203', '95908')
    context['plans'].insert(0, 'CI')
    decisions = evaluate(result, context, [])
    assert [row['referral']['status'] for row in decisions['procedures']] == ['Not required'] * 2
    assert all(row['authorization']['status'] != 'Not required'
               for row in decisions['procedures'])

    # The open-access rule cannot clear a different product or a listed drug.
    assert evaluate(result, {**context, 'plans': ['Managed Choice POS']}, [])['referral']['status'] == 'Verification required'
    botox, botox_context = case('Aetna', 'Open Access MC', '64615')
    assert evaluate(botox, botox_context, [])['authorization']['status'] == 'Verification required'


def test_aetna_signature_diagnostics_keep_preparation_separate_from_approval():
    result, context = case('Aetna', 'Aetna Medicare Signature (PPO)', '95908', '95886',
                           day='2026-10-28')
    result['benefits']['plan_details'] = {
        'names': ['Aetna Medicare Signature (PPO)'],
        'group_number': '000003-TX', 'plan_number': '000003-TX000006'}
    decisions = evaluate(result, context, [])
    assert all(row['referral']['status'] == 'Not required' for row in decisions['procedures'])
    assert all(row['authorization']['status'] == 'Verification required'
               for row in decisions['procedures'])
    assert all(row['authorization']['pa_approval'] == 'UNVERIFIED'
               for row in decisions['procedures'])


def test_same_day_bcbstx_active_271_can_show_network_referral_rule_without_dates():
    result, context = case('BCBSTX', 'Blue Choice PPO', '95908')
    result['benefits']['service_date_matches'] = None
    result['checked_at'] = context['service_date'] + 'T15:00:00Z'
    decisions = evaluate(result, context, [])
    assert decisions['referral']['status'] == 'Not required'
    assert decisions['authorization']['status'] == 'Verification required'
    result['checked_at'] = '2026-10-13T15:00:00Z'
    assert evaluate(result, context, [])['referral']['status'] == 'Verification required'


def test_aetna_oxy_ppo_new_office_99204_requirements_are_scoped():
    result, context = case('Aetna', 'Oxy PPO with Rx', '99204', day='2026-10-30')
    context['plans'].insert(0, 'CI')
    decisions = evaluate(result, context, [])
    assert decisions['referral']['status'] == 'Not required'
    assert decisions['authorization']['status'] == 'Not required'
    assert all(row['sources'][0]['source_type'] == 'official_published_rule'
               for row in (decisions['referral'], decisions['authorization']))
    for changed in ({**context, 'plans': ['Other PPO']}, {**context, 'pos': '22'}):
        assert evaluate(result, changed, [])['authorization']['status'] != 'Not required'
    other_code, _ = case('Aetna', 'Oxy PPO with Rx', '95908', day='2026-10-30')
    assert evaluate(other_code, context, [])['authorization']['status'] != 'Not required'
    wrong_date = deepcopy(result)
    wrong_date['benefits']['service_date_matches'] = False
    assert evaluate(wrong_date, context, [])['authorization']['status'] != 'Not required'
    payer_requires_auth = deepcopy(result)
    payer_requires_auth['price_range'] = {'lines': [{'id': '0', 'code': '99204', 'benefits': {
        'requirements': {'authorization': {'payer_signal': 'required'}}}}]}
    assert evaluate(payer_requires_auth, context, [])['authorization']['status'] == 'Required'


def test_bcbstx_in_network_ppo_99213_office_requirements():
    result, context = case('BCBSTX', 'PREFERRED PROVIDER OPTION MEDICAL', '99213')
    context['service_date'] = '2026-10-26'
    decision = evaluate(result, context, [])
    assert decision['referral']['status'] == 'Not required'
    assert decision['procedures'][0]['referral']['status'] == 'Not required'
    assert decision['authorization']['status'] == 'Not required'
    assert decision['procedures'][0]['authorization']['status'] == 'Not required'
    assert decision['authorization']['sources'][0]['source_type'] == 'clinic_confirmation'
    displayed_payer = {**context, 'payer_names': ['Blue Cross Blue Shield Tx']}
    assert evaluate(result, displayed_payer, [])['referral']['status'] == 'Not required'
    assert evaluate(result, displayed_payer, [])['authorization']['status'] == 'Not required'
    assert evaluate(result, {**context, 'plans': ['PREFERRED PROVIDER OPTION MEDICAL', 'Blue Choice PPO']}, [])['authorization']['status'] == 'Not required'
    assert evaluate(result, {**context, 'plans': ['Blue Choice PPO']}, [])['authorization']['status'] == 'Not required'
    assert evaluate(result, {**context, 'plans': ['Health Maintenance Organization Medical']}, [])['referral']['status'] == 'Verification required'
    assert evaluate(result, {**context, 'pos': '22'}, [])['authorization']['status'] == 'Verification required'
    assert evaluate(result, {**context, 'pos': None}, [])['authorization']['status'] == 'Verification required'
    higher, _ = case('BCBSTX', 'PREFERRED PROVIDER OPTION MEDICAL', '99214')
    assert evaluate(higher, context, [])['authorization']['status'] == 'Verification required'
    result['price_range'] = {'lines': [{'id': '0', 'code': '99213', 'benefits': {
        'requirements': {'authorization': {'payer_signal': 'required'}}}}]}
    assert evaluate(result, context, [])['authorization']['status'] == 'Required'
    result.pop('price_range')
    result['benefits']['service_date_matches'] = None
    result['checked_at'] = '2026-10-26T15:00:00Z'
    assert evaluate(result, context, [])['authorization']['status'] == 'Not required'
    result['checked_at'] = '2026-10-25T15:00:00Z'
    assert evaluate(result, context, [])['authorization']['status'] == 'Verification required'
    result['benefits']['coverage_evidence'] = {'status': 'ACTIVE', 'start': '2026-01-01'}
    assert evaluate(result, context, [])['authorization']['status'] == 'Not required'


def test_uhc_group_lppo_routine_office_followup_requirements():
    result, context = case('UnitedHealthcare',
                           'LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PPO)',
                           '99213', day='2026-10-29')
    context['group_number'] = '15725'
    decision = evaluate(result, context, [])
    assert decision['referral']['status'] == 'Not required'
    assert decision['authorization']['status'] == 'Not required'
    assert decision['procedures'][0]['authorization']['status'] == 'Not required'
    for code in ('99202', '99203', '99204', '99212', '99214', '99215'):
        result['codes'] = [{'id': '0', 'code': code, 'units': 1}]
        assert evaluate(result, context, [])['authorization']['status'] == 'Not required'
    result['codes'] = [{'id': '0', 'code': '99213', 'units': 1}]
    assert evaluate(result, {**context, 'plans': ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']}, [])['authorization']['status'] == 'Not required'
    assert evaluate(result, {**context, 'pos': '22'}, [])['authorization']['status'] != 'Not required'
    assert evaluate(result, {**context, 'plans': ['UnitedHealthcare Navigate']}, [])['authorization']['status'] != 'Not required'
    result['benefits']['service_date_matches'] = None
    result['checked_at'] = '2026-10-29T15:00:00Z'
    assert evaluate(result, context, [])['authorization']['status'] == 'Not required'
    result['checked_at'] = '2026-10-28T15:00:00Z'
    assert evaluate(result, context, [])['authorization']['status'] == 'Verification required'
    result['benefits']['coverage_evidence'] = {'status': 'ACTIVE', 'start': '2026-01-01'}
    assert evaluate(result, context, [])['authorization']['status'] == 'Not required'


def test_99205_transplant_rule_requires_confirmed_indication():
    result, context = case('UnitedHealthcare', 'Choice Plus', '99205')
    ordinary = evaluate(result, context, [])['authorization']
    assert ordinary['status'] == 'Verification required'
    assert 'transplant evaluation' in ordinary['reason'].lower()
    transplant = evaluate(result, {**context, 'indication': 'transplant_evaluation'}, [])['authorization']
    assert transplant['status'] == 'Required'
    assert transplant['pa_approval'] == 'UNVERIFIED'


def test_selected_codes_and_units_are_rescoped_and_member_review_is_separate():
    result, context = case('Cigna', 'Cigna PPO', 'J0585', '64615')
    decisions = evaluate(result, context, [])
    assert [row['authorization']['status'] for row in decisions['procedures']] == ['Required', 'Required']
    assert all(row['authorization']['pa_approval'] == 'UNVERIFIED' for row in decisions['procedures'])
    changed = deepcopy(result)
    changed['codes'][0]['units'] = 40
    updated = evaluate(changed, context, [])
    assert updated['procedures'][0]['authorization']['applicable_scope']['units_by_code']['J0585'] == 40
    changed['price_range'] = {'lines': [{'id': '0', 'code': 'J0585', 'benefits': {'requirements': {
        'authorization': {'status': 'valid', 'cleared': True, 'reference': 'SYNTHETIC-APPROVAL',
                          'verified_at': '2026-10-14'}}}}]}
    partial = evaluate(changed, context, [])
    assert partial['procedures'][0]['authorization']['pa_approval'] == 'APPROVED'
    assert partial['procedures'][1]['authorization']['pa_approval'] == 'UNVERIFIED'
    assert partial['authorization']['pa_approval'] == 'UNVERIFIED'


def test_referral_on_file_is_distinct_from_published_required_rule():
    result, context = case('UnitedHealthcare', 'Navigate', '99203')
    result['price_range'] = {'lines': [{'id': '0', 'code': '99203', 'benefits': {'requirements': {
        'referral': {'status': 'valid', 'cleared': True, 'reference': 'SYNTHETIC-REFERRAL',
                     'verified_at': '2026-10-14'}}}}]}
    decision = evaluate(result, context, [])['referral']
    assert decision['status'] == 'Required'
    assert decision['referral_obtained'] == 'YES'
    changed_result = {**result, 'price_range': {'lines': []}}
    changed = evaluate(changed_result, {**context, 'diagnoses': ['SYNTHETIC-DX'],
                                 'rendering_npi': '3333333333'}, [])['referral']
    assert changed['referral_obtained'] == 'UNKNOWN'
    assert changed['applicable_scope']['diagnoses'] == ['SYNTHETIC-DX']
    assert changed['applicable_scope']['rendering_npi'] == '3333333333'
    assert evaluate(changed_result, {**context, 'plans': ['NexusACO OA']}, [])['referral']['status'] == 'Not required'


@pytest.mark.parametrize('recorded,display', [
    ('pending', 'PENDING'), ('denied', 'DENIED'), ('expired', 'EXPIRED'),
])
def test_member_authorization_state_does_not_change_the_published_rule(recorded, display):
    result, context = case('Cigna', 'Cigna PPO', 'J0585')
    result['price_range'] = {'lines': [{'id': '0', 'code': 'J0585', 'benefits': {'requirements': {
        'authorization': {'status': recorded, 'cleared': False, 'reference': 'SYNTHETIC-DECISION',
                          'verified_at': '2026-10-14'}}}}]}
    authorization = evaluate(result, context, [])['authorization']
    assert authorization['status'] == 'Required'
    assert authorization['pa_approval'] == display


def test_published_rule_expires_and_delegation_does_not_inherit_it():
    result, context = case('UnitedHealthcare', 'Navigate', '99203')
    assert evaluate(result, context, [])['referral']['status'] == 'Required'
    assert evaluate(result, {**context, 'service_date': '2027-01-01'}, [])['referral']['status'] == 'Verification required'
    assert evaluate(result, {**context, 'payer_names': ['UnitedHealthcare', 'WellMed']}, [])['referral']['status'] == 'Verification required'


def test_emg_lcd_is_not_authorization_and_each_code_is_checked():
    result, context = case('Traditional Medicare', 'Original Medicare', '95910', '95886')
    decisions = evaluate(result, context, [])
    for line in decisions['procedures']:
        assert line['authorization']['status'] == 'Verification required'
        assert 'medical necessity and frequency' in line['authorization']['reason']


def test_bluecard_requires_home_plan_before_host_plan_rules():
    result, context = case('BCBSTX', 'Participating out-of-state BlueCard/BCBS networks', 'J0585')
    decisions = evaluate(result, context, [])
    assert decisions['authorization']['status'] == 'Verification required'
    assert 'home plan' in decisions['authorization']['reason'].lower()
    assert not any(source.get('source_type') == 'official_published_rule'
                   for source in decisions['authorization']['sources'])


def test_pending_referral_is_not_an_obtained_referral():
    result, context = case('UnitedHealthcare', 'Navigate', '99203')
    result['price_range'] = {'lines': [{'id': '0', 'code': '99203', 'benefits': {'requirements': {
        'referral': {'status': 'pending', 'cleared': False, 'reference': 'SYNTHETIC-PENDING',
                     'verified_at': '2026-10-14'}}}}]}
    referral = evaluate(result, context, [])['referral']
    assert referral['status'] == 'Required'
    assert referral['referral_obtained'] == 'NO'
