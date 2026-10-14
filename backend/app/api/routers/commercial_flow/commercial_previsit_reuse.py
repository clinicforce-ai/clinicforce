"""Reuse a compatible saved previsit case without repeating payer activity."""
from __future__ import annotations

from types import ModuleType


async def reuse_previsit_case(api: ModuleType, *, case: dict, result: dict,
                              inputs: dict, source: dict, session: dict,
                              coverage: dict, provider: dict, background_tasks,
                              requested_stc: list[str], payer_id: str,
                              refresh_benefits: bool) -> dict | None:
    """Return an existing or held case result when another request is unsafe."""
    saved = case.get("previsit") or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await api._start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get('request_state') == 'rejected' and saved.get('identity_retry_version') == 1 and case.get('previsit_context'):
        old = case['previsit_context']['result']
        failure = old.get('eligibility_failure') or {}
        if failure.get('codes'):
            failure = {**api.commercial.eligibility_failure({'errors': [{'code': code} for code in failure['codes']]}), 'attempted_at': failure.get('attempted_at')}
        if {'65', '67', '71', '72', '73', '75'}.intersection(failure.get('codes', [])):
            result.update(eligibility_failure=failure, eligibility_error=old.get('eligibility_error'),
                          issues=[f"{failure['title']}. {failure['action']}"],
                          benefits=api._previsit_benefits({}, coverage, inputs['service_date']))
            await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, None, session, payer_id)
            result['response_reference'] = old.get('response_reference')
            case = await api.run_in_threadpool(api._save, case, session, 'previsit-rejection-reused')
            return await api._start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get("request_state") == "complete":
        try:
            fresh = api.datetime.fromisoformat(saved["checked_at"]) + api.timedelta(hours=24) > api.datetime.now(api.timezone.utc)
        except (TypeError, ValueError, KeyError):
            fresh = False
        if api.visit_estimate.reconcile_eligibility(source.get('saved_eligibility') or {},
                saved.get('benefits') or {}, saved.get('checked_at'))['requires_review']:
            fresh = False
        if fresh and (saved.get("requested_service_types", [saved.get("requested_service_type", "98")]) == requested_stc):
            result["checked_at"] = saved.get("checked_at")
            result["benefits"] = dict(saved.get("benefits") or {})
            payload = None
            if case.get("previsit_raw_object_key"):
                try:
                    raw = await api.run_in_threadpool(api.object_store.get_object_bytes, case["previsit_raw_object_key"])
                    payload = api.json.loads(raw)
                    result["benefits"] = api._case_benefits(payload, inputs, source, case)
                except Exception:
                    result["benefits"]["requires_review"] = True
                    result["benefits"].setdefault("review_reasons", []).append("Saved benefit evidence could not be verified.")
                    result["issues"].append("Saved benefit evidence could not be verified. Clinic staff should check the protected Stedi response before quoting a patient amount.")
            if payload is None:
                result['benefits']['requires_review'] = True
                result['benefits'].setdefault('review_reasons', []).append('Saved benefit evidence is unavailable for recalculation.')
            await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, payload, session, payer_id)
            saved_case = await api.run_in_threadpool(api._save, case, session, "previsit-recalculated")
            if (result['benefits'].get('identity') or {}).get('policy_differences'):
                saved_case.setdefault('previsit_discovery', {'request_state': 'not_requested', 'recovery_requested': True})
                return await api._start_previsit_recovery(saved_case, session, provider, background_tasks)
            return api._previsit_result(saved_case)
    if saved.get("request_state") in {"pending", "indeterminate"}:
        result["issues"].append("The payer request is pending or its outcome is unknown. Check Stedi activity before retrying.")
        api.collection_event('eligibility_held', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', status=saved.get('request_state'))
        return result
    return None
