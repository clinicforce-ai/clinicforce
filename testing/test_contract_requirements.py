"""Synthetic PDF evidence for referral and authorization cards; no patient data."""
from datetime import date
from unittest.mock import patch

from app.insurance import contract_requirements as rules


def context(plan='Choice PPO', payer='Example Health'):
    return {'service_date': '2026-10-12', 'payer_verified': True, 'payer_names': [payer],
            'plans': [plan], 'network': 'Preferred', 'rendering_npi': '1234567890',
            'billing_npi': '0987654321', 'pos': '11', 'member_id': 'M-1', 'group_number': 'G-1'}


def contract(rows=None, *, payer='Example Health', document_id='contract-a', page_text=''):
    return {'id': document_id, 'kind': 'contract', 'content_type': 'application/pdf',
            'payer': payer, 'filename': document_id + '.pdf', 'sha256': document_id,
            'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
            'extraction': {'sha256': document_id, 'pages': [{'number': 4, 'text': page_text}],
                           'tables': [{'locator': 'Page 4', 'rows': rows}] if rows else []}}


HEADERS = ['CPT', 'Plan', 'Network', 'Rendering NPI', 'Billing NPI', 'POS', 'Effective From',
           'Referral', 'Prior Authorization']


def row(code, plan, referral, authorization):
    return [code, plan, 'Preferred', '1234567890', '0987654321', '11', '2026-01-01', referral, authorization]


def estimate(*codes, active=True, payer_signals=None):
    signals = payer_signals or {}
    return {'codes': [{'code': code} for code in codes], 'benefits': {'active': active,
            'service_date_matches': True, 'network_status': 'in_network', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}},
            'price_range': {'lines': [{'code': code, 'benefits': {
                'requirements': {kind: {'payer_signal': signal} for kind, signal in signals.items()},
                'authorization_basis': 'Payer indicator'}} for code in codes]}}


def test_two_patients_and_procedures_keep_the_requirements_separate():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Required', 'Not required'),
                    row('95910', 'Choice PPO', 'Not required', 'Required')])
    office = rules.evaluate(estimate('99213'), context(), [pdf])
    testing = rules.evaluate(estimate('95910'), context(), [pdf])
    assert (office['referral']['status'], office['authorization']['status']) == ('Required', 'Not required')
    assert (testing['referral']['status'], testing['authorization']['status']) == ('Not required', 'Required')
    assert office['referral']['sources'] == [{'filename': 'contract-a.pdf', 'document_id': 'contract-a', 'page': 4}]
    assert 'CPT 99213' in office['referral']['reason']


def test_other_plan_or_missing_provider_scope_cannot_use_a_contract_row():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Not required')])
    assert rules.evaluate(estimate('99213'), context(plan='Other HMO'), [pdf])['referral']['status'] == 'Verification required'
    wrong_provider = context(); wrong_provider['rendering_npi'] = '1111111111'
    assert rules.evaluate(estimate('99213'), wrong_provider, [pdf])['authorization']['status'] == 'Verification required'
    assert rules.evaluate(estimate('99213'), context(), [pdf | {'effective_to': '2026-10-01'}])['referral']['status'] == 'Verification required'


def test_generic_uhc_payer_label_does_not_match_an_excluded_exact_product():
    pdf = contract([HEADERS, row('99203', 'United Healthcare', 'Not required', 'Required')],
                   payer='UnitedHealthcare')
    visit_context = context(plan='United Healthcare', payer='UnitedHealthcare')
    visit_context['plans'] = ['United Healthcare', 'UHC Complete Regional PPO Medicare Advantage']
    with patch.object(rules.plan_matrix, 'evaluate', return_value=None):
        evidence = rules.evaluate(estimate('99203'), visit_context, [pdf])
    assert evidence['referral']['status'] == 'Verification required'
    assert evidence['authorization']['status'] == 'Verification required'


def test_database_date_objects_and_unconfirmed_procedure_options():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Required')])
    pdf.update(effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31))
    assert rules.evaluate(estimate('99213'), context(), [pdf])['authorization']['status'] == 'Required'
    tentative = estimate('99213')
    tentative['codes'][0]['needs_confirmation'] = True
    assert rules.evaluate(tentative, context(), [pdf])['authorization']['status'] == 'Verification required'


def test_unselected_office_alternatives_keep_exact_conditional_cpt_rules():
    pdf = contract([HEADERS,
                    row('99202', 'Choice PPO', 'Required', 'Not required'),
                    row('99203', 'Choice PPO', 'Not required', 'Required')])
    result = estimate('99202', '99203')
    for option in result['codes']:
        option['source'] = 'conditional office E/M alternative'
    evidence = rules.evaluate(result, context(), [pdf])
    assert evidence['referral']['status'] == 'Verification required'
    assert evidence['authorization']['status'] == 'Verification required'
    procedures = {item['code']: item for item in evidence['procedures']}
    assert procedures['99202']['referral']['status'] == 'Required'
    assert procedures['99202']['authorization']['status'] == 'Not required'
    assert procedures['99203']['referral']['status'] == 'Not required'
    assert procedures['99203']['authorization']['status'] == 'Required'


def test_one_payer_required_signal_does_not_apply_to_every_unselected_cpt():
    result = estimate('99202', '99203') | {'benefit_source': 'Stedi'}
    for option in result['codes']:
        option['source'] = 'conditional office E/M alternative'
    result['price_range']['lines'][0]['benefits']['requirements']['authorization'] = {
        'payer_signal': 'required'}
    evidence = rules.evaluate(result, context(), [])
    assert evidence['authorization']['status'] == 'Verification required'
    procedures = {item['code']: item for item in evidence['procedures']}
    assert procedures['99202']['authorization']['status'] == 'Required'
    assert procedures['99203']['authorization']['status'] == 'Verification required'


def test_selected_required_testing_still_sets_visit_rule_with_office_alternatives():
    pdf = contract([HEADERS,
                    row('99202', 'Choice PPO', 'Not required', 'Not required'),
                    row('99203', 'Choice PPO', 'Not required', 'Not required'),
                    row('95910', 'Choice PPO', 'Not required', 'Required')])
    result = estimate('99202', '99203', '95910')
    for option in result['codes'][:2]:
        option['source'] = 'conditional office E/M alternative'
    evidence = rules.evaluate(result, context(), [pdf])
    assert evidence['authorization']['status'] == 'Required'
    assert evidence['authorization']['approval_status'] == 'Not verified'
    assert {item['code']: item['authorization']['status'] for item in evidence['procedures']} == {
        '99202': 'Not required', '99203': 'Not required', '95910': 'Required'}


def test_uhc_choice_plus_open_access_applies_to_all_unselected_office_options():
    result = estimate('99202', '99203', '99204', '99205') | {'benefit_source': 'Stedi'}
    for option in result['codes']:
        option['source'] = 'conditional office E/M alternative'
    evidence = rules.evaluate(result, context(plan='POS CHOICE PLUS', payer='UnitedHealthcare'), [])
    assert evidence['referral']['status'] == 'Not required'
    assert all(item['referral']['status'] == 'Not required' for item in evidence['procedures'])
    assert all(item['authorization']['status'] == 'Verification required'
               for item in evidence['procedures'])


def test_choice_plus_rule_does_not_clear_a_second_specific_uhc_product():
    result = estimate('99203') | {'benefit_source': 'Stedi'}
    visit_context = context(plan='POS CHOICE PLUS', payer='UnitedHealthcare')
    visit_context['plans'] = ['POS CHOICE PLUS', 'REGIONAL PPO']
    with patch.object(rules.plan_matrix, 'evaluate', return_value=None):
        evidence = rules.evaluate(result, visit_context, [])
    assert evidence['referral']['status'] == 'Verification required'


def test_all_planned_codes_must_be_covered_and_eligibility_needed_to_clear_requirement():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Not required')])
    assert rules.evaluate(estimate('99213', '95910'), context(), [pdf])['referral']['status'] == 'Verification required'
    unclear = rules.evaluate(estimate('99213', active=False), context(), [pdf])['referral']
    assert unclear['status'] == 'Verification required' and unclear['sources'][0]['page'] == 4
    uncertain_identity = estimate('99213')
    uncertain_identity['benefits']['identity'] = {'status': 'warning'}
    assert rules.evaluate(uncertain_identity, context(), [pdf])['referral']['status'] == 'Verification required'
    undated = estimate('99213')
    undated['benefits']['service_date_matches'] = None
    assert rules.evaluate(undated, context(), [pdf])['authorization']['status'] == 'Verification required'


def test_contract_delegating_to_payer_policy_cites_page_without_inventing_a_rule():
    text = ('Provider will comply with all Proper Referral procedures set forth in this Agreement and the Policies and Procedures. '
            'Provider will comply with all Preauthorization requirements set forth in this Agreement and the Policies and Procedures.')
    linked = estimate('99213') | {'network_evidence': {'document_id': 'contract-a'}}
    result = rules.evaluate(linked, context(), [contract(page_text=text)])
    for kind in ('referral', 'authorization'):
        assert result[kind]['status'] == 'Verification required'
        assert result[kind]['sources'][0]['page'] == 4
        assert 'payer policies' in result[kind]['reason']


def test_missing_contract_uses_only_explicit_payer_evidence():
    no_contract = rules.evaluate(estimate('99213'), context(), [])
    assert no_contract['referral']['status'] == 'Verification required'
    assert no_contract['referral']['sources'] == []
    explicit = rules.evaluate(estimate('99213', payer_signals={'referral': 'required', 'authorization': 'not_required'})
                              | {'benefit_source': 'Stedi'}, context(), [])
    assert explicit['referral']['status'] == 'Required'
    assert explicit['authorization']['status'] == 'Not required'
    legacy = estimate('99213', payer_signals={'referral': 'required'}) | {'benefit_source': 'Stedi'}
    legacy['benefits']['identity'] = {'status': 'matched', 'binding': 'legacy-binding'}
    assert rules.evaluate(legacy, context(), [])['referral']['status'] == 'Verification required'
    absent = estimate('99213', payer_signals={'authorization': 'not_required'})
    absent['price_range']['lines'][0]['benefits']['authorization_basis'] = 'Derived from absent indicator in applicable response'
    assert rules.evaluate(absent, context(), [])['authorization']['status'] == 'Verification required'
    wrong_patient = estimate('99213', payer_signals={'referral': 'required'})
    wrong_patient['benefits']['identity'] = {'status': 'warning'}
    assert rules.evaluate(wrong_patient, context(), [])['referral']['status'] == 'Verification required'
    missing_group = context(); missing_group['group_number'] = ''
    assert rules.evaluate(explicit, missing_group, [])['authorization']['status'] == 'Verification required'


def test_uhc_texas_choice_plus_official_open_access_rule_clears_specialist_referral_only():
    result = estimate('99203') | {'benefit_source': 'Stedi'}
    visit_context = context(plan='POS CHOICE PLUS', payer='UnitedHealthcare')
    referral = rules.evaluate(result, visit_context, [])
    assert referral['referral']['status'] == 'Not required'
    assert referral['referral']['sources'][0]['url'].startswith('https://www.uhcprovider.com/')
    assert referral['authorization']['status'] == 'Verification required'
    ambiguous = {**result, 'recovery': {'reason_code': 'multiple_active_policies'}}
    assert rules.evaluate(ambiguous, visit_context, [])['referral']['status'] == 'Verification required'
    wrong_identity = estimate('99203') | {'benefit_source': 'Stedi'}
    wrong_identity['benefits']['identity'] = {'status': 'review'}
    assert rules.evaluate(wrong_identity, visit_context, [])['referral']['status'] == 'Verification required'
    excluded_product = context(plan='UHC COMPLETE REGIONAL PPO MEDICARE ADVANTAGE', payer='UnitedHealthcare')
    regional = rules.evaluate(result, excluded_product, [])
    assert regional['referral']['status'] == 'Verification required'
    assert regional['authorization']['status'] == 'Verification required'
    assert '10-1-26.pdf' in regional['authorization']['verify_url']
    group = rules.evaluate(result, context(plan='UnitedHealthcare Group Medicare Advantage (PPO)', payer='UnitedHealthcare'), [])
    assert group['referral']['status'] == 'Not required'
    assert '2026-UHC-Administrative-Guide.pdf' in group['referral']['sources'][0]['url']


def test_current_identity_mismatch_overrides_stale_matched_benefit_identity():
    mismatched = estimate('99203') | {'benefit_source': 'Stedi',
                                    'identity': {'status': 'review', 'differences': [{'field': 'first_name'}]}}
    choice_plus = context(plan='POS CHOICE PLUS', payer='UnitedHealthcare')
    assert rules.evaluate(mismatched, choice_plus, [])['referral']['status'] == 'Verification required'
    pdf = contract([HEADERS, row('99203', 'Choice PPO', 'Not required', 'Required')])
    assert rules.evaluate(mismatched, context(), [pdf])['authorization']['status'] == 'Verification required'
    mismatched['price_range']['lines'][0]['benefits']['requirements']['authorization'] = {
        'payer_signal': 'required'}
    assert rules.evaluate(mismatched, context(), [])['authorization']['status'] == 'Verification required'


def test_current_payer_required_signal_stays_yes_with_a_missing_group_or_static_rule():
    required = estimate('99213', payer_signals={'referral': 'required'}) | {'benefit_source': 'Stedi'}
    missing_group = context(); missing_group['group_number'] = ''
    assert rules.evaluate(required, missing_group, [])['referral']['status'] == 'Required'
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Not required')])
    assert rules.evaluate(required, context(), [pdf])['referral']['status'] == 'Required'


def test_requirement_sources_link_the_saved_live_stedi_check_without_clearing_an_unknown_rule():
    result = estimate('99213') | {'benefit_source': 'Stedi', 'response_reference': 'ec_123e4567-e89b-12d3-a456-426614174000'}
    evidence = rules.evaluate(result, context(), [])
    for kind in ('referral', 'authorization'):
        assert evidence[kind]['status'] == 'Verification required'
        assert evidence[kind]['sources'][-1]['url'].endswith('/ec_123e4567-e89b-12d3-a456-426614174000')


def test_generic_ppo_research_retains_explicit_payer_auth_indicator_as_conditional_evidence():
    result = estimate('99203', payer_signals={'authorization': 'not_required'})
    result['benefit_source'] = 'Stedi'
    result['benefits']['network_status'] = 'unknown'
    result['benefits']['service_date_matches'] = None
    result['price_range']['lines'][0]['benefits']['authorization_basis'] = 'Payer indicator'
    visit_context = context(plan='PREFERRED PROVIDER OPTION PLUS MEDICAL', payer='BCBSTX')
    visit_context['coverage_period'] = {'start': '2026-01-01'}
    evidence = rules.evaluate(result, visit_context, [])['authorization']
    assert evidence['status'] == 'Verification required'
    assert 'not required' in evidence['reason'].lower()
    assert 'provider network' in evidence['reason'].lower()
    assert any('row 17' in source.get('label', '') for source in evidence['sources'])
    assert any('Stedi' in source.get('label', '') for source in evidence['sources'])
    result['benefits']['service_date_matches'] = True
    assert rules.evaluate(result, visit_context, [])['authorization']['status'] == 'Verification required'


def test_conflicting_contract_rows_or_payer_response_require_verification():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Required', 'Not required'),
                    row('99213', 'Choice PPO', 'Not required', 'Not required')])
    assert rules.evaluate(estimate('99213'), context(), [pdf])['referral']['status'] == 'Verification required'
    pdf['extraction']['tables'][0]['rows'].pop()
    conflicting_payer = estimate('99213', payer_signals={'referral': 'not_required'}) | {'benefit_source': 'Stedi'}
    assert rules.evaluate(conflicting_payer, context(), [pdf])['referral']['status'] == 'Verification required'


def test_each_procedure_keeps_its_own_requirement_and_visit_flags_required():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Not required'),
                    row('95910', 'Choice PPO', 'Not required', 'Required')])
    result = estimate('99213', '95910', '95886')
    for index, item in enumerate(result['codes']):
        item['id'] = f'line-{index}'
        result['price_range']['lines'][index]['id'] = item['id']
    evidence = rules.evaluate(result, context(), [pdf])
    procedures = {item['code']: item for item in evidence['procedures']}
    assert procedures['99213']['authorization']['status'] == 'Not required'
    assert procedures['95910']['authorization']['status'] == 'Required'
    assert procedures['95886']['authorization']['status'] == 'Verification required'
    assert evidence['authorization']['status'] == 'Required'
    assert '95886' in evidence['authorization']['reason']
    assert evidence['referral']['status'] == 'Verification required'


def test_verified_authorization_applies_to_the_required_cpt_only():
    pdf = contract([HEADERS, row('99213', 'Choice PPO', 'Not required', 'Not required'),
                    row('95910', 'Choice PPO', 'Not required', 'Required')])
    result = estimate('99213', '95910')
    result['price_range']['lines'][1]['benefits']['requirements']['authorization'] = {
        'status': 'valid', 'required': 'required', 'cleared': True,
        'reference': 'approval-123', 'verified_at': '2026-10-12T12:00:00Z'}
    evidence = rules.evaluate(result, context(), [pdf])
    assert evidence['authorization']['status'] == 'Required'
    assert evidence['authorization']['approval_status'] == 'Verified'
    assert evidence['procedures'][1]['authorization']['approval_status'] == 'Verified'
    assert evidence['procedures'][1]['authorization']['approval_reference'] == 'approval-123'
    result['price_range']['lines'][1]['benefits']['requirements']['authorization'].pop('reference')
    unreferenced = rules.evaluate(result, context(), [pdf])
    assert unreferenced['authorization']['approval_status'] == 'Not verified'
    assert unreferenced['procedures'][1]['authorization']['approval_status'] == 'Not verified'
