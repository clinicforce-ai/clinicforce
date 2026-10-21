"""Bounded COB follow-up for a matched UHC member with overlapping coverage."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance.identity import payer_identity_verified


async def _check_previsit_cob(case: dict, session: dict, provider: dict, *,
                              payer: dict | None = None, payload: dict | None = None,
                              api_key: str | None = None) -> None:
    context = case.get('previsit_context') or {}
    result = context.get('result') or {}
    _api.commercial.discard_stale_directory_payer(result, context.get('payer_id'))
    benefits = result.get('benefits') or {}
    recovery = case.get('previsit_discovery') or {}
    if (not _api.commercial.is_united_healthcare_payer(result, payer)
            or not (benefits.get('has_secondary') or recovery.get('reason_code') == 'multiple_active_policies')):
        return
    existing = case.get('previsit_cob') or {}
    if existing.get('state') in {'pending', 'complete', 'indeterminate'}:
        return
    async def not_ready(reason: str, missing_fields: tuple[str, ...] = ()) -> None:
        if (existing.get('state') == 'not_ready' and existing.get('reason_code') == reason
                and existing.get('missing_fields') == list(missing_fields)):
            return
        case['previsit_cob'] = {'state': 'not_ready', 'reason_code': reason,
                                'missing_fields': list(missing_fields)}
        case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-cob-not-ready'))

    identity = benefits.get('identity') or result.get('identity') or {}
    if (benefits.get('active') is not True or not payer_identity_verified(identity)
            or identity.get('differences') or identity.get('policy_differences')):
        await not_ready('cob_identity_mismatch')
        return
    key = api_key or _api.stedi_financial.credentials(session['practice_id'])
    if not key:
        await not_ready('cob_credentials_missing')
        return
    if payer is None and context.get('payer_id'):
        try:
            payer = await _api.stedi_payers.search_payer('', [context['payer_id']], key)
        except (_api.httpx.HTTPError, ValueError, TypeError):
            await not_ready('cob_payer_route_lookup_failed')
            return
    if not payer:
        await not_ready('cob_payer_route_unresolved')
        return
    transaction_support = payer.get('transactionSupport') or {}
    support = transaction_support.get('coordinationOfBenefits') if isinstance(transaction_support, dict) else None
    if support != 'SUPPORTED':
        await not_ready('cob_payer_route_unsupported' if support == 'NOT_SUPPORTED'
                        else 'cob_payer_support_unreported')
        return
    if payload is None and case.get('previsit_raw_object_key'):
        try:
            payload = _api.json.loads(await _api.run_in_threadpool(
                _api.object_store.get_object_bytes, case['previsit_raw_object_key']))
        except (ValueError, TypeError, KeyError):
            payload = None
    payload, source_fields = _api.stedi_cob.with_ecw_birth_dates(
        payload, _api._coverage_snapshot(context.get('inputs') or {}, context.get('source') or {}))
    body, reason, missing_fields = _api.stedi_cob.prepare_request(
        payer, provider, payload, benefits,
        str(result.get('service_date') or (context.get('inputs') or {}).get('service_date') or ''))
    if body is None:
        await not_ready(reason or 'cob_request_not_ready', missing_fields)
        return
    fingerprint = _api.hashlib.sha256(_api.json.dumps(body, sort_keys=True).encode()).hexdigest()
    if existing.get('fingerprint') == fingerprint:
        return
    request_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
        session['practice_id'], case['id'], 'stedi-cob-request.json',
        _api.json.dumps(body).encode(), 'application/json')
    case['previsit_cob'] = {'state': 'pending', 'fingerprint': fingerprint,
                            'request_object_key': request_key, 'started_at': _api._now(),
                            'source_fields': list(source_fields)}
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-cob-submitting'))
    _api._recovery_event(case, session, 'coordination_of_benefits', 'requested')
    try:
        async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(35, connect=5)) as client:
            response = await client.post(_api.stedi_cob.URL,
                headers={'Authorization': key, 'Content-Type': 'application/json'}, json=body)
        answer = response.json()
        if not isinstance(answer, dict):
            raise ValueError('Invalid COB response')
        response_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
            session['practice_id'], case['id'], 'stedi-cob-response.json',
            _api.json.dumps(answer).encode(), 'application/json')
        summary = _api.stedi_cob.response_summary(answer)
        if response.is_error:
            summary['status'] = 'rejected'
            summary['primacy_determined'] = False
            summary['payer_roles'] = []
        case['previsit_cob'].update(state='complete', checked_at=_api._now(),
                                     response_object_key=response_key, summary=summary,
                                     http_status=response.status_code)
        _api._recovery_event(case, session, 'coordination_of_benefits', summary['status'],
                             http_status=response.status_code)
    except (_api.httpx.HTTPError, ValueError, TypeError):
        # The POST may have reached Stedi. Never replay an uncertain request.
        case['previsit_cob'].update(state='indeterminate', reason_code='cob_outcome_unknown')
        _api._recovery_event(case, session, 'coordination_of_benefits', 'indeterminate', reason='cob_outcome_unknown')
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'stedi-cob-finished'))
