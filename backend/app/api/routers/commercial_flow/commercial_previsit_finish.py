"""Commercial previsit finish workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_catalog import build_catalog
from app.api.routers.commercial_flow.commercial_previsit_requirements import attach_requirements
from app.insurance.identity import patient_benefits_bound, patient_plan_applies
from app.insurance import blue_cross_proxy, botox_review, evidence_resolution, published_plan_benefits

def _visit_context_parts(inputs: dict, source: dict) -> dict:
    context = {'coverage': {k: v for k, v in _api._coverage_snapshot(inputs, source).items() if k != 'other_active_coverage'},
               'codes': _api._previsit_codes(inputs),
               'provider': inputs.get('rendering_provider'),
               'provider_evidence': {key: (source.get('rendering_provider') or {}).get(key) for key in ('npi', 'roles')},
               'diagnoses': inputs.get('diagnoses'), 'billing': source.get('billing_provider'),
               'reason': inputs.get('reason'), 'location': source.get('location_id'), 'pos': inputs.get('place_of_service'), 'date': inputs.get('service_date')}
    if inputs.get('telehealth_modality'):
        context['telehealth_modality'] = inputs['telehealth_modality']
    return context


def _visit_context_changes(saved_inputs: dict, saved_source: dict, current_inputs: dict, current_source: dict) -> list[str]:
    saved, current = _visit_context_parts(saved_inputs, saved_source), _visit_context_parts(current_inputs, current_source)
    return [key for key in dict.fromkeys([*saved, *current]) if saved.get(key) != current.get(key)]


def _clinical_context_refreshable(changed: list[str]) -> bool:
    return bool(changed) and set(changed) <= {'codes', 'diagnoses', 'reason'}


def _visit_context_token(inputs: dict, source: dict) -> str:
    context = _visit_context_parts(inputs, source)
    return _api.hashlib.sha256(_api.json.dumps(context, sort_keys=True).encode()).hexdigest()


def _payer_plan_pricing_source(source: dict, payer_plan: dict, benefits: dict,
                               comparison: dict, service_date: str = '') -> dict:
    """Use one matched 271 product when eCW has no product-specific plan label."""
    if (not _api.insurance_identity.generic_plan_label(source.get('plan')) or benefits.get('active') is not True
            or not (patient_plan_applies(benefits, comparison, service_date) if service_date
                    else benefits.get('service_date_matches') is True)
            or not patient_benefits_bound(comparison)):
        return source
    names = {}
    for name in payer_plan.get('names') or []:
        if isinstance(name, str) and name.strip() and not _api.insurance_identity.generic_plan_label(name):
            names.setdefault(_api.insurance_identity.normalized(name), name.strip())
    if len(names) != 1:
        return source
    name = next(iter(names.values()))
    return {**source, 'plan': name, 'pricing_plan_source': 'Stedi eligibility plan name'}


def _finish_previsit(result: dict, case: dict, inputs: dict, source: dict, payload: dict | None, session: dict, payer_id: str,
                     *, pricing_source: dict | None = None) -> None:
    token = _api._visit_context_token(inputs, source)
    review = case.get('visit_review') or {}
    if review.get('context_token') != token:
        review = {'context_token': token}
        case['visit_review'] = review
    planning_inputs = {**inputs, 'emg_required': review.get('emg_required'),
                       'separate_em_planned': review.get('separate_em_planned')}
    comparison = result['benefits'].get('identity') or {}
    remembered = ((session.get('intake') or {}).get('patient') or {}).get('insurance_identities') or {}
    evidence = review.get('identity_confirmation') or remembered.get(comparison.get('binding'))
    if (comparison.get('can_confirm') and evidence and evidence.get('binding') == comparison.get('binding')
            and evidence.get('reference') and evidence.get('actor_id') and evidence.get('verified_at')):
        comparison = {**comparison, 'status': 'reconciled', 'evidence': evidence, 'message': ''}
        result['benefits']['review_reasons'] = [r for r in result['benefits'].get('review_reasons', []) if 'identity' not in r]
        result['benefits']['requires_review'] = bool(result['benefits']['review_reasons'])
    result['library_pricing'] = session.get('library_pricing', case.get('library_pricing', []))
    case['library_pricing'] = result['library_pricing']
    result['benefit_source'] = (payload or {}).get('benefit_source') or ('Stedi' if result.get('checked_at') else None)
    result['payer_source'] = (pricing_source or source).get('payer_source')
    result['identity'] = comparison
    result['benefits']['identity'] = comparison
    result['eligibility_reconciliation'] = _api.visit_estimate.reconcile_eligibility(
        source.get('saved_eligibility') or {}, result['benefits'], result.get('checked_at'))
    if result['eligibility_reconciliation']['requires_review']:
        result['benefits'].setdefault('review_reasons', []).append(result['eligibility_reconciliation']['message'])
        result['benefits']['requires_review'] = True
    payer_plan = _api.commercial.plan_details(payload or {})
    payer_plan_bound = patient_plan_applies(result['benefits'], comparison, inputs['service_date'])
    published_plan_benefits.apply_plan_terms(result['benefits'], payer_plan,
                                             inputs['service_date'], payer_plan_bound)
    protection = review.get('billing_protection') or {}
    product_binding = _api.payer_allowance.product_binding(payer_plan)
    result['billing_protection'] = protection if (payer_plan_bound
        and protection.get('identity_binding') == comparison.get('binding')
        and protection.get('product_binding') == product_binding
        and protection.get('payer_id') == payer_id
        and protection.get('service_date') == inputs.get('service_date')) else {}
    product_names = ([payer_plan['identified_product']] if payer_plan_bound and payer_plan.get('identified_product')
                     else payer_plan['names'])
    exact_source = {**_payer_plan_pricing_source(pricing_source or source, payer_plan, result['benefits'],
                                                comparison, inputs['service_date']),
                    'payer_plan_names': product_names,
                    'payer_plan_bound': payer_plan_bound,
                    'payer_network_names': payer_plan['network_names'] if payer_plan_bound else [],
                    'payer_verification_party': payer_plan.get('verification_payer') if payer_plan_bound else None,
                    'payer_plan_scope': {key: payer_plan.get(key) for key in ('plan_number', 'group_number')}}
    payer_id, connections, codes, available, uploaded_cash = build_catalog(
        _api, result, session, planning_inputs, source, pricing_source, exact_source, payer_id, payload,
    )
    # Switching a planned procedure can replace a catalog row's ID while keeping its CPT.
    previous = {row['id']: row for row in result.get('available_codes') or []}
    current = {row['id']: row for row in available}
    for old_id in list(review.get('selected_ids') or []):
        if old_id in current or old_id not in previous:
            continue
        old = previous[old_id]
        matches = [row for row in available if row['code'] == old['code']
                   and (row.get('modifiers') or []) == (old.get('modifiers') or [])]
        if len(matches) != 1:
            continue
        new_id = matches[0]['id']
        review['selected_ids'] = [new_id if key == old_id else key for key in review['selected_ids']]
        for key in ('confirmed_units', 'confirmed_modifiers'):
            if old_id in (review.get(key) or {}):
                review[key][new_id] = review[key].pop(old_id)
        for key in ('lines', 'payer_allowances'):
            (review.get(key) or {}).pop(old_id, None)
    _api.commercial.discard_stale_directory_payer(result, payer_id)
    plan_key = _api.hashlib.sha256(_api.json.dumps([token, review.get('emg_required'), review.get('separate_em_planned'), source.get('recent_completed_appointments', []),
        [(row['id'], row.get('units')) for row in available]], sort_keys=True).encode()).hexdigest()
    prior_plan = case.get('previsit_plan') or {}
    if (prior_plan.get('key') != plan_key or prior_plan.get('plan', {}).get('version') != 16
            or prior_plan.get('plan', {}).get('method') == 'unavailable'):
        prior_plan = {'key': plan_key, 'plan': _api.cpt_suggester.recommend_visit_plan(
            planning_inputs, source, available)}
        case['previsit_plan'] = prior_plan
    result['service_plan'] = {**prior_plan['plan'], 'selection_source': 'clinician' if 'selected_ids' in review else 'suggested'}
    result['separate_em_planned'] = bool(review.get('separate_em_planned'))
    result['established_for_planning'] = _api.cpt_suggester.established_visit_for_planning(planning_inputs, source)
    auto_units = result['service_plan'].get('recommended_units') or {}
    units = {**auto_units, **(review.get('confirmed_units') or {})}
    result['available_codes'] = [{**row, 'units': units.get(row['id'], row.get('units'))} for row in available]
    result['billing_note'] = _api.ecw_billing.collection_note(source.get('appointment_fields') or [])
    note_self_pay = (result['billing_note'] or {}).get('kind') == 'self_pay'
    route_connections = [row for row in _api.commercial_store.list_connections(session['practice_id'])
                         if row.get('stedi_payer_id') == payer_id]
    route_payer = route_connections[0] if len(route_connections) == 1 else None
    united_healthcare = _api.commercial.is_united_healthcare_payer(
        result, {'displayName': route_payer.get('display_name')} if route_payer else None)
    # eCW self-pay fields do not authorize an automatic UHC insurance-to-cash switch.
    result['payment_mode'] = review.get('payment_mode',
        'insurance' if united_healthcare else
        'cash' if source.get('self_pay_recorded') or note_self_pay else 'insurance')
    result['ecw_saved_eligibility'] = {k: v for k, v in (source.get('saved_eligibility') or {}).items() if not k.startswith('_')}
    labels = {'refresh_context': 'Visit details', 'selected_ids': 'Services', 'confirmed_units': 'Service quantities', 'payment_mode': 'Payment method',
              'cash_selection': 'Cash package', 'cash_visit_quote': 'Visit cash quote',
              'clear_cash_visit_quote': 'Visit cash quote removed',
              'emg_required': 'EMG testing', 'insurance_check_from': 'Insurance checking date', 'verification': 'Benefit or approval evidence',
              'identity_confirmation': 'Patient identity', 'product_mapping': 'Plan mapping',
              'payer_allowance': 'Payer allowed amount',
              'billing_protection': 'Medicare billing protection',
              'clear_payer_allowance_line_id': 'Payer allowed amount removed',
              'payments_received_cents': 'Recorded payments'}
    labels['botox'] = 'Botox clinical review'
    result['audit_trail'] = [{'checked_at': entry.get('verified_at'), 'by': entry.get('verified_by') or 'Clinic staff',
                            'changes': [label for key, label in labels.items() if key in entry.get('changes', {})]}
                           for entry in case.get('visit_review_history', [])]
    result['referral_evidence'] = source.get('referral_evidence', [])
    result['appointment_context'] = {key: source.get(key) for key in ('location', 'rendering_provider', 'appointment_status')}
    result['appointment_context'].update(appointment_type=inputs.get('appointment_type'), imported_fields=source.get('appointment_fields', []),
                                         notes_status=source.get('appointment_notes_status', 'not_exposed'),
                                         timezone=source.get('timezone') or _api.os.getenv('COMMERCIAL_PRACTICE_TIMEZONE', 'America/New_York'))
    result['coverage_records'] = source.get('coverage_records', [])
    result['insurance_fallback'] = {key: value for key, value in source.get('insurance_fallback', {}).items() if key != 'source'}
    by_id = {row['id']: row for row in available}
    selected_ids = [key for key in review.get('selected_ids', result['service_plan']['recommended_ids'])
                    if key in by_id]
    if 'selected_ids' in review or selected_ids:
        codes = [by_id[key] for key in selected_ids]
    result['codes'] = [{**row, 'confirmed': 'selected_ids' in review, 'units': units.get(row['id'], row.get('units')),
                        'modifiers': (review.get('confirmed_modifiers') or {}).get(row['id'], row.get('modifiers') or [])} for row in codes]
    if botox_review.is_botox_visit(inputs) or any(row['code'] in botox_review.SELECTABLE_CODES for row in result['codes']):
        result['botox_review'] = review.get('botox') or {}
    result['service_selection_status'] = 'approved' if 'selected_ids' in review else 'preview'
    result['payer_allowances'] = {key: {field: quote.get(field) for field in
        ('unit_allowed_cents', 'channel', 'reference', 'valid_through', 'verified_at')}
        for key, quote in (review.get('payer_allowances') or {}).items() if isinstance(quote, dict)}
    if selected_ids:
        for row in result['codes']:
            if row.get('source') in {'conditional office E/M alternative', 'Contract service option'}:
                row['source'] = 'Anticipated service from appointment'
    result['patient_details'] = source.get('patient_details', {})
    result['insurance_details'] = {**_api._coverage_snapshot(inputs, source), 'record_status': source.get('coverage_record_status'), 'coverage_period': source.get('coverage_period', {})}
    result['insurance_details']['group_source'] = ('card_ocr' if 'group_number' in (result.get('card_check') or {}).get('fields_added', [])
                                                   else 'eCW Coverage' if result['insurance_details'].get('group_number') else None)
    recovered = case.get('previsit_discovery') or {}
    if recovered.get('outcome') == 'recovered' and recovered.get('identity_binding') == _api._recovery_binding(inputs, source):
        result['insurance_details'].update(recovered.get('verified_coverage') or {})
        result['insurance_details'].update(ecw_member_id=inputs.get('member_id'),
            member_source=recovered.get('evidence_source') or 'Stedi verified coverage',
            group_source=(recovered.get('evidence_source') or 'Stedi verified coverage') if (recovered.get('verified_coverage') or {}).get('group_number') else result['insurance_details'].get('group_source'))
    elif not inputs.get('member_id') and result['benefits'].get('active') is True:
        returned_identity = _api.stedi_discovery.candidate_identity(payload or {}, inputs)
        if returned_identity:
            result['insurance_details'].update(member_id=returned_identity['member_id'], ecw_member_id=None, member_source='Stedi eligibility')
    subscriber = (payload or {}).get('subscriber') or {}
    dependent = (payload or {}).get('dependent') or {}
    result['insurance_details'].update(payer_subscriber=_api.commercial.payer_person(subscriber),
        payer_patient=_api.commercial.payer_person(dependent or subscriber),
        payer_relationship=dependent.get('relationToSubscriber') or ('SELF' if subscriber else None),
        payer_coverage_period=((dependent or subscriber).get('dates') or {}).get('plan') or {})
    result.update(ecw_coverage_active=source.get('active_coverages', 0) > 0, calculated_at=_api._now(), context_token=token, revision=case.get('revision', 0), plan_details={
        'ecw': source.get('plan_details') or {'plan': [source['plan']] if source.get('plan') else []},
        'stedi': _api.commercial.plan_details(payload) if payload else {}}, ecw_retrieved_at=source.get('retrieved_at'),
        response_reference=(payload or {}).get('id'), selected_ids=selected_ids or [])
    cpt_check = case.get('cpt_authorization') or {}
    result['cpt_authorization'] = {key: cpt_check[key] for key in
        ('state', 'service_date', 'pos', 'network', 'patient_binding', 'decisions', 'financial_terms', 'check_id', 'check_ids')
        if key in cpt_check}
    if 'selected_ids' in review:
        result.pop('provisional_scenario', None)
    if 'selected_ids' not in review and not selected_ids and codes and all(row.get('source') in {
            'conditional office E/M alternative', 'conditional NCS alternative'} for row in codes):
        result['provisional_scenario'] = {
            'kind': 'ncs_study_count' if codes[0]['source'] == 'conditional NCS alternative' else 'office_level',
            'count': 1, 'candidate_ids': [row['id'] for row in codes],
            'basis': 'eCW booked EMG/NCS visit' if codes[0]['source'] == 'conditional NCS alternative'
                     else 'eCW booked office visit and patient status'}
    result['selected_service_count'] = len(selected_ids) if selected_ids else (
        (result.get('provisional_scenario') or {}).get('count') or 0)
    result.pop('network_evidence', None)
    result.pop('agreement_exclusion', None)
    result.pop('temporary_rate_reference', None)
    result.pop('pricing_assumptions', None)
    _api._attach_previsit_pricing(result, session['practice_id'], planning_inputs, exact_source, payer_id, result['benefits'], payload=payload,
                            review=review)
    clinical_issues = botox_review.clinical_issues(
        result['codes'], review, botox_visit=botox_review.is_botox_visit(inputs),
        completed=str(source.get('appointment_status') or '').lower() in {'finished', 'completed'},
    )
    if clinical_issues:
        price = result.get('price_range') or {}
        price['service_issues'] = list(dict.fromkeys([*(price.get('service_issues') or []), *clinical_issues]))
        price['complete'] = False
        price['patient_min_cents'] = price['patient_max_cents'] = None
        result['price_range'] = price
        result['issues'].extend(clinical_issues)
    result['arrival_collection'] = _api.visit_estimate.arrival_collection(result.get('price_range') or {}, result['benefits'], review)
    result['planning_estimate'] = (_api.visit_estimate.planning_estimate(result.get('price_range') or {}, result['benefits'], comparison)
                                   if result['arrival_collection']['amount_cents'] is None else None)
    if len(connections) == 1:
        try:
            _, network, profile = _api._previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']},
                                                {**exact_source, 'payer_plan_names': []})
            contract = _api._network_contract_evidence(session['practice_id'], network, profile, inputs['service_date'])
            if (network.get('network_name') and contract.get('document_id')
                    and not (payer_plan.get('identified_product') == 'FEP Blue Basic PSHB'
                             and 'fep' not in network['network_name'].casefold())):
                result['mapping_candidate'] = {'network': network['network_name'], 'products': exact_source['payer_plan_names'],
                                               'reference': network['evidence_reference'],
                                               'contract': contract}
            else:
                result.pop('mapping_candidate', None)
        except _api.HTTPException:
            result.pop('mapping_candidate', None)
    if uploaded_cash and not _api.visit_estimate.is_telehealth(planning_inputs):
        _api.practice_prices.apply(result, uploaded_cash, review)
    _api.practice_prices.apply_visit_quote(result, review)
    attach_requirements(_api, result, case, planning_inputs, source, session, connections, payer_id)
    result.pop('fee_proxy_estimate', None)
    if proxy := (blue_cross_proxy.estimate(result, inputs['service_date'])
                 or blue_cross_proxy.cigna_estimate(result, inputs['service_date'])
                 or blue_cross_proxy.cigna_oap_office_scenario(result, inputs['service_date'])):
        result['fee_proxy_estimate'] = proxy
    result['collection_guidance'] = _api.collection_guidance.build(result, review)
    result['combined_emg_plan'] = _api.visit_estimate.combined_emg_plan(result, review)
    result.pop('emg_option', None)
    if inputs.get('visit_type') == 'new' and result['payment_mode'] == 'insurance':
        emg_rates, emg_rules = [], {**_api._benefit_context(inputs, source),
            'office_service_types': _api._office_service_types(session['practice_id'], source)}
        if (review.get('emg_required') or any(row['code'] in _api.cpt_rules.NCS_CODES | _api.cpt_rules.EMG_CODES for row in result['codes'])) and len(connections) == 1:
            try:
                _, network, profile = _api._previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']}, exact_source)
                emg_rates = _api.commercial_store.matching_rates(session['practice_id'], profile, network, inputs['service_date'], None)
                emg_rules.update(profile['rules'])
            except (_api.HTTPException, _api.commercial.CommercialError):
                pass
        result['emg_option'] = _api.collection_guidance.emg_option(result, emg_rates, emg_rules, payload, inputs['service_date'], review)
    result['encounter_summary'] = _api.encounter_summary.build(result, review)
    result['evidence_resolution'] = evidence_resolution.build(result, source)
    guidance = result['collection_guidance']
    emg = result.get('emg_option') or {}
    price = result.get('price_range') or {}
    _api.collection_event('collection_calculated', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case.get('id'), stage='calculation',
                     outcome='amount_available' if guidance.get('amount_cents') is not None else 'review_required',
                     status=guidance.get('status'), basis=guidance.get('basis'), payment_mode=result.get('payment_mode'),
                     source='ecw_saved' if str(result.get('benefit_source') or '').lower().startswith('ecw') else
                     'stedi' if result.get('checked_at') else 'none',
                     emg_state='separate' if emg.get('requested') and not emg.get('included') else
                     'included' if emg.get('included') else 'none',
                     amount_cents=guidance.get('amount_cents'), emg_min_cents=emg.get('total_min_cents'),
                     emg_max_cents=emg.get('total_max_cents'), cash_min_cents=price.get('cash_min_cents'),
                     cash_max_cents=price.get('cash_max_cents'), count=len(result.get('codes') or []))
    if result.get('eligibility_timing', {}).get('status') == 'too_early':
        case.setdefault('visit_review', {'context_token': token}).setdefault('insurance_check_from', result['eligibility_timing']['check_from'])
    case['previsit_context'] = {'inputs': inputs, 'source': source, 'payer_id': payer_id,
                              'result': result, 'context_token': token}
