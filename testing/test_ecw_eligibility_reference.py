"""Scanned eCW printout limits stay reference-only and bound to one visit."""
import asyncio
from io import BytesIO

import fitz
import pytest
from starlette.datastructures import Headers, UploadFile

from app.api.routers import commercial_estimator  # noqa: F401 - initialize route imports
from app.insurance.ecw_eligibility_reference import parse_ecw_eligibility_pdf


def _pdf(*, family_remaining: str = '20193.62') -> bytes:
    document = fitz.open()
    header = document.new_page()
    header.insert_text((40, 50), 'Eligibility Response for SAMPLE,JANE 11/03/2026 09:25 AM')
    for _ in range(3):
        document.new_page()
    for page, y, level, period, amount in (
            (1, 610, 'Individual', 'Service Year', '10600'),
            (1, 665, 'Family', 'Service Year', '21200'),
            (2, 680, 'Individual', 'Remaining', '10150.85'),
            (3, 170, 'Family', 'Remaining', family_remaining)):
        sheet = document[page]
        sheet.insert_text((125, y - 25), 'Office')
        sheet.insert_text((83, y), level)
        sheet.insert_text((287, y), period)
        sheet.insert_text((335, y), amount)
    content = document.tobytes()
    document.close()
    return content


def _parse(content: bytes, **changes) -> dict:
    values = dict(first='Jane', last='Sample', service_date='2026-11-04')
    values.update(changes)
    return parse_ecw_eligibility_pdf(content, **values)


def test_office_limits_are_read_without_inventing_deductible_or_cpt():
    result = _parse(_pdf())
    assert result == {
        'source': 'eCW eligibility PDF', 'report_date': '2026-11-03',
        'service_date': '2026-11-04',
        'individual_oop_annual_cents': 1060000,
        'individual_oop_remaining_cents': 1015085,
        'family_oop_annual_cents': 2120000,
        'family_oop_remaining_cents': 2019362,
    }
    with pytest.raises(ValueError, match='patient differs'):
        _parse(_pdf(), first='Janet')
    with pytest.raises(ValueError, match='not current'):
        _parse(_pdf(), service_date='2026-11-12')
    with pytest.raises(ValueError, match='figures conflict'):
        _parse(_pdf(family_remaining='22000.00'))
    assert _parse(_pdf(family_remaining='0.00'))['family_oop_remaining_cents'] == 0


def test_admin_upload_keeps_pdf_protected_and_projects_only_same_visit(monkeypatch):
    from app.api.routers.commercial_flow import commercial_previsit_ecw_pdf as route

    case = {
        'id': 'synthetic-case', 'source_mode': 'commercial_previsit', 'revision': 2,
        'previsit_context': {'context_token': 'synthetic-token',
                             'inputs': {'service_date': '2026-11-04'},
                             'source': {}, 'result': {'service_date': '2026-11-04'}},
    }
    async def session(_):
        return {'staff': {'role': 'admin'}, 'patient_id': 'synthetic-patient',
                'share_manifest_id': None, 'practice_id': 'synthetic-practice'}
    writes = []
    monkeypatch.setattr(route._api, '_session', session)
    monkeypatch.setattr(route._api, '_scope', lambda _: {})
    monkeypatch.setattr(route._api.store, 'get_case', lambda *_args, **_kwargs: case)
    monkeypatch.setattr(route._api, '_coverage_snapshot', lambda *_: {
        'patient_first': 'Jane', 'patient_last': 'Sample'})
    monkeypatch.setattr(route._api.object_store, 'put_staff_document',
                        lambda *_args: writes.append('source') or 'protected-object-key')
    monkeypatch.setattr(route._api, '_save', lambda value, *_: value)
    monkeypatch.setattr(route._api, '_previsit_result',
                        lambda value, *_: {'ecw_eligibility_reference': value['ecw_eligibility_reference']['evidence']})
    upload = UploadFile(file=BytesIO(_pdf()), filename='synthetic.pdf',
                        headers=Headers({'content-type': 'application/pdf'}))
    result = asyncio.run(route.attach_ecw_eligibility_reference('synthetic-case', None,
        revision=2, context_token='synthetic-token', confirmed_matches=True, file=upload))
    assert result['ecw_eligibility_reference']['family_oop_remaining_cents'] == 2019362
    assert 'raw_object_key' not in result['ecw_eligibility_reference']
    assert case['ecw_eligibility_reference']['raw_object_key'] == 'protected-object-key'
    assert writes == ['source']


def test_saved_result_projects_reference_only_for_matching_visit():
    from app.api.routers.commercial_flow.commercial_previsit_status import _previsit_result

    evidence = _parse(_pdf())
    case = {
        'previsit_context': {
            'context_token': 'synthetic-token', 'source': {},
            'result': {'service_date': '2026-11-04', 'benefits': {},
                       'price_range': {'lines': []}, 'codes': []},
        },
        'ecw_eligibility_reference': {
            'context_token': 'synthetic-token', 'service_date': '2026-11-04',
            'evidence': evidence,
        },
    }
    result = _previsit_result(case)
    assert result['ecw_eligibility_reference'] == evidence
    assert 'raw_object_key' not in str(result)
    case['previsit_context']['context_token'] = 'new-visit-context'
    assert 'ecw_eligibility_reference' not in _previsit_result(case)
