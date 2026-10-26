"""Commercial previsit routes workflow."""
from __future__ import annotations

import base64

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_reuse import (
    SAVED_RESPONSE_UNAVAILABLE, saved_benefits_for_review,
)

@_api.router.get('/cases/{case_id}/previsit')
async def saved_previsit(case_id: str, request: _api.Request) -> dict:
    """Read saved recovery without submitting or retrying a paid transaction.

    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND.
    NOT_FOUND means a completed search returned no policies and no earlier
    active payer response was retained. A retained active response with an
    empty follow-up search is REVIEW_REQUIRED, never an uninsured conclusion.
    recovery.state separately reports pending/indeterminate transport state.
    Coverage verification does not imply that a price is available.
    evidence_resolution reports each field's source findings and precise
    remaining action after the shared estimator evaluates saved evidence.
    A saved read projects the current network rule without a payer call or write.
    NMCT (practice_id primary) treats all insurer products as in network under
    clinic_nmct_policy unless a provider-specific payer statement says otherwise.
    Old clinic-derived out-of-network prices are cleared for recalculation.
    Other practices still use dated group-network contracts; absent Blue
    agreements can select clinic_no_blue_contract. Network tier alone does not
    establish a collectible amount.
    If the protected raw payer response cannot be reopened, same-context saved
    benefit terms may remain with benefits.historical_only and a source error;
    they cannot authorize an insured collection. A changed policy context does
    not inherit them.

    Parseable candidates include protected member/subscriber/dependent details,
    nullable subscriber DOB, source and verification reason_codes. Only this
    authenticated patient/practice scope may read them. A completed legacy
    response may be reparsed and its diagnosis saved, without payer submission
    or automatic adoption. Historical snapshots are unchanged.

    insurance_diagnosis separates raw/retained/verified response counts from
    activePolicyCount, the distinct active medical-policy identity count.
    Copies of one policy with conflicting applicable evidence return
    same_policy_evidence_conflict for review, without asserting COB.
    The diagnosis also separates
    identityStatus, eligibilityStatus, pricingStatus, reasonCodes, states,
    pricingReasons and enrollment status. identityStatus is PAYER_RESPONSE_MISSING
    before a dated payer benefit response exists. reasonCodes distinguishes an unverified
    provider network from an in-network contract-rate gap and an out-of-network
    payer-allowance gap. A prior enrollment failure is retained for audit but
    is not a current blocker after a current active Stedi 271. An active,
    patient-bound 271 stays verified after inconclusive discovery; its recovery
    status is separate. Active payer benefits stay in insurance mode when an
    insured price is unresolved unless staff explicitly choose cash. Missing
    collection inputs return collection_guidance with a null
    amount, status action_required and basis missing_collection_inputs; the reason
    names the next staff action. An administrator-approved policy can give a $0 check-in advance
    with basis claim_after_service. It does not waive the later patient share or
    change coverage evidence. Saved reads apply the current practice policy.
    An unlinked payer-reported zero individual out-of-pocket balance sets
    collection_guidance.unconfirmed_zero_oop and withholds a positive advance.
    A linked, applicable zero balance can return basis out_of_pocket_limit
    and a $0 covered office-visit collection.
    temporary_rate_reference, when present, identifies Blue Cross Clinical
    Library CPT prices used as a disclosed UnitedHealthcare or NMCT Aetna planning reference,
    not a contracted allowance or patient collection.
    benefits.reported_options[].places_of_service retains the payer's place-of-service
    codes for scoped benefit display; a reported office copay is not a collection decision.
    benefits.oop_annual_cents is a unique plan-wide individual maximum when
    the payer returned one; it is distinct from oop_remaining_cents.
    Conflicting individual annual or remaining OOP rows leave that amount null
    and retain their payer paths in the corresponding *_conflict_ids field.
    coordination, when present, is a safe summary of a separate Stedi COB
    check, including fixed field names missing at preflight and any eCW birth
    date source used when the 271 omitted it. Payer names and roles remain
    unassigned to discovered policies until
    exact evidence supports the mapping. Saved GET never submits COB.
    payment_explanation is recalculated from the saved result and remains one
    plain sentence with the network/pricing basis and any unresolved CPTs. It
    also returns structured plan, network decision, contract source, selected
    services, allowed-rate provenance, arithmetic and collection-policy fields.
    encounter_summary retains per-CPT rounding and withholds a saved positive
    advance if a CPT's stated coinsurance rate and base contradict its amount.
    Saved insured amounts are withheld if the linked clinic contract, scoped
    plan/provider configuration, or active selected CPT rate changed; staff
    must rerun against current configuration.
    published_requirements_version identifies the current published payer-rule
    set. Saved reads re-evaluate CPT referral and authorization decisions when
    that version, their Clinical Library sources, or network evidence changes.
    selected_ids includes source-backed provisional CPTs as well as reviewed
    CPTs; service_selection_status distinguishes preview from approval. The
    selected IDs and priced code list refer to the same service scenario.
    emg_option.calculation_version identifies the full patient-cost method.
    Older or newly invalid scenarios retain the testing request but return null
    numeric totals until staff recalculates with current visit evidence.
    """
    session = await _api._session(request)
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    recovery = case.get('previsit_discovery') or {}
    if (recovery.get('request_state') == 'complete' and recovery.get('outcome') in {'review_needed', 'no_match'}
            and recovery.get('raw_object_key') and recovery.get('candidate_version') != 5):
        await _api._reconcile_saved_discovery(case, session)
    return _api._previsit_result(case, session['practice_id'])


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


@_api.router.get('/cases/{case_id}/historical-replay-bundle')
async def historical_replay_bundle(case_id: str, request: _api.Request) -> dict:
    """Export a read-only protected replay bundle for a scoped administrator.

    This route makes no payer request and never updates the saved estimate. The
    response contains PHI and raw payer evidence; keep it in protected local
    storage and do not place it in repository notes or test fixtures. Current
    Library records are returned with dates for the replay's historical cutoff;
    missing historical versions cannot be reconstructed by this export. The
    replay reports when a saved clinic agreement cannot be reconstructed, so
    its unknown network tier is not mistaken for the saved network decision.
    """
    session = await _api._session(request)
    if (session.get('staff') or {}).get('role') != 'admin':
        raise _api.HTTPException(403, 'An administrator is required to export protected replay evidence.')
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'],
        session['share_manifest_id'], **_api._scope(session))
    if (not case or case.get('source_mode') != 'commercial_previsit'
            or not case.get('previsit_raw_object_key') or not case.get('previsit_context')):
        raise _api.HTTPException(404, 'No linked saved previsit response is available.')
    try:
        raw = await _api.run_in_threadpool(_api.object_store.get_object_bytes,
                                           case['previsit_raw_object_key'])
        _api.json.loads(raw)
    except Exception as exc:
        raise _api.HTTPException(502, 'The protected saved payer response could not be loaded.') from exc
    digest = _api.hashlib.sha256(raw).hexdigest()
    if case.get('previsit_raw_sha256') and case['previsit_raw_sha256'] != digest:
        raise _api.HTTPException(409, 'The saved payer response bytes differ from the recorded digest.')
    context = case['previsit_context']
    payer_id = str(context.get('payer_id') or '')
    connections = await _api.run_in_threadpool(_api.commercial_store.list_connections,
                                                session['practice_id'])
    matches = [row for row in connections if row.get('stedi_payer_id') == payer_id]
    payer = matches[0] if len(matches) == 1 else {}
    payer_name = str(payer.get('display_name') or (context.get('result') or {}).get('payer') or '')
    metadata = await _api.run_in_threadpool(_api.staff_store.list_library, session['practice_id'])
    documents = []
    for row in metadata:
        same_payer = (_api.insurance_identity.normalized(row.get('payer')) ==
                      _api.insurance_identity.normalized(payer_name))
        same_uhc = (_api.commercial.is_united_healthcare_payer({'payer': row.get('payer')})
                    and _api.commercial.is_united_healthcare_payer({'payer': payer_name}))
        if row.get('kind') == 'contract' and (same_payer or same_uhc):
            document = await _api.run_in_threadpool(_api.staff_store.get_library,
                                                     row['id'], session['practice_id'])
            if document:
                documents.append(document)
    saved_result = context.get('result') or {}
    selected = set((case.get('visit_review') or {}).get('selected_ids') or
                   saved_result.get('selected_ids') or [])
    selected_lines = [row for row in saved_result.get('codes') or [] if row.get('id') in selected]
    documented = bool(selected) and len(selected_lines) == len(selected) and all(
        row.get('code') and type(row.get('units')) is int for row in selected_lines)
    return {'case_reference': case_id, 'intake_id': case.get('intake_id') or session.get('intake_id'),
            'case': case, 'raw_reference': case['previsit_raw_object_key'],
            'raw_response_b64': base64.b64encode(raw).decode(), 'retrieved_raw_sha256': digest,
            'source_digest_recorded': bool(case.get('previsit_raw_sha256')),
            'documents': documents,
            'payer_route': payer or {'display_name': payer_name, 'stedi_payer_id': payer_id, 'rules': {}},
            'payer_route_unique': len(matches) == 1,
            'practice_id': session['practice_id'],
            'service_date': (context.get('inputs') or {}).get('service_date'),
            'selection': {'origin': 'documented', 'lines': selected_lines} if documented else None,
            'selection_status': 'documented' if documented else 'requires_historical_cpt_evidence'}


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
    return _api._previsit_result(case, session['practice_id'])


@_api.router.post('/cases/{case_id}/coverage-candidate')
async def select_coverage_candidate(case_id: str, value: _api.CoverageCandidateInput, request: _api.Request,
                                    background_tasks: _api.BackgroundTasks) -> dict:
    """Select a returned policy for a follow-up payer check.

    The saved recovery result distinguishes payer error, returned identity mismatch,
    missing service-date medical coverage and an indeterminate submitted check.
    A mismatched response stays protected as evidence without assigning its
    member balances to this patient.
    """
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
                     selected_candidate_source_ids=[value.candidate_id],
                     selection_error=None, checked_at=_api._now())
    case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-candidate-selected')
    background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider)
    return _api._previsit_result(case, session['practice_id'])


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
        same_context = old_context.get('context_token') == _api._visit_context_token(inputs, source)
        if payload:
            result['benefits'] = _api._case_benefits(payload, inputs, source, case)
        else:
            result['benefits'] = (saved_benefits_for_review(old_context['result'].get('benefits'))
                                  if same_context else None) or _api._previsit_benefits({}, coverage, inputs['service_date'])
        if not payload:
            result.setdefault('issues', []).append(SAVED_RESPONSE_UNAVAILABLE if same_context else
                'The saved payer response belongs to earlier visit or policy details. Rerun the payer check for this visit.')
        pricing_source = case.get('previsit_discovery', {}).get('pricing_source')
        await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, payload, session,
                                old_context.get('payer_id') or '', pricing_source=pricing_source)
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'previsit-library-recalculated'))
        _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_result')
        if payload and (result['benefits'].get('has_secondary') or
                        (case.get('previsit_discovery') or {}).get('reason_code') == 'multiple_active_policies'):
            await _api._check_previsit_cob(case, session, provider)
        return _api._previsit_result(case, session['practice_id'])
    discovery_ready = bool(_api.stedi_discovery.request_body(inputs, provider))
    routing_failure = bool(source.get('payer') and coverage.get('member_id'))
    if routing_failure and not discovery_ready:
        missing = _api.stedi_discovery.missing_request_fields(inputs, provider)
        detail = ('The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not start '
                  'because the request lacks ' + ', '.join(missing) + '.')
    if not card_conflict:
        result.update(eligibility_error='payer_routing_unresolved' if routing_failure else 'coverage_identity_missing', eligibility_failure={
            'kind': 'payer_routing_unresolved' if routing_failure else 'coverage_identity_missing',
            'title': 'Insurance payer needs recovery' if routing_failure else 'eCW coverage needs recovery', 'detail': detail,
            'action': ('ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'
                       if routing_failure and discovery_ready else
                       'Correct the listed patient or provider fields in eCW or clinic provider setup, then rerun insurance discovery.' if routing_failure else
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
