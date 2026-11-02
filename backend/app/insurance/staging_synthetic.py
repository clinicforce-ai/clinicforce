"""Invented October-pattern visits for staging-only administrator acceptance."""
from __future__ import annotations

from copy import deepcopy
import os

STAGING_NMCT_PRACTICE_ID = '66eadd87-37e3-43c8-b34a-9012a53de4d3'
FIXTURE_PRICING_SCOPE = {'place_of_service': '11', 'rendering_npi': 'synthetic-rendering',
                         'billing_npi': 'synthetic-billing', 'location_id': 'synthetic-office'}


def _scoped_fixture_result(result: dict) -> dict:
    # Older fake cases predate the full scope; complete only marked staging facts.
    result['pricing_scope'] = FIXTURE_PRICING_SCOPE.copy()
    for row in (result.get('temporary_rate_reference') or {}).get('lines') or []:
        row.update(FIXTURE_PRICING_SCOPE)
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
)


def visit(name: str) -> dict:
    """Return one complete synthetic saved result; no external service is used."""
    value = deepcopy(case(diagnostic=None if name == 'specialist_271_conflict' else
                          'copay' if name in {'office_and_diagnostic_day', 'covered_oop_limit'} else
                          'deductible'))
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
    return value
