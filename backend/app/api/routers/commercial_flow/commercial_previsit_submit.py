"""Submit one previsit eligibility request after case and payer checks."""
from __future__ import annotations

from types import ModuleType
from app.api.routers.commercial_flow.commercial_previsit_requirements import check_cpt_authorization


async def submit_previsit_eligibility(api: ModuleType, *, payer: dict, provider: dict,
                                      api_key: str, session: dict, case: dict,
                                      result: dict, coverage: dict, inputs: dict,
                                      source: dict, background_tasks,
                                      requested_stc: list[str], body: dict,
                                      basic_body: dict) -> dict:
    """Persist pending before the paid request and retain indeterminate outcomes."""
    payer_id = payer['stediId']
    rendering_npi = provider['rendering_npi']
    rendering_first = provider['rendering_first']
    rendering_last = provider['rendering_last']
    billing_npi = provider['billing_npi']
    if api.stedi_payers.original_medicare_routing_conflict(payer,
            product=str(source.get('plan') or ''), issuer=str(source.get('payer') or '')):
        result['eligibility_failure'] = {'kind': 'payer_routing_unresolved',
            'title': 'Medicare Advantage payer route needed',
            'detail': 'This policy identifies a plan issuer or Medicare Advantage product, but the selected route is CMS HETS.',
            'action': 'Select the actual insurer’s supported Stedi eligibility route before requesting benefits.',
            'codes': []}
        result['issues'].append(result['eligibility_failure']['action'])
        case['previsit'] = {'request_state': 'not_requested'}
        result['benefits'] = api._previsit_benefits({}, coverage, inputs['service_date'])
        await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, None, session, payer_id)
        await api.run_in_threadpool(api._save, case, session, 'previsit-payer-route-review')
        return result
    if api.stedi_payers.enrollment_required(payer):
        npi = billing_npi if api.stedi_payers.original_medicare_route(payer) else (
            rendering_npi if rendering_first and rendering_last and rendering_npi else billing_npi)
        enrollment = await api.stedi_payers.eligibility_enrollment(payer_id, npi, api_key)
        result['enrollment'] = {**enrollment, 'payer_id': payer_id, 'provider_npi': npi,
                                'route': 'original_medicare_hets' if api.stedi_payers.original_medicare_route(payer)
                                else 'payer_specific_eligibility'}
        if enrollment['status'] != 'LIVE':
            result['eligibility_failure'] = api._enrollment_failure(result['enrollment'])
            result['issues'].append(result['eligibility_failure']['action'])
            case['previsit'] = {'request_state': 'not_requested'}
            result['benefits'] = api._previsit_benefits({}, coverage, inputs['service_date'])
            await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, None, session, payer_id)
            case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
            case = await api.run_in_threadpool(api._save, case, session, 'previsit-enrollment-required')
            return await api._start_previsit_recovery(case, session, provider, background_tasks)
    case.pop('previsit_discovery', None)
    case.pop('previsit_cob', None)
    case["previsit"] = {"request_state": "pending"}
    case = await api.run_in_threadpool(api._save, case, session, "previsit-stedi-pending")
    api.collection_event('eligibility_requested', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case['id'], stage='eligibility', source='stedi', count=len(requested_stc))
    try:
        async with api.httpx.AsyncClient(timeout=api.httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await api._post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (api.httpx.HTTPError, ValueError):
        case["previsit"] = {"request_state": "indeterminate"}
        await api.run_in_threadpool(api._save, case, session, "previsit-stedi-indeterminate")
        api.collection_event('eligibility_indeterminate', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', reason_code='transport_or_parse_error')
        result["issues"].append("The Stedi request outcome is unknown. Clinic staff must check payer activity before retrying.")
        return result
    demographic_unresolved = bool(not coverage.get('member_id') and isinstance(payload, dict)
                                  and not api.stedi_discovery.candidate_identity(payload, inputs))
    if response.is_error or not isinstance(payload, dict) or str(payload.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED"} or api.commercial.eligibility_errors(payload) or demographic_unresolved:
        case["previsit"] = {"request_state": "rejected", "identity_retry_version": 1}
        if isinstance(payload, dict):
            raw_bytes = api.json.dumps(payload).encode()
            case["previsit_raw_object_key"] = await api.run_in_threadpool(
                api.object_store.put_staff_document, session["practice_id"], case["id"],
                "previsit-stedi-error.json", raw_bytes, "application/json")
            case["previsit_raw_sha256"] = api.hashlib.sha256(raw_bytes).hexdigest()
        failure = api.commercial.eligibility_failure(payload if isinstance(payload, dict) else {})
        if demographic_unresolved and not api.commercial.eligibility_errors(payload):
            failure = {'kind': 'member_identity_unresolved', 'title': 'Demographic lookup needs recovery',
                'detail': 'The payer did not return matching patient identity and a usable subscriber ID.',
                'action': 'Continuing through card and insurance discovery recovery.', 'codes': []}
        if not coverage.get('member_id'):
            case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        api.collection_event('eligibility_rejected', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', source='stedi', http_status=response.status_code,
                         reason_code=failure.get('kind'))
        failure['attempted_at'] = api._now()
        result['eligibility_failure'] = failure
        result['eligibility_error'] = failure['kind']
        result['issues'].append(f"{failure['title']}. {failure['action']}")
        result['benefits'] = api._previsit_benefits({}, coverage, inputs['service_date'])
        await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, None, session, payer_id)
        result['response_reference'] = payload.get('id') if isinstance(payload, dict) else None
        # Planned services remain editable; failed benefits cannot be verified away.
        case = await api.run_in_threadpool(api._save, case, session, "previsit-stedi-rejected")
        return await api._start_previsit_recovery(case, session, provider, background_tasks)
    raw_bytes = api.json.dumps(payload).encode()
    raw_key = await api.run_in_threadpool(api.object_store.put_staff_document, session["practice_id"], case["id"],
                                      "previsit-stedi-271.json", raw_bytes, "application/json")
    benefits = api._previsit_benefits(payload, coverage, inputs["service_date"], requested_stc[0], inputs=inputs, source=source)
    result["benefits"] = benefits
    result["checked_at"] = api._now()
    api.collection_event('eligibility_received', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case['id'], stage='eligibility', source='stedi', http_status=response.status_code)
    await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, payload, session, payer_id)
    case["previsit"] = {"request_state": "complete", "benefits": benefits,
                        "checked_at": result["checked_at"], "requested_service_types": requested_stc}
    case["previsit_raw_object_key"] = raw_key
    case["previsit_raw_sha256"] = api.hashlib.sha256(raw_bytes).hexdigest()
    case = await check_cpt_authorization(api, result, case, payload, inputs, session, api_key)
    if (case.get('cpt_authorization') or {}).get('state') == 'complete':
        await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, payload, session, payer_id)
    saved_case = await api.run_in_threadpool(api._save, case, session, "previsit-stedi-complete")
    if (benefits.get('identity') or {}).get('policy_differences'):
        saved_case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        return await api._start_previsit_recovery(saved_case, session, provider, background_tasks)
    if benefits.get('active') is False:
        saved_case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        result.update(eligibility_error='coverage_inactive', eligibility_failure={
            'kind': 'coverage_inactive', 'title': 'Searching for current coverage',
            'detail': 'The known policy returned inactive coverage. Continuing through insurance recovery.',
            'action': 'Searching for active medical coverage for this appointment.', 'codes': []})
        return await api._start_previsit_recovery(saved_case, session, provider, background_tasks)
    if benefits.get('has_secondary'):
        await api._check_previsit_cob(saved_case, session, provider, payer=payer,
                                      payload=payload, api_key=api_key)
        return api._previsit_result(saved_case, session['practice_id'])
    result["revision"] = saved_case.get("revision", 0)
    return result
