"""Keep saved service categories and incomplete payer evidence scoped to a visit."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api


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
