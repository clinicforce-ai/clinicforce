"""Bounded, patient-free tasks from already saved visit evidence; no provider calls."""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import date, timedelta

from app.insurance import visit_collection_model


def fingerprint(result: dict, review: dict | None) -> str:
    """Change when selected evidence, services, pricing, policy or rule changes."""
    relevant = {key: result.get(key) for key in (
        'service_date', 'codes', 'selected_ids', 'benefits', 'price_range',
        'temporary_rate_reference', 'pricing_scope', 'collection_policy',
        'visit_collection_policies', 'requirement_evidence', 'payment_mode')}
    relevant['review'] = review or {}
    relevant['rule_version'] = visit_collection_model.RULE_VERSION
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()


def summarize(case: dict) -> dict:
    """Return deidentified visit and clinic tasks for one already linked case."""
    result = (case.get('previsit_context') or {}).get('result') or {}
    guidance = result.get('collection_guidance') or {}
    model = guidance.get('visit_model') or {}
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []
    requirement_tasks = [
        {'code': item.get('code'), 'kind': kind, 'status': item[kind].get('status')}
        for item in procedures for kind in ('referral', 'authorization')
        if item.get(kind, {}).get('status') == 'Verification required']
    clinic_tasks = [{key: row.get(key) for key in ('code', 'modifiers', 'place_of_service')}
                    for row in model.get('price_acquisition') or []]
    return {'case_id': case.get('id'), 'scenario': result.get('fixture_scenario'),
            'service_date': result.get('service_date'),
            'evidence_fingerprint': fingerprint(result, case.get('visit_review')),
            'decision': model.get('collection_decision_type'),
            'visit_actions': list(model.get('remaining_actions') or []),
            'requirement_tasks': requirement_tasks, 'clinic_price_tasks': clinic_tasks}


def worklist(cases: list[dict]) -> dict:
    visits = [summarize(case) for case in cases]
    unique = {}
    for visit in visits:
        for task in visit['clinic_price_tasks']:
            key = (task['code'], tuple(task['modifiers'] or []), task['place_of_service'])
            unique.setdefault(key, task)
    return {'visits': visits, 'clinic_price_tasks': list(unique.values()),
            'checked_count': len(visits), 'payer_requests': 0}


def staging_schedule_summary(today: date | None = None) -> dict:
    """Run the daily staging-only saved-fixture check; log aggregates only."""
    if (os.getenv('PUBLIC_BASE_URL', '').rstrip('/') != 'https://staging.clinicforce.ai'
            or os.getenv('COLLECTION_ENGINE_V2_ENABLED') != 'true'):
        raise RuntimeError('Scheduled collection check is staging-only')
    from app.insurance import store
    from app.insurance.staging_synthetic import STAGING_NMCT_PRACTICE_ID, project_saved
    today = today or date.today()
    cases = store.staging_synthetic_due(STAGING_NMCT_PRACTICE_ID, today.isoformat(),
                                        (today + timedelta(days=30)).isoformat())
    projected = []
    for case in cases:
        copy = deepcopy(case)
        copy['previsit_context']['result'] = project_saved(
            copy, copy['previsit_context']['result'])
        projected.append(copy)
    tasks = worklist(projected)
    fingerprint = hashlib.sha256(json.dumps(sorted(
        visit['evidence_fingerprint'] for visit in tasks['visits'])).encode()).hexdigest()
    return {'synthetic_only': True, 'checked_count': tasks['checked_count'],
            'clinic_price_task_count': len(tasks['clinic_price_tasks']),
            'visit_action_count': sum(bool(visit['visit_actions']) for visit in tasks['visits']),
            'payer_requests': 0, 'batch_fingerprint': fingerprint}


if __name__ == '__main__':
    print(json.dumps(staging_schedule_summary(), sort_keys=True))
