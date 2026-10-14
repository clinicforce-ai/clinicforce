"""Commercial recovery search workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

async def _recover_known_payer(case: dict, session: dict, provider: dict) -> None:
    context, recovery = case['previsit_context'], case['previsit_discovery']
    source = context['source']
    # The normal path already queried a resolved payer; do not pay to repeat it.
    if (context.get('payer_id') or recovery.get('steps', {}).get('known_payer')
            or not source.get('payer') or source.get('active_coverages', 0) > 1
            or context['result'].get('eligibility_error') == 'payer_routing_unresolved'):
        return
    recovery.setdefault('steps', {})['known_payer'] = 'unavailable'
    routes = []
    try:
        payer = await _api.stedi_payers.search_payer(source['payer'], source.get('payer_identifiers') or [],
            _api.stedi_financial.credentials(session['practice_id']), plan=source.get('plan'), candidates=routes)
        if not payer:
            recovery.update(payer_routes=routes, route_source='ecw')
            return
        recovery.update(route_source='ecw', evidence_source='ecw_insurance_hints')
        await _api._recheck_discovery_candidate(case, session, provider, selected_route=payer, inquiry_only=True)
        recovery['steps']['known_payer'] = recovery.get('outcome') or recovery['request_state']
    except (_api.httpx.HTTPError, ValueError):
        _api._recovery_event(case, session, 'known_payer', 'unavailable', reason='payer_directory_unavailable')


async def _recover_mbi(case: dict, session: dict, provider: dict) -> None:
    context, recovery = case['previsit_context'], case['previsit_discovery']
    if recovery.get('steps', {}).get('mbi'):
        return
    if recovery.get('coverage_count') or recovery.get('candidates'):
        recovery.setdefault('steps', {})['mbi'] = 'policy_order_unresolved'
        return
    body = _api.stedi_discovery.mbi_request_body(context['inputs'], context['source'], provider)
    recovery.setdefault('steps', {})['mbi'] = 'not_applicable' if not body else 'enrollment_unavailable'
    if not body:
        return
    ip_chain = context['source'].get('_request_ip_chain')
    if not ip_chain:
        recovery['steps']['mbi'] = 'request_trace_unavailable'
        return
    api_key = _api.stedi_financial.credentials(session['practice_id'])
    submitted = False
    try:
        # Both are read-only checks. Never submit or attest enrollment here.
        cms = await _api.stedi_payers.search_payer('CMS', ['CMS'], api_key)
        lookup = await _api.stedi_payers.search_payer('CMS MBI Lookup Without SSN', ['MBILUNOSSN'], api_key)
        for payer in (cms, lookup):
            enrollment = (await _api.stedi_payers.eligibility_enrollment(payer['stediId'], provider['billing_npi'], api_key)
                          if payer else {'status': 'MISSING', 'reason_code': 'payer_route_missing'})
            recovery['mbi_enrollment'] = enrollment
            if enrollment['status'] != 'LIVE':
                _api._recovery_event(case, session, 'mbi', 'skipped', reason=enrollment['reason_code'])
                return
        request_key = await _api.run_in_threadpool(_api.object_store.put_staff_document, session['practice_id'], case['id'],
            'mbi-request.json', _api.json.dumps(body).encode(), 'application/json')
        recovery.update(request_state='pending', stage='eligibility', outcome=None, mbi_request_object_key=request_key)
        recovery['steps']['mbi'] = 'pending'
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'mbi-lookup-submitting'))
        recovery = case['previsit_discovery']
        _api._recovery_event(case, session, 'mbi', 'requested')
        async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(35, connect=5)) as client:
            submitted = True
            response = await client.post(_api.stedi_discovery.MBI_URL,
                headers={'Authorization': api_key, 'X-Forwarded-For': ip_chain}, json=body)
        item = response.json()
        if not isinstance(item, dict):
            raise ValueError('Invalid MBI response')
        recovery['mbi_response_object_key'] = await _api.run_in_threadpool(_api.object_store.put_staff_document,
            session['practice_id'], case['id'], 'mbi-response.json', _api.json.dumps(item).encode(), 'application/json')
        if str(item.get('status') or '').upper() == 'PENDING':
            recovery.update(request_state='indeterminate', stage='eligibility', reason_code='mbi_outcome_unknown')
            recovery['steps']['mbi'] = 'indeterminate'
            return
        identity = _api.stedi_discovery.candidate_identity(item, context['inputs'])
        managed_plan = any(row.get('code') == 'U' and row.get('insuranceTypeCode') in {'HM', 'HN'}
                           for row in _api.commercial._stedi_entries(item))
        if (response.is_error or str(item.get('status') or '').upper() in {'ERROR', 'FAILED', 'REJECTED'}
                or item.get('tradingPartnerServiceId') not in {'CMS', 'CMSMED', cms['stediId']}
                or managed_plan or not identity or identity['relationship'] != 'self'
                or not _api.stedi_discovery.active_medical(item, context['inputs'])):
            recovery.update(request_state='complete', outcome='review_needed', reason_code='mbi_not_verified')
            recovery['steps']['mbi'] = 'not_verified'
            return
        recovery.update(eligibility_object_key=recovery['mbi_response_object_key'], evidence_source='medicare_mbi_lookup')
        recovery['steps']['mbi'] = 'recovered'
        # STC 30 can verify a policy without supplying specialist cost sharing.
        # The normalizer preserves unavailable service benefits and prices.
        await _api._accept_recovered_coverage(case, session, item, cms,
            _api._discovered_coverage(context['inputs'], context['source'], item), http_status=response.status_code)
        case['previsit']['requested_service_types'] = ['30']
    except (_api.httpx.HTTPError, ValueError, TypeError, KeyError):
        recovery['steps']['mbi'] = 'indeterminate' if submitted else 'unavailable'
        if submitted:
            recovery.update(request_state='indeterminate', stage='eligibility', reason_code='mbi_outcome_unknown')
        _api._recovery_event(case, session, 'mbi', recovery['steps']['mbi'])


async def _recover_previsit(case: dict, session: dict, provider: dict) -> None:
    """Finish every applicable automatic route before choosing cash pricing."""
    if _api._previsit_result(case).get('eligibility_timing'):
        return
    recovery = case['previsit_discovery']
    recovery['exhausted'] = False
    if recovery.get('card_conflict'):
        # A conflicting eCW policy cannot be tried as a fallback. Only the
        # patient-bound card route is checked, once, with response validation.
        await _api._recover_card(case, session, provider)
        recovery = case['previsit_discovery']
        if recovery.get('request_state') not in {'indeterminate'} and recovery.get('outcome') != 'recovered':
            recovery.update(request_state='complete', outcome='review_needed', stage='card_review',
                            reason_code=recovery.get('reason_code') or 'card_alternative_not_verified')
        recovery['exhausted'] = recovery.get('request_state') == 'complete'
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'card-alternative-finished'))
        return
    # Resuming a saved discovery polls/consumes that transaction first. Opening
    # a completed legacy result never starts these new paid recovery stages.
    if not recovery.get('raw_object_key'):
        await _api._recover_saved_policy(case, session, provider)
        if not _api._recovery_stopped(case):
            await _api._recover_card(case, session, provider)
        if not _api._recovery_stopped(case):
            await _api._recover_known_payer(case, session, provider)
    if not _api._recovery_stopped(case):
        recovery = case['previsit_discovery']
        recovery.update(request_state='pending', outcome=None, stage='discovery')
        await _api._recover_discovery(case, session, provider)
    if not _api._recovery_stopped(case):
        await _api._recover_mbi(case, session, provider)
    recovery = case['previsit_discovery']
    recovery['exhausted'] = recovery.get('request_state') in {'complete', 'rejected'}
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'coverage-recovery-finished'))
    _api._recovery_event(case, session, 'recovery', 'complete' if recovery['exhausted'] else 'indeterminate',
        reason='coverage_recovered' if recovery.get('outcome') == 'recovered' else 'available_paths_exhausted' if recovery['exhausted'] else 'outcome_unknown')


async def _recover_discovery(case: dict, session: dict, provider: dict) -> None:
    """Discovery with distinct documented addresses; never replay an unknown POST."""
    context = case['previsit_context']
    inputs = context['inputs']
    if _api._previsit_result(case).get('eligibility_timing'):
        return
    discovery = case['previsit_discovery']
    api_key = _api.stedi_financial.credentials(session['practice_id'])
    try:
        if api_key and not discovery.get('raw_object_key') and not discovery.get('steps', {}).get('discovery_enrollment'):
            try:
                route = await _api.stedi_payers.search_payer('Stedi Insurance Discovery', ['DISCOVERY'], api_key)
                enrollment = (await _api.stedi_payers.eligibility_enrollment(route['stediId'],
                    provider.get('rendering_npi') or provider.get('billing_npi'), api_key)
                    if route else {'status': 'MISSING', 'reason_code': 'payer_route_missing'})
                discovery['discovery_enrollment'] = enrollment
                status = 'live' if enrollment['status'] == 'LIVE' else 'unavailable' if enrollment['status'] == 'LOOKUP_FAILED' else 'limited_search'
            except (_api.httpx.HTTPError, ValueError):
                status = 'unavailable'
            discovery.setdefault('steps', {})['discovery_enrollment'] = status
            _api._recovery_event(case, session, 'discovery_enrollment', status)
            # Enrollment expands the search but is not required by DISCOVERY.
        addresses = [inputs.get('patient', {}).get('address') or {},
                     *inputs.get('patient', {}).get('previous_addresses', [])[:2]]
        # Deduplicate before changing state for a retry, including when the last
        # recorded address would produce the same request as the current one.
        addresses = list({_api.json.dumps(_api.stedi_discovery.request_body(
            {**inputs, 'patient': {**inputs['patient'], 'address': address}}, provider), sort_keys=True): address
            for address in addresses}.values())
        for address_index in range(discovery.get('address_index', 0), len(addresses)):
            discovery['address_index'] = address_index
            async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(140, connect=5)) as client:
                headers = {'Authorization': api_key}
                payload = None
                if discovery.get('raw_object_key'):
                    payload = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, discovery['raw_object_key']))
                    _api._recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_response')
                if payload is None:
                    request_inputs = {**inputs, 'patient': {**inputs['patient'], 'address': addresses[address_index]}}
                    body = _api.stedi_discovery.request_body(request_inputs, provider)
                    fingerprint = _api.hashlib.sha256(_api.json.dumps(body, sort_keys=True).encode()).hexdigest()
                    attempts = discovery.setdefault('attempts', [])
                    if any(row['fingerprint'] == fingerprint for row in attempts):
                        continue
                    request_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
                        session['practice_id'], case['id'], 'stedi-discovery-request.json', _api.json.dumps(body).encode(), 'application/json')
                    attempts.append({'fingerprint': fingerprint, 'request_object_key': request_key})
                    discovery.update(request_state='pending', stage='discovery', outcome=None, selection_error=None)
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-submitting'))
                    discovery = case['previsit_discovery']
                    _api._recovery_event(case, session, 'discovery', 'requested', count=len(attempts),
                                    reason='previous_address' if address_index else 'current_address')
                    response = await client.post(_api.stedi_discovery.URL, headers=headers, json=body)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid discovery response')
                    if response.is_error:
                        payload['status'] = 'ERROR'
                    discovery['discovery_id'] = payload.get('discoveryId')
                    discovery['raw_object_key'] = await _api.run_in_threadpool(_api.object_store.put_staff_document,
                        session['practice_id'], case['id'], 'stedi-discovery.json', _api.json.dumps(payload).encode(), 'application/json')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-response'))
                    discovery = case['previsit_discovery']
                    _api._recovery_event(case, session, 'discovery', 'received', http_status=response.status_code)
                for _ in range(2):
                    if payload.get('status') != 'PENDING' or not _api.re.fullmatch(r'[A-Za-z0-9_-]{1,200}', str(discovery.get('discovery_id') or '')):
                        break
                    response = await client.get(_api.stedi_discovery.URL + '/' + str(discovery['discovery_id']), headers=headers)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid discovery response')
                    if response.is_error:
                        payload['status'] = 'ERROR'
                discovery.update(checked_at=_api._now(), coverage_count=payload.get('coveragesFound'))
                if any(row.get('code') == 'discovery::enrollment::auto_enrollment_created' for row in payload.get('warnings') or []):
                    _api._recovery_event(case, session, 'discovery', 'warning', reason='auto_enrollment_created')
                discovery['raw_object_key'] = await _api.run_in_threadpool(_api.object_store.put_staff_document,
                    session['practice_id'], case['id'], 'stedi-discovery.json', _api.json.dumps(payload).encode(), 'application/json')
                if discovery.get('attempts'):
                    discovery['attempts'][-1].update(response_object_key=discovery['raw_object_key'],
                        discovery_id=discovery.get('discovery_id'), status=payload.get('status'))
                if payload.get('status') == 'PENDING':
                    discovery.update(request_state='indeterminate', stage='awaiting_result', reason_code='discovery_pending')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-awaiting-result'))
                    _api._recovery_event(case, session, 'discovery', 'pending', reason='discovery_pending')
                    return
                items = payload.get('items') or []
                candidates = _api.stedi_discovery.candidates(payload, inputs, context['source'])
                _api._recovery_event(case, session, 'discovery_result', 'received', count=len(items),
                                reason='upstream_no_policies' if payload.get('status') == 'COMPLETE' and not payload.get('errors') and not items else 'upstream_response')
                _api._recovery_event(case, session, 'candidate_filter', 'complete', count=len(candidates),
                                reason='candidates_retained' if candidates else 'all_candidates_filtered' if items else 'no_upstream_candidates')
                for row in candidates:
                    row['matches_ecw_policy'] = _api.stedi_discovery.matches_ecw_policy(items[row['index']], inputs, context['source'])
                discovery.update(request_state='complete', candidates=candidates, raw_candidate_count=len(items), candidate_version=4,
                                 outcome='no_match' if not items else 'review_needed')
                if payload.get('status') != 'COMPLETE' or payload.get('errors'):
                    discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                                     reason_code='discovery_request_rejected')
                    _api._recovery_event(case, session, 'discovery', 'rejected', reason='discovery_request_rejected')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-finished'))
                    return
                matching = [row for row in candidates if row['verification_status'] == 'VERIFIED']
                # Identical discovery responses with different payer route IDs
                # describe one policy; distinct active policies still need order.
                candidate = (_api.stedi_discovery.one_active_policy(candidates, items, _api._previsit_service_types(inputs))
                             if context['source'].get('active_coverages', 0) <= 1 else None)
                if candidate:
                    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=candidate['id'],
                                     evidence_source='stedi_discovery')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-recovered-member-pending'))
                    _api._recovery_event(case, session, 'candidate_match', 'matched', count=1)
                    await _api._recheck_discovery_candidate(case, session, provider, discovery_payload=payload, reuse_benefits=True)
                    return
                inquiry = [row for row in candidates if row.get('inquiry_eligible')]
                if len(inquiry) == 1 and sum(row['active_medical'] for row in candidates) <= 1 and context['source'].get('active_coverages', 0) <= 1:
                    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=inquiry[0]['id'], evidence_source='stedi_discovery')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-candidate-inquiry-pending'))
                    await _api._recheck_discovery_candidate(case, session, provider, discovery_payload=payload, inquiry_only=True)
                    return
                reason = 'no_coverage_found' if not items else 'multiple_active_policies' if len(matching) > 1 else 'identity_or_coverage_review'
                detail = ('No coverage was returned by the completed insurance search. This does not establish that the patient is uninsured.'
                          if not items else 'Returned coverage requires identity, service-date or payer-order reconciliation before it can supply an insured estimate.')
                discovery.update(stage='candidate_match_review', reason_code=reason, selection_error=detail)
                context['result'].setdefault('eligibility_failure', {})['detail'] = detail
                _api._recovery_event(case, session, 'candidate_match', discovery['outcome'], reason=reason, count=len(candidates))
                case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-finished'))
                if items or address_index + 1 >= len(addresses):
                    return
                # Retry only a completed no-match with a different recorded address.
                discovery.pop('raw_object_key', None)
                discovery.pop('discovery_id', None)
                discovery.update(request_state='pending', stage='discovery', address_index=address_index + 1)
                case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-address-retry'))
                discovery = case['previsit_discovery']
    except (_api.httpx.HTTPError, ValueError, KeyError, TypeError):
        case['previsit_discovery'].update(request_state='indeterminate', reason_code='transport_or_parse_error')
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-discovery-indeterminate'))
        _api._recovery_event(case, session, 'discovery', 'indeterminate', reason='transport_or_parse_error')
