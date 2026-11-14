"""Published Choice Plus referral source and bounded member decision."""
from __future__ import annotations

from app.insurance import stedi_cpt_authorization
from app.insurance.identity import generic_plan_label, payer_identity_verified
from app.insurance.requirements.evidence import _key

UHC_TEXAS_CHOICE_PLUS_SOURCE = {
    'label': 'UnitedHealthcare Texas Choice Plus plan description',
    'url': 'https://www.uhcprovider.com/en/health-plans-by-state/texas-health-plans/tx-commercial-plans.html',
    'source_version': 'Texas commercial plan description, checked 2026-10-21',
    'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}


def _uhc_choice_plus_referral(result: dict, context: dict, plans: list[str]) -> dict | None:
    """Use the published Texas open-access rule for an identified Choice Plus member."""
    names = context.get('payer_names') or set()
    if not any(name in {'unitedhealthcare', 'uhc'} for name in names) or 'wellmed' in names:
        return None
    products = {_key(plan) for plan in plans if not generic_plan_label(plan)}
    if not products or not products <= {'choiceplus', 'poschoiceplus',
                                        'unitedhealthcarechoiceplus', 'uhcchoiceplus'}:
        return None
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (not '2026-01-01' <= str(context.get('service_date') or '') <= '2026-12-31'
            or not stedi_cpt_authorization.visit_coverage_bound(benefits, str(context.get('service_date') or ''))
            or not payer_identity_verified(identity)
            or not result.get('benefit_source') or not context.get('member_id')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or not result.get('codes')):
        return None
    return {'status': 'Not required',
            'reason': 'UnitedHealthcare Texas Choice Plus permits specialist care without a referral.',
            'sources': [{**UHC_TEXAS_CHOICE_PLUS_SOURCE, 'source_type': 'official_published_rule'}],
            'member_specific_check': False,
            'applicable_scope': {'payer_product': next(plan for plan in plans if not generic_plan_label(plan)),
                                 'codes': sorted(row['code'] for row in result['codes']),
                                 'units_by_code': {row['code']: row.get('units') for row in result['codes']},
                                 'diagnoses': context.get('diagnoses'),
                                 'rendering_npi': context.get('rendering_npi'),
                                 'billing_npi': context.get('billing_npi'), 'tin': context.get('tin'),
                                 'location_id': context.get('location_id'),
                                 'network_tier': benefits.get('network_status'), 'place_of_service': context.get('pos'),
                                 'service_date': context.get('service_date')}}
