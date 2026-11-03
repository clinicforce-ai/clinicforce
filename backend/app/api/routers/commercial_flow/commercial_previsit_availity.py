"""Attach a dated Availity eligibility printout to an existing commercial visit."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance.availity_visit_evidence import parse_visit_pdf


@_api.router.post('/cases/{case_id}/availity-eligibility')
async def attach_availity_eligibility(case_id: str, request: _api.Request,
                                      revision: int = _api.Form(...),
                                      context_token: str = _api.Form(...),
                                      file: _api.UploadFile = _api.File(...)) -> dict:
    """Attach a matching Availity Essentials PDF as an office planning reference.

    An administrator must upload a text-readable PDF for the saved patient,
    visit date and requesting rendering NPI. The source is stored as a protected
    staff document. Its specialist terms are displayed separately and do not
    replace Stedi/eCW data, establish QMB status, or authorize a positive charge.
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
    if file.content_type != 'application/pdf':
        raise _api.HTTPException(422, 'Upload an Availity Essentials PDF.')
    content = await file.read(8_000_001)
    if len(content) > 8_000_000:
        raise _api.HTTPException(413, 'Upload a PDF under 8 MB.')
    inputs, source = context['inputs'], context['source']
    coverage = _api._coverage_snapshot(inputs, source)
    rendering = source.get('rendering_provider') or {}
    if (str(inputs.get('place_of_service') or '') != '11' or
            _api.provider_specialty.office_role(rendering.get('roles') or []) != 'specialist' or
            str((inputs.get('rendering_provider') or {}).get('npi') or '') != str(rendering.get('npi') or '')):
        raise _api.HTTPException(422, 'This reference requires a specialist office visit at place of service 11.')
    try:
        evidence = parse_visit_pdf(content,
            first=str(coverage.get('patient_first') or ''),
            last=str(coverage.get('patient_last') or ''),
            birth=str(coverage.get('patient_birth_date') or ''),
            service_date=str(inputs.get('service_date') or ''),
            rendering_npi=str(rendering.get('npi') or ''))
    except ValueError as exc:
        raise _api.HTTPException(422, str(exc)) from exc
    digest = _api.hashlib.sha256(content).hexdigest()
    existing = case.get('availity_visit_reference') or {}
    if (existing.get('raw_sha256') == digest and existing.get('context_token') == context_token):
        return _api._previsit_result(case, session['practice_id'])
    raw_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
        session['practice_id'], case['id'], 'availity-eligibility.pdf', content, 'application/pdf')
    case['availity_visit_reference'] = {
        'context_token': context_token,
        'service_date': evidence['service_date'],
        'evidence': evidence,
        'raw_object_key': raw_key,
        'raw_sha256': digest,
        'attached_at': _api._now(),
    }
    case = await _api.run_in_threadpool(_api._save, case, session, 'availity-eligibility-attached')
    return _api._previsit_result(case, session['practice_id'])
