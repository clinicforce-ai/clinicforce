"""Commercial previsit routes workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

@_api.router.get('/cases/{case_id}/previsit')
async def saved_previsit(case_id: str, request: _api.Request) -> dict:
    """Read saved recovery without submitting or retrying a paid transaction.

    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND.
    NOT_FOUND means a completed search returned no policies, not uninsured.
    recovery.state separately reports pending/indeterminate transport state.
    Coverage verification does not imply that a price is available.

    Parseable candidates include protected member/subscriber/dependent details,
    nullable subscriber DOB, source and verification reason_codes. Only this
    authenticated patient/practice scope may read them. A completed legacy
    response may be reparsed and its diagnosis saved, without payer submission
    or automatic adoption. Historical snapshots are unchanged.

    insurance_diagnosis separates raw/retained/verified candidate counts,
    identityStatus, eligibilityStatus, pricingStatus, reasonCodes, states,
    pricingReasons and enrollment status. A completed missing insurance price
    selects separate cash pricing unless explicitly overridden; it never
    changes coverage verification or manufactures a missing cash amount.
    payment_explanation is recalculated from the saved result and remains one
    plain sentence with the network/pricing basis and any unresolved CPTs.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    recovery = case.get('previsit_discovery') or {}
    if (recovery.get('request_state') == 'complete' and recovery.get('outcome') in {'review_needed', 'no_match'}
            and recovery.get('raw_object_key') and recovery.get('candidate_version') != 4):
        await _api._reconcile_saved_discovery(case, session)
    return _api._previsit_result(case)


@_api.router.get('/cases/{case_id}/payer-response')
async def saved_payer_response(case_id: str, request: _api.Request):
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if (not case or case.get('source_mode') != 'commercial_previsit'
            or case.get('previsit', {}).get('request_state') not in {'complete', 'rejected'}
            or not case.get('previsit_raw_object_key')):
        raise _api.HTTPException(404, 'No completed payer response is available for this visit.')
    try:
        payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, case['previsit_raw_object_key']))
    except Exception as exc:
        raise _api.HTTPException(502, 'The saved payer response could not be loaded.') from exc
    return _api._response({'source': 'Stedi', 'response': payload})


@_api.router.post('/cases/{case_id}/payer-route')
async def select_payer_route(case_id: str, value: _api.PayerRouteInput, request: _api.Request,
                            background_tasks: _api.BackgroundTasks) -> dict:
    """Select a saved directory suggestion and run eligibility with known identity.

    Selection is not coverage verification. Stale, arbitrary and already-pending
    selections are rejected before any payer transaction. No eCW data is changed.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    context = case['previsit_context']
    if case.get('revision', 0) != value.revision or context['context_token'] != value.context_token:
        raise _api.HTTPException(409, 'This estimate changed. Reopen it before selecting a payer route.')
    recovery = case.get('previsit_discovery') or {}
    if _api.stedi_discovery.public_status(recovery)['state'] not in {'complete', 'rejected'} or recovery.get('outcome') == 'recovered':
        raise _api.HTTPException(409, 'Wait for the existing recovery request before selecting a payer route.')
    payer = next((row for row in recovery.get('payer_routes', []) if row['stediId'] == value.route_id), None)
    if not payer:
        raise _api.HTTPException(422, 'Select one of the payer routes returned for this policy.')
    inputs, source = context['inputs'], context['source']
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
        'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
        'billing_npi': str(billing.get('npi') or ''),
        'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    recovery.update(request_state='pending', outcome='review_needed', stage='eligibility',
        selected_route_id=value.route_id, selection_error=None, started_at=_api._now())
    case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-payer-route-selected')
    _api._recovery_event(case, session, 'payer_routing', 'selected', reason=payer.get('match_basis', 'name_or_alias'))
    background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider, selected_route=payer)
    return _api._previsit_result(case)


@_api.router.post('/cases/{case_id}/coverage-candidate')
async def select_coverage_candidate(case_id: str, value: _api.CoverageCandidateInput, request: _api.Request,
                                    background_tasks: _api.BackgroundTasks) -> dict:
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    if case.get('revision', 0) != value.revision or case['previsit_context'].get('context_token') != value.context_token:
        raise _api.HTTPException(409, 'This estimate changed in another session. Reopen it before selecting coverage.')
    discovery = case.get('previsit_discovery') or {}
    if discovery.get('request_state') != 'complete' or discovery.get('outcome') != 'review_needed':
        raise _api.HTTPException(409, 'There is no coverage candidate ready to select.')
    if not any(row.get('id') == value.candidate_id for row in discovery.get('candidates', [])):
        raise _api.HTTPException(422, 'Select a coverage option returned for this patient.')
    source, inputs = case['previsit_context']['source'], case['previsit_context']['inputs']
    # Staff may reconcile a different/new policy. Its patient identity and active
    # service-date coverage are still revalidated against the payer response.
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
                'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
                'billing_npi': str(billing.get('npi') or ''),
                'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=value.candidate_id,
                     selection_error=None, checked_at=_api._now())
    case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-candidate-selected')
    background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider)
    return _api._previsit_result(case)


async def _begin_coverage_discovery(result: dict, session: dict, inputs: dict, source: dict, provider: dict,
                                    background_tasks: _api.BackgroundTasks | None, detail: str, *, refresh_benefits: bool = False,
                                    payer_routes: list[dict] | None = None) -> dict:
    """Save a no-policy appointment as a recoverable Stedi discovery case."""
    coverage = _api._coverage_snapshot(inputs, source)
    binding = _api.hashlib.sha256(_api.json.dumps({'patient': inputs.get('patient'), 'appointment': source.get('appointment_id'),
        'provider': provider, 'service_date': inputs.get('service_date')}, sort_keys=True).encode()).hexdigest()
    card_conflict = bool((result.get('card_check') or {}).get('alternative_supported') and
                         (result.get('card_check') or {}).get('status') == 'conflict')
    slot = 'commercial-previsit-recovery:' + _api.hashlib.sha256(
        f"{source.get('appointment_id')}:{binding}:{(result.get('card_check') or {}).get('alternative_fingerprint') if card_conflict else ''}".encode()).hexdigest()
    try:
        case = await _api.run_in_threadpool(_api.store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'], workflow_slot=slot, **_api._scope(session))
    except _api.store.StaffCaseConflict as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    result['check_id'] = case['id']
    if (case.get('previsit_context') or {}).get('context_token') == _api._visit_context_token(inputs, source):
        result['suggestions'] = case['previsit_context']['result'].get('suggestions')
    elif inputs.get('reason'):
        result['suggestions'] = await _api.run_in_threadpool(_api.cpt_suggester.suggest_preliminary_cpt_codes,
            str(inputs['reason'])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
            source.get('recent_completed_appointments'))
    saved = case.get('previsit') or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await _api._start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get('request_state') == 'complete' and case.get('previsit_context'):
        try:
            fresh = _api.datetime.fromisoformat(saved['checked_at']) + _api.timedelta(hours=24) > _api.datetime.now(_api.timezone.utc)
        except (KeyError, TypeError, ValueError):
            fresh = False
        # Reopening completed recovery never submits a payer request. Reprice
        # current evidence; stale/unreadable evidence cannot authorize insurance.
        old_context = case['previsit_context']
        raw_key = case.get('previsit_raw_object_key')
        payload = None
        if fresh and raw_key:
            try:
                payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, raw_key))
            except Exception:
                payload = None
        result = _api.deepcopy(old_context['result'])
        result['benefits'] = _api._case_benefits(payload, inputs, source, case) if payload else _api._previsit_benefits({}, coverage, inputs['service_date'])
        if not payload:
            result.setdefault('issues', []).append('Saved payer evidence is stale or unavailable for insurance recalculation.')
        pricing_source = case.get('previsit_discovery', {}).get('pricing_source')
        await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, payload, session,
                                old_context.get('payer_id') or '', pricing_source=pricing_source)
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'previsit-library-recalculated'))
        _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_result')
        return _api._previsit_result(case)
    discovery_ready = bool(_api.stedi_discovery.request_body(inputs, provider))
    routing_failure = bool(source.get('payer') and coverage.get('member_id'))
    if routing_failure and not discovery_ready:
        detail = ('The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not run '
                  'because eCW did not provide the patient and provider details it requires. Coverage remains unverified, not proven inactive.')
    if not card_conflict:
        result.update(eligibility_error='payer_routing_unresolved' if routing_failure else 'coverage_identity_missing', eligibility_failure={
            'kind': 'payer_routing_unresolved' if routing_failure else 'coverage_identity_missing',
            'title': 'Insurance payer needs recovery' if routing_failure else 'eCW coverage needs recovery', 'detail': detail,
            'action': ('ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'
                       if routing_failure and discovery_ready else
                       'Insurance discovery could not start from the available eCW data.' if routing_failure else
                       'ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'),
            'codes': [], 'attempted_at': _api._now()})
    result['benefits'] = _api._previsit_benefits({}, coverage, inputs['service_date'])
    await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, None, session, '')
    recovery = case.get('previsit_discovery') or {}
    try:
        reusable = (not refresh_benefits and recovery.get('request_state') == 'complete'
                    and recovery.get('identity_binding') == _api._recovery_binding(inputs, source)
                    and _api.datetime.fromisoformat(recovery['checked_at']) + _api.timedelta(hours=24) > _api.datetime.now(_api.timezone.utc))
    except (KeyError, TypeError, ValueError):
        reusable = False
    if reusable:
        _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='completed_search')
        case = await _api.run_in_threadpool(_api._save, case, session, 'previsit-discovery-reused')
        return await _api._start_previsit_recovery(case, session, provider, background_tasks)
    case['previsit'] = {'request_state': 'rejected'}
    case['previsit_discovery'] = {'recovery_requested': discovery_ready,
                                  'request_state': 'not_requested' if discovery_ready else 'complete',
                                  'identity_binding': _api._recovery_binding(inputs, source),
                                  'stage': 'not_started' if discovery_ready else 'not_available',
                                  'reason_code': 'payer_route_unresolved' if discovery_ready else 'discovery_input_missing'}
    if card_conflict:
        case['previsit_discovery'].update(card_conflict=True,
            card_fingerprint=result['card_check']['alternative_fingerprint'],
            reason_code='card_alternative_check')
    if payer_routes:
        case['previsit_discovery'].update(payer_routes=payer_routes, route_source='ecw')
    if not discovery_ready:
        case['previsit_discovery'].update(outcome='not_available', exhausted=True, checked_at=_api._now(), selection_error=detail)
    case = await _api.run_in_threadpool(_api._save, case, session, 'previsit-coverage-recovery-requested')
    return await _api._start_previsit_recovery(case, session, provider, background_tasks)
