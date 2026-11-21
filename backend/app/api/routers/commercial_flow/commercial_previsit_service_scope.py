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
    codes = result.get('codes') or []
    selected = result.get('selected_ids') or []
    lines = price.get('lines') or []
    suggested_injection = (
        plan.get('selection_source') == 'suggested'
        and plan.get('method') in {'history', 'appointment'}
        and len(codes) == len(selected) == len(lines) == 1
        and plan.get('recommended_ids') == selected
        and codes[0].get('id') == lines[0].get('id') == selected[0]
        and codes[0].get('code') == lines[0].get('code')
        and codes[0].get('code') in botox_review.PROCEDURE_CODES
        and codes[0].get('units') == 1 and not codes[0].get('modifiers')
        and price.get('complete') is not True)
    if (plan.get('kind') != 'botox' or plan.get('selection_source') == 'clinician'
            or 'selected_ids' in review or result.get('service_selection_status') != 'preview'
            or ((codes or selected or lines) and not suggested_injection)
            or price.get('mode') != 'services'
            or not _api.re.search(r'\b(?:fu|f/u|follow[ -]?up)\b.*\b(?:after|post[ -]?)\s*(?:botox|btx)\b', reason, _api.re.I)
            or botox_review.is_botox_visit(inputs)
            or any(row.get('service_status') == 'scheduled'
                   or row.get('code') in botox_review.SELECTABLE_CODES
                   for row in inputs.get('lines') or [])
            or inputs.get('appointment_type') != 'Televisit'
            or inputs.get('place_of_service') != '10'
            or inputs.get('telehealth_modality') != 'audio_video'):
        return
    office_inputs = ({**inputs, 'visit_type': 'established'}
                     if not inputs.get('visit_type') and result.get('established_for_planning') is True else inputs)
    available = _api._previsit_codes(office_inputs)
    result['available_codes'] = available
    result['service_plan'] = {**_api.visit_estimate.service_plan(available, office_inputs, context.get('source') or {}),
                              'kind': 'office', 'recommended_ids': [], 'selection_source': 'suggested',
                              'method': 'saved_projection',
                              'recommendation_basis': 'Post-injection follow-up without a new injection.'}
    result['codes'] = []
    result['selected_ids'] = []
    result['selected_service_count'] = 0
    price.update(complete=False, lines=[], scenarios=[], collection=None, service_issues=[], allowed_min_cents=None,
                 allowed_max_cents=None, patient_min_cents=None, patient_max_cents=None)
    for key in ('botox_review', 'botox_preview', 'provisional_scenario',
                'temporary_rate_reference', 'fee_proxy_estimate', 'arrival_collection',
                'planning_estimate', 'cpt_authorization', 'requirement_evidence',
                'requirement_sources', 'candidate_requirements', 'public_source_rules',
                'public_source_checked_day', 'published_requirements_version'):
        result.pop(key, None)
