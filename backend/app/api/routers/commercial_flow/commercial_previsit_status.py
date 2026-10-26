"""Commercial previsit status workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance.identity import patient_benefits_bound, patient_plan_applies, payer_identity_verified
from app.insurance.network_participation import verified_in_network, verified_out_of_network
from app.insurance import evidence_resolution, group_contract_support
from app.api.routers.commercial_flow.commercial_previsit_finish import _payer_plan_pricing_source
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_network_evidence, recheck_saved_payer_message
from app.api.routers.commercial_flow.commercial_previsit_saved_pricing import _reprice_saved_new_network
from app.api.routers.commercial_flow.commercial_previsit_requirements import refresh_saved_requirements
from app.api.routers.commercial_flow.commercial_previsit_enrollment import _enrollment_failure
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
    """Read pre-payer prerequisites; scheduled_codes remains actual eCW coding.
    Clinic rate/setup gaps are pricing_followups, not eligibility blockers.
    A selected active policy permits inquiry; coverage_followups still require COB.
    Readiness does not establish benefits, payment priority or a dollar amount.
    Unresolved identity returns 409 with ecw_patient_selection_required.
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
    benefits = _api._previsit_benefits(payload, coverage, inputs['service_date'], _api._previsit_service_types(inputs), inputs=inputs, source=source)
    if recovered:
        benefits['normalization_notes'].append('Coverage was selected from Stedi discovery for this estimate. eCW was not changed.')
    return benefits


def _insurance_diagnosis(case: dict, result: dict, guidance: dict) -> dict:
    """Independent, persisted stage results; never put candidate identities in logs."""
    recovery = result.get('recovery') or {}
    benefits = result.get('benefits') or {}
    candidates = recovery.get('candidates') or []
    price = result.get('price_range') or {}
    failure = result.get('eligibility_failure') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    historical_only = benefits.get('historical_only') is True or any(
        row.get('kind') == 'saved_raw_unavailable' for row in benefits.get('source_errors') or []
        if isinstance(row, dict))
    verified = not historical_only and benefits.get('active') is True and (
        (recovery.get('outcome') == 'recovered' and recovery.get('state') == 'complete')
        or payer_identity_verified(identity))
    payer_benefits_returned = benefits.get('active') is not None and bool(result.get('benefit_source') or result.get('checked_at'))
    payer_identity_available = bool(result.get('checked_at') or benefits.get('coverage_status_conflict') or
        (benefits.get('active') is not None and (benefits.get('benefit_source') or result.get('benefit_source'))))
    # A discovered candidate match cannot overrule an explicit mismatch in
    # the member-specific payer response. With no payer identity decision, a
    # single candidate may still establish the patient match for diagnosis.
    identity_verified = payer_identity_verified(identity) or (
        not identity.get('status') and len(candidates) == 1
        and candidates[0].get('identity_status') == 'MATCHED')
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
    if historical_only:
        reasons.append('SAVED_PAYER_RESPONSE_UNAVAILABLE')
    states = list(dict.fromkeys(row.get('state', 'CANDIDATE_FOUND') for row in candidates))
    raw_count = recovery.get('raw_candidate_count')
    if raw_count == 0 and recovery.get('state') == 'complete' and recovery.get('outcome') == 'no_match':
        states.append('UPSTREAM_NO_POLICIES')
    elif raw_count and not candidates:
        states.append('CANDIDATES_REJECTED')
    active_policy_count = recovery.get('active_policy_count')
    if (active_policy_count if type(active_policy_count) is int else
            sum(row.get('active_medical') is True for row in candidates)) > 1:
        reasons.append('MULTIPLE_COVERAGES'); states.append('MULTIPLE_COVERAGES')
    states.append('IDENTITY_VERIFIED' if identity_verified else
                  'IDENTITY_REVIEW_REQUIRED' if payer_identity_available else 'PAYER_RESPONSE_MISSING')
    enrollment = raw_recovery.get('enrollment') or result.get('enrollment')
    if enrollment and enrollment.get('status') != 'LIVE' and (benefits.get('active') is not True or historical_only):
        if enrollment.get('reason_code'):
            reasons.append(str(enrollment['reason_code']).upper())
        states.append('ENROLLMENT_BLOCKED')
    if not verified or benefits.get('active') is not True:
        states.append('SAVED_PAYER_RESPONSE_UNAVAILABLE' if historical_only else
                      'PAYER_BENEFITS_RETURNED_POLICY_UNRESOLVED' if payer_benefits_returned
                      else 'ELIGIBILITY_NOT_VERIFIED')
    else:
        states.append('BENEFITS_VERIFIED')
    candidate_states = {row.get('coverage_evidence', {}).get('status') for row in candidates}
    eligibility_state = (benefits.get('coverage_evidence') or {}).get('status')
    if not eligibility_state and len(candidate_states) == 1:
        eligibility_state = next(iter(candidate_states))
    available = guidance.get('amount_cents') is not None and guidance.get('basis') != 'claim_after_service'
    partial = bool(available and guidance.get('basis') == 'office_copay' and any(
        line.get('calculation') is None and _api.visit_estimate.clinical_group(str(line.get('code') or '')) != 'visit'
        for line in price.get('lines', [])))
    details = []
    if not available:
        if not payer_identity_available and benefits.get('active') is None:
            # Old saved placeholders may say "Stedi" and carry many derived review
            # reasons. There is no payer benefit to reconcile or price yet.
            state = recovery.get('state')
            details = [
                'The payer check outcome is unknown. Check Stedi activity before retrying.' if state == 'indeterminate' else
                'The payer check is still in progress.' if state == 'pending' else
                failure.get('action') or 'No payer eligibility benefits are available for this visit yet.',
                *(price.get('service_issues') or []),
            ]
        else:
            details = list(dict.fromkeys([guidance.get('reason') or '', *benefits.get('review_reasons', []), *result.get('issues', []),
                *price.get('issues', []), *price.get('service_issues', []),
                *(issue for row in price.get('lines', []) for issue in row.get('issues', []))]))
        details = [value for value in details if value]
        if benefits.get('active') is not True:
            reasons.append('APPLICABLE_BENEFITS_MISSING')
        if benefits.get('coverage_status_conflict'):
            reasons.append('COVERAGE_STATUS_CONFLICT')
        if payer_identity_available and identity.get('status') == 'review':
            reasons.append('PATIENT_IDENTITY_REVIEW_REQUIRED')
        if benefits.get('service_date_matches') is False:
            reasons.append('COVERAGE_DATE_MISMATCH')
        in_network = verified_in_network(benefits)
        out_of_network = verified_out_of_network(benefits)
        if not (in_network or out_of_network or benefits.get('network_status') == 'not_applicable'):
            reasons.append('PROVIDER_NETWORK_NOT_VERIFIED')
        if not price.get('complete') and any(row.get('allowed_cents') is None for row in price.get('lines', [])):
            if in_network:
                reasons.append('CONTRACTED_RATE_MISSING')
            elif out_of_network:
                reasons.append('PAYER_OUT_OF_NETWORK_ALLOWANCE_MISSING')
        if not reasons:
            reasons.append('BENEFIT_OR_PRICE_REQUIREMENT_UNRESOLVED')
    elif partial:
        details = list(dict.fromkeys(issue for line in price.get('lines', []) if line.get('calculation') is None
            for issue in line.get('issues', [])))
        reasons.append('ADDITIONAL_SERVICE_PRICE_UNRESOLVED')
    if failure.get('kind') and (benefits.get('active') is not True or historical_only):
        reasons.append(failure['kind'].upper())
    states.append('COLLECTION_POLICY_APPLIED' if guidance.get('basis') == 'claim_after_service' else
                  'PARTIAL_AMOUNT_READY' if partial else 'ESTIMATE_READY' if available else 'PRICING_UNAVAILABLE')
    return {'coverageSource': (case.get('previsit_discovery') or {}).get('evidence_source') or
                ('stedi_discovery' if recovery else result.get('benefit_source') or 'ecw'),
            'rawCandidateCount': raw_count, 'retainedCandidateCount': len(candidates),
            'verifiedCandidateCount': recovery.get('verified_candidate_count', 0),
            'activePolicyCount': active_policy_count if type(active_policy_count) is int else None,
            'identityStatus': 'VERIFIED' if identity_verified else
                'REVIEW_REQUIRED' if payer_identity_available else 'PAYER_RESPONSE_MISSING',
            'eligibilityStatus': 'BENEFITS_VERIFIED' if verified and benefits.get('active') is True else
                'PAYER_BENEFITS_RETURNED' if payer_benefits_returned else
                eligibility_state if eligibility_state in {'INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'} else 'NOT_VERIFIED',
            'pricingStatus': 'PARTIAL_AMOUNT' if partial else 'ESTIMATE_READY' if available else 'NOT_CALCULATED',
            'reasonCodes': list(dict.fromkeys(reasons)), 'states': list(dict.fromkeys(states)),
            'pricingReasons': details, 'enrollment': enrollment, 'ecwCoverage': retrieval,
            'discoveryEnrollment': raw_recovery.get('discovery_enrollment'), 'mbiEnrollment': raw_recovery.get('mbi_enrollment')}


def _recheck_saved_clinic_pricing(result: dict, case: dict, practice_id: str) -> None:
    """Do not quote a saved insured total after its clinic contract or rate changes."""
    context = case['previsit_context']
    if result.get('payment_mode') == 'cash':
        return
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    clinic_lines = [line for line in price.get('lines') or []
                    if line.get('allowed_basis') == 'clinic_contract' and line.get('allowed_cents') is not None]
    clinic_network = benefits.get('network_source') == 'clinic_library_contract'
    if not clinic_network and not clinic_lines:
        return
    reason = None
    payer_id = context.get('payer_id')
    connections = [row for row in _api.commercial_store.list_connections(practice_id)
                   if row.get('stedi_payer_id') == payer_id and row.get('status') == 'staff_guidance_enabled'] if payer_id else []
    if len(connections) != 1:
        reason = 'The payer connection used by this saved estimate is no longer the single enabled clinic connection.'
    else:
        inputs = context.get('inputs') or {}
        source = ((case.get('previsit_discovery') or {}).get('pricing_source')
                  or context.get('source') or {})
        plan = ((result.get('plan_details') or {}).get('stedi') or {})
        identity = result.get('identity') or benefits.get('identity') or {}
        exact_source = {**_payer_plan_pricing_source(source, plan, benefits, identity,
                                                    str(inputs.get('service_date') or '')),
                        'payer_plan_names': ([plan['identified_product']] if plan.get('identified_product') and patient_benefits_bound(identity)
                                             else plan.get('names', source.get('payer_plan_names', []))),
                        'payer_network_names': (plan.get('network_names', source.get('payer_network_names', []))
                            if patient_plan_applies(benefits, identity, str(inputs.get('service_date') or '')) else []),
                        'payer_plan_scope': {key: plan.get(key) for key in ('plan_number', 'group_number')}}
        try:
            _, network, profile = _api._previsit_practice_path(
                practice_id, {**inputs, 'payer_id': connections[0]['id']}, exact_source)
            calculated_at = result.get('calculated_at')
            if calculated_at and any(row.get('updated_at') for row in (network, profile)):
                try:
                    calculated = _api.datetime.fromisoformat(str(calculated_at).replace('Z', '+00:00'))
                    if any(_api.datetime.fromisoformat(str(row['updated_at']).replace('Z', '+00:00')) > calculated
                           for row in (network, profile) if row.get('updated_at')):
                        reason = 'The clinic plan or provider configuration changed since this saved estimate was calculated.'
                except (TypeError, ValueError):
                    reason = 'The clinic configuration timestamp for this saved estimate could not be checked.'
            if clinic_network:
                old_document = (result.get('network_evidence') or {}).get('document_id')
                current_document = _api._network_contract_evidence(
                    practice_id, network, profile, str(inputs.get('service_date') or '')).get('document_id')
                if not old_document or current_document != old_document:
                    reason = 'The Clinical Library contract used by this saved estimate is no longer linked to this plan and provider.'
            if not reason and clinic_lines:
                rates = _api.commercial_store.matching_rates(
                    practice_id, profile, network, str(inputs.get('service_date') or ''), clinic_lines)
                current = {(row['code'], row.get('modifier') or ''): row for row in rates}
                if any((not line.get('rate_version') or line.get('unit_allowed_cents') is None
                        or line.get('rate_version') != (rate or {}).get('version_id')
                        or line.get('unit_allowed_cents') != (rate or {}).get('allowed_cents'))
                       for line in clinic_lines
                       for rate in [current.get((line['code'], ','.join(line.get('modifiers') or [])))]):
                    reason = 'The active clinic rate for a selected CPT changed since this saved estimate was calculated.'
        except (_api.HTTPException, _api.commercial.CommercialError):
            reason = 'The clinic plan, provider or rate scope used by this saved estimate no longer matches current configuration.'
    if not reason:
        return
    result.setdefault('issues', []).append(reason + ' Rerun the insured estimate with the current clinic configuration.')
    if clinic_network:
        benefits.update(network_status='unknown', network_source=None)
        result['network_evidence'] = {'reason': reason}
    price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                 allowed_min_cents=None, allowed_max_cents=None,
                 plan_min_cents=None, plan_max_cents=None,
                 collection={'action': 'rerun', 'amount_cents': None, 'reason': reason})
    for line in clinic_lines:
        line.update(allowed_cents=None, unit_allowed_cents=None, calculation=None)
    result.pop('arrival_collection', None)
    result.pop('planning_estimate', None)


def _recheck_saved_payer_allowance(result: dict, case: dict, practice_id: str) -> None:
    """Invalidate a saved OON total if its visit quote or clinic rule no longer applies."""
    price = result.get('price_range') or {}
    quoted = [line for line in price.get('lines') or []
              if line.get('allowed_basis') == 'payer_out_of_network' and line.get('allowed_cents') is not None]
    if not quoted:
        return
    context = case['previsit_context']
    inputs = context.get('inputs') or {}
    source = ((case.get('previsit_discovery') or {}).get('pricing_source') or context.get('source') or {})
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    scope = _api._benefit_context(inputs, source)
    rates = _api.payer_allowance.quoted_rates(case.get('visit_review') or {}, quoted,
        payer_id=str(context.get('payer_id') or ''), service_date=str(inputs.get('service_date') or ''),
        provider_npi=str(scope.get('provider_npi') or ''),
        place_of_service=str(scope.get('place_of_service') or ''),
        patient_binding=str(identity.get('binding') or ''),
        product_binding=_api.payer_allowance.product_binding(benefits.get('plan_details')))
    current = {rate['line_id']: rate for rate in rates}
    network_valid = _api.network_participation.verified_out_of_network(benefits)
    quote_valid = all(
        (rate := current.get(line.get('id')))
        and rate['allowed_cents'] == line.get('unit_allowed_cents')
        and rate['source_reference'] == line.get('rate_source') for line in quoted)
    if network_valid and benefits.get('network_source') == 'clinic_nonparticipation_rule':
        connections = [row for row in _api.commercial_store.list_connections(practice_id)
                       if row.get('stedi_payer_id') == context.get('payer_id')
                       and row.get('status') == 'staff_guidance_enabled']
        plan = (result.get('plan_details') or {}).get('stedi') or {}
        exact_source = {**_payer_plan_pricing_source(source, plan, benefits,
                           result.get('identity') or benefits.get('identity') or {},
                           str(inputs.get('service_date') or '')),
                        'payer_plan_names': plan.get('names') or [],
                        'payer_plan_scope': {key: plan.get(key) for key in ('plan_number', 'group_number')}}
        try:
            if len(connections) != 1:
                network_valid = False
            else:
                _api._previsit_practice_path(practice_id,
                    {**inputs, 'payer_id': connections[0]['id']}, exact_source)
                network_valid = False  # A participating path now applies instead.
        except _api.HTTPException as exc:
            network_valid = str(exc.detail) == 'A sourced nonparticipation rule applies to this provider, product, location and date.'
    if network_valid and quote_valid:
        return
    reason = 'The saved payer allowance or out-of-network participation rule no longer matches this visit.'
    result.setdefault('issues', []).append(reason + ' Recheck the payer quote and this provider’s network status.')
    if not network_valid:
        benefits.update(network_status='unknown', network_source=None)
        result['network_evidence'] = {'reason': reason}
    price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                 allowed_min_cents=None, allowed_max_cents=None,
                 plan_min_cents=None, plan_max_cents=None,
                 collection={'action': 'rerun', 'amount_cents': None, 'reason': reason})
    for line in quoted:
        line.update(allowed_cents=None, unit_allowed_cents=None, calculation=None)
    result.pop('arrival_collection', None)
    result.pop('planning_estimate', None)


def _previsit_result(case: dict, practice_id: str | None = None) -> dict:
    result = _api.deepcopy(case['previsit_context']['result'])
    reference = case.get('previsit_reference') or {}
    if (reference.get('context_token') == case['previsit_context'].get('context_token')
            and reference.get('service_date') == result.get('service_date')):
        result['reference_benefits'] = _api.deepcopy(reference['benefits'])
        result['reference_only'] = True
    # Legacy discovery wrote a detail-only failure after a successful 271.
    # Keep its review message in recovery; it is not an eligibility failure.
    failure = result.get('eligibility_failure') or {}
    if ((result.get('benefits') or {}).get('active') is True
            and failure and not failure.get('kind') and not failure.get('codes')):
        result.pop('eligibility_failure', None)
    recheck_saved_payer_message(result, case)
    _api.commercial.discard_stale_directory_payer(result, case['previsit_context'].get('payer_id'))
    route_payer = None
    if practice_id:
        # Use current clinic policy so disabled deferral cannot retain an old $0 decision.
        result.pop('collection_policy', None)
        payer_id = case['previsit_context'].get('payer_id')
        if payer_id:
            matches = [row for row in _api.commercial_store.list_connections(practice_id)
                       if row.get('stedi_payer_id') == payer_id]
            if len(matches) == 1:
                route_payer = matches[0]
                policy = (matches[0].get('rules') or {}).get('collection_policy') or {}
                if (isinstance(policy, dict) and policy.get('mode') in {'defer_to_claim', 'collect_verified_copay', 'collect_supported_advance'} and policy.get('approved_by')
                        and policy.get('approved_at') and policy.get('reference')):
                    result['collection_policy'] = policy
        _recheck_saved_clinic_pricing(result, case, practice_id)
        prior_network = ((result.get('benefits') or {}).get('network_status'),
                         (result.get('benefits') or {}).get('network_source'))
        recheck_saved_network_evidence(result, case, practice_id, route_payer)
        if (prior_network != ((result.get('benefits') or {}).get('network_status'),
                             (result.get('benefits') or {}).get('network_source'))
                and (result.get('benefits') or {}).get('network_status') == 'in_network'
                and (result.get('benefits') or {}).get('network_source') == 'clinic_group_agreement'):
            _reprice_saved_new_network(result, case, practice_id,
                group_contract_support.eligibility_route(result, str(payer_id or ''), route_payer))
        _recheck_saved_payer_allowance(result, case, practice_id)
        refresh_saved_requirements(_api, result, case, practice_id)
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
        identity = result.get('identity') or (result.get('benefits') or {}).get('identity') or {}
        if ((result.get('benefits') or {}).get('active') is True
                and (result.get('benefits') or {}).get('service_date_matches') is not False
                and payer_identity_verified(identity)
                and not (result.get('benefits') or {}).get('coverage_status_conflict')):
            result['coverage_status'] = 'VERIFIED'
        if (result['recovery'].get('outcome') == 'no_match'
                and (result.get('benefits') or {}).get('active') is True
                and result['coverage_status'] != 'VERIFIED'):
            result['recovery']['coverage_status'] = 'REVIEW_REQUIRED'
            result['coverage_status'] = 'REVIEW_REQUIRED'
            result['recovery']['message'] = (
                'The follow-up search returned no policies. The earlier payer response still contains active benefits, '
                'but its patient and subscriber identity must match eCW before those amounts are used.')
    if case.get('previsit_cob'):
        cob = case['previsit_cob']
        result['coordination'] = {'state': cob.get('state'), 'reason_code': cob.get('reason_code'),
                                  'missing_fields': cob.get('missing_fields') or [],
                                  'source_fields': cob.get('source_fields') or [],
                                  'checked_at': cob.get('checked_at'), **(cob.get('summary') or {})}
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
    explicit_payment_mode = (case.get('visit_review') or {}).get('payment_mode')
    if explicit_payment_mode in {'cash', 'insurance'}:
        result['payment_mode'] = explicit_payment_mode
        result.pop('cash_fallback', None)
    united_healthcare = _api.commercial.is_united_healthcare_payer(
        result, {'displayName': route_payer.get('display_name')} if route_payer else None)
    if (united_healthcare and result.get('cash_fallback') is not None
            and (case.get('visit_review') or {}).get('payment_mode') != 'cash'):
        # Older saves chose cash automatically when pricing was incomplete.
        # Reopen those UHC payer responses on the insurance path; preserve an
        # explicit clinician choice of cash.
        result['payment_mode'] = 'insurance'
        result.pop('cash_fallback', None)
    insurance_guidance = _api.collection_guidance.build(result, case.get('visit_review'))
    result['insurance_diagnosis'] = _api._insurance_diagnosis(case, result, insurance_guidance)
    pricing_unavailable = (result.get('payment_mode') != 'cash' and insurance_guidance.get('amount_cents') is None
        and case.get('previsit', {}).get('request_state') in {'complete', 'rejected', 'not_requested'}
        and not recovery)
    if (not united_healthcare and (result.get('benefits') or {}).get('active') is not True
            and not timing and recovery.get('state') in {'complete', 'rejected'}
            and recovery.get('outcome') != 'recovered'
            and recovery.get('exhausted') is not False
            and explicit_payment_mode is None):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {}
        result.pop('emg_option', None)
    elif (not united_healthcare and (result.get('benefits') or {}).get('active') is not True
            and not timing and (pricing_unavailable or (recovery.get('state') == 'complete' and recovery.get('outcome') == 'recovered'
            and insurance_guidance.get('amount_cents') is None))
            and explicit_payment_mode is None):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {}
        result.pop('emg_option', None)
    if 'cash_fallback' in result:
        result['cash_fallback']['reason'] = 'Clinic self-pay pricing is shown because ' + _api.amount_explanation.fallback_cause(result) + '.'
    # Cash is a separate price path, never evidence of inactive insurance.
    result['collection_guidance'] = _api.collection_guidance.build(result, case.get('visit_review'))
    option = result.get('emg_option')
    if isinstance(option, dict) and (
            option.get('calculation_version') != 2
            or result['collection_guidance'].get('basis') not in {'patient_obligations', 'anticipated_services', 'out_of_pocket_limit'}
            or (result.get('identity') or {}).get('status') == 'review'
            or (result.get('benefits') or {}).get('has_secondary')
            or recovery.get('reason_code') == 'multiple_active_policies'):
        # Older saved scenarios used the check-in advance as patient cost.
        # A later payer-order or clinic-policy change can also invalidate one.
        option.update(additional_min_cents=None, additional_max_cents=None,
                      total_min_cents=None, total_max_cents=None, scenario_count=0,
                      message='Recalculate the testing scenario with current payer and visit evidence.')
    result['encounter_summary'] = _api.encounter_summary.build(result, case.get('visit_review'))
    result['payment_explanation'] = _api.amount_explanation.build(result, result['collection_guidance'], case.get('visit_review'))
    result['evidence_resolution'] = evidence_resolution.build(result, source)
    return result


def _recovery_event(case: dict, session: dict, stage: str, outcome: str, *, reason: str | None = None,
                    count: int | None = None, http_status: int | None = None) -> None:
    _api.collection_event('coverage_recovery', practice_id=session['practice_id'],
        intake_id=(session.get('intake') or {}).get('id'), case_id=case['id'], stage=stage,
        outcome=outcome, reason_code=reason, count=count, http_status=http_status)
