"""Keep saved service categories and incomplete payer evidence scoped to a visit."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance import botox_review


def cash_preview_selection(result: dict, available: dict) -> list[str] | None:
    """Confirm only the exact saved nerve-testing preview for a package-only review."""
    selected = result.get('selected_ids') or []
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    if (not selected or len(selected) != len(set(selected)) or price.get('mode') != 'services'
            or set(selected) != {row.get('id') for row in lines}
            or any(key not in available for key in selected)
            or not any(_api.visit_estimate.clinical_group(available[key]['code']) == 'nerve'
                       for key in selected)):
        return None
    return selected


def reviewed_service_types(cases: list[dict], appointment_id: str, service_date: str,
                           context_token: str) -> list[str]:
    """Carry selected or provisional combined-care CPT categories into a fresh inquiry."""
    for case in reversed(cases):
        context = case.get('previsit_context') or {}
        source = context.get('source') or {}
        prior_inputs = context.get('inputs') or {}
        review = case.get('visit_review') or {}
        if (source.get('appointment_id') != appointment_id
                or prior_inputs.get('service_date') != service_date
                or review.get('context_token') != context_token):
            continue
        saved_result = context.get('result') or {}
        selected = set(review.get('selected_ids') or saved_result.get('selected_ids') or [])
        rows = [row for row in saved_result.get('codes') or [] if row.get('id') in selected]
        if not selected and review.get('separate_em_planned'):
            rows = saved_result.get('codes') or []
            categories = list(dict.fromkeys(
                category for row in rows
                if (category := _api.visit_estimate.service_type(str(row.get('code') or ''), {}))))
            if '73' in categories and '98' in categories:
                return categories
        if selected and len(rows) == len(selected):
            return list(dict.fromkeys(
                category for row in rows
                if (category := _api.visit_estimate.service_type(str(row.get('code') or ''), {}))))
    return []


def retain_incomplete_service_evidence(benefits: dict, case: dict) -> dict:
    """Keep an unreturned linked service from becoming a collectable estimate."""
    if (case.get('previsit') or {}).get('request_state') in {'partial', 'indeterminate'} and case.get('previsit_raw_object_key'):
        reason = 'Separate requested service inquiry evidence is unavailable for the combined visit.'
        reasons = benefits.setdefault('review_reasons', [])
        if reason not in reasons:
            reasons.append(reason)
        benefits['requires_review'] = True
    return benefits


def upgrade_saved_post_botox_followup(result: dict, case: dict) -> None:
    """Reproject an unedited old injection preview as an office follow-up."""
    context = case.get('previsit_context') or {}
    inputs = context.get('inputs') or {}
    review = case.get('visit_review') or {}
    plan = result.get('service_plan') or {}
    price = result.get('price_range') or {}
    reason = str(inputs.get('reason') or '')
    if (plan.get('kind') != 'botox' or plan.get('selection_source') == 'clinician'
            or 'selected_ids' in review or result.get('service_selection_status') != 'preview'
            or result.get('codes') or result.get('selected_ids') or price.get('lines')
            or price.get('mode') != 'services'
            or not _api.re.search(r'\b(?:fu|f/u|follow[ -]?up)\b.*\b(?:after|post[ -]?)\s*(?:botox|btx)\b', reason, _api.re.I)
            or botox_review.is_botox_visit(inputs)
            or inputs.get('appointment_type') != 'Televisit'
            or inputs.get('place_of_service') != '10'
            or inputs.get('telehealth_modality') != 'audio_video'):
        return
    result['available_codes'] = _api._previsit_codes(inputs)
    result['service_plan'] = {**plan, 'kind': 'office', 'recommended_ids': [],
                              'selection_source': 'suggested', 'method': 'saved_projection',
                              'recommendation_basis': 'Post-injection follow-up without a new injection.'}
    result['selected_service_count'] = 0
    price.update(complete=False, lines=[], service_issues=[], allowed_min_cents=None,
                 allowed_max_cents=None, patient_min_cents=None, patient_max_cents=None)
    for key in ('botox_review', 'botox_preview', 'provisional_scenario',
                'temporary_rate_reference', 'fee_proxy_estimate', 'arrival_collection',
                'planning_estimate'):
        result.pop(key, None)
