"""Administrator-only synthetic visit setup for the staging NMCT practice."""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import date, timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.routers.staff import require_staff
from app.core import staff_store
from app.insurance import collection_guidance, insurance_research_agent, previsit_cached_tasks, store
from app.insurance.staging_synthetic import SCENARIOS, STAGING_NMCT_PRACTICE_ID, journey, project_saved, visit


router = APIRouter(prefix='/api/staff/testing', tags=['staging-test-fixtures'])


def _prepared_result(result: dict) -> dict:
    result['staging_synthetic_fixture'] = True
    lines = result['price_range']['lines']
    result['codes'] = [{'id': line['id'], 'code': line['code'], 'units': line['units'],
                        'modifiers': line['modifiers']} for line in lines]
    result['selected_ids'] = [line['id'] for line in lines]
    result['collection_guidance'] = collection_guidance.build(result, result['visit_review'])
    return result


def _staging_only(staff: dict) -> None:
    if (os.getenv('PUBLIC_BASE_URL', '').rstrip('/') != 'https://staging.clinicforce.ai'
            or os.getenv('COLLECTION_ENGINE_V2_ENABLED') != 'true'):
        raise HTTPException(404, 'Staging collection fixtures are unavailable.')
    if staff.get('practice_id') != STAGING_NMCT_PRACTICE_ID or staff.get('role') != 'admin':
        raise HTTPException(403, 'The staging NMCT administrator is required.')


def _seed_one(staff: dict, name: str, index: int) -> dict:
    patient_id = str(uuid5(NAMESPACE_URL, f'clinicforce-staging-collection-patient:{name}'))
    intake_id = str(uuid5(NAMESPACE_URL, f'clinicforce-staging-collection-intake:{name}'))
    family = f'CollectionFixture{index:02d}'
    with staff_store._connection() as db:
        db.execute('''INSERT INTO staff_patients
            (id,practice_id,given_name,family_name,birth_date,created_at)
            VALUES (%s,%s,'Synthetic',%s,'1990-01-01',now()) ON CONFLICT (id) DO NOTHING''',
            (patient_id, staff['practice_id'], family))
        patient = db.execute('SELECT practice_id,given_name,family_name FROM staff_patients WHERE id=%s',
                             (patient_id,)).fetchone()
        if not patient or (patient['practice_id'], patient['given_name'], patient['family_name']) != (
                staff['practice_id'], 'Synthetic', family):
            raise ValueError('Synthetic patient ID is already used by another record.')
        db.execute('''INSERT INTO staff_intakes
            (id,practice_id,created_by,patient_id,status,created_at,updated_at,source)
            VALUES (%s,%s,%s,%s,'in_progress',now(),now(),'manual_entry')
            ON CONFLICT (id) DO NOTHING''',
            (intake_id, staff['practice_id'], staff['id'], patient_id))
        intake = db.execute('SELECT practice_id,patient_id FROM staff_intakes WHERE id=%s',
                            (intake_id,)).fetchone()
        if not intake or (intake['practice_id'], intake['patient_id']) != (staff['practice_id'], patient_id):
            raise ValueError('Synthetic intake ID is already used by another record.')
    stages = journey(name)
    result = visit(name)
    source = {'appointment_id': f'synthetic-appointment-{index}',
              'coverage_id': f'synthetic-coverage-{index}',
              'coverage_period': {'start': '2026-01-01'}, 'location_id': 'synthetic-office'}
    workflow = {'source_mode': 'commercial_previsit', 'synthetic_fixture': True,
                'previsit_context': {'result': {}, 'context_token': f'synthetic-visit-{index}',
                    'payer_id': f'synthetic-route-{name}',
                    'inputs': {'service_date': result['service_date'], 'place_of_service': '11'},
                    'source': source},
                'previsit': {'request_state': 'complete'}, 'visit_review': result['visit_review']}
    saved = store.create_case(patient_id, '', 'staging-synthetic-only', workflow,
        practice_id=staff['practice_id'], intake_id=intake_id,
        workflow_slot=f'commercial:synthetic-{name.replace("_", "-")}')
    if saved.get('synthetic_fixture') is not True:
        raise ValueError('The existing case is not a synthetic fixture.')
    for step_index, (stage, step_result) in enumerate(stages, 1):
        if saved.get('revision', 0) >= step_index:
            continue
        step_result = _prepared_result(step_result)
        review = step_result['visit_review']
        saved['previsit_context']['result'] = step_result
        saved['visit_review'] = review
        saved['synthetic_step_index'] = step_index
        saved = store.save_case(saved, patient_id, '', f'synthetic-{stage}',
            snapshot={'result': step_result, 'patient': {'given_name': 'Synthetic', 'family_name': family}},
            practice_id=staff['practice_id'], intake_id=intake_id)
    # The first 38-case release used October dates. Move only those marked fake
    # newer cases to an upcoming date so the due-visit harness can exercise them.
    if (index > 8 and saved['previsit_context']['result'].get('service_date') != result['service_date']):
        result = _prepared_result(result)
        saved['previsit_context']['result'] = result
        saved['previsit_context']['inputs']['service_date'] = result['service_date']
        saved['visit_review'] = result['visit_review']
        saved['synthetic_step_index'] = saved.get('synthetic_step_index', len(stages)) + 1
        saved = store.save_case(saved, patient_id, '', 'synthetic-upcoming-rescope',
            snapshot={'result': result, 'patient': {'given_name': 'Synthetic', 'family_name': family}},
            practice_id=staff['practice_id'], intake_id=intake_id)
    guidance = saved['previsit_context']['result']['collection_guidance']
    model = guidance.get('visit_model') or {}
    return {'scenario': name, 'intake_id': intake_id, 'case_id': saved['id'],
            'iterations': saved.get('synthetic_step_index') or len(stages),
            'expected_modeled_cents': model.get('estimated_patient_responsibility_cents'),
            'expected_collect_now_cents': guidance.get('collect_now_cents', guidance.get('amount_cents'))}


@router.post('/collection-engine-scenarios', description=(
    'Staging NMCT administrator only. Create visibly synthetic October-pattern '
    'patients and iterative saved cases using invented appointment, Coverage and benefit facts. '
    'No eCW, payer or Bedrock request occurs. Synthetic policy data is confined to these cases '
    'and has no authority for real NMCT patients. The route returns only fixture case IDs and totals.'
))
async def seed_collection_engine_scenarios(request: Request) -> dict:
    staff = await require_staff(request)
    _staging_only(staff)
    cases = [await run_in_threadpool(_seed_one, staff, name, index)
             for index, (name, *_expected) in enumerate(SCENARIOS, 1)]
    return {'synthetic_only': True, 'practice_id': STAGING_NMCT_PRACTICE_ID, 'cases': cases}


@router.get('/collection-engine-due', description=(
    'Staging NMCT administrator only. Recalculate at most 100 already saved synthetic '
    'upcoming visits from cached evidence. Return deidentified visit and deduplicated '
    'clinic price tasks. No eCW, Stedi, Bedrock or database write occurs.'
))
async def due_collection_engine_scenarios(request: Request, days: int = 30) -> dict:
    staff = await require_staff(request)
    _staging_only(staff)
    if not 1 <= days <= 30:
        raise HTTPException(422, 'Choose a horizon of 1 to 30 days.')
    today = date.today()
    cases = await run_in_threadpool(store.staging_synthetic_due, staff['practice_id'],
                                    today.isoformat(), (today + timedelta(days=days)).isoformat())
    projected = []
    for case in cases:
        result = deepcopy(case['previsit_context']['result'])
        case['previsit_context']['result'] = project_saved(case, result)
        projected.append(case)
    return {'synthetic_only': True, **previsit_cached_tasks.worklist(projected)}


@router.post('/collection-engine-research-check', description=(
    'Staging NMCT administrator only. Make one bounded Sonnet 5 advisory request '
    'with invented CPT and benefit facts, then return status and finding count. '
    'No patient, eCW, payer or database request occurs; no collection decision is changed.'
))
async def check_collection_engine_research(request: Request) -> dict:
    staff = await require_staff(request)
    _staging_only(staff)
    result = visit('specialist_271_conflict')
    result['payer'] = 'Cigna Synthetic'
    result['price_range']['lines'][0]['benefits']['review_reasons'] = [
        'Prior authorization requirement needs source verification']
    result['requirement_evidence'] = {'procedures': [{'code': '99204',
        'authorization': {'status': 'Verification required', 'sources': [{
            'url': 'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'}]},
        'referral': {'status': 'Verification required', 'sources': []}}]}
    answer = await run_in_threadpool(insurance_research_agent.investigate, result)
    return {'synthetic_only': True, 'status': answer['status'],
            'finding_count': len(answer.get('findings') or []),
            'model_id': answer.get('model_id'), 'reason': answer.get('reason')}
