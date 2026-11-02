"""Invented October-pattern visits for staging-only administrator acceptance."""
from __future__ import annotations

from copy import deepcopy
import os

STAGING_NMCT_PRACTICE_ID = '66eadd87-37e3-43c8-b34a-9012a53de4d3'
FIXTURE_PRICING_SCOPE = {'place_of_service': '11', 'rendering_npi': 'synthetic-rendering',
                         'billing_npi': 'synthetic-billing', 'location_id': 'synthetic-office'}


def _scoped_fixture_result(result: dict) -> dict:
    # Older fake cases predate the full scope; complete only marked staging facts.
    scope = result.setdefault('pricing_scope', FIXTURE_PRICING_SCOPE.copy())
    for row in (result.get('temporary_rate_reference') or {}).get('lines') or []:
        for key, value in scope.items():
            row.setdefault(key, value)
    return result


def is_fixture(case: dict, practice_id: str | None) -> bool:
    return (case.get('synthetic_fixture') is True and practice_id == STAGING_NMCT_PRACTICE_ID
            and os.getenv('PUBLIC_BASE_URL', '').rstrip('/') == 'https://staging.clinicforce.ai'
            and os.getenv('COLLECTION_ENGINE_V2_ENABLED') == 'true')


def project_saved(case: dict, result: dict) -> dict:
    """Recalculate marked fake facts without a live payer or Library refresh."""
    from app.insurance import amount_explanation, canonical_evidence, collection_guidance, encounter_summary

    _scoped_fixture_result(result)
    result['revision'] = case.get('revision', 0)
    result['canonical_evidence'] = canonical_evidence.build(result, case.get('visit_review'))
    result['collection_guidance'] = collection_guidance.build(result, case.get('visit_review'))
    result['encounter_summary'] = encounter_summary.build(result, case.get('visit_review'))
    result['payment_explanation'] = amount_explanation.build(
        result, result['collection_guidance'], case.get('visit_review'))
    result['staging_synthetic_fixture'] = True
    return result


def case(*, office=4000, diagnostic='copay', oop=100000, approved=True):
    benefit = {'active': True, 'copay_cents': office, 'copay_period': 'VISIT',
               'deductible_applies': False, 'coinsurance_percent': '0',
               'oop_remaining_cents': oop, 'oop_id': 'individual-in',
               'selected_benefit_ids': ['specialist-office']}
    lines = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': [],
              'benefits': benefit, 'rate_resolution': {'status': 'RATE_UNAVAILABLE'}}]
    references = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': [],
                   'unit_reference_cents': 20000, 'source_reference': 'library:synthetic',
                   'reference_basis': 'BCBSTX_CROSS_PAYER_REFERENCE', 'rate_version': 'reference-v1',
                   'place_of_service': '11'}]
    if diagnostic:
        for index, (code, units, cents) in enumerate([('95908', 1, 10511), ('95886', 2, 8918)]):
            terms = ({'active': True, 'copay_cents': 7500, 'copay_period': 'DAY',
                      'deductible_applies': False, 'coinsurance_percent': '0',
                      'oop_remaining_cents': oop, 'oop_id': 'individual-in',
                      'selected_benefit_ids': ['diagnostic-day']}
                     if diagnostic == 'copay' else
                     {'active': True, 'copay_cents': None, 'deductible_applies': True,
                      'deductible_remaining_cents': 10000, 'deductible_id': 'individual-in',
                      'coinsurance_percent': '20', 'oop_remaining_cents': oop,
                      'oop_id': 'individual-in', 'selected_benefit_ids': ['diagnostic-share']})
            line_id = f'diagnostic-{index}'
            lines.append({'id': line_id, 'code': code, 'units': units, 'modifiers': [],
                          'benefits': terms, 'rate_resolution': {'status': 'RATE_UNAVAILABLE'}})
            references.append({'id': line_id, 'code': code, 'units': units, 'modifiers': [],
                               'unit_reference_cents': cents, 'source_reference': 'library:synthetic',
                               'reference_basis': 'BCBSTX_CROSS_PAYER_REFERENCE', 'rate_version': 'reference-v1',
                               'place_of_service': '11'})
    references = [{**row, **FIXTURE_PRICING_SCOPE} for row in references]
    policy = ({'mode': 'collect_visit_model', 'version': 1, 'id': 'synthetic-policy',
               'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01',
               'reference': 'synthetic approval', 'allow_benchmark_advance': True,
               'product_binding': 'synthetic-product', 'network_tier': 'in_network',
               'service_groups': ['specialist_office', *(['emg_ncs'] if diagnostic else [])],
               'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
               'max_cents': 100000, 'disclosure': 'Synthetic estimate for check-in.'}
              if approved else {})
    return {'payment_mode': 'insurance', 'price_range': {'mode': 'services', 'lines': lines},
            'temporary_rate_reference': {'lines': references},
            'pricing_scope': FIXTURE_PRICING_SCOPE.copy(),
            'benefits': {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract',
                         'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}},
            'collection_policy': policy, 'service_selection_status': 'approved',
            'payer_product_binding': 'synthetic-product', 'service_date': '2026-10-01',
            'place_of_service': '11',
            'visit_review': {'payments': {'amount_cents': 0, 'reference': 'synthetic-ledger-check'}},
            'arrival_collection': {'payments_received_cents': 0}}



SCENARIOS = (
    ('specialist_271_conflict', 8000, None, 'POLICY_APPROVAL_REQUIRED'),
    ('office_and_diagnostic_day', 11500, 11500, 'APPROVED_VISIT_ADVANCE'),
    ('office_and_emg_deductible', 17669, 17669, 'APPROVED_VISIT_ADVANCE'),
    ('covered_oop_limit', 2500, 2500, 'APPROVED_VISIT_ADVANCE'),
    ('cigna_reference_no_policy', 17669, None, 'POLICY_APPROVAL_REQUIRED'),
    ('aetna_draft_route', 17669, None, 'POLICY_APPROVAL_REQUIRED'),
    ('member_identity_conflict', None, None, 'EVIDENCE_REQUIRED'),
    ('missing_benchmark_cpt', None, None, 'EVIDENCE_REQUIRED'),
    ('office_copay_approved', 4000, 4000, 'APPROVED_VISIT_ADVANCE'),
    ('encounter_copay_once', 4000, 4000, 'APPROVED_VISIT_ADVANCE'),
    ('deductible_then_copay', 21500, 21500, 'APPROVED_VISIT_ADVANCE'),
    ('copay_plus_coinsurance', 15669, 15669, 'APPROVED_VISIT_ADVANCE'),
    ('copay_plus_deductible_coinsurance', 23169, 23169, 'APPROVED_VISIT_ADVANCE'),
    ('unknown_copay_interaction', None, None, 'EVIDENCE_REQUIRED'),
    ('unknown_deductible_remaining', None, None, 'EVIDENCE_REQUIRED'),
    ('unknown_oop_with_staff_note', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('family_oop_exhausted', 0, 0, 'DOCUMENTED_ZERO'),
    ('prior_visit_payment', 11500, 9500, 'APPROVED_VISIT_ADVANCE'),
    ('payment_verification_missing', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('secondary_coverage_unresolved', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('provider_network_conflict', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('policy_product_mismatch', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('excluded_diagnostic_cpt', 11500, None, 'POLICY_EXCLUSION'),
    ('protected_qmb', 4000, 0, 'PROHIBITED_COLLECTION'),
    ('authorization_denied', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('service_date_mismatch', None, None, 'EVIDENCE_REQUIRED'),
    ('telehealth_pos_mismatch', None, None, 'EVIDENCE_REQUIRED'),
    ('modifier_rate_missing', None, None, 'EVIDENCE_REQUIRED'),
    ('expired_reference_rate', None, None, 'EVIDENCE_REQUIRED'),
    ('cash_quote_complete', None, 5000, 'practice_cash_price'),
    ('cash_quote_missing', None, None, 'practice_cash_price'),
    ('approved_defer_to_claim', 11500, 0, 'DEFERRED_BILLING'),
    ('percentage_advance', 17669, 8835, 'APPROVED_VISIT_ADVANCE'),
    ('overlapping_policies', 11500, None, 'POLICY_CONFLICT'),
    ('diagnostic_selection_preview', 11500, None, 'POLICY_OR_EVIDENCE_REQUIRED'),
    ('skin_biopsy_day', 11500, 11500, 'APPROVED_VISIT_ADVANCE'),
    ('out_of_network_no_policy', 17669, None, 'POLICY_APPROVAL_REQUIRED'),
    ('noncovered_procedure', None, None, 'EVIDENCE_REQUIRED'),
)


def visit(name: str) -> dict:
    """Return one complete synthetic saved result; no external service is used."""
    office_only = {'specialist_271_conflict', 'office_copay_approved', 'protected_qmb',
                   'cash_quote_complete', 'cash_quote_missing', 'skin_biopsy_day'}
    diagnostic_copay = {'office_and_diagnostic_day', 'covered_oop_limit', 'encounter_copay_once',
        'unknown_oop_with_staff_note', 'family_oop_exhausted', 'prior_visit_payment',
        'payment_verification_missing', 'secondary_coverage_unresolved', 'provider_network_conflict',
        'policy_product_mismatch', 'excluded_diagnostic_cpt', 'authorization_denied',
        'approved_defer_to_claim', 'overlapping_policies', 'diagnostic_selection_preview',
        'noncovered_procedure', 'service_date_mismatch'}
    value = deepcopy(case(diagnostic=None if name in office_only else
                          'copay' if name in diagnostic_copay else 'deductible'))
    value['payer'] = ('Cigna Synthetic' if name == 'cigna_reference_no_policy' else
                      'Aetna Synthetic' if name == 'aetna_draft_route' else 'BCBSTX Synthetic')
    value['fixture_scenario'] = name
    _scoped_fixture_result(value)
    if name == 'specialist_271_conflict':
        office = value['price_range']['lines'][0]['benefits']
        office.update(copay_cents=8000, oop_remaining_cents=None,
                      selected_benefit_ids=['synthetic-specialist-98'])
        value['benefits']['reported_options'] = [
            {'id': 'synthetic-specialist-98', 'service': 'specialist_office', 'copay_cents': 8000,
             'coinsurance_percent': '0', 'deductible_remaining_cents': 0},
            {'id': 'synthetic-general-30', 'service': 'general_office', 'copay_cents': 4000,
             'coinsurance_percent': '30'},
        ]
        value['benefits']['oop_annual_cents'] = 500000
        value['staff_note'] = 'OOP met (unverified synthetic note)'
        value.pop('collection_policy')
    elif name == 'covered_oop_limit':
        for line in value['price_range']['lines']:
            line['benefits']['oop_remaining_cents'] = 2500
    elif name in {'cigna_reference_no_policy', 'aetna_draft_route'}:
        value.pop('collection_policy')
    elif name == 'member_identity_conflict':
        value['benefits']['identity'] = {'status': 'review', 'binding': 'synthetic-conflict'}
        value['identity'] = value['benefits']['identity']
    elif name == 'missing_benchmark_cpt':
        value['temporary_rate_reference']['lines'] = [
            line for line in value['temporary_rate_reference']['lines'] if line['code'] != '95886']
    elif name == 'encounter_copay_once':
        for line in value['price_range']['lines']:
            line['benefits'].update(copay_cents=4000, copay_period='VISIT', copay_id='one-encounter')
    elif name in {'deductible_then_copay', 'copay_plus_coinsurance',
                  'copay_plus_deductible_coinsurance'}:
        patterns = {
            'deductible_then_copay': ('DEDUCTIBLE_THEN_COPAY',
                {'copay_cents': 7500, 'copay_period': 'DAY', 'copay_after_deductible': True,
                 'coinsurance_percent': None}),
            'copay_plus_coinsurance': ('COPAY_PLUS_COINSURANCE',
                {'copay_cents': 7500, 'copay_period': 'DAY', 'deductible_remaining_cents': None,
                 'coinsurance_percent': '20', 'coinsurance_base': 'AFTER_COPAY'}),
            'copay_plus_deductible_coinsurance': ('COPAY_PLUS_DEDUCTIBLE_COINSURANCE',
                {'copay_cents': 7500, 'copay_period': 'DAY', 'copay_component_price_cents': 10000,
                 'coinsurance_percent': '20'}),
        }
        pattern, fields = patterns[name]
        for line in value['price_range']['lines'][1:]:
            line['benefits'].update(fields, interaction_rule=pattern,
                                    interaction_evidence_id='diagnostic-share')
    elif name in {'unknown_copay_interaction', 'unknown_deductible_remaining'}:
        for line in value['price_range']['lines'][1:]:
            line['benefits'][('coinsurance_percent' if name == 'unknown_copay_interaction'
                              else 'deductible_remaining_cents')] = None
    elif name in {'unknown_oop_with_staff_note', 'family_oop_exhausted'}:
        for line in value['price_range']['lines']:
            line['benefits']['oop_remaining_cents'] = None
        value['staff_note'] = 'Synthetic staff note: OOP met, not verified by payer.'
        if name == 'family_oop_exhausted':
            value['benefits'].update(family_oop_zero_applies=True, family_oop_remaining_cents=0)
    elif name == 'prior_visit_payment':
        value['visit_review']['payments'].update(amount_cents=2000, reference='synthetic-ledger-payment')
    elif name == 'payment_verification_missing':
        value['visit_review']['payments'].pop('reference')
    elif name == 'secondary_coverage_unresolved':
        value['benefits']['has_secondary'] = True
    elif name == 'provider_network_conflict':
        value['benefits']['network_conflict'] = True
    elif name == 'policy_product_mismatch':
        value['collection_policy']['product_binding'] = 'different-synthetic-product'
    elif name == 'excluded_diagnostic_cpt':
        value['collection_policy']['excluded_cpts'] = ['95908']
    elif name == 'protected_qmb':
        value['billing_protection'] = {'status': 'qmb'}
    elif name == 'authorization_denied':
        value['price_range']['lines'][1]['benefits']['requirements'] = {
            'authorization': {'required': 'required', 'status': 'denied'}}
    elif name == 'service_date_mismatch':
        value['benefits']['service_date_matches'] = False
    elif name == 'telehealth_pos_mismatch':
        value['pricing_scope']['place_of_service'] = value['place_of_service'] = '02'
    elif name == 'modifier_rate_missing':
        value['price_range']['lines'][-1]['modifiers'] = ['26']
    elif name == 'expired_reference_rate':
        value['temporary_rate_reference']['lines'][-1]['effective_to'] = '2026-09-30'
    elif name in {'cash_quote_complete', 'cash_quote_missing'}:
        value['payment_mode'] = 'cash'
        value['price_range'] = {'mode': 'cash', 'lines': [], 'cash_complete': name == 'cash_quote_complete',
                                'cash_min_cents': 5000 if name == 'cash_quote_complete' else None,
                                'cash_max_cents': 5000 if name == 'cash_quote_complete' else None}
        value['cash_quote'] = {'reason': 'Synthetic clinic cash schedule, separate from insurance.'}
    elif name == 'approved_defer_to_claim':
        value['collection_policy']['mode'] = 'defer_to_claim'
    elif name == 'percentage_advance':
        value['collection_policy']['advance_percent'] = 50
    elif name == 'overlapping_policies':
        value['visit_collection_policies'] = [
            {**value['collection_policy'], 'id': f'synthetic-policy-{index}'} for index in (1, 2)]
    elif name == 'diagnostic_selection_preview':
        value['service_selection_status'] = 'preview'
    elif name == 'skin_biopsy_day':
        for index, (code, cents) in enumerate((('11104', 10000), ('11105', 8000)), 1):
            line_id = f'biopsy-{index}'
            value['price_range']['lines'].append({'id': line_id, 'code': code, 'units': 1,
                'modifiers': [], 'rate_resolution': {'status': 'RATE_UNAVAILABLE'},
                'benefits': {'active': True, 'copay_cents': 7500, 'copay_period': 'DAY',
                    'deductible_applies': False, 'coinsurance_percent': '0',
                    'oop_remaining_cents': 100000, 'oop_id': 'individual-in',
                    'selected_benefit_ids': ['biopsy-day']}})
            value['temporary_rate_reference']['lines'].append({
                'id': line_id, 'code': code, 'units': 1, 'modifiers': [],
                'unit_reference_cents': cents, 'source_reference': 'library:synthetic',
                'reference_basis': 'BCBSTX_CROSS_PAYER_REFERENCE', 'rate_version': 'reference-v1',
                **FIXTURE_PRICING_SCOPE})
        value['collection_policy']['service_groups'] = ['specialist_office', 'skin_biopsy']
    elif name == 'out_of_network_no_policy':
        value['benefits'].update(network_status='out_of_network', network_source='clinic_nonparticipation_rule')
        value.pop('collection_policy')
    elif name == 'noncovered_procedure':
        value['price_range']['lines'][1]['benefits']['active'] = False
    return value


def journey(name: str) -> list[tuple[str, dict]]:
    """Invented successive saves for one fake visit; old fixture IDs stay stable."""
    final = visit(name)
    if name in {row[0] for row in SCENARIOS[:8]}:
        return [('estimated', final)]
    pending = visit('cash_quote_missing') if final['payment_mode'] == 'cash' else deepcopy(final)
    pending['fixture_scenario'] = name
    pending['benefits']['active'] = None
    pending['fixture_iteration'] = 'eligibility_pending'
    steps = [('eligibility-pending', pending)]
    if name in {'office_copay_approved', 'approved_defer_to_claim'}:
        provisional = deepcopy(final)
        provisional.pop('collection_policy', None)
        steps.append(('planning-only', provisional))
    elif name == 'encounter_copay_once':
        provisional = visit('office_and_diagnostic_day')
        provisional['fixture_scenario'] = name
        steps.append(('independent-copays', provisional))
    elif name == 'prior_visit_payment':
        provisional = deepcopy(final)
        provisional['visit_review']['payments']['amount_cents'] = 0
        steps.append(('before-payment', provisional))
    elif name == 'protected_qmb':
        provisional = deepcopy(final)
        provisional.pop('billing_protection', None)
        steps.append(('before-protection', provisional))
    elif name == 'cash_quote_complete':
        provisional = visit('cash_quote_missing')
        provisional['fixture_scenario'] = name
        steps.append(('cash-price-missing', provisional))
    elif name in {'expired_reference_rate', 'telehealth_pos_mismatch'}:
        provisional = visit('office_and_emg_deductible')
        provisional['fixture_scenario'] = name
        steps.append(('before-scope-change', provisional))
    final['fixture_iteration'] = 'final'
    return [*steps, ('estimated', final)]
