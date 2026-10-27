"""Resolve clinic and published CPT requirements only within their saved scope."""
from __future__ import annotations

from app.insurance.identity import generic_plan_label, payer_identity_verified
from app.insurance.requirements.evidence import _key


def decide(kind: str, result: dict, context: dict, codes: set[str], plans: list[str]) -> dict | None:
    benefits = result.get('benefits') or {}
    if (not codes or benefits.get('active') is not True
            or benefits.get('service_date_matches') is not True
            or benefits.get('network_status') not in {'in_network', 'out_of_network'}
            or not payer_identity_verified(result.get('identity') or benefits.get('identity'))
            or not result.get('benefit_source') or not context.get('member_id')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'):
        return None
    def product_key(plan: str) -> str:
        value = _key(plan)
        return 'choiceplus' if value in {'choiceplus', 'unitedhealthcarechoiceplus', 'uhcchoiceplus'} else value

    exact_plans = {product_key(plan) for plan in plans if plan and not generic_plan_label(plan)}
    if len(exact_plans) != 1:
        return None
    day = context.get('service_date') or ''
    names = {_key(name) for name in context.get('payer_names') or [] if name}
    column = 'referral_required' if kind == 'referral' else 'prior_authorization_required'
    applicable = [row for row in context.get('stored_rules') or []
                  if row['payer_key'] in names and row['product_key'] in exact_plans
                  and row['cpt'] in codes and row['network_tier'] == benefits['network_status']
                  and row['place_of_service'] == str(context.get('pos') or '')
                  and row['effective_from'] <= day
                  and (not row.get('effective_to') or row['effective_to'] >= day)]
    by_code = {code: [row for row in applicable if row['cpt'] == code] for code in codes}
    if any(len(rows) != 1 or rows[0][column] is None for rows in by_code.values()):
        return None
    required = any(rows[0][column] is True for rows in by_code.values())
    rows = [rows[0] for rows in by_code.values()]
    sources = list({(row['source_label'], row.get('source_url')): {
        'label': row['source_label'], 'url': row.get('source_url'),
        'source_type': 'stored_cpt_requirement'} for row in rows}.values())
    return {'status': 'Required' if required else 'Not required',
            'reason': 'The sourced CPT rule matches the verified payer product, network, setting and visit date.',
            'sources': sources,
            'applicable_scope': {'payer_product': next(iter(exact_plans)), 'codes': sorted(codes),
                                 'network_tier': benefits['network_status'],
                                 'place_of_service': context.get('pos'), 'service_date': day}}
