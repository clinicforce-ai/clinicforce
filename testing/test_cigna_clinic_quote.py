"""Synthetic Cigna clinic rates and deductible-first BCBS fallback."""

from copy import deepcopy

from app.insurance import cigna_clinic_quote, collection_guidance, cpt_rules


def visit(code='99203', fee=10117):
    binding = 'synthetic-current-member'
    benefit = {'active': True, 'service_date_matches': True,
               'network_status': 'in_network', 'network_source': 'payer_participation_message',
               'coverage_level': 'individual', 'deductible_remaining_cents': 335505,
               'oop_remaining_cents': 335505, 'coinsurance_percent': '0',
               'deductible_applies': True, 'referral_signal': 'not_required',
               'authorization_signal': 'not_required'}
    line = {'id': 'selected', 'code': code, 'units': 1, 'modifiers': [],
            'benefits': deepcopy(benefit), 'conditions': [], 'issues': []}
    return {'payer': 'Cigna', 'payment_mode': 'insurance', 'service_date': '2026-11-12',
            'codes': [{'id': 'selected', 'code': code, 'units': 1, 'modifiers': []}],
            'selected_ids': ['selected'], 'service_selection_status': 'approved',
            'identity': {'status': 'matched', 'binding': binding, 'match_version': 2},
            'benefits': benefit, 'price_range': {'mode': 'services', 'lines': [line],
                                                'service_issues': [], 'conditional': False},
            'temporary_rate_reference': {'complete': True, 'source': 'BCBS Texas clinic schedule',
                'lines': [{'id': 'selected', 'code': code, 'units': 1, 'modifiers': [],
                           'unit_reference_cents': fee, 'source_reference': 'library:synthetic'}]},
            'cpt_authorization': {'state': 'complete', 'service_date': '2026-11-12',
                'pos': '11', 'network': 'in_network', 'patient_binding': binding}}


def test_deductible_precedes_zero_percent_coinsurance_and_collects_clinic_advance():
    result = visit()
    quote = cigna_clinic_quote.estimate(result, result['service_date'])
    assert quote['reference_total_cents'] == 10117
    assert quote['deductible_cents'] == quote['patient_share_cents'] == 10117
    assert quote['coinsurance_cents'] == 0
    assert quote['lines'][0]['source'] == 'BCBS Texas clinic fee proxy'
    result['fee_proxy_estimate'] = quote
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 10117
    assert guidance['basis'] == 'cigna_clinic_fee_quote'
    assert guidance['collection_rule']['mode'] == 'cigna_clinic_fee_quote'
    assert 'not a Cigna Cost of Care Estimator' in guidance['reason']


def test_clinic_cigna_rate_has_priority_and_oop_caps_share():
    result = visit('99204', 13066)
    result['cigna_clinic_rates'] = [{'code': '99204', 'modifier': '', 'allowed_cents': 15113,
                                    'source_reference': 'Clinic administrator supplied Cigna contract rates'}]
    quote = cigna_clinic_quote.estimate(result, result['service_date'])
    assert quote['reference_total_cents'] == quote['patient_share_cents'] == 15113
    assert quote['lines'][0]['source'] == 'Cigna clinic contract'
    result['benefits']['oop_remaining_cents'] = 5000
    result['price_range']['lines'][0]['benefits']['oop_remaining_cents'] = 5000
    assert cigna_clinic_quote.estimate(result, result['service_date'])['patient_share_cents'] == 5000


def test_mixed_cigna_and_blue_cross_lines_share_one_deductible_and_prior_payment():
    result = visit('99204', 13066)
    result['cigna_clinic_rates'] = [{'code': '99204', 'modifier': '', 'allowed_cents': 15113,
                                    'source_reference': 'Clinic administrator supplied Cigna contract rates'}]
    result['codes'].append({'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': []})
    result['price_range']['lines'].append({
        'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': [],
        'benefits': deepcopy(result['benefits']), 'conditions': [], 'issues': []})
    result['temporary_rate_reference']['lines'].append({
        'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': [],
        'unit_reference_cents': 3412, 'source_reference': 'library:synthetic'})
    result['selected_ids'].append('maneuver')
    quote = cigna_clinic_quote.estimate(result, result['service_date'])
    assert quote['reference_total_cents'] == quote['patient_share_cents'] == 18525
    assert quote['deductible_cents'] == 18525
    result['fee_proxy_estimate'] = quote
    guidance = collection_guidance.build(result, {'payments': {'reference': 'synthetic-ledger',
                                                               'amount_cents': 2500}})
    assert guidance['amount_cents'] == 16025


def test_confirmed_office_and_maneuver_use_clinic_blue_cross_fees():
    result = visit()
    result['reason'] = 'Dizziness, possible BPPV'
    reasons = ['Separate medical and supplementary coverage was returned; unassigned out-of-pocket rows are retained but cannot set this medical visit’s limit.',
               'Confirm the remaining individual deductible that applies to this visit.',
               'Confirm the remaining individual out-of-pocket maximum.',
               "Confirm the plan's referral requirement and, if required, that a referral is on file."]
    result['benefits'].update(deductible_remaining_cents=None, oop_remaining_cents=None,
        deductible_applies=False, reported_options=[
            {'kind': kind, 'service_type_code': '30', 'procedure_code': '',
             'coverage_level': 'Individual', 'network': 'Y', 'time_period': '29',
             'plan_association': 'unassigned', 'amount_cents': 335505,
             'payer_note': "Deductible does apply to member's out-of-pocket maximum" if kind == 'out_of_pocket' else ''}
            for kind in ('deductible', 'out_of_pocket')] + [
            {'id': 'preventive-office', 'kind': 'deductible', 'service_type_code': '98', 'network': 'Y',
             'amount_cents': 0, 'payer_note': 'Preventive', 'places_of_service': ['11']}])
    result['price_range']['lines'][0]['benefits'].update(
        deductible_remaining_cents=None, oop_remaining_cents=None,
        deductible_applies=False, deductible_waiver_ids=['preventive-office'],
        requires_review=True, review_reasons=reasons)
    result['codes'].append({'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': []})
    result['price_range']['lines'].append({
        'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': [],
        'benefits': deepcopy(result['price_range']['lines'][0]['benefits']), 'conditions': [], 'issues': []})
    result['temporary_rate_reference']['lines'].append({
        'id': 'maneuver', 'code': '95992', 'units': 1, 'modifiers': [],
        'unit_reference_cents': 3412,
        'source_reference': 'https://www.bcbstx.com/docs/provider/tx/standards/general-reimbursement-information/'
                            'gri-info/professional-schedules/2026-internet-posting-notices/other-codes-2026-files/dft-2026-v1.pdf'})
    result['selected_ids'].append('maneuver')
    quote = cigna_clinic_quote.estimate(result, result['service_date'])
    assert quote['reference_total_cents'] == quote['patient_share_cents'] == 13529
    assert quote['paired_general_balances'] is True
    assert quote['preventive_waiver_excluded'] is True
    result['fee_proxy_estimate'] = quote
    assert collection_guidance.build(result)['amount_cents'] == 13529
    result['benefits']['reported_options'].append({
        **result['benefits']['reported_options'][1], 'amount_cents': 1000})
    assert cigna_clinic_quote.estimate(result, result['service_date']) is None
    result['benefits']['reported_options'].pop()
    result['reason'] = 'Preventive wellness visit'
    assert cigna_clinic_quote.estimate(result, result['service_date']) is None


def test_missing_rate_or_deductible_exclusion_blocks_quote():
    result = visit()
    result['temporary_rate_reference']['lines'] = []
    assert cigna_clinic_quote.estimate(result, result['service_date']) is None
    result = visit()
    result['price_range']['lines'][0]['benefits']['deductible_applies'] = False
    assert cigna_clinic_quote.estimate(result, result['service_date']) is None
    result = visit()
    result['service_selection_status'] = 'preview'
    assert cigna_clinic_quote.estimate(result, result['service_date']) is None


def test_published_maneuver_reference_and_unsupported_visual_field_code():
    result = visit('95992', 3412)
    result['temporary_rate_reference']['lines'][0]['source_reference'] = (
        'https://www.bcbstx.com/docs/provider/tx/standards/general-reimbursement-information/'
        'gri-info/professional-schedules/2026-internet-posting-notices/other-codes-2026-files/dft-2026-v1.pdf')
    assert cigna_clinic_quote.estimate(result, result['service_date'])['patient_share_cents'] == 3412
    assert cpt_rules.service_selection_issues(['99203', '92084', '95992'])
