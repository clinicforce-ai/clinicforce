"""Load an eCW visit while prefetching its patient-bound card evidence."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api


async def load_previsit_appointment(session: dict, value, request, review_service_date: str | None):
    library_documents = await _api.run_in_threadpool(_api.library_pricing.prepare, session['practice_id'])
    session['_library_documents'] = library_documents
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'),
                                   'summary': row.get('extraction_summary')} for row in library_documents]
    _api.collection_event('previsit_started', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     staff_id=(session.get('staff') or {}).get('id'), stage='appointment')
    if not _api.ecw_backend.configured():
        _api.collection_event('previsit_blocked', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_not_configured')
        raise _api.HTTPException(503, "The eClinicalWorks backend connection is not configured.")
    confirmed_patient_id = ((session.get('intake') or {}).get('patient') or {}).get('ecw_patient_id')
    card_task = (_api.asyncio.create_task(_api.asyncio.wait_for(
        _api._read_card_hints(session, str(confirmed_patient_id), allow_pending_reference=True), timeout=30))
        if confirmed_patient_id else None)
    try:
        inputs, _, source = await _api.ecw_backend.fetch_inputs(
            _api._ecw_identity(session, _api.date.today().isoformat()),
            _api._ecw_practice_config(session["practice_id"]), value.appointment_id,
            review_service_date=review_service_date)
    except _api.ecw_backend.EcwBackendError as exc:
        if card_task:
            card_task.cancel()
        _api.collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_error')
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return _api.JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise _api.HTTPException(502, str(exc)) from exc
    except BaseException:
        if card_task:
            card_task.cancel()
        raise
    if source.get("appointment_id") != value.appointment_id:
        if card_task:
            card_task.cancel()
        _api.collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='appointment_mismatch')
        raise _api.HTTPException(409, "The selected upcoming eClinicalWorks appointment could not be verified.")
    if value.telehealth_pos and not _api.visit_estimate.is_telehealth(inputs):
        if card_task:
            card_task.cancel()
        raise _api.HTTPException(422, 'Confirm a telehealth appointment before recording its location and modality.')
    source['_request_ip_chain'] = _api._request_ip_chain(request)
    _api.collection_event('previsit_appointment_loaded', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     stage='appointment', count=len(inputs.get('lines') or []), source='ecw')
    try:
        source['saved_eligibility'] = await _api.asyncio.wait_for(
            _api.ecw_backend.fetch_saved_eligibility(source, inputs['service_date']), timeout=6)
    except _api.asyncio.TimeoutError:
        source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW check took too long to load. Continue with Stedi verification and retry eCW later.'}
    return inputs, source, card_task, confirmed_patient_id
