"""Attach a dated eCW eligibility printout to a saved commercial visit."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance.ecw_eligibility_reference import parse_ecw_eligibility_pdf


@_api.router.post('/cases/{case_id}/ecw-eligibility-reference')
async def attach_ecw_eligibility_reference(case_id: str, request: _api.Request,
                                           revision: int = _api.Form(...),
                                           context_token: str = _api.Form(...),
                                           confirmed_matches: bool = _api.Form(...),
                                           file: _api.UploadFile = _api.File(...)) -> dict:
    """Save patient-matched eCW PDF limits as reference-only visit evidence.

    An administrator confirms the PDF's member, birth date, payer and provider
    against the saved visit. Its dated out-of-pocket figures may fill missing
    display rows; they never change the Stedi calculation or collection amount.
    """
    session = await _api._session(request)
    if (session.get('staff') or {}).get('role') != 'admin':
        raise _api.HTTPException(403, 'An administrator is required to attach eligibility evidence.')
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'],
                                        session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    context = case['previsit_context']
    if case.get('revision', 0) != revision or context.get('context_token') != context_token:
        raise _api.HTTPException(409, 'This visit changed. Reopen it before attaching eligibility evidence.')
    if not confirmed_matches:
        raise _api.HTTPException(422, 'Confirm the member, birth date, payer and provider match this visit.')
    if file.content_type != 'application/pdf':
        raise _api.HTTPException(422, 'Upload an eCW eligibility PDF.')
    content = await file.read(8_000_001)
    if len(content) > 8_000_000:
        raise _api.HTTPException(413, 'Upload a PDF under 8 MB.')
    inputs, source = context['inputs'], context['source']
    coverage = _api._coverage_snapshot(inputs, source)
    try:
        evidence = await _api.run_in_threadpool(parse_ecw_eligibility_pdf, content,
            first=str(coverage.get('patient_first') or ''),
            last=str(coverage.get('patient_last') or ''),
            service_date=str(inputs.get('service_date') or ''))
    except ValueError as exc:
        raise _api.HTTPException(422, str(exc)) from exc
    digest = _api.hashlib.sha256(content).hexdigest()
    existing = case.get('ecw_eligibility_reference') or {}
    if existing.get('raw_sha256') == digest and existing.get('context_token') == context_token:
        return _api._previsit_result(case, session['practice_id'])
    raw_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
        session['practice_id'], case['id'], 'ecw-eligibility-reference.pdf', content, 'application/pdf')
    case['ecw_eligibility_reference'] = {
        'context_token': context_token,
        'service_date': evidence['service_date'],
        'evidence': evidence,
        'raw_object_key': raw_key,
        'raw_sha256': digest,
        'attached_at': _api._now(),
    }
    case = await _api.run_in_threadpool(_api._save, case, session, 'ecw-eligibility-reference-attached')
    return _api._previsit_result(case, session['practice_id'])
