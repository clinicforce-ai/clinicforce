"""Commercial review routes workflow."""
from __future__ import annotations

from uuid import uuid4

from app.api.routers import commercial_estimator as _api
from app.insurance import botox_review, cpt_rules
from app.api.routers.commercial_flow.commercial_previsit_requirements import check_cpt_authorization

@_api.router.post("/cases/{case_id}/visit-review")
async def update_visit_review(case_id: str, value: _api.VisitReviewInput, request: _api.Request) -> dict:
    """Reprice a saved visit after service, payment or evidence review.

    A clinician can select a named cash package from the active schedule or
    approve a visit-only cash price for one selected CPT 99213 follow-up;
    included care is priced once and source CPT examples are never auto-selected.
    A verified payment is deducted once from the displayed cash or insured
    amount. The returned and saved payment_explanation describes only the
    known amount; unpriced CPTs stay unresolved. An administrator can record
    or remove a payer-supplied out-of-network allowance for one selected CPT.
    The quote needs its payer channel, reference and validity date, and is
    bound to this visit's patient, payer product, rendering NPI, place and units.
    Product mappings for local or regional Medicare Advantage PPO products
    require their own exact-product provider-network record and applicable
    Clinical Library agreement; a general UHC PPO agreement is insufficient.
    An administrator can record current, patient-bound QMB evidence for an
    identified Medicare Advantage PPO product. Unknown QMB status never
    authorizes a positive Medicare cost-sharing advance.
    Benefit verification requires saved payer rows for a compatible service
    category or the exact procedure; unrelated broad categories are rejected.
    Selected CPTs also receive scoped Stedi CPT and place-of-service inquiries
    when the active member and network are bound. Cigna uses one inquiry per
    CPT; matching financial terms may support a separate provisional BCBS
    Texas fee-reference share without authorizing collection.
    A saved past visit may reuse its dated payer response after 24 hours;
    current and future visits still need a fresh benefit response.
    A changed eCW visit returns only the changed context category names, never
    patient or coverage values.
    Changed clinical details may be refreshed explicitly when all patient,
    coverage, provider, location and date context remains identical. The
    refreshed CPT plan requires a new clinician selection before collection.
    Staff may mark a distinct office assessment planned with EMG/NCS. With
    supported prior office history, this previews established CPT 99213 for
    cost planning; the clinician confirms the final E/M level and modifier.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found. Check this visit first.')
    if case.get('revision', 0) != value.revision:
        raise _api.HTTPException(409, 'This estimate changed in another session. Check this visit again before saving.')
    context = case['previsit_context']
    not_requested = case.get('previsit', {}).get('request_state') == 'not_requested'
    failed = not_requested or case.get('previsit', {}).get('request_state') == 'rejected'
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        raise _api.HTTPException(409, 'Coverage recovery is still in progress. Wait for the result before changing the estimate.')
    if failed and (value.verification or value.payments_received_cents is not None or value.identity_confirmation
                   or value.billing_protection
                   or value.product_mapping or value.payer_allowance or value.clear_payer_allowance_line_id):
        raise _api.HTTPException(409, 'Complete the eligibility check before verifying benefits or recording payments against an insurance estimate.')
    try:
        checked = _api.datetime.now(_api.timezone.utc) if failed else _api.datetime.fromisoformat(case['previsit']['checked_at'])
        historical_visit = context['inputs']['service_date'] < _api.date.today().isoformat()
        if not failed and not historical_visit and checked + _api.timedelta(hours=24) <= _api.datetime.now(_api.timezone.utc):
            raise ValueError('expired')
    except (KeyError, ValueError, TypeError):
        raise _api.HTTPException(409, 'The benefit response is stale. Check this visit again before saving verification.') from None
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(_api._ecw_identity(session, context['inputs']['service_date']),
            _api._ecw_practice_config(session['practice_id']), context['source']['appointment_id'],
            review_service_date=context['inputs']['service_date'])
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc
    library_documents = await _api.run_in_threadpool(_api.library_pricing.prepare, session['practice_id'])
    session['_library_documents'] = library_documents
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'), 'summary': row.get('extraction_summary')} for row in library_documents]
    if value.context_token != context['context_token']:
        raise _api.HTTPException(409, 'The appointment, coverage or services changed in eCW. Check the visit again.')
    changed = _api._visit_context_changes(context['inputs'], context['source'], inputs, source)
    refreshable = _api._clinical_context_refreshable(changed)
    refreshing = bool(value.refresh_context and refreshable)
    if changed and not refreshing:
        categories = ', '.join(changed) if changed else 'visit context'
        action = ('Refresh saved visit details and confirm the planned services.'
                  if refreshable else 'Check the visit again.')
        raise _api.HTTPException(409, f'The eCW visit changed ({categories}). {action}')
    if refreshing and value.model_fields_set - {'context_token', 'revision', 'refresh_context'}:
        raise _api.HTTPException(422, 'Refresh the changed visit details before selecting or verifying services.')
    saved_eligibility = (context.get('source') or {}).get('saved_eligibility')
    if saved_eligibility:
        source['saved_eligibility'] = saved_eligibility
        if saved_eligibility.get('checked_at') and _api.datetime.fromisoformat(saved_eligibility['checked_at']) + _api.timedelta(hours=24) <= _api.datetime.now(_api.timezone.utc):
            source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW verification is older than 24 hours. Use the current Stedi check.'}
    try:
        payload = {} if failed else _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, case['previsit_raw_object_key']))
    except Exception:
        raise _api.HTTPException(503, 'Protected payer evidence could not be read. Retry before saving verification.') from None
    import copy
    result = copy.deepcopy(context['result'])
    review = {'context_token': _api._visit_context_token(inputs, source)} if refreshing else copy.deepcopy(case.get('visit_review') or {'context_token': value.context_token})
    if refreshing:
        case['visit_review'] = review
        result.pop('suggestions', None)
        if inputs.get('reason'):
            result['suggestions'] = await _api.run_in_threadpool(
                _api.cpt_suggester.suggest_preliminary_cpt_codes,
                str(inputs['reason'])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
                source.get('recent_completed_appointments'))
        result['refresh_requires_selection'] = True
    if value.identity_confirmation:
        current_identity = _api._case_benefits(payload, inputs, source, case).get('identity') or {}
        if not current_identity.get('can_confirm') or current_identity.get('binding') != value.identity_confirmation.binding:
            raise _api.HTTPException(409, 'Patient identity evidence changed, or the birth date/member identity needs correction at its source.')
        review['identity_confirmation'] = {**value.identity_confirmation.model_dump(), 'verified_at': _api._now(),
                                           'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name')}
    if value.billing_protection:
        _api._admin(session)
        current = _api._case_benefits(payload, inputs, source, case)
        identity = current.get('identity') or {}
        plan = _api.commercial.plan_details(payload)
        product = _api.payer_allowance.product_binding(plan)
        from app.insurance.library_network_exclusions import product_kind
        if (current.get('active') is not True or current.get('service_date_matches') is False
                or not _api.insurance_identity.patient_benefits_bound(identity)
                or len(plan['names']) != 1 or not product_kind(plan['names'][0]) or not product):
            raise _api.HTTPException(422, 'Bind one active Medicare Advantage PPO medical product and this patient before recording QMB status.')
        review['billing_protection'] = {**value.billing_protection.model_dump(),
            'identity_binding': identity['binding'], 'product_binding': product,
            'payer_id': context['payer_id'], 'service_date': inputs['service_date'],
            'verified_at': _api._now(), 'actor_id': session['staff']['id']}
    available = {row['id']: row for row in result.get('available_codes', result['codes'])}
    if value.botox is not None:
        if not botox_review.is_botox_visit(inputs) and not any(
                row['code'] in botox_review.SELECTABLE_CODES for row in available.values()):
            raise _api.HTTPException(422, 'This visit has no Botox procedure or drug candidates to review.')
        if review.get('botox') != value.botox.model_dump(mode='json', exclude_none=True):
            review['botox'] = value.botox.model_dump(mode='json', exclude_none=True)
            review.pop('lines', None)
            review.pop('payer_allowances', None)
            review.pop('payments', None)
    if value.selected_ids is not None:
        result.pop('refresh_requires_selection', None)
        if not value.selected_ids or len(set(value.selected_ids)) != len(value.selected_ids) or set(value.selected_ids) - available.keys():
            raise _api.HTTPException(422, 'Select valid planned services from this visit.')
        visit_levels = [available[key] for key in value.selected_ids if available[key]['code'].isdigit() and 99202 <= int(available[key]['code']) <= 99215]
        if len(visit_levels) > 1:
            raise _api.HTTPException(422, 'Select one office visit level; alternatives cannot be added together.')
        selection_issues = _api.cpt_rules.service_selection_issues([available[key]['code'] for key in value.selected_ids])
        if selection_issues:
            raise _api.HTTPException(422, selection_issues[0])
        # Pricing follows the clinical procedure sequence, never a user-entered
        # billing order. Preserve source order within each procedure group.
        group_order = [group['id'] for group in _api.visit_estimate.service_plan(list(available.values()), inputs, source)['groups']]
        review['selected_ids'] = sorted(value.selected_ids, key=lambda key: (group_order.index(_api.visit_estimate.clinical_group(available[key]['code'])), list(available).index(key)))
        review['confirmed_modifiers'] = {key: codes for key, codes in (review.get('confirmed_modifiers') or {}).items()
                                         if key in review['selected_ids']}
        for line_id in list(review.get('payer_allowances') or {}):
            if line_id not in review['selected_ids']:
                review['payer_allowances'].pop(line_id, None)
        review.pop('payments', None)
    if value.emg_required is not None:
        review['emg_required'] = value.emg_required
        if value.emg_required is False and review.get('selected_ids'):
            keep = [line_id for line_id in review['selected_ids']
                    if _api.visit_estimate.clinical_group(available[line_id]['code']) not in {'nerve', 'muscle'}]
            if keep:
                review['selected_ids'] = keep
            else:
                review.pop('selected_ids', None)
            for key in ('payer_allowances', 'confirmed_units'):
                review[key] = {line_id: stored for line_id, stored in (review.get(key) or {}).items()
                               if line_id in keep}
            review.pop('payments', None)
    if value.separate_em_planned is not None:
        review['separate_em_planned'] = value.separate_em_planned
        if not value.separate_em_planned and review.get('selected_ids'):
            review['selected_ids'] = [line_id for line_id in review['selected_ids']
                                      if _api.visit_estimate.clinical_group(available[line_id]['code']) != 'visit']
            if not review['selected_ids']:
                review.pop('selected_ids')
            review.pop('payments', None)
    if value.insurance_check_from is not None:
        review['insurance_check_from'] = value.insurance_check_from.isoformat()
    if value.payment_mode is not None:
        review['payment_mode'] = value.payment_mode
    if value.cash_selection is None and (value.selected_ids is not None or value.confirmed_units is not None):
        review['cash_selection'] = ''
    if value.cash_selection is not None:
        packages = {row['id'] for row in (result.get('cash_schedule') or {}).get('packages', [])}
        if value.cash_selection and value.cash_selection not in packages:
            raise _api.HTTPException(422, 'Choose a package from the active cash schedule for this visit.')
        review['cash_selection'] = value.cash_selection
        review.pop('payments', None)
    if value.confirmed_units is not None:
        if set(value.confirmed_units) - available.keys() or any(type(units) is not int or not 1 <= units <= 10000 for units in value.confirmed_units.values()):
            raise _api.HTTPException(422, 'Confirm whole-number service units between 1 and 10,000.')
        previous = review.get('confirmed_units') or {}
        for key, units in value.confirmed_units.items():
            if previous.get(key, available[key].get('units')) != units:
                review.setdefault('lines', {}).pop(key, None)
                (review.get('payer_allowances') or {}).pop(key, None)
        review['confirmed_units'] = {**previous, **value.confirmed_units}
        review.pop('payments', None)
    if value.confirmed_modifiers is not None:
        selected = set(review.get('selected_ids') or [])
        if any(key not in selected
               or len(modifiers) > 3 or any(not _api.re.fullmatch(r'[A-Z0-9]{2}', code) for code in modifiers)
               or len(set(modifiers)) != len(modifiers)
               for key, modifiers in value.confirmed_modifiers.items()):
            raise _api.HTTPException(422, 'Confirm up to three distinct two-character modifiers on each selected service line.')
        previous = review.get('confirmed_modifiers') or {}
        for key, modifiers in value.confirmed_modifiers.items():
            if previous.get(key, available[key].get('modifiers') or []) != modifiers:
                review.setdefault('lines', {}).pop(key, None)
                (review.get('payer_allowances') or {}).pop(key, None)
        review['confirmed_modifiers'] = {**previous, **value.confirmed_modifiers}
        review.pop('payments', None)
    if any(item is not None for item in (value.selected_ids, value.confirmed_units, value.confirmed_modifiers)):
        review.pop('cash_visit_quote', None)
    if value.cash_visit_quote and value.clear_cash_visit_quote:
        raise _api.HTTPException(422, 'Set or remove the visit cash quote, not both.')
    if value.clear_cash_visit_quote:
        review.pop('cash_visit_quote', None)
        review.pop('payments', None)
    if value.cash_visit_quote:
        selected = review.get('selected_ids') or []
        if review.get('payment_mode') != 'cash' or len(selected) != 1:
            raise _api.HTTPException(422, 'Select Cash and confirm only CPT 99213 before quoting this follow-up.')
        line = available.get(selected[0]) or {}
        units = (review.get('confirmed_units') or {}).get(selected[0], line.get('units'))
        modifiers = (review.get('confirmed_modifiers') or {}).get(selected[0], line.get('modifiers') or [])
        if line.get('code') != '99213' or units != 1 or modifiers:
            raise _api.HTTPException(422, 'This visit cash quote applies only to one unmodified CPT 99213.')
        reason = value.cash_visit_quote.reason.strip()
        if len(reason) < 3:
            raise _api.HTTPException(422, 'Enter a reason for the visit cash quote.')
        review['cash_visit_quote'] = {'line_id': selected[0], 'code': '99213', 'units': 1,
            'amount_cents': value.cash_visit_quote.amount_cents, 'reason': reason,
            'service_date': inputs['service_date'], 'context_token': value.context_token,
            'approved_at': _api._now(), 'actor_id': session['staff']['id']}
        review.pop('payments', None)
    if value.payer_allowance or value.clear_payer_allowance_line_id:
        _api._admin(session)
        if value.payer_allowance and value.clear_payer_allowance_line_id:
            raise _api.HTTPException(422, 'Record or remove one payer allowance at a time.')
        if value.clear_payer_allowance_line_id:
            if value.clear_payer_allowance_line_id not in available:
                raise _api.HTTPException(422, 'Choose a CPT from this visit.')
            (review.get('payer_allowances') or {}).pop(value.clear_payer_allowance_line_id, None)
        else:
            quote = value.payer_allowance
            line = available.get(quote.line_id)
            if not line or quote.line_id not in (review.get('selected_ids') or []):
                raise _api.HTTPException(422, 'Select this clinician-documented CPT before recording its payer allowance.')
            benefits = result.get('benefits') or {}
            identity = result.get('identity') or benefits.get('identity') or {}
            if (benefits.get('active') is not True or not _api.insurance_identity.patient_benefits_bound(identity)
                    or not _api.network_participation.verified_out_of_network(benefits)):
                raise _api.HTTPException(409, 'Match the member and establish this provider as out of network before recording its payer allowance.')
            scope = _api._benefit_context(inputs, source)
            provider_npi = str(scope.get('provider_npi') or '')
            pos = str(scope.get('place_of_service') or '')
            product_binding = _api.payer_allowance.product_binding(benefits.get('plan_details'))
            units = (review.get('confirmed_units') or {}).get(quote.line_id, line.get('units'))
            if not provider_npi or not pos or not context.get('payer_id') or not product_binding or type(units) is not int or units < 1:
                raise _api.HTTPException(422, 'Link the payer product, rendering provider NPI, place of service and CPT units first.')
            if quote.valid_through < _api.date.fromisoformat(inputs['service_date']):
                raise _api.HTTPException(422, 'The payer quote must cover the appointment date.')
            entry = {**quote.model_dump(mode='json'), 'id': 'payer-allowance:' + uuid4().hex,
                     'code': line['code'], 'modifiers': ','.join(line.get('modifiers') or []),
                     'units': units, 'payer_id': context['payer_id'],
                     'service_date': inputs['service_date'], 'provider_npi': provider_npi,
                     'place_of_service': pos, 'patient_binding': identity['binding'],
                     'product_binding': product_binding,
                     'verified_at': _api._now(), 'actor_id': session['staff']['id']}
            allowances = review.get('payer_allowances')
            if not isinstance(allowances, dict):
                allowances = {}
                review['payer_allowances'] = allowances
            allowances[quote.line_id] = entry
    if value.verification:
        item = value.verification
        if item.line_id not in available or not item.valid_from <= _api.date.fromisoformat(inputs['service_date']) <= item.valid_to:
            raise _api.HTTPException(422, 'Verification must match a service and include the appointment date.')
        units = (review.get('confirmed_units') or {}).get(item.line_id, available[item.line_id].get('units'))
        try:
            units = _api.commercial._integer(units, 'Service units')
        except _api.commercial.CommercialError:
            units = None
        if units is None or item.units < units:
            raise _api.HTTPException(422, 'Verification must cover the confirmed number of service units.')
        evidence = item.model_dump(mode='json')
        if item.kind == 'benefits':
            line_code = available[item.line_id]['code']
            definition = cpt_rules.service_definition(line_code)
            medical_line = definition['role'] == 'drug' or definition['family'] == 'chemodenervation'
            category = item.service_type_code or definition['service_type'] or '98'
            if medical_line and (not item.service_type_code or item.service_type_code in {'98', 'BQ', 'BY'}):
                raise _api.HTTPException(422, 'Choose a payer-confirmed medical benefit category; office-only terms cannot price a drug or injection procedure.')
            options = {row['id']: row for row in _api.commercial.reported_stedi_options(payload, category)}
            wanted = {'copay'} if item.cost_share == 'copay' else {'coinsurance'} if item.cost_share == 'coinsurance' else {'copay', 'coinsurance'}
            picked = [options[key] for key in item.selected_ids if key in options]
            if len(picked) != len(wanted) or {row['kind'] for row in picked} != wanted:
                raise _api.HTTPException(422, 'Select exactly the returned payer terms needed for this cost-share method.')
            if any(row.get('procedure_code') not in {None, '', line_code}
                    or category not in
                    str(row.get('service_type_code') or '').replace(' ', '').split(',') for row in picked):
                raise _api.HTTPException(422, 'The selected payer terms do not describe this service line and benefit category.')
            if medical_line and not cpt_rules.benefit_category_compatible(
                    line_code, category,
                    procedure_specific=bool(picked) and all(row.get('procedure_code') == line_code for row in picked)):
                raise _api.HTTPException(422, 'The selected payer category does not describe this drug or procedure. Verify its medical benefit or an exact procedure row.')
            if len(set(item.selected_ids)) != len(item.selected_ids):
                raise _api.HTTPException(422, 'Select each benefit term once.')
            if item.status != 'valid':
                raise _api.HTTPException(422, 'Benefit applicability must be verified before selecting terms.')
        evidence.update(verified_at=_api._now(), verified_by=session['staff'].get('display_name'), actor_id=session['staff']['id'])
        review.setdefault('lines', {}).setdefault(item.line_id, {})[item.kind] = evidence
        review.pop('payments', None)
    if value.payments_received_cents is not None:
        if not value.payment_reference:
            raise _api.HTTPException(422, 'Record the payment ledger reference, including when no payment was received.')
        review['payments'] = {'amount_cents': value.payments_received_cents, 'reference': value.payment_reference,
                              'verified_at': _api._now(), 'actor_id': session['staff']['id']}
    if value.product_mapping:
        _api._admin(session)
        item = value.product_mapping
        if not item.valid_from <= _api.date.fromisoformat(inputs['service_date']) <= item.valid_to:
            raise _api.HTTPException(422, 'The contract confirmation must include this appointment date.')
        products = _api.commercial.plan_details(payload)['names']
        if not products:
            raise _api.HTTPException(422, 'The payer did not return a product to map.')
        payer_connection, network, profile = _api._practice_path(session['practice_id'], inputs, {**source, 'payer_plan_names': []})
        if _api._excluded_ma_ppo_contract_mismatch(products, network, payer_connection):
            raise _api.HTTPException(422, 'Link a separate exact-product local or regional PPO agreement before mapping this payer product.')
        contract_product = item.contract_product or (products[0] if len(products) == 1 else '')
        if (not contract_product or not any(_api.commercial.plan_details(payload).get(key)
                                          for key in ('plan_number', 'group_number'))
                or (item.rate_plan_matcher and _api.insurance_identity.normalized(item.rate_plan_matcher) !=
                    _api.insurance_identity.normalized(network.get('plan_matcher')))
                or not _api._mapped_products_in_contract_scope([contract_product], network, profile)
                or not _api._network_contract_evidence(session['practice_id'], network, profile, inputs['service_date']).get('document_id')):
            raise _api.HTTPException(422, 'Confirm the member plan scope, contract rate plan and sourced exact-product profile in a current signed Library agreement before mapping this label.')
        await _api.run_in_threadpool(_api.commercial_store.save_product_mapping, session['practice_id'], profile['id'], products,
            {**item.model_dump(mode='json'), 'verified_at': _api._now(), 'actor_id': session['staff']['id'],
             'plan_scope': {key: _api.commercial.plan_details(payload).get(key) for key in ('plan_number', 'group_number')},
             'network_id': network['id'], 'network_reference': network['evidence_reference']})
    case.setdefault('visit_review_history', []).append({'revision': value.revision, 'context_token': value.context_token, 'changes': value.model_dump(mode='json', exclude_none=True, exclude={'refresh_context'} if not value.refresh_context else None), 'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name'), 'verified_at': _api._now()})
    case['visit_review'] = review
    if not failed:
        result['issues'] = []
    result['benefits'] = _api._case_benefits({} if failed else payload, inputs, source, case)
    recovery = case.get('previsit_discovery') or {}
    pricing_source = recovery.get('pricing_source') if recovery.get('outcome') == 'recovered' else None
    await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, None if failed else payload, session, context['payer_id'], pricing_source=pricing_source)
    if not failed:
        case = await check_cpt_authorization(_api, result, case, payload, inputs, session,
                                             _api.stedi_financial.credentials(session['practice_id']))
        if (case.get('cpt_authorization') or {}).get('state') == 'complete':
            await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, payload, session,
                                         context['payer_id'], pricing_source=pricing_source)
    if failed:
        result['response_reference'] = context['result'].get('response_reference')
    saved = await _api.run_in_threadpool(_api._save, case, session, 'visit-evidence-verified')
    if value.identity_confirmation:
        await _api.run_in_threadpool(_api.staff_store.save_insurance_identity, session['patient_id'], session['practice_id'], review['identity_confirmation'])
    return _api._previsit_result(saved, session['practice_id'])


@_api.router.get('/cases/{case_id}/financials')
async def previsit_financials(case_id: str, request: _api.Request) -> dict:
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    return _api._response(await _api.saved_visit_financials(case, session['practice_id']))


async def saved_visit_financials(case: dict, practice_id: str) -> dict:
    context = case['previsit_context']
    inputs, source = context['inputs'], context['source']
    billing_npi = (source.get('billing_provider') or {}).get('npi')
    networks = await _api.run_in_threadpool(_api.commercial_store.list_networks, practice_id)
    tins = {row['tin'] for row in networks if row.get('status') == 'verified' and row.get('billing_npi') == billing_npi}
    identity = {**_api._coverage_snapshot(inputs, source), 'billing_npi': billing_npi,
                'tin': next(iter(tins)) if len(tins) == 1 else None}
    identity['claim_identifiers'] = await _api.ecw_backend.fetch_claim_identifiers(source)
    return await _api.stedi_financial.fetch_patient_financials(practice_id, identity)


@_api.router.get("/history")
async def ecw_completed_appointments(request: _api.Request) -> dict:
    """List prior eCW visits and a matching saved estimate without recalculating."""
    session = await _api._session(request)
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        visits = await _api.ecw_backend.list_completed_appointments(
            _api._ecw_identity(session, _api.date.today().isoformat()), _api._ecw_practice_config(session["practice_id"]),
        )
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc
    workflows = session["intake"].get("workflows", {})
    case_ids = [workflows.get("commercial"), *(workflows.get(_api._commercial_slot(visit["id"])) for visit in visits)]
    saved_cases = await _api.run_in_threadpool(_api.store.get_cases, case_ids, session["patient_id"], session["share_manifest_id"], **_api._scope(session))
    by_appointment = {str(((case.get("commercial") or {}).get("ecw") or {}).get("appointment_id") or ""): case
                      for case in saved_cases if case.get("source_mode") == "commercial"}
    return {"visits": [_api._past_visit_with_saved_estimate(visit, by_appointment.get(visit["id"])) for visit in visits]}


@_api.router.post("/cases/from-ecw")
async def start_from_ecw(value: _api.EcwStartInput, request: _api.Request) -> _api.Any:
    """Create a practice-scoped estimate case from the selected eCW appointment only."""
    session = await _api._session(request)
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(_api._ecw_identity(session, _api.date.today().isoformat()), _api._ecw_practice_config(session["practice_id"]), value.appointment_id)
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc
    payer, network, profile = _api._practice_path(session["practice_id"], inputs, source)
    lines, line_tasks = _api._ecw_coded_lines(session["practice_id"], profile, network, inputs["service_date"], inputs)
    commercial_data = {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"],
                       "service_date": inputs["service_date"], "service_category": profile["service_category"], "lines": lines,
                       "evidence_id": None, "request_state": "not_requested"}
    conflicts = _api._ecw_conflicts({"commercial": commercial_data}, session, inputs, source)
    snapshot = _api._coverage_snapshot(inputs, source)
    workflow = {"source_mode": "commercial", "commercial": {**commercial_data, "ecw": _api._ecw_case_data(inputs, source, conflicts)},
                "ecw_coverage": snapshot,
                # An empty list means the automatic eCW → Stedi path may continue.
                # It is not a clinician confirmation step.
                "review_tasks": conflicts or line_tasks}
    binding = _api.hashlib.sha256(_api.json.dumps({"payer": payer["id"], "network": network["id"], "profile": profile["id"],
                                          "date": inputs["service_date"], "appointment": value.appointment_id,
                                          "coverage": snapshot, "lines": lines}, sort_keys=True).encode()).hexdigest()
    try:
        case = await _api.run_in_threadpool(_api.store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], workflow_slot=_api._commercial_slot(value.appointment_id), **_api._scope(session))
    except _api.store.StaffCaseConflict as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    if case.get("estimate"):
        evidence_id = case.get("commercial", {}).get("evidence_id")
        evidence = _api.commercial_store.evidence(session["practice_id"], evidence_id) if evidence_id else None
        try:
            current = bool(evidence and evidence.get("source") == "stedi" and evidence.get("expires_at")
                           and _api.datetime.fromisoformat(evidence["expires_at"]) > _api.datetime.now(_api.timezone.utc))
        except ValueError:
            current = False
        if not current:
            case["estimate"] = None
            case["review_tasks"] = ["The saved Stedi response is stale. Start a new visit intake before estimating again."]
            case = await _api.run_in_threadpool(_api._save, case, session, "stedi-evidence-expired")
    # Upgrade a case opened by the pre-autopilot screen.  This was a display-only
    # prompt, not a real condition requiring staff action.
    if case.get("review_tasks") == ["Ready to check Stedi eligibility using the eClinicalWorks-coded services."]:
        case["review_tasks"] = []
        case = await _api.run_in_threadpool(_api._save, case, session, "automatic-path-ready")
    return _api._response(_api._public(case))


@_api.router.post("/cases/{case_id}/ecw-intake")
async def read_ecw_appointment(case_id: str, request: _api.Request) -> _api.Any:
    """Read the scheduled appointment and fail closed on any card/eCW mismatch."""
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api._load, case_id, session)
    data = case["commercial"]
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(
            _api._ecw_identity(session, data["service_date"]),
            _api._ecw_practice(session["practice_id"], data["payer_connection_id"], data["provider_network_id"]),
        )
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc
    conflicts = _api._ecw_conflicts(case, session, inputs, source)
    data["ecw"] = _api._ecw_case_data(inputs, source, conflicts)
    case["review_tasks"] = (conflicts or [
        "Confirm eClinicalWorks-suggested CPT/HCPCS codes and units; they are not automatically selected.",
        "Run Stedi eligibility or record a staff-reviewed saved payer response.",
    ])
    return _api._response(_api._public(await _api.run_in_threadpool(_api._save, case, session, "ecw-appointment-read")))
