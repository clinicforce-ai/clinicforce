"""Reuse a compatible saved previsit case without repeating payer activity."""
from __future__ import annotations

from copy import deepcopy
from types import ModuleType


SAVED_RESPONSE_UNAVAILABLE = ('The protected saved payer response could not be reopened. '
                              'These saved benefit terms are for review only. Rerun the payer check before collection.')


def telehealth_setting(api: ModuleType, value, inputs: dict, cases: list[dict], original_token: str):
    if value.telehealth_pos:
        return {'place_of_service': value.telehealth_pos, 'modality': value.telehealth_modality}, api._now()
    if not api.visit_estimate.is_telehealth(inputs):
        return None, None
    saved = []
    for case in cases:
        context = case.get('previsit_context') or {}
        source = context.get('source') or {}
        setting = (context.get('result') or {}).get('telehealth') or {}
        if (source.get('_telehealth_base_token') == original_token and source.get('_telehealth_confirmed_at')
                and setting.get('place_of_service') in {'10', '02'}
                and setting.get('modality') in {'audio_video', 'audio_only'}):
            saved.append((source['_telehealth_confirmed_at'], setting))
    saved.sort(key=lambda item: item[0], reverse=True)
    if saved and (len(saved) == 1 or saved[0][0] != saved[1][0]):
        return saved[0][1], saved[0][0]
    return None, None


async def telehealth_inquiry_coverage(api: ModuleType, cases: list[dict], inputs: dict,
                                     source: dict, original_token: str, payer_id: str,
                                     coverage: dict) -> dict:
    """Use only a fresh, same-visit payer-bound dependent identity for a new setting."""
    if coverage.get('active_coverages') != 1 or coverage.get('other_active_coverage') or not coverage.get('member_id'):
        return coverage
    candidates = []
    for case in cases:
        context, saved = case.get('previsit_context') or {}, case.get('previsit') or {}
        try:
            checked = api.datetime.fromisoformat(saved['checked_at'])
            fresh = checked <= api.datetime.now(api.timezone.utc) < checked + api.timedelta(hours=24)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if not (fresh and context.get('context_token') == original_token
                and context.get('payer_id') == payer_id and saved.get('request_state') == 'complete'
                and case.get('previsit_raw_object_key')):
            continue
        try:
            raw = await api.run_in_threadpool(api.object_store.get_object_bytes, case['previsit_raw_object_key'])
            if case.get('previsit_raw_sha256') and api.hashlib.sha256(raw).hexdigest() != case['previsit_raw_sha256']:
                continue
            payload = api.json.loads(raw)
            benefits = api._previsit_benefits(payload, coverage, inputs['service_date'], inputs=inputs, source=source)
            identity = benefits.get('identity') or {}
            matched = api.stedi_discovery.candidate_identity(payload, inputs)
            if (matched and matched.get('relationship') == 'dependent'
                    and str((payload.get('provider') or {}).get('npi') or '')
                        == str((source.get('rendering_provider') or {}).get('npi') or '')
                    and bool((source.get('rendering_provider') or {}).get('npi'))
                    and api.insurance_identity.patient_plan_applies(benefits, identity, inputs['service_date'])
                    and api.insurance_identity.patient_benefits_bound(identity)
                    and all(not coverage.get(key) or not matched.get(key)
                            or api.insurance_identity.normalized(coverage[key]) == api.insurance_identity.normalized(matched[key])
                            for key in ('group_number', 'plan_number'))
                    and api.insurance_identity.normalized(matched.get('member_id')) == api.insurance_identity.normalized(coverage.get('member_id'))):
                candidates.append(api.stedi_discovery.discovered_coverage(coverage, inputs, payload))
        except Exception:
            continue
    return candidates[0] if len(candidates) == 1 else coverage


def retain_saved_group_binding(api: ModuleType, inputs: dict, card: dict, card_check: dict,
                               prior_cases: list[dict], original_token: str,
                               original_group: object) -> None:
    """A card-confirmed group already in the 271 need not create a new inquiry slot."""
    if (card.get('count') not in {1, 2} or card_check.get('status') != 'filled_missing'
            or card_check.get('fields_added') != ['group_number']):
        return
    group = str(inputs.get('group_number') or '')
    member = str((card.get('fields') or {}).get('member_id') or '')
    if not group or not member:
        return
    matches = []
    for case in prior_cases:
        saved = case.get('previsit') or {}
        context = case.get('previsit_context') or {}
        result = context.get('result') or {}
        try:
            checked = api.datetime.fromisoformat(saved['checked_at'])
            now = api.datetime.now(api.timezone.utc)
            fresh = checked <= now < checked + api.timedelta(hours=24)
        except (KeyError, TypeError, ValueError):
            fresh = False
        payer_member = ((result.get('insurance_details') or {}).get('payer_subscriber') or {}).get('memberId')
        payer_group = ((result.get('plan_details') or {}).get('stedi') or {}).get('group_number')
        card_payer = str((card.get('fields') or {}).get('payer') or '')
        saved_payer = str((result.get('stedi_payer') or {}).get('name') or result.get('payer') or '')
        payer_matches = not card_payer or bool(saved_payer and api.stedi_payers._same_name(card_payer, saved_payer, []))
        normalize = api.stedi_discovery.normalized
        if (saved.get('request_state') == 'complete' and fresh and case.get('previsit_raw_object_key')
                and context.get('context_token') == original_token
                and (result.get('benefits') or {}).get('active') is True
                and (result.get('benefits') or {}).get('service_date_matches') is not False
                and not (result.get('benefits') or {}).get('coverage_status_conflict')
                and api.insurance_identity.patient_benefits_bound(result.get('identity') or {})
                and payer_matches
                and normalize(member) == normalize(payer_member)
                and normalize(group) == normalize(payer_group)):
            matches.append(case)
    if len(matches) == 1:
        inputs['group_number'] = original_group
        card_check.update(status='read', fields_added=[])


def saved_benefits_for_review(saved: dict | None) -> dict | None:
    """Retain old terms as evidence without authorizing a new financial result."""
    if not isinstance(saved, dict) or not saved:
        return None
    benefits = deepcopy(saved)
    benefits['requires_review'] = True
    benefits['historical_only'] = True
    reasons = benefits.setdefault('review_reasons', [])
    if SAVED_RESPONSE_UNAVAILABLE not in reasons:
        reasons.append(SAVED_RESPONSE_UNAVAILABLE)
    errors = benefits.setdefault('source_errors', [])
    if not any(row.get('kind') == 'saved_raw_unavailable' for row in errors if isinstance(row, dict)):
        errors.append({'path': 'saved_payer_response', 'kind': 'saved_raw_unavailable',
                       'message': SAVED_RESPONSE_UNAVAILABLE})
    return benefits


async def reuse_previsit_case(api: ModuleType, *, case: dict, result: dict,
                              inputs: dict, source: dict, session: dict,
                              coverage: dict, provider: dict, background_tasks,
                              requested_stc: list[str], payer_id: str,
                              refresh_benefits: bool) -> dict | None:
    """Return an existing or held case result when another request is unsafe."""
    saved = case.get("previsit") or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await api._start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get('request_state') == 'partial' and case.get('previsit_context'):
        return api._previsit_result(case, session['practice_id'])
    if saved.get('request_state') == 'indeterminate' and case.get('previsit_context'):
        return api._previsit_result(case, session['practice_id'])
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
                    payload = None
            if payload is None:
                result['benefits'] = saved_benefits_for_review(result['benefits']) or api._previsit_benefits({}, coverage, inputs['service_date'])
                result['issues'].append(SAVED_RESPONSE_UNAVAILABLE)
            await api.run_in_threadpool(api._finish_previsit, result, case, inputs, source, payload, session, payer_id)
            saved_case = await api.run_in_threadpool(api._save, case, session, "previsit-recalculated")
            if api.insurance_identity.unresolved_policy_difference(result['benefits'].get('identity')):
                saved_case.setdefault('previsit_discovery', {'request_state': 'not_requested', 'recovery_requested': True})
                return await api._start_previsit_recovery(saved_case, session, provider, background_tasks)
            if payload and (result['benefits'].get('has_secondary') or
                            (saved_case.get('previsit_discovery') or {}).get('reason_code') == 'multiple_active_policies'):
                await api._check_previsit_cob(saved_case, session, provider)
            return api._previsit_result(saved_case, session['practice_id'])
    if saved.get("request_state") in {"pending", "indeterminate"}:
        result["issues"].append("The payer request is pending or its outcome is unknown. Check Stedi activity before retrying.")
        api.collection_event('eligibility_held', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', status=saved.get('request_state'))
        return result
    return None
