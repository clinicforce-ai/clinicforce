"""Read patient-bound insurance card hints for recovery."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

async def _read_card_hints(session: dict, patient_id: str | None, *, allow_pending_reference: bool = False) -> dict:
    """Read one patient-bound card set; OCR fields are inquiry hints, not benefits."""
    from app.insurance import v1_sources
    from app.api.routers.staff import inspect_file
    contents = []
    source = None
    reference_only = False
    submitted_member_id = None
    try:
        intakes = await _api.run_in_threadpool(_api.staff_store.patient_card_intakes, session['practice_id'], session['patient_id'])
        card_intakes = [intake for intake in intakes if intake.get('documents')]
        if len(card_intakes) > 1:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        pending = [] if card_intakes or not allow_pending_reference else await _api.run_in_threadpool(
            _api.staff_store.patient_pending_card_references, session['practice_id'], session['patient_id'])
        if len(pending) > 1:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        reference_only = bool(pending)
        docs = card_intakes[0]['documents'] if card_intakes else pending[0]['documents'] if pending else []
        submitted_member_id = (pending[0].get('fields') or {}).get('member_id') if pending else None
        if len(docs) > 2:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        for doc in docs:
            if doc.get('size_bytes', 0) <= 10_000_000 and doc.get('object_key'):
                contents.append(await _api.run_in_threadpool(_api.object_store.get_object_bytes, doc['object_key']))
        if contents:
            source = 'patient_upload_pending' if reference_only else 'clinicforce_card'
        elif patient_id:
            source = 'ecw_card'
            cards = await _api.asyncio.wait_for(_api.ecw_backend.insurance_cards(patient_id), timeout=24)
            if len(cards) > 2:
                return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
            for card in cards:
                data, _ = await _api.asyncio.wait_for(_api.ecw_backend.insurance_cards(patient_id, document_id=card['id']), timeout=24)
                contents.append(data)
            if contents:
                source = 'ecw_card'
        if not contents:
            return {'status': 'not_found', 'fields': {}, 'count': 0, 'source': source,
                    'failure_reason': 'ecw_no_matching_card_document' if patient_id else 'ecw_patient_unlinked'}
        if any(len(content) > 10_000_000 for content in contents):
            return {'status': 'unavailable', 'fields': {}, 'count': len(contents), 'failure_reason': 'card_too_large'}
        for content in contents:
            await _api.run_in_threadpool(inspect_file, content)
        fields, evidence, _ = await _api.asyncio.wait_for(_api.asyncio.to_thread(v1_sources.read_card_fields, contents), timeout=20)
        if reference_only and submitted_member_id:
            fields['member_id'] = submitted_member_id
        return {'status': 'read', 'fields': fields, 'evidence': evidence, 'count': len(contents),
                'source': source, 'reference_only': reference_only}
    except (_api.ecw_backend.EcwBackendError, _api.httpx.HTTPError, TimeoutError, ValueError, _api.HTTPException,
            _api.golden.OCRServiceError, _api.golden.GoldenCaseError) as exc:
        return {'status': 'unavailable', 'fields': {}, 'count': len(contents),
                'failure_reason': 'card_read_timeout' if isinstance(exc, (TimeoutError, _api.httpx.TimeoutException)) else 'card_unavailable'}
