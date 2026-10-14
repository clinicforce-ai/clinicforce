"""Commercial review routes workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

@_api.router.post("/cases/{case_id}/visit-review")
async def update_visit_review(case_id: str, value: _api.VisitReviewInput, request: _api.Request) -> dict:
    """Reprice a saved visit after service, payment or evidence review.

    A clinician can select a named cash package from the active schedule;
    included care is priced once and source CPT examples are never auto-selected.
    A verified payment is deducted once from the displayed cash or insured
    amount. The returned and saved payment_explanation describes only the
    known amount; unpriced CPTs stay unresolved.
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
    if failed and (value.verification or value.payments_received_cents is not None or value.identity_confirmation or value.product_mapping):
        raise _api.HTTPException(409, 'Complete the eligibility check before verifying benefits or recording payments against an insurance estimate.')
    try:
        checked = _api.datetime.now(_api.timezone.utc) if failed else _api.datetime.fromisoformat(case['previsit']['checked_at'])
        if not failed and checked + _api.timedelta(hours=24) <= _api.datetime.now(_api.timezone.utc):
            raise ValueError('expired')
    except (KeyError, ValueError, TypeError):
        raise _api.HTTPException(409, 'The benefit response is stale. Check this visit again before saving verification.') from None
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(_api._ecw_identity(session, context['inputs']['service_date']),
            _api._ecw_practice_config(session['practice_id']), context['source']['appointment_id'])
    except _api.ecw_backend.EcwBackendError as exc:
        raise _api.HTTPException(502, str(exc)) from exc
    library_documents = await _api.run_in_threadpool(_api.library_pricing.prepare, session['practice_id'])
    session['_library_documents'] = library_documents
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'), 'summary': row.get('extraction_summary')} for row in library_documents]
    if value.context_token != context['context_token'] or _api._visit_context_token(inputs, source) != value.context_token:
        raise _api.HTTPException(409, 'The appointment, coverage or services changed in eCW. Check the visit again.')
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
    review = copy.deepcopy(case.get('visit_review') or {'context_token': value.context_token})
    if value.identity_confirmation:
        current_identity = _api._case_benefits(payload, inputs, source, case).get('identity') or {}
        if not current_identity.get('can_confirm') or current_identity.get('binding') != value.identity_confirmation.binding:
            raise _api.HTTPException(409, 'Patient identity evidence changed, or the birth date/member identity needs correction at its source.')
        review['identity_confirmation'] = {**value.identity_confirmation.model_dump(), 'verified_at': _api._now(),
                                           'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name')}
    available = {row['id']: row for row in result.get('available_codes', result['codes'])}
    if value.selected_ids is not None:
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
        review.pop('payments', None)
    if value.emg_required is not None:
        review['emg_required'] = value.emg_required
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
        review['confirmed_units'] = {**previous, **value.confirmed_units}
        review.pop('payments', None)
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
            options = {row['id']: row for row in _api.commercial.reported_stedi_options(payload, '98')}
            wanted = {'copay'} if item.cost_share == 'copay' else {'coinsurance'} if item.cost_share == 'coinsurance' else {'copay', 'coinsurance'}
            picked = [options[key] for key in item.selected_ids if key in options]
            if len(picked) != len(wanted) or {row['kind'] for row in picked} != wanted:
                raise _api.HTTPException(422, 'Select exactly the returned payer terms needed for this cost-share method.')
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
        _, network, profile = _api._practice_path(session['practice_id'], inputs, {**source, 'payer_plan_names': []})
        await _api.run_in_threadpool(_api.commercial_store.save_product_mapping, session['practice_id'], profile['id'], products,
            {**item.model_dump(mode='json'), 'verified_at': _api._now(), 'actor_id': session['staff']['id'],
             'plan_scope': {key: _api.commercial.plan_details(payload).get(key) for key in ('plan_number', 'group_number')},
             'network_id': network['id'], 'network_reference': network['evidence_reference']})
    case.setdefault('visit_review_history', []).append({'revision': value.revision, 'context_token': value.context_token, 'changes': value.model_dump(mode='json', exclude_none=True), 'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name'), 'verified_at': _api._now()})
    case['visit_review'] = review
    if not failed:
        result['issues'] = []
    result['benefits'] = _api._case_benefits({} if failed else payload, inputs, source, case)
    recovery = case.get('previsit_discovery') or {}
    pricing_source = recovery.get('pricing_source') if recovery.get('outcome') == 'recovered' else None
    await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, None if failed else payload, session, context['payer_id'], pricing_source=pricing_source)
    if failed:
        result['response_reference'] = context['result'].get('response_reference')
    saved = await _api.run_in_threadpool(_api._save, case, session, 'visit-evidence-verified')
    if value.identity_confirmation:
        await _api.run_in_threadpool(_api.staff_store.save_insurance_identity, session['patient_id'], session['practice_id'], review['identity_confirmation'])
    return _api._previsit_result(saved)


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
