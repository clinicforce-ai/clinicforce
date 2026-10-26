"""Commercial recovery flow workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api


def _original_medical_policy_established(case: dict) -> bool:
    """A pricing gap cannot justify another paid policy search."""
    result = (case.get('previsit_context') or {}).get('result') or {}
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    return bool(benefits.get('active') is True
                and benefits.get('service_date_matches') is not False
                and not benefits.get('coverage_status_conflict')
                and _api.insurance_identity.patient_benefits_bound(identity))

async def _run_coverage_recovery(case: dict, session: dict, provider: dict) -> None:
    """Bound background work and retain unknown submissions for reconciliation."""
    try:
        await _api.asyncio.wait_for(_api._recover_previsit(case, session, provider), timeout=540)
    except Exception as exc:
        reason = 'recovery_deadline' if isinstance(exc, TimeoutError) else 'recovery_failed'
        _api._recovery_event(case, session, 'recovery', 'indeterminate', reason=reason)
        case['previsit_discovery'].update(request_state='indeterminate', reason_code=reason)
        try:
            case.update(await _api.run_in_threadpool(_api._save, case, session, 'coverage-recovery-interrupted'))
        except Exception:
            _api._recovery_event(case, session, 'persistence', 'unavailable', reason='recovery_save_failed')


async def _reconcile_saved_discovery(case: dict, session: dict) -> None:
    """Add current safe match diagnostics to a legacy discovery response.

    This reads the protected response already obtained from Stedi. It must never
    turn opening an older estimate into a new discovery or eligibility request.
    """
    context, discovery = case['previsit_context'], case['previsit_discovery']
    try:
        payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, discovery['raw_object_key']))
        if not isinstance(payload, dict):
            raise ValueError('Invalid saved discovery response')
        inputs, source = context['inputs'], context['source']
        items = payload.get('items') or []
        candidates = _api.stedi_discovery.candidates(payload, inputs, source)
        for row in candidates:
            row['matches_ecw_policy'] = _api.stedi_discovery.matches_ecw_policy(items[row['index']], inputs, source)
        active_count = _api.stedi_discovery.active_policy_count(candidates, items, _api._previsit_service_types(inputs))
        discovery.update(request_state='complete', candidates=candidates, coverage_count=payload.get('coveragesFound'),
                         raw_candidate_count=len(items), active_policy_count=active_count, candidate_version=5,
                         outcome='no_match' if not items else 'review_needed', checked_at=_api._now())
        if payload.get('status') != 'COMPLETE' or payload.get('errors'):
            discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                             reason_code='discovery_request_rejected')
        else:
            matching = [row for row in candidates if row['verification_status'] == 'VERIFIED']
            conflict = _api.stedi_discovery.active_policy_evidence_conflict(
                candidates, items, _api._previsit_service_types(inputs))
            reason = ('upstream_no_policies' if not items else 'multiple_active_policies' if active_count > 1 else
                      'same_policy_evidence_conflict' if conflict else
                      'identity_or_coverage_review' if not matching else 'verified_candidate_retained')
            discovery.update(stage='candidate_match_review', reason_code=reason)
            detail = ('The saved search returned no policies; this does not establish uninsured status.' if not items else
                'The same medical policy returned conflicting subscriber, date or visit-benefit evidence; reconcile the saved payer rows before pricing.'
                if conflict else 'Saved insurance candidates are retained for review. No new payer request was submitted.')
            if not _original_medical_policy_established(case):
                context['result'].setdefault('eligibility_failure', {})['detail'] = detail
            discovery['selection_error'] = detail
        await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-reconciled')
    except (ValueError, KeyError, TypeError, _api.json.JSONDecodeError):
        discovery.update(request_state='complete', outcome='review_needed', stage='saved_discovery_unavailable',
                         reason_code='saved_discovery_unavailable',
                         selection_error='The saved discovery response could not be read. Check the current insurance card before another discovery request.')
        await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-reconcile-failed')


def _discovered_coverage(inputs: dict, source: dict, item: dict) -> dict:
    return _api.stedi_discovery.discovered_coverage(_api._coverage_snapshot(inputs, source), inputs, item)


def _discovery_pricing_source(source: dict, item: dict, payer: dict) -> dict:
    details = _api.commercial.plan_details(item)
    return {**source, 'payer': payer.get('displayName') or (item.get('payer') or {}).get('name'),
            'payer_source': 'Stedi verified coverage',
            'payer_identifiers': [str((item.get('payer') or {}).get('payorIdentification') or '')],
            'plan': (details.get('names') or [None])[0],
            'plan_details': {'plan': details.get('names') or [],
                             'group_number': [details['group_number']] if details.get('group_number') else []}}


async def _accept_recovered_coverage(case: dict, session: dict, verified: dict, payer: dict,
                                     coverage: dict, *, http_status: int = 200) -> None:
    context, discovery = case['previsit_context'], case['previsit_discovery']
    inputs, source = context['inputs'], context['source']
    pricing_source = _api._discovery_pricing_source(source, verified, payer)
    discovery.update(request_state='complete', outcome='recovered', stage='complete', verified_coverage=coverage,
                     pricing_source=pricing_source, checked_at=_api._now(), reason_code='coverage_recovered', selection_error=None)
    result = _api.deepcopy(context['result'])
    result.pop('eligibility_failure', None); result.pop('eligibility_error', None)
    # A prior route's blocked enrollment does not describe verified reused
    # benefits or a successful check through another route. Snapshots retain it.
    result.pop('enrollment', None)
    if (discovery.get('enrollment') or {}).get('status') != 'LIVE':
        discovery.pop('enrollment', None)
    for candidate in discovery.get('candidates') or []:
        if candidate['id'] == discovery.get('selected_candidate_id'):
            candidate.update(verification_status='VERIFIED',
                verification={'status': 'VERIFIED', 'pathway': discovery.get('identity_pathway') or 'accepted_payer_evidence', 'reason_codes': []},
                reason_codes=(_api.stedi_discovery.parse_discovery_candidate(verified) or {}).get('reason_codes', []))
    result.update(payer=pricing_source.get('payer'), stedi_payer={'name': payer.get('displayName'), 'id': payer['stediId']},
                  plan=pricing_source.get('plan'), issues=[], checked_at=_api._now(),
                  benefits=_api._case_benefits(verified, inputs, source, case))
    await _api.run_in_threadpool(_api._finish_previsit, result, case, inputs, source, verified, session, payer['stediId'], pricing_source=pricing_source)
    guidance = result.get('collection_guidance') or _api.collection_guidance.build(result, case.get('visit_review'))
    _api._recovery_event(case, session, 'pricing', 'priced' if guidance.get('amount_cents') is not None else 'unavailable',
                    reason=guidance.get('basis') if guidance.get('amount_cents') is not None else 'benefits_or_rates_incomplete')
    case['previsit'].update(request_state='complete', checked_at=result['checked_at'], benefits=result['benefits'], requested_service_types=_api._previsit_service_types(inputs))
    case['previsit_original_error_object_key'] = case.get('previsit_raw_object_key')
    case['previsit_raw_object_key'] = discovery['eligibility_object_key']
    if discovery.get('eligibility_sha256'):
        case['previsit_raw_sha256'] = discovery['eligibility_sha256']
    elif case['previsit_raw_object_key'] != case.get('previsit_original_error_object_key'):
        case.pop('previsit_raw_sha256', None)  # Older reused objects have no recorded byte digest.
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-coverage-recovered'))
    _api._recovery_event(case, session, 'eligibility', 'verified', reason='coverage_recovered', http_status=http_status)


async def _recheck_discovery_candidate(case: dict, session: dict, provider: dict, *, discovery_payload: dict | None = None,
                                      selected_route: dict | None = None, inquiry_only: bool = False,
                                      reuse_benefits: bool = False) -> None:
    """Use a selected, persisted discovery candidate without altering eCW."""
    context, discovery = case['previsit_context'], case['previsit_discovery']
    inputs, source = context['inputs'], context['source']
    if _api._previsit_result(case, session['practice_id']).get('eligibility_timing'):
        return
    submitted = False
    try:
        ecw_route = selected_route is not None and discovery.get('route_source') == 'ecw'
        if ecw_route:
            item = {}
            coverage = _api._coverage_snapshot(inputs, source)
        else:
            payload = discovery_payload or _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes,
                discovery.get('route_payload_key') if selected_route else discovery['raw_object_key']))
            candidate = ({'index': discovery['route_item_index']} if selected_route else
                next(row for row in discovery.get('candidates', []) if row['id'] == discovery.get('selected_candidate_id')))
            item = (payload.get('items') or [])[candidate['index']]
            if 'RELATIONSHIP_CONFLICT' in _api.stedi_discovery.verify_candidate(item, inputs, source)['reason_codes']:
                discovery.update(request_state='complete', outcome='review_needed', stage='candidate_match_review',
                    reason_code='subscriber_relationship_conflict',
                    selection_error='Available eCW and payer evidence could not link this subscriber policy to the patient.')
                case.update(await _api.run_in_threadpool(_api._save, case, session, 'subscriber-link-unresolved'))
                return
            coverage = _api._discovered_coverage(inputs, source, item)
        address_inquiry = bool(inquiry_only and not ecw_route and
            _api.stedi_discovery.address_conflict_inquiry(item, inputs, source))
        api_key = _api.stedi_financial.credentials(session['practice_id'])
        payer_data = item.get('payer') or {}
        routes = []
        payer = selected_route or await _api.stedi_payers.search_payer(payer_data.get('name') or payer_data.get('lastName') or '',
            [str(value) for value in (item.get('payerId'), payer_data.get('payorIdentification')) if value], api_key, candidates=routes)
        if not payer:
            if routes:
                discovery.update(payer_routes=routes, route_source='returned_policy',
                    route_item_index=candidate['index'],
                    route_payload_key=discovery.get('card_object_key') if item.get('evidence_source') == 'insurance_card' else discovery.get('raw_object_key'))
            discovery.update(request_state='complete', outcome='review_needed', stage='payer_route_review',
                reason_code='discovered_payer_not_routable', selection_error='The returned payer has no unique supported eligibility route.')
            case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-route-unresolved'))
            _api._recovery_event(case, session, 'payer_routing', 'review_needed', reason='discovered_payer_not_routable')
            return
        if _api.stedi_payers.original_medicare_routing_conflict(payer,
                product=str(item.get('planCoverage') or source.get('plan') or ''),
                issuer=str(payer_data.get('name') or source.get('payer') or '')):
            discovery.update(request_state='complete', outcome='review_needed', stage='payer_route_review',
                reason_code='medicare_advantage_issuer_route_required',
                selection_error='The returned insurer or Medicare Advantage product cannot be checked through CMS HETS. Select its own supported Stedi payer route.')
            case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-route-review'))
            return
        returned_services = {code for row in _api.commercial._stedi_entries(item) for code in row.get('serviceTypeCodes', [])}
        discovery.pop('enrollment', None)
        if (reuse_benefits and _api.stedi_discovery.automatic_match(item, inputs, source)
                and set(_api._previsit_service_types(inputs)) <= returned_services):
            saved_bytes = _api.json.dumps(item).encode()
            key = await _api.run_in_threadpool(_api.object_store.put_staff_document, session['practice_id'], case['id'],
                'discovered-eligibility.json', saved_bytes, 'application/json')
            discovery.update(eligibility_object_key=key, evidence_source='stedi_discovery',
                             eligibility_sha256=_api.hashlib.sha256(saved_bytes).hexdigest())
            await _api._accept_recovered_coverage(case, session, item, payer, coverage)
            _api._recovery_event(case, session, 'eligibility', 'reused', reason='discovery_benefits_sufficient')
            return
        if _api.stedi_payers.enrollment_required(payer):
            npi = (provider.get('billing_npi') if _api.stedi_payers.original_medicare_route(payer)
                   else provider.get('rendering_npi') or provider.get('billing_npi'))
            enrollment = await _api.stedi_payers.eligibility_enrollment(payer['stediId'], npi, api_key)
            discovery['enrollment'] = {**enrollment, 'payer_id': payer['stediId'], 'provider_npi': npi,
                'route': 'original_medicare_hets' if _api.stedi_payers.original_medicare_route(payer)
                else 'payer_specific_eligibility'}
            if enrollment['status'] != 'LIVE':
                discovery.update(request_state='complete', outcome='review_needed', stage='provider_enrollment',
                    reason_code=enrollment['reason_code'], selection_error=_api._enrollment_failure(discovery['enrollment'])['detail'])
                case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-enrollment-required'))
                _api._recovery_event(case, session, 'eligibility', 'unavailable', reason=enrollment['reason_code'])
                return
        body = _api.stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _api._previsit_service_types(inputs),
            include_identity_hints=not address_inquiry, allow_demographic=not coverage.get('member_id'),
            inquiry_only=inquiry_only, place_of_service=inputs.get('place_of_service'))
        basic_body = _api.stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _api._previsit_service_types(inputs),
                                        include_identity_hints=False, allow_demographic=not coverage.get('member_id'), inquiry_only=inquiry_only,
                                        place_of_service=inputs.get('place_of_service'))
        if inputs['service_date'] == _api.date.today().isoformat():
            body['encounter'].pop('dates', None)
            basic_body['encounter'].pop('dates', None)
        fingerprint = _api.hashlib.sha256(_api.json.dumps(body, sort_keys=True).encode()).hexdigest()
        attempts = discovery.setdefault('eligibility_attempts', [])
        if any(row['fingerprint'] == fingerprint for row in attempts):
            discovery.update(request_state='complete', outcome='review_needed', reason_code='duplicate_inquiry_skipped')
            return
        request_key = await _api.run_in_threadpool(_api.object_store.put_staff_document, session['practice_id'], case['id'],
            'recovery-eligibility-request.json', _api.json.dumps(body).encode(), 'application/json')
        attempts.append({'fingerprint': fingerprint, 'request_object_key': request_key})
        discovery.update(request_state='pending', stage='eligibility', outcome=None)
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'recovery-eligibility-submitting'))
        discovery = case['previsit_discovery']
        async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(35, connect=5)) as client:
            _api._recovery_event(case, session, 'eligibility', 'requested')
            submitted = True
            response, verified, _ = await _api._post_stedi_eligibility(
                client, {'Authorization': api_key, 'Content-Type': 'application/json'}, body, basic_body)
        if not isinstance(verified, dict):
            raise ValueError('Invalid eligibility response')
        saved_bytes = _api.json.dumps(verified).encode()
        discovery['eligibility_object_key'] = await _api.run_in_threadpool(_api.object_store.put_staff_document,
            session['practice_id'], case['id'], 'stedi-recovered-eligibility.json', saved_bytes, 'application/json')
        discovery['eligibility_sha256'] = _api.hashlib.sha256(saved_bytes).hexdigest()
        returned_identity = _api.stedi_discovery.candidate_identity(verified, inputs)
        payer_confirmed = bool(address_inquiry and
            _api.stedi_discovery.payer_followup_confirms(item, verified, inputs, source))
        identity_matches = False
        recorded = _api.insurance_identity.compare(verified, _api._coverage_snapshot(inputs, source))
        if returned_identity:
            identity_matches = bool((not coverage.get('member_id') or _api.stedi_discovery.normalized(returned_identity['member_id']) == _api.stedi_discovery.normalized(coverage['member_id'])) and
                all(_api.stedi_discovery.normalized(returned_identity[key]) == _api.stedi_discovery.normalized(coverage.get(key))
                    for key in ('subscriber_first', 'subscriber_last')) and
                (not coverage.get('subscriber_birth_date') or not returned_identity.get('subscriber_birth_date') or
                 _api.insurance_identity.birth_date(coverage['subscriber_birth_date']) == _api.insurance_identity.birth_date(returned_identity['subscriber_birth_date'])) and
                ((returned_identity['relationship'] == 'self') == (coverage['relationship'] in {'18', 'self'})))
            if inquiry_only and coverage.get('relationship') in {None, '', 'unknown'}:
                identity_matches = bool(not coverage.get('member_id') or
                    _api.stedi_discovery.normalized(returned_identity['member_id']) == _api.stedi_discovery.normalized(coverage['member_id']))
            if returned_identity.get('address_conflict') and not payer_confirmed:
                identity_matches = False
            if (recorded.get('policy_differences') and not payer_confirmed
                    and not _api.stedi_discovery.automatic_match(verified, inputs, source)):
                identity_matches = False
        payer_error = (response.is_error or str(verified.get('status') or '').upper() in {'ERROR', 'FAILED', 'REJECTED'}
                       or bool(_api.commercial.eligibility_errors(verified)))
        service_active = _api.stedi_discovery.active_medical(verified, inputs, _api._previsit_service_types(inputs)[0])
        if payer_error or not identity_matches or not service_active:
            if payer_error:
                reason_code, stage = 'eligibility_rejected', 'eligibility_rejected'
                selection_error = ('The follow-up payer check returned an error. Read the saved payer response and '
                                   'correct the member or payer details before retrying.')
            elif not identity_matches:
                reason_code, stage = 'returned_identity_or_coverage_mismatch', 'candidate_match_review'
                fields = sorted({str(row.get('field') or '').replace('_', ' ') for row in
                                 [*(recorded.get('differences') or []), *(recorded.get('policy_differences') or [])]
                                 if row.get('field')})
                payer_subscriber_birth_date = (verified.get('subscriber') or {}).get('dateOfBirth')
                if (coverage.get('subscriber_birth_date') and payer_subscriber_birth_date
                        and _api.insurance_identity.birth_date(coverage['subscriber_birth_date'])
                        != _api.insurance_identity.birth_date(payer_subscriber_birth_date)):
                    fields.append('subscriber date of birth')
                if returned_identity and returned_identity.get('address_conflict'):
                    fields.append('address')
                fields = sorted(set(fields))
                selection_error = ('The follow-up payer response returned, but its '
                                   + (', '.join(fields) if fields else 'patient or subscriber identity')
                                   + ' differs from eCW. Reconcile those fields before using its member balances.')
            else:
                reason_code, stage = 'returned_coverage_not_established', 'service_coverage_review'
                selection_error = ('The follow-up payer response did not establish active medical coverage for '
                                   'the visit and requested service. Check the policy dates and service with the payer.')
            discovery.update(request_state='complete', outcome='review_needed', stage=stage,
                             reason_code=reason_code, selection_error=selection_error)
            _api._recovery_event(case, session, 'eligibility', 'rejected' if payer_error else 'review_needed',
                                 reason=reason_code, http_status=response.status_code)
            if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
                context['result']['eligibility_failure']['detail'] = (
                    'The eCW payer name and ID did not identify one Stedi route. Discovery found a policy. '
                    + selection_error)
                discovery['selection_error'] = context['result']['eligibility_failure']['detail']
            case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-selection-rejected'))
            return
        coverage.update({key: returned_identity[key] for key in ('member_id', 'group_number', 'plan_number') if returned_identity.get(key)})
        if inquiry_only:
            coverage = _api._discovered_coverage(inputs, source, verified)
        if payer_confirmed:
            discovery['identity_pathway'] = 'payer_eligibility_followup'
        await _api._accept_recovered_coverage(case, session, verified, payer, coverage, http_status=response.status_code)
    except (_api.httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, IndexError, _api.commercial.CommercialError):
        selection_error = ('The follow-up payer check outcome is unknown. Check Stedi activity before another payer request.'
                           if submitted else
                           'The selected policy could not be checked. Review its payer route and member details before retrying.')
        discovery.update(request_state='indeterminate' if submitted else 'complete', outcome='review_needed',
                         stage='eligibility' if submitted else 'selection_error', reason_code='selected_policy_check_failed',
                         selection_error=selection_error)
        _api._recovery_event(case, session, 'eligibility', 'indeterminate' if submitted else 'unavailable', reason='selected_policy_check_failed')
        if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
            context['result']['eligibility_failure']['detail'] = (
                'The eCW payer name and ID did not identify one Stedi route. Discovery found a policy. '
                + selection_error)
            discovery['selection_error'] = context['result']['eligibility_failure']['detail']
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-selection-error'))


async def _start_previsit_recovery(case: dict, session: dict, provider: dict, background_tasks: _api.BackgroundTasks | None) -> dict:
    result = case['previsit_context']['result']
    if _original_medical_policy_established(case):
        return _api._previsit_result(case, session['practice_id'])
    if _api._previsit_result(case, session['practice_id']).get('eligibility_timing'):
        return _api._previsit_result(case, session['practice_id'])
    recovery = case.get('previsit_discovery') or {}
    if recovery and _api.stedi_discovery.public_status(recovery)['state'] == 'indeterminate':
        recovery['request_state'] = 'indeterminate'
    legacy_result = bool(
        recovery.get('request_state') == 'complete'
        and recovery.get('outcome') in {'review_needed', 'no_match'}
        and recovery.get('raw_object_key')
        and recovery.get('candidate_version') != 5
    )
    if legacy_result and background_tasks is not None:
        recovery.update(request_state='pending', stage='reconciling_saved_discovery',
                        reason_code='reconciling_saved_discovery')
        case['previsit_discovery'] = recovery
        case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-reconciling')
        background_tasks.add_task(_api._reconcile_saved_discovery, case, session)
        return _api._previsit_result(case, session['practice_id'])
    # A new previsit POST may reuse one recent, patient-bound discovery result
    # for an eligibility inquiry. Reads never submit a payer transaction.
    if (background_tasks is not None and recovery.get('request_state') == 'complete'
            and recovery.get('outcome') == 'review_needed'
            and recovery.get('reason_code') == 'identity_or_coverage_review'
            and recovery.get('raw_object_key') and not recovery.get('selected_candidate_id')
            and not recovery.get('eligibility_attempts')
            and recovery.get('identity_binding') == _api._recovery_binding(
                case['previsit_context']['inputs'], case['previsit_context']['source'])):
        try:
            fresh = (_api.datetime.fromisoformat(recovery['checked_at']) + _api.timedelta(hours=24)
                     > _api.datetime.now(_api.timezone.utc))
            payload = _api.json.loads(await _api.run_in_threadpool(
                _api.object_store.get_object_bytes, recovery['raw_object_key'])) if fresh else {}
            candidates = (_api.stedi_discovery.candidates(payload, case['previsit_context']['inputs'],
                case['previsit_context']['source']) if isinstance(payload, dict)
                and payload.get('status') == 'COMPLETE' and not payload.get('errors') else [])
            one = candidates[0] if len(candidates) == 1 else None
            item = (payload.get('items') or [])[one['index']] if one else None
        except Exception:
            one = item = None
        if (one and item and one['active_medical'] and one['inquiry_eligible']
                and _api.stedi_discovery.address_conflict_inquiry(
                    item, case['previsit_context']['inputs'], case['previsit_context']['source'])):
            recovery.update(request_state='pending', stage='eligibility', outcome=None,
                exhausted=False, selected_candidate_id=one['id'], candidates=candidates,
                evidence_source='stedi_discovery')
            case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-saved-candidate-inquiry-pending')
            background_tasks.add_task(_api._recheck_discovery_candidate, case, session, provider,
                discovery_payload=payload, inquiry_only=True)
            return _api._previsit_result(case, session['practice_id'])
    failure = result.get('eligibility_failure') or {}
    identity_errors = {'65', '67', '71', '72', '73', '75'}
    ready = bool(background_tasks is not None and
                 (recovery.get('recovery_requested') or identity_errors.intersection(failure.get('codes', []))) and
                 _api.stedi_discovery.request_body(case['previsit_context']['inputs'], provider))
    # Completed externally retrieved results can be consumed once without a new
    # transaction. All subsequent opens use the recorded outcome.
    process_saved = recovery.get('request_state') == 'complete' and not recovery.get('outcome') and recovery.get('raw_object_key')
    resume_poll = recovery.get('request_state') == 'indeterminate' and recovery.get('discovery_id') and recovery.get('stage') != 'eligibility'
    if ready and (not recovery or recovery.get('request_state') == 'not_requested' or process_saved or resume_poll):
        recovery.update(request_state='pending', exhausted=False, started_at=recovery.get('started_at') or _api._now(),
                        context_token=case['previsit_context']['context_token'],
                        identity_binding=_api._recovery_binding(case['previsit_context']['inputs'], case['previsit_context']['source']))
        case['previsit_discovery'] = recovery
        case = await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-pending')
        background_tasks.add_task(_api._run_coverage_recovery, case, session, provider)
    return _api._previsit_result(case, session['practice_id'])
