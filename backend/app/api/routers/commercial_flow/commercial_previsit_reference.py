"""Attach an existing payer check as a clearly limited visit reference."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api


class PayerReferenceInput(_api.BaseModel):
    model_config = _api.ConfigDict(extra='forbid')
    eligibility_check_id: str = _api.Field(pattern=r'^ec_[0-9a-f-]{36}$')
    revision: int = _api.Field(ge=0)
    context_token: str = _api.Field(min_length=64, max_length=64)


def _person(payload: dict) -> dict:
    return (((payload.get('subscriber') or {}).get('name') or {}).get('person') or {})


def _same_person_for_reference(request_body: dict, response: dict, coverage: dict) -> bool:
    """Permit one recorded first-name typo for a reference, never a verified match."""
    requested = request_body.get('subscriber') or {}
    returned = response.get('subscriber') or {}
    expected_first = _api.insurance_identity.normalized(coverage.get('patient_first'))
    expected_last = _api.insurance_identity.normalized(coverage.get('patient_last'))
    request_first = _api.insurance_identity.normalized(_person(request_body).get('firstName'))
    response_first = _api.insurance_identity.normalized(_person(response).get('firstName'))
    request_last = _api.insurance_identity.normalized(_person(request_body).get('lastName'))
    response_last = _api.insurance_identity.normalized(_person(response).get('lastName'))
    birth = _api.insurance_identity.birth_date(coverage.get('patient_birth_date'))
    request_birth = _api.insurance_identity.birth_date(requested.get('dateOfBirth'))
    response_birth = _api.insurance_identity.birth_date(returned.get('dateOfBirth'))
    member = str(requested.get('memberId') or '')
    return bool(expected_first and expected_last and birth and member
        and request_last == response_last == expected_last
        and request_birth == response_birth == birth
        and len(request_first) == len(response_first) == len(expected_first)
        and sum(left != right for left, right in zip(request_first, expected_first)) <= 1
        and sum(left != right for left, right in zip(response_first, expected_first)) <= 1
        and member == str(returned.get('memberId') or ''))


def _reference_check_matches(request_body: dict, response: dict, case: dict) -> bool:
    context = case['previsit_context']
    inputs, source = context['inputs'], context['source']
    coverage = _api._coverage_snapshot(inputs, source)
    provider = source.get('rendering_provider') or {}
    service = ((request_body.get('encounter') or {}).get('dates') or {}).get('service') or {}
    requested_date = service.get('start') if isinstance(service, dict) else service
    payer_id = context.get('payer_id')
    return bool(payer_id == 'HPQRS'
        and request_body.get('payerId') == payer_id
        and response.get('payerId') in {payer_id, '60054'}
        and str((request_body.get('provider') or {}).get('npi') or '') == str(provider.get('npi') or '')
        and requested_date == inputs.get('service_date')
        and not response.get('errors')
        and _same_person_for_reference(request_body, response, coverage))


@_api.router.post('/cases/{case_id}/payer-reference')
async def attach_payer_reference(case_id: str, value: PayerReferenceInput, request: _api.Request) -> dict:
    """Attach a same-visit Stedi 271 as a reference when eCW identity differs.

    Admin only. The Stedi account, payer, provider, visit date, subscriber last
    name and birth date must match the saved case. At most one first-name
    character may differ. The reference alone does not verify identity, replace
    eCW coverage, establish network participation, or authorize collection.
    On saved reads, NMCT's separate all-plan policy can label the network tier.
    It does not send
    another paid eligibility request. Repeating the same ID is idempotent.
    """
    session = await _api._session(request)
    if (session.get('staff') or {}).get('role') != 'admin':
        raise _api.HTTPException(403, 'An administrator is required to attach a payer reference.')
    case = await _api.run_in_threadpool(_api.store.get_case, case_id, session['patient_id'],
        session['share_manifest_id'], **_api._scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise _api.HTTPException(404, 'Saved visit estimate not found.')
    context = case['previsit_context']
    if case.get('revision', 0) != value.revision or context.get('context_token') != value.context_token:
        raise _api.HTTPException(409, 'This visit changed. Reopen it before attaching payer evidence.')
    existing = case.get('previsit_reference') or {}
    if existing:
        if existing.get('eligibility_check_id') == value.eligibility_check_id:
            return _api._previsit_result(case, session['practice_id'])
        raise _api.HTTPException(409, 'This visit already has a payer reference.')
    key = _api.stedi_financial.credentials(session['practice_id'])
    if not key:
        raise _api.HTTPException(503, 'The Stedi production connection is unavailable.')
    url = ('https://manager.us.stedi.com/2024-04-01/eligibility-manager/'
           f'eligibility-checks/{value.eligibility_check_id}')
    try:
        async with _api.httpx.AsyncClient(timeout=_api.httpx.Timeout(15, connect=5)) as client:
            requested, returned = await _api.asyncio.gather(
                client.get(url + '/request', headers={'Authorization': key}),
                client.get(url + '/response', headers={'Authorization': key}))
        requested.raise_for_status(); returned.raise_for_status()
        request_body = requested.json().get('document')
        response = returned.json().get('document')
    except (_api.httpx.HTTPError, ValueError, AttributeError) as exc:
        raise _api.HTTPException(502, 'The Stedi check could not be retrieved.') from exc
    if not isinstance(request_body, dict) or not isinstance(response, dict) or not _reference_check_matches(request_body, response, case):
        raise _api.HTTPException(422, 'The Stedi check does not match this payer, provider, subscriber, and visit.')
    inputs, source = context['inputs'], context['source']
    benefits = _api._previsit_benefits(response, _api._coverage_snapshot(inputs, source),
        inputs['service_date'], _api._previsit_service_types(inputs), inputs=inputs, source=source)
    if benefits.get('active') is not True:
        raise _api.HTTPException(422, 'The Stedi check did not return active benefit terms.')
    raw = _api.json.dumps(response).encode()
    raw_key = await _api.run_in_threadpool(_api.object_store.put_staff_document,
        session['practice_id'], case['id'], 'previsit-payer-reference.json', raw, 'application/json')
    case['previsit_reference'] = {
        'eligibility_check_id': value.eligibility_check_id,
        'context_token': context['context_token'],
        'service_date': inputs['service_date'],
        'benefits': benefits,
        'subscriber': {
            'first_name': _person(response).get('firstName'),
            'last_name': _person(response).get('lastName'),
            'member_id': (response.get('subscriber') or {}).get('memberId'),
        },
        'raw_object_key': raw_key,
        'raw_sha256': _api.hashlib.sha256(raw).hexdigest(),
        'attached_at': _api._now(),
    }
    case = await _api.run_in_threadpool(_api._save, case, session, 'payer-reference-attached')
    return _api._previsit_result(case, session['practice_id'])
