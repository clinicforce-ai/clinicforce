"""Commercial previsit status workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

@_api.router.get("/configuration")
async def configuration(request: _api.Request) -> dict:
    session = await _api._session(request)
    return _api._config(session["practice_id"])


@_api.router.get("/appointments", responses={409: {"description": "ecw_patient_selection_required: return to staff lookup and confirm the patient before checking benefits."}})
async def ecw_appointments(request: _api.Request) -> dict:
    """List confirmed scheduled visits without an eligibility check.

    Identity no-match/ambiguity returns HTTP 409 with the machine-readable code
    ecw_patient_selection_required. The UI opens a prefilled staff lookup; it
    never switches the patient or repeats eligibility from a similarity score.

    The shared reader has a 24-second deadline. HTTP 502 errors identify the
    failed lookup phase; X-ClinicForce-Request-ID joins the safe stage logs in
    CloudWatch. A failed read does not establish that no appointment exists.
    """
    session = await _api._session(request)
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        values = await _api.ecw_backend.list_appointments(_api._ecw_identity(session, _api.date.today().isoformat()), _api._ecw_practice_config(session["practice_id"]))
    except _api.ecw_backend.EcwBackendError as exc:
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return _api.JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise _api.HTTPException(502, str(exc)) from exc
    return {"appointments": values}


@_api.router.post("/appointments/readiness")
async def ecw_appointment_readiness(value: _api.EcwStartInput, request: _api.Request) -> dict:
    """Read visit prerequisites without a payer transaction. An uncoded routine
    office visit can use the estimator's anticipated service without staff
    confirmation; scheduled_codes remains the actual eCW coding, possibly empty.
    Readiness does not establish payer benefits or a dollar amount. Unresolved identity
    returns 409 with code ecw_patient_selection_required for staff selection.
    """
    session = await _api._session(request)
    if not _api.ecw_backend.configured():
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(
            _api._ecw_identity(session, _api.date.today().isoformat()),
            _api._ecw_practice_config(session["practice_id"]), value.appointment_id)
    except _api.ecw_backend.EcwBackendError as exc:
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return _api.JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise _api.HTTPException(502, str(exc)) from exc
    return _api._appointment_readiness(session["practice_id"], session, inputs, source)


def _recovery_binding(inputs: dict, source: dict) -> str:
    # Choosing anticipated services must not invalidate the recovered member identity.
    return _api._visit_context_token({**inputs, 'lines': [], 'reason': None, 'visit_type': None}, source)


def _case_benefits(payload: dict, inputs: dict, source: dict, case: dict) -> dict:
    coverage = _api._coverage_snapshot(inputs, source)
    recovery = case.get('previsit_discovery') or {}
    recovered = (recovery.get('outcome') == 'recovered' and recovery.get('identity_binding') == _api._recovery_binding(inputs, source)
                 and recovery.get('verified_coverage'))
    if recovered:
        coverage.update(recovery['verified_coverage'])
    benefits = _api._previsit_benefits(payload, coverage, inputs['service_date'], _api._previsit_service_types(inputs)[0], inputs=inputs, source=source)
    if recovered:
        benefits['normalization_notes'].append('Coverage was selected from Stedi discovery for this estimate; eCW was not changed.')
    return benefits


def _enrollment_failure(enrollment: dict) -> dict:
    descriptions = {
        'LOOKUP_FAILED': 'The provider enrollment status could not be read. This does not establish missing enrollment. Retry the enrollment lookup before eligibility.',
        'MISSING': 'No eligibility enrollment was returned for this exact payer and provider NPI.',
        'REJECTED': 'The payer rejected this provider eligibility enrollment. Review the enrollment decision.',
        'CANCELED': 'This provider eligibility enrollment was canceled.',
        'DRAFT': 'The provider eligibility enrollment is still a draft and has not been submitted.',
        'PROVIDER_ACTION_REQUIRED': 'The provider must complete the outstanding enrollment requirements.',
    }
    detail = descriptions.get(enrollment['status'], 'Provider eligibility enrollment is pending or not verified as live.')
    return {'kind': 'provider_enrollment', 'title': 'Provider enrollment status', 'detail': detail,
            'action': detail + ' No paid eligibility request was submitted.', 'codes': [],
            'reason_code': enrollment['reason_code']}


def _insurance_diagnosis(case: dict, result: dict, guidance: dict) -> dict:
    """Independent, persisted stage results; never put candidate identities in logs."""
    recovery = result.get('recovery') or {}
    benefits = result.get('benefits') or {}
    candidates = recovery.get('candidates') or []
    price = result.get('price_range') or {}
    failure = result.get('eligibility_failure') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    verified = (recovery.get('outcome') == 'recovered' and recovery.get('state') == 'complete') or (
        not recovery and benefits.get('active') is True and identity.get('status') in {'matched', 'verified', 'reconciled'})
    identity_verified = identity.get('status') in {'matched', 'verified', 'reconciled'} or any(
        row.get('identity_status') == 'MATCHED' for row in candidates)
    reasons = [reason for row in candidates for reason in row.get('reason_codes', [])]
    raw_recovery = case.get('previsit_discovery') or {}
    retrieval = case['previsit_context'].get('source', {}).get('coverage_retrieval') or {}
    if retrieval.get('status') == 'empty':
        reasons.append('ECW_UPSTREAM_NO_COVERAGE')
    elif retrieval.get('status') == 'unavailable':
        reasons.append('ECW_COVERAGE_UNAVAILABLE')
    elif retrieval.get('raw_count') and not retrieval.get('retained_count'):
        reasons.append('ECW_COVERAGE_FILTERED')
    if raw_recovery.get('card_failure_reason'):
        reasons.append(raw_recovery['card_failure_reason'].upper())
    states = list(dict.fromkeys(row.get('state', 'CANDIDATE_FOUND') for row in candidates))
    raw_count = recovery.get('raw_candidate_count')
    if raw_count == 0 and recovery.get('state') == 'complete' and recovery.get('outcome') == 'no_match':
        states.append('UPSTREAM_NO_POLICIES')
    elif raw_count and not candidates:
        states.append('CANDIDATES_REJECTED')
    if sum(row.get('active_medical') is True for row in candidates) > 1:
        reasons.append('MULTIPLE_COVERAGES'); states.append('MULTIPLE_COVERAGES')
    states.append('IDENTITY_VERIFIED' if identity_verified else 'IDENTITY_REVIEW_REQUIRED')
    enrollment = raw_recovery.get('enrollment') or result.get('enrollment')
    if enrollment and enrollment.get('status') != 'LIVE':
        reasons.append(enrollment['reason_code'].upper()); states.append('ENROLLMENT_BLOCKED')
    if not verified or benefits.get('active') is not True:
        states.append('ELIGIBILITY_NOT_VERIFIED')
    else:
        states.append('BENEFITS_VERIFIED')
    candidate_states = {row.get('coverage_evidence', {}).get('status') for row in candidates}
    eligibility_state = (benefits.get('coverage_evidence') or {}).get('status')
    if not eligibility_state and len(candidate_states) == 1:
        eligibility_state = next(iter(candidate_states))
    available = guidance.get('amount_cents') is not None
    partial = bool(available and guidance.get('basis') == 'office_copay' and any(
        line.get('calculation') is None and _api.visit_estimate.clinical_group(str(line.get('code') or '')) != 'visit'
        for line in price.get('lines', [])))
    details = []
    if not available:
        details = list(dict.fromkeys([guidance.get('reason') or '', *benefits.get('review_reasons', []), *result.get('issues', []),
            *price.get('issues', []), *price.get('service_issues', []),
            *(issue for row in price.get('lines', []) for issue in row.get('issues', []))]))
        details = [value for value in details if value]
        if benefits.get('active') is not True:
            reasons.append('APPLICABLE_BENEFITS_MISSING')
        if benefits.get('coverage_status_conflict'):
            reasons.append('COVERAGE_STATUS_CONFLICT')
        if identity.get('status') == 'review':
            reasons.append('PATIENT_IDENTITY_REVIEW_REQUIRED')
        if benefits.get('service_date_matches') is False:
            reasons.append('COVERAGE_DATE_MISMATCH')
        if benefits.get('network_status') not in {'in_network', 'not_applicable'}:
            reasons.append('PROVIDER_NETWORK_NOT_VERIFIED')
        if not price.get('complete') and any(row.get('allowed_cents') is None for row in price.get('lines', [])):
            reasons.append('CONTRACTED_RATE_MISSING')
        if not reasons:
            reasons.append('BENEFIT_OR_PRICE_REQUIREMENT_UNRESOLVED')
    elif partial:
        details = list(dict.fromkeys(issue for line in price.get('lines', []) if line.get('calculation') is None
            for issue in line.get('issues', [])))
        reasons.append('ADDITIONAL_SERVICE_PRICE_UNRESOLVED')
    if failure.get('kind'):
        reasons.append(failure['kind'].upper())
    states.append('PARTIAL_AMOUNT_READY' if partial else 'ESTIMATE_READY' if available else 'PRICING_UNAVAILABLE')
    return {'coverageSource': (case.get('previsit_discovery') or {}).get('evidence_source') or
                ('stedi_discovery' if recovery else result.get('benefit_source') or 'ecw'),
            'rawCandidateCount': raw_count, 'retainedCandidateCount': len(candidates),
            'verifiedCandidateCount': recovery.get('verified_candidate_count', 0),
            'identityStatus': 'VERIFIED' if identity_verified else 'REVIEW_REQUIRED',
            'eligibilityStatus': 'BENEFITS_VERIFIED' if verified and benefits.get('active') is True else eligibility_state if eligibility_state in {'INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'} else 'NOT_VERIFIED',
            'pricingStatus': 'PARTIAL_AMOUNT' if partial else 'ESTIMATE_READY' if available else 'NOT_CALCULATED',
            'reasonCodes': list(dict.fromkeys(reasons)), 'states': list(dict.fromkeys(states)),
            'pricingReasons': details, 'enrollment': enrollment, 'ecwCoverage': retrieval,
            'discoveryEnrollment': raw_recovery.get('discovery_enrollment'), 'mbiEnrollment': raw_recovery.get('mbi_enrollment')}


def _previsit_result(case: dict) -> dict:
    result = _api.deepcopy(case['previsit_context']['result'])
    result['revision'] = case.get('revision', 0)
    check_from = max((case.get('visit_review') or {}).get('insurance_check_from') or '',
        ((case['previsit_context'].get('source', {}).get('coverage_period') or {}).get('start') or '')[:10])
    timing = _api._eligibility_timing(result['service_date'], check_from or None) if result.get('service_date') else None
    if timing:
        result['eligibility_timing'] = timing
    else:
        result.pop('eligibility_timing', None)
    if case.get('previsit_discovery'):
        result['recovery'] = _api.stedi_discovery.public_status(case['previsit_discovery'])
        result['coverage_status'] = result['recovery']['coverage_status']
    recovery = result.get('recovery') or {}
    source = case['previsit_context'].get('source') or {}
    hints = {}
    evidence = [{'payer': ' / '.join(row.get('payer') or []), 'member_id': row.get('member_id'), 'source': 'eCW Coverage'}
                for row in source.get('coverage_records', [])]
    evidence += [{**row, 'source': 'eCW appointment notes'} for row in source.get('insurance_candidates', [])]
    evidence += (case.get('previsit_discovery') or {}).get('card_candidates', [])
    for row in evidence:
        key = (_api.stedi_discovery.normalized(row.get('payer')), _api.stedi_discovery.normalized(row.get('member_id')))
        if not any(key):
            continue
        hint = hints.setdefault(key, {'payer': row.get('payer'), 'member_id': row.get('member_id'), 'sources': []})
        if row['source'] not in hint['sources']:
            hint['sources'].append(row['source'])
    result['insurance_hints'] = list(hints.values())
    insurance_guidance = _api.collection_guidance.build(result, case.get('visit_review'))
    result['insurance_diagnosis'] = _api._insurance_diagnosis(case, result, insurance_guidance)
    pricing_unavailable = (result.get('payment_mode') != 'cash' and insurance_guidance.get('amount_cents') is None
        and case.get('previsit', {}).get('request_state') in {'complete', 'rejected', 'not_requested'}
        and not recovery)
    if (not timing and recovery.get('state') in {'complete', 'rejected'}
            and recovery.get('outcome') != 'recovered'
            and recovery.get('exhausted') is not False
            and (case.get('visit_review') or {}).get('payment_mode') != 'insurance'):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {}
        result.pop('emg_option', None)
    elif (not timing and (pricing_unavailable or (recovery.get('state') == 'complete' and recovery.get('outcome') == 'recovered'
            and insurance_guidance.get('amount_cents') is None))
            and (case.get('visit_review') or {}).get('payment_mode') != 'insurance'):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {}
        result.pop('emg_option', None)
    if 'cash_fallback' in result:
        result['cash_fallback']['reason'] = 'Clinic self-pay pricing is shown because ' + _api.amount_explanation.fallback_cause(result) + '.'
    # Cash is a separate price path, never evidence of inactive insurance.
    result['collection_guidance'] = _api.collection_guidance.build(result, case.get('visit_review'))
    result['payment_explanation'] = _api.amount_explanation.build(result, result['collection_guidance'], case.get('visit_review'))
    return result


def _recovery_event(case: dict, session: dict, stage: str, outcome: str, *, reason: str | None = None,
                    count: int | None = None, http_status: int | None = None) -> None:
    _api.collection_event('coverage_recovery', practice_id=session['practice_id'],
        intake_id=(session.get('intake') or {}).get('id'), case_id=case['id'], stage=stage,
        outcome=outcome, reason_code=reason, count=count, http_status=http_status)


async def _read_card_hints(session: dict, patient_id: str | None) -> dict:
    """Read one patient-bound card set; OCR fields are inquiry hints, not benefits."""
    from app.insurance import v1_sources
    from app.api.routers.staff import inspect_file
    contents = []
    source = None
    try:
        intakes = await _api.run_in_threadpool(_api.staff_store.patient_card_intakes, session['practice_id'], session['patient_id'])
        card_intakes = [intake for intake in intakes if intake.get('documents')]
        if len(card_intakes) > 1:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        docs = card_intakes[0]['documents'] if card_intakes else []
        if len(docs) > 2:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        for doc in docs:
            if doc.get('size_bytes', 0) <= 10_000_000 and doc.get('object_key'):
                contents.append(await _api.run_in_threadpool(_api.object_store.get_object_bytes, doc['object_key']))
        if contents:
            source = 'clinicforce_card'
        elif patient_id:
            cards = await _api.asyncio.wait_for(_api.ecw_backend.insurance_cards(patient_id), timeout=24)
            if len(cards) > 2:
                return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
            for card in cards:
                data, _ = await _api.asyncio.wait_for(_api.ecw_backend.insurance_cards(patient_id, document_id=card['id']), timeout=24)
                contents.append(data)
            if contents:
                source = 'ecw_card'
        if not contents:
            return {'status': 'not_found', 'fields': {}, 'count': 0}
        if any(len(content) > 10_000_000 for content in contents):
            return {'status': 'unavailable', 'fields': {}, 'count': len(contents), 'failure_reason': 'card_too_large'}
        for content in contents:
            await _api.run_in_threadpool(inspect_file, content)
        fields, evidence, _ = await _api.asyncio.wait_for(_api.asyncio.to_thread(v1_sources.read_card_fields, contents), timeout=20)
        return {'status': 'read', 'fields': fields, 'evidence': evidence, 'count': len(contents), 'source': source}
    except (_api.ecw_backend.EcwBackendError, _api.httpx.HTTPError, TimeoutError, ValueError, _api.HTTPException,
            _api.golden.OCRServiceError, _api.golden.GoldenCaseError) as exc:
        return {'status': 'unavailable', 'fields': {}, 'count': len(contents),
                'failure_reason': 'card_read_timeout' if isinstance(exc, (TimeoutError, _api.httpx.TimeoutException)) else 'card_unavailable'}
