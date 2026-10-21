"""Synthetic product, service and approval boundaries for verified workbook research."""
from copy import deepcopy

import pytest

from app.insurance.contract_requirements import evaluate


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
