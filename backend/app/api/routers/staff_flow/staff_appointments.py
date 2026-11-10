"""Staff appointments routes."""

from __future__ import annotations

import asyncio
import logging
from datetime import date as Date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import staff_store, staff_deletions
from app.insurance import ecw_backend, store

from app.api.routers import staff as _staff

router = _staff.router
NO_STORE = _staff.NO_STORE
logger = logging.getLogger(__name__)


class DateVisit(BaseModel):
    clinic_patient_id: str = Field(description='Existing confirmed clinic patient ID; no new profile is created.')
    date: Date
    start: str
    status: str


class DateAppointmentsResponse(BaseModel):
    date: Date = Field(description='Practice-local day searched at eCW.')
    visits: list[DateVisit] = Field(description='Only encounters mapped to this clinic’s confirmed patient links.')


async def _appointments_for_date(practice_id: str, day: str) -> JSONResponse:
    linked = await run_in_threadpool(staff_store.linked_ecw_patient_ids, practice_id)
    if not linked:
        return JSONResponse({'date': day, 'visits': []}, headers=NO_STORE)
    try:
        encounters = await asyncio.wait_for(ecw_backend.practice_appointment_date(day), timeout=15)
    except (ecw_backend.EcwBackendError, asyncio.TimeoutError):
        raise HTTPException(503, 'The eCW appointment date could not be verified. Retry the date.') from None
    visits = [{**{key: encounter[key] for key in ('date', 'start', 'status')}, 'clinic_patient_id': patient_id}
              for encounter in encounters for patient_id in linked.get(encounter['patient_id'], [])]
    return JSONResponse({'date': day, 'visits': visits}, headers=NO_STORE)


@router.get('/patients/today-appointments', response_model=DateAppointmentsResponse,
            responses={503: {'description': 'The clinic timezone or complete eCW date read is unavailable.'}},
            description=(
    'Read today’s eCW encounters once for the practice and return only minimal dates for '
    'patients already linked and confirmed in this staff member’s clinic. An incomplete '
    'provider read fails instead of presenting a partial today list. No patient profile is created.'
))
async def today_appointments(request: Request):
    from app.api.routers.commercial_estimator import _ecw_practice_config
    actor = await _staff.require_staff(request)
    practice_id = actor['practice_id']
    try:
        timezone = ZoneInfo(_ecw_practice_config(practice_id)['timezone'])
    except (KeyError, ValueError):
        raise HTTPException(503, 'The clinic timezone is not configured correctly.') from None
    day = datetime.now(timezone).date().isoformat()
    return await _appointments_for_date(practice_id, day)


@router.get('/patients/appointment-date', response_model=DateAppointmentsResponse,
            responses={503: {'description': 'The complete eCW date read is unavailable.'}},
            description=('Read one selected eCW appointment date for this staff member’s clinic. '
                         'Only confirmed existing clinic patient links are returned; no patient profile is created. '
                         'A failed or incomplete provider read does not claim that the day has no patients.'))
async def appointment_date(request: Request, day: Date):
    actor = await _staff.require_staff(request)
    return await _appointments_for_date(actor['practice_id'], day.isoformat())

@router.get('/patients/{patient_id}/appointments')
async def patient_appointments(patient_id: str, request: Request, include_ecw: bool = False):
    """Read saved visits and optional verified eCW history for one practice patient.

    A complete eCW read refreshes the minimal date-list cache. An incomplete read
    preserves its previous snapshot and returns saved visits with incomplete status.
    """
    from app.insurance import store
    from app.api.routers.commercial_estimator import _ecw_practice_config
    actor = await _staff.require_staff(request)
    patient = await run_in_threadpool(staff_store.get_patient, patient_id, actor['practice_id'])
    if not patient:
        raise HTTPException(404, 'Clinic patient not found.')
    records = await run_in_threadpool(store.patient_checks, actor['practice_id'], patient_id)
    visits = {}
    account_number = patient.get('ecw_account_number')
    ecw_patient_id = patient.get('ecw_patient_id')
    for record in records:
        case = record['case']
        context = case.get('previsit_context') or {}
        result, source = context.get('result') or {}, context.get('source') or {}
        if case.get('source_mode') != 'commercial_previsit' or not result:
            continue
        appointment_id = source.get('appointment_id') or result.get('appointment_id')
        source_patient_id = source.get('_patient_id') or ecw_patient_id
        key = f'{source_patient_id}:{appointment_id}' if appointment_id and source_patient_id else f'check:{case["id"]}'
        visit = visits.setdefault(key, {'id': key, 'appointment_id': appointment_id,
            'start': source.get('appointment_date') or result.get('service_date'),
            'date': result.get('service_date'), 'reason': result.get('reason') or '',
            'visit_type': result.get('visit_type'), 'status': source.get('appointment_status') or 'Saved visit',
            'from_ecw': False, 'checks': []})
        visit['checks'].append({'id': case['id'], 'intake_id': record['intake_id'], 'created_at': case['created_at'],
            'source_mode': 'commercial_previsit', 'service_date': result.get('service_date'),
            'visit_type': result.get('visit_type'), 'label': 'Visit estimate', 'pending': staff_deletions._pending(case)})
        account_number = account_number or (result.get('patient_details') or {}).get('ecw_account_number')
    ecw_status = 'not_requested'
    if include_ecw:
        if patient.get('identity_status') != 'confirmed' or not ecw_patient_id or not ecw_backend.configured():
            ecw_status = 'not_linked'
        else:
            try:
                history = await asyncio.wait_for(ecw_backend.patient_appointment_history(
                    {'ecw_patient_id': ecw_patient_id, 'patient_first_name': patient['given_name'],
                     'patient_last_name': patient['family_name'], 'patient_birth_date': patient['birth_date']},
                    _ecw_practice_config(actor['practice_id'])), timeout=38)
                if history['patient_id'] != ecw_patient_id:
                    raise ecw_backend.EcwBackendError('Patient link differs from the requested record.')
                for item in history['appointments']:
                    key = f'{ecw_patient_id}:{item["id"]}'
                    previous = visits.get(key) or {}
                    visits[key] = {**item, 'id': key, 'appointment_id': item['id'],
                                   'from_ecw': True, 'checks': previous.get('checks', [])}
                account_number = history.get('ecw_account_number') or account_number
                ecw_status = 'complete'
                try:
                    await run_in_threadpool(staff_store.save_ecw_visit_cache, patient_id,
                                            actor['practice_id'], ecw_patient_id,
                                            [item for item in visits.values() if item.get('from_ecw')])
                except Exception:
                    logger.warning('Could not update practice-scoped eCW visit-date cache')
            except (ecw_backend.EcwBackendError, asyncio.TimeoutError):
                # An optional history refresh must not hide saved recommendations.
                ecw_status = 'incomplete'
    return JSONResponse({'appointments': sorted(visits.values(), key=lambda item: (item.get('start') or '', item['id']), reverse=True),
                         'ecw_status': ecw_status, 'ecw_account_number': account_number}, headers=NO_STORE)

@router.get('/intakes/{intake_id}/checks/{case_id}')
async def saved_insurance_check(intake_id: str, case_id: str, request: Request):
    from copy import deepcopy
    from app.insurance import store
    actor, intake, case = await owned_saved_check(intake_id, case_id, request)
    patient = intake['patient']
    snapshots = await run_in_threadpool(store.check_snapshots, case_id, actor['practice_id'], patient['id'], intake_id)
    if not snapshots:
        # Older checks retain their stored result. Never recalculate historical
        # guidance using today's benefits, contract, or eligibility window.
        result = deepcopy(case['previsit_context']['result'])
        result.update(check_id=case['id'], revision=case.get('revision', 0))
        snapshots = [{'result': result, 'patient': patient, 'saved_at': None, 'stage': 'legacy', 'legacy': True}]
    return JSONResponse({'snapshots': snapshots}, headers=NO_STORE)

async def owned_saved_check(intake_id: str, case_id: str, request: Request):
    from app.insurance import store
    actor = await _staff.require_staff(request)
    intake = await _staff.owned_intake(intake_id, actor)
    patient = intake.get('patient')
    case = await run_in_threadpool(store.get_case, case_id, patient['id'], '',
        practice_id=actor['practice_id'], intake_id=intake_id) if patient else None
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
    return actor, intake, case

@router.get('/intakes/{intake_id}/checks/{case_id}/financials')
async def saved_insurance_check_financials(intake_id: str, case_id: str, request: Request):
    from app.api.routers.commercial_estimator import saved_visit_financials
    actor, _, case = await owned_saved_check(intake_id, case_id, request)
    return JSONResponse(await saved_visit_financials(case, actor['practice_id']), headers=NO_STORE)
