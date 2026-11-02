"""Administrator-only synthetic visit setup for the staging NMCT practice."""
from __future__ import annotations

import os
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app.api.routers.staff import require_staff
from app.core import staff_store
from app.insurance import collection_guidance, store
from app.insurance.staging_synthetic import SCENARIOS, visit


router = APIRouter(prefix='/api/staff/testing', tags=['staging-test-fixtures'])


def _staging_only(staff: dict) -> None:
    if (os.getenv('PUBLIC_BASE_URL', '').rstrip('/') != 'https://staging.clinicforce.ai'
            or os.getenv('COLLECTION_ENGINE_V2_ENABLED') != 'true'):
        raise HTTPException(404, 'Staging collection fixtures are unavailable.')
    if staff.get('practice_id') != 'primary' or staff.get('role') != 'admin':
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
    result = visit(name)
    result['staging_synthetic_fixture'] = True
    lines = result['price_range']['lines']
    result['codes'] = [{'id': line['id'], 'code': line['code'], 'units': line['units'],
                        'modifiers': line['modifiers']} for line in lines]
    result['selected_ids'] = [line['id'] for line in lines]
    review = {'payments': {'amount_cents': 0, 'reference': 'synthetic-ledger-check'}}
    result['collection_guidance'] = collection_guidance.build(result, review)
    source = {'appointment_id': f'synthetic-appointment-{index}',
              'coverage_id': f'synthetic-coverage-{index}',
              'coverage_period': {'start': '2026-01-01'}, 'location_id': 'synthetic-office'}
    workflow = {'source_mode': 'commercial_previsit', 'synthetic_fixture': True,
                'previsit_context': {'result': result, 'context_token': f'synthetic-visit-{index}',
                    'payer_id': f'synthetic-route-{name}',
                    'inputs': {'service_date': result['service_date'], 'place_of_service': '11'},
                    'source': source},
                'previsit': {'request_state': 'complete'}, 'visit_review': review}
    saved = store.create_case(patient_id, '', 'staging-synthetic-only', workflow,
        practice_id=staff['practice_id'], intake_id=intake_id,
        workflow_slot=f'commercial:synthetic-{name.replace("_", "-")}')
    if saved.get('synthetic_fixture') is not True:
        raise ValueError('The existing case is not a synthetic fixture.')
    if saved.get('revision', 0) == 0:
        saved = store.save_case(saved, patient_id, '', 'synthetic-visit-estimated',
            snapshot={'result': result, 'patient': {'given_name': 'Synthetic', 'family_name': family}},
            practice_id=staff['practice_id'], intake_id=intake_id)
    return {'scenario': name, 'intake_id': intake_id, 'case_id': saved['id'],
            'expected_modeled_cents': result['collection_guidance']['visit_model'][
                'estimated_patient_responsibility_cents'],
            'expected_collect_now_cents': result['collection_guidance']['collect_now_cents']}


@router.post('/collection-engine-scenarios', description=(
    'Staging NMCT administrator only. Create eight visibly synthetic October-pattern '
    'patients and saved collection cases using invented appointment, Coverage and benefit facts. '
    'No eCW, payer or Bedrock request occurs. Synthetic policy data is confined to these cases '
    'and has no authority for real NMCT patients. The route returns only fixture case IDs and totals.'
))
async def seed_collection_engine_scenarios(request: Request) -> dict:
    staff = await require_staff(request)
    _staging_only(staff)
    cases = [await run_in_threadpool(_seed_one, staff, name, index)
             for index, (name, *_expected) in enumerate(SCENARIOS, 1)]
    return {'synthetic_only': True, 'practice_id': 'primary', 'cases': cases}
