"""Reprice saved eligibility after current contract evidence changes its network tier."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_finish import _payer_plan_pricing_source
from app.insurance.identity import patient_plan_applies


def guard_unresolved_telehealth(result: dict, case: dict) -> bool:
    if not _api.visit_estimate.telehealth_pos_unresolved(case['previsit_context'].get('inputs') or {}):
        return False
    price = result.get('price_range') or {}
    result['price_range'] = price
    price['service_issues'] = [*(price.get('service_issues') or []),
        'Confirm the patient telehealth location and obtain benefits for that place of service before collection.']
    price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                 allowed_min_cents=None, allowed_max_cents=None, plan_min_cents=None, plan_max_cents=None)
    for line in price.get('lines') or []:
        line.update(allowed_cents=None, unit_allowed_cents=None, calculation=None)
    for key in ('arrival_collection', 'planning_estimate', 'fee_proxy_estimate'):
        result.pop(key, None)
    return True


def guard_telehealth_guidance(result: dict, unresolved: bool) -> None:
    if unresolved and result.get('payment_mode') != 'cash':
        result['collection_guidance'].update(amount_cents=None, status='action_required',
            basis='missing_collection_inputs',
            reason='Confirm the patient telehealth location and obtain benefits for that place of service before collection.')


def _reprice_saved_new_network(result: dict, case: dict, practice_id: str, payer: dict | None) -> None:
    """Refresh per-CPT benefits after a saved 271 gains a supported network tier."""
    price = result.get('price_range') or {}
    price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                 allowed_min_cents=None, allowed_max_cents=None,
                 plan_min_cents=None, plan_max_cents=None)
    for line in price.get('lines') or []:
        line.update(benefits={}, calculation=None, allowed_cents=None, unit_allowed_cents=None)
    result['price_range'] = price
    result.pop('arrival_collection', None)
    result.pop('planning_estimate', None)
    if not payer or not case.get('previsit_raw_object_key') or not case.get('previsit_raw_sha256'):
        return
    try:
        raw = _api.object_store.get_object_bytes(case['previsit_raw_object_key'])
        if _api.hashlib.sha256(raw).hexdigest() != case['previsit_raw_sha256']:
            return
        payload = _api.json.loads(raw)
        context = case['previsit_context']
        inputs = context.get('inputs') or {}
        source = ((case.get('previsit_discovery') or {}).get('pricing_source')
                  or context.get('source') or {})
        plan = ((result.get('plan_details') or {}).get('stedi') or {})
        benefits = result.get('benefits') or {}
        identity = result.get('identity') or benefits.get('identity') or {}
        exact = {**_payer_plan_pricing_source(source, plan, benefits, identity,
                                             str(inputs.get('service_date') or '')),
                 'payer_plan_bound': patient_plan_applies(benefits, identity, str(inputs.get('service_date') or '')),
                 'payer_plan_names': [plan['identified_product']] if plan.get('identified_product') else plan.get('names') or [],
                 'payer_network_names': plan.get('network_names') or []}
        price, _ = _api._previsit_range(practice_id, inputs, exact, payer['id'],
                                       result.get('codes') or [], benefits, payload=payload,
                                       review=case.get('visit_review'),
                                       payer_route_id=context.get('payer_id'))
    except Exception:
        # A saved view must not fail when its protected response or current
        # clinic pricing configuration is temporarily unavailable.
        return
    result['price_range'] = price
