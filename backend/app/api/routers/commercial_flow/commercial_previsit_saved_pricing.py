"""Reprice saved eligibility after current contract evidence changes its network tier."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_finish import _payer_plan_pricing_source
from app.api.routers.commercial_flow.commercial_previsit_rate_reference import refresh_saved_temporary_rates
from app.insurance import blue_cross_proxy, choice_plus_emg_proxy, group_contract_support, published_plan_benefits, uhc_dsnp_office_proxy
from app.insurance.identity import patient_plan_applies
from app.insurance.stedi_benefits.normalize import ambiguous_combined_zero_oop


def restore_saved_fep_standard_product(result: dict, case: dict) -> None:
    """Identify an older saved FEP response from its intact protected 271."""
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    if plan.get('identified_product') != 'FEP Blue Standard FEHB':
        if (plan.get('identified_product') or plan.get('names') != ['STANDARD']
                or not case.get('previsit_raw_object_key') or not case.get('previsit_raw_sha256')):
            return
        try:
            raw = _api.object_store.get_object_bytes(case['previsit_raw_object_key'])
            if _api.hashlib.sha256(raw).hexdigest() != case['previsit_raw_sha256']:
                return
            identified = _api.commercial.plan_details(_api.json.loads(raw))
        except Exception:
            return
        if (identified.get('identified_product') != 'FEP Blue Standard FEHB'
                or identified.get('names') != plan.get('names')):
            return
        plan = {**plan, **identified}
        result['plan_details']['stedi'] = plan
    benefits = result.get('benefits') or {}
    benefits['plan_details'] = {**(benefits.get('plan_details') or {}), **plan}
    service_date = str(((case.get('previsit_context') or {}).get('inputs') or {}).get('service_date') or '')
    identity = result.get('identity') or benefits.get('identity') or {}
    published_plan_benefits.apply_plan_terms(benefits, plan, service_date,
        patient_plan_applies(benefits, identity, service_date))


def saved_fep_standard_office_needs_reprice(result: dict) -> bool:
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    if plan.get('identified_product') != 'FEP Blue Standard FEHB':
        return False
    return any(
        str(line.get('code') or '') in {str(code) for code in (*range(99202, 99206), *range(99212, 99216))}
        and ((line.get('benefits') or {}).get('copay_cents') != 4000
             or (line.get('benefits') or {}).get('deductible_applies') is not False
             or (line.get('benefits') or {}).get('published_plan_rule') != published_plan_benefits.FEP_STANDARD_2026)
        for line in ((result.get('price_range') or {}).get('lines') or []))


def suppress_saved_ambiguous_combined_zero_oop(result: dict) -> None:
    benefits = result.get('benefits') or {}
    if not ambiguous_combined_zero_oop(benefits.get('reported_options') or []):
        return
    reason = ('The payer reports a $0 combined individual out-of-pocket limit while the '
              'in-network family balance is positive. Confirm the applicable office-visit limit with the payer.')
    benefits.update(oop_annual_cents=None, oop_remaining_cents=None)
    reasons = benefits.setdefault('review_reasons', [])
    if reason not in reasons:
        reasons.append(reason)
    price = result.get('price_range') or {}
    for line in price.get('lines') or []:
        terms = line.get('benefits') or {}
        terms.update(oop_annual_cents=None, oop_remaining_cents=None)
        line['calculation'] = None
    if price:
        price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                     plan_min_cents=None, plan_max_cents=None,
                     collection={'action': 'rerun', 'amount_cents': None, 'reason': reason})
    result.pop('arrival_collection', None)
    result.pop('planning_estimate', None)


def upgrade_saved_biopsy_preview(result: dict, case: dict) -> None:
    """Project the new follow-up suggestion onto an unedited saved biopsy preview."""
    context = case.get('previsit_context') or {}
    inputs = context.get('inputs') or {}
    review = case.get('visit_review') or {}
    codes = result.get('codes') or []
    price = result.get('price_range') or {}
    available = result.get('available_codes') or []
    if ('selected_ids' in review or inputs.get('visit_type') != 'established'
            or not _api.re.search(r'\b(?:follow[ -]?up|f/u|fu)\b', str(inputs.get('reason') or ''), _api.re.I)
            or len(codes) != 1 or codes[0].get('code') != '11104'
            or price.get('mode') != 'services' or len(price.get('lines') or []) != 1
            or not any(row.get('code') == '99213' for row in available)
            or (result.get('service_plan') or {}).get('selection_source') == 'clinician'):
        return
    plan = _api.cpt_suggester.recommend_visit_plan(inputs, context.get('source') or {}, available)
    by_id = {row['id']: row for row in available}
    selected = [by_id[key] for key in plan.get('recommended_ids') or [] if key in by_id]
    if ([row['code'] for row in selected] != ['99213', '11104']
            or selected[1]['id'] != codes[0].get('id')):
        return
    office = {**selected[0], 'units': 1, 'modifiers': [], 'confirmed': False}
    biopsy_line = price['lines'][0]
    price['lines'] = [{'id': office['id'], 'code': '99213', 'units': 1, 'modifiers': [],
                       'benefits': {}, 'issues': ['Office CPT benefit terms require a refreshed payer check'],
                       'rate_resolution': {'status': 'MISSING'}}, biopsy_line]
    price.update(complete=False, allowed_min_cents=None, allowed_max_cents=None,
                 patient_min_cents=None, patient_max_cents=None)
    result['codes'] = [office, codes[0]]
    result['selected_ids'] = plan['recommended_ids']
    result['selected_service_count'] = 2
    result['service_plan'] = {**plan, 'selection_source': 'suggested'}


def refresh_saved_cigna_office_benefits(result: dict, case: dict, practice_id: str) -> None:
    """Reproject an older Cigna office check from its verified protected 271."""
    context = case.get('previsit_context') or {}
    inputs = context.get('inputs') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    old = result.get('benefits') or {}
    if (not _api.network_participation.is_nmct_practice(practice_id)
            or str(result.get('payer') or '').casefold() != 'cigna'
            or inputs.get('place_of_service') != '11'
            or old.get('active') is not True or old.get('copay_cents') is not None
            or old.get('coverage_status_conflict') or old.get('has_secondary')
            or old.get('source_errors') or result.get('payment_mode') == 'cash'
            or case.get('previsit', {}).get('request_state') != 'complete'
            or str(result.get('checked_at') or '')[:10] != inputs.get('service_date')
            or price.get('mode') != 'services' or price.get('complete') or len(lines) != 1
            or lines[0].get('code') not in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
            or lines[0].get('units') != 1
            or not case.get('previsit_raw_object_key') or not case.get('previsit_raw_sha256')):
        return
    try:
        raw = _api.object_store.get_object_bytes(case['previsit_raw_object_key'])
        if _api.hashlib.sha256(raw).hexdigest() != case['previsit_raw_sha256']:
            return
        payload = _api.json.loads(raw)
        source = context.get('source') or {}
        fresh = _api._case_benefits(payload, inputs, source, case)
        if (fresh.get('active') is not True or fresh.get('coverage_status_conflict')
                or fresh.get('source_errors') or fresh.get('copay_cents') is None
                or not patient_plan_applies(fresh, fresh.get('identity') or {}, inputs['service_date'])):
            return
        selected = [code for code in result.get('codes') or [] if code.get('id') == lines[0].get('id')]
        if len(selected) != 1:
            return
        rules = {'office_service_types': _api._office_service_types(practice_id, source),
                 **_api._benefit_context(inputs, source)}
        repriced = _api.visit_estimate.price_lines(selected, [], fresh, rules, payload=payload,
            service_date=inputs['service_date'], reviews=(case.get('visit_review') or {}).get('lines'))
        new_lines = (repriced or {}).get('lines') or []
        if (len(new_lines) != 1 or new_lines[0].get('code') != lines[0].get('code')
                or (new_lines[0].get('benefits') or {}).get('copay_cents') is None):
            return
    except Exception:
        # Missing or changed protected evidence cannot create a collection amount.
        return
    result['benefits'] = fresh
    result['identity'] = fresh.get('identity') or {}
    lines[0]['benefits'] = new_lines[0]['benefits']
    lines[0]['calculation'] = None
    result.setdefault('appointment_context', {})['place_of_service'] = inputs['place_of_service']


def restore_active_blue_cross_insurance(result: dict, case: dict) -> None:
    if ((result.get('benefits') or {}).get('active') is True
            and result.get('cash_fallback') is not None
            and (case.get('visit_review') or {}).get('payment_mode') != 'cash'
            and _api.re.search(r'blue cross.*blue shield|bcbstx', result.get('payer') or '', _api.re.I)):
        result['payment_mode'] = 'insurance'
        result.pop('cash_fallback', None)


def retain_conflicted_insurance(result: dict, case: dict) -> bool:
    """Keep conflicting payer coverage in insured review, including old cash saves."""
    conflict = bool((result.get('benefits') or {}).get('coverage_status_conflict'))
    if conflict and result.get('cash_fallback') is not None and (case.get('visit_review') or {}).get('payment_mode') != 'cash':
        result.update(payment_mode='insurance')
        result.pop('cash_fallback', None)
    return conflict


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
                                       payer_route_id=context.get('payer_id'),
                                       network_evidence=result.get('network_evidence'))
    except Exception:
        # A saved view must not fail when its protected response or current
        # clinic pricing configuration is temporarily unavailable.
        return
    result['price_range'] = price


def refresh_saved_network_pricing(result: dict, case: dict, practice_id: str,
                                  route_payer: dict | None, prior_network: tuple) -> None:
    benefits = result.get('benefits') or {}
    current_network = (benefits.get('network_status'), benefits.get('network_source'))
    network_changed = (prior_network != current_network and current_network[1] in {
        'clinic_group_agreement', 'clinic_nonparticipation_rule', 'clinic_attested_phcs_roster'})
    if network_changed or saved_fep_standard_office_needs_reprice(result):
        payer_id = str((case.get('previsit_context') or {}).get('payer_id') or '')
        route = group_contract_support.eligibility_route(result, payer_id, route_payer)
        _reprice_saved_new_network(result, case, practice_id, route)


def refresh_saved_cross_payer_planning(result: dict, case: dict, practice_id: str, route_payer: dict | None) -> None:
    """Rebuild a saved visit's current fee references and conditional target."""
    for key in ('fee_proxy_estimate', 'temporary_rate_reference', 'aetna_diagnostic_reference',
                'provisional_collection_target'):
        result.pop(key, None)
    context = case['previsit_context']
    result['visit_type'] = (context.get('inputs') or {}).get('visit_type')
    refresh_saved_temporary_rates(result, practice_id, context.get('inputs') or {},
                                  context.get('source') or {}, route_payer)
    result.pop('cigna_office_fee_scenarios', None)
    if office_scenarios := blue_cross_proxy.cigna_office_fee_scenarios(result, str(result.get('service_date') or '')):
        result['cigna_office_fee_scenarios'] = office_scenarios
    if proxy := (choice_plus_emg_proxy.choice_plus_emg_estimate(result, str(result.get('service_date') or ''))
                 or blue_cross_proxy.estimate(result, str(result.get('service_date') or ''))
                 or uhc_dsnp_office_proxy.estimate(result, str(result.get('service_date') or ''))
                 or blue_cross_proxy.cigna_estimate(result, str(result.get('service_date') or ''))
                 or blue_cross_proxy.cigna_oap_family_office_estimate(result, str(result.get('service_date') or ''))
                 or blue_cross_proxy.cigna_oap_office_scenario(result, str(result.get('service_date') or ''))):
        result['fee_proxy_estimate'] = proxy
    if target := blue_cross_proxy.biopsy_collection_target(result, str(result.get('service_date') or '')):
        result['provisional_collection_target'] = target
