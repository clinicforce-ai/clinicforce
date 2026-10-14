"""Staff library routes."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import BackgroundTasks, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core import object_store, staff_store, library_files
from app.insurance import commercial, commercial_store, era835, missing_rates, store
from app.api.schemas.staff import MissingRatesResponse

from app.api.routers import staff as _staff

router = _staff.router
NO_STORE = _staff.NO_STORE
MAX_UPLOAD_BYTES = _staff.MAX_UPLOAD_BYTES

def public_library(document: dict) -> dict:
    return {key: value for key, value in document.items() if key not in {"object_key", "created_by", "extraction"}}

@router.get("/library")
async def library(request: Request, background_tasks: BackgroundTasks):
    """List sourced estimator use; resume unfinished Library processing automatically."""
    staff = await _staff.require_staff(request)
    documents = await run_in_threadpool(staff_store.list_library, staff["practice_id"])
    from app.insurance import library_pricing
    background_tasks.add_task(library_pricing.prepare, staff["practice_id"])
    usage = await run_in_threadpool(library_files.estimator_links, staff['practice_id'], documents)
    return JSONResponse({"documents": [{**public_library(value), 'estimator_uses': usage['links'][value['id']]} for value in documents]}, headers=NO_STORE)

@router.get("/library/missing-rates", response_model=MissingRatesResponse)
async def library_missing_rates(request: Request):
    """Admin-only recent CPT price gaps from saved estimates, without patient data.

    These are case-backed observations, not proof that a rate does not exist.
    Upload an approved scoped schedule, then recheck affected visits; historical
    estimate snapshots are never rewritten by this inventory.
    """
    staff = await _staff.require_staff(request)
    if staff['role'] != 'admin':
        raise HTTPException(403, 'Only a clinic administrator can inspect pricing gaps.')
    rows = await run_in_threadpool(store.recent_missing_rate_inputs, staff['practice_id'])
    connections = await run_in_threadpool(commercial_store.list_connections, staff['practice_id'])
    body = MissingRatesResponse.model_validate(missing_rates.summarize(rows, 500, connections))
    return JSONResponse(body.model_dump(), headers=NO_STORE)

@router.post("/library/rate-versions/{version_id}/retire")
async def retire_library_rate_version(version_id: str, request: Request):
    staff = await _staff.require_staff(request)
    if staff['role'] != 'admin':
        raise HTTPException(403, "Only a clinic administrator can retire contract rates.")
    try:
        version = await run_in_threadpool(commercial_store.retire_rates, staff['practice_id'], version_id)
    except commercial.CommercialError as exc:
        raise HTTPException(422, str(exc)) from exc
    return JSONResponse({'id': version['id'], 'status': version['status']}, headers=NO_STORE)

@router.post("/library")
async def upload_library(request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), kind: Literal["contract", "fee_schedule", "self_pay", "era_835", "plan_matrix"] = Form(...),
                         label: str = Form(default="", max_length=200), payer: str = Form(default="", max_length=200),
                         effective_from: str = Form(default="", max_length=10), effective_to: str = Form(default="", max_length=10)):
    """Save a practice source and automatically compile supported final rules.

    Processing covers PDF/OCR, DOCX, XLSX and CSV. The Library exposes per-source
    processing status, usable rules and unresolved dependencies. Final supported
    prices publish without per-patient staff activation; provisional or ambiguous
    terms remain unavailable. Originals and historical estimates are retained.
    """
    staff = await _staff.require_staff(request)
    if staff["role"] != "admin":
        raise HTTPException(403, "Only a clinic administrator can update clinic documents")
    if not object_store.enabled():
        raise HTTPException(503, "Clinic document storage is unavailable")
    try:
        start = date.fromisoformat(effective_from) if effective_from else None
        end = date.fromisoformat(effective_to) if effective_to else None
        if start and end and start > end:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Enter valid effective dates in order") from None
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Clinic documents must be 15 MB or smaller")
    filename = Path(file.filename or "clinic-document").name[:200]
    era = None
    if kind == "era_835":
        if Path(filename).suffix.lower() not in {".835", ".edi", ".txt", ".x12"}:
            raise HTTPException(422, "Upload the original 835 ERA as a .835, .edi, .x12, or .txt file.")
        try:
            era = era835.parse(content)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        content_type = "application/edi-x12"
    elif Path(filename).suffix.lower() in library_files.OFFICE_TYPES:
        try:
            await run_in_threadpool(library_files.office_preview, content, filename)
        except Exception:
            raise HTTPException(422, "Upload a readable DOCX or XLSX file without macros, up to 30 MB when expanded.") from None
        content_type = library_files.OFFICE_TYPES[Path(filename).suffix.lower()]
    elif filename.lower().endswith(".csv"):
        try:
            library_files.csv_check(content)
        except (UnicodeError, csv.Error, ValueError):
            raise HTTPException(422, "Upload a readable CSV with a header and 1–20,000 rows, up to 100 columns.") from None
        content_type = "text/csv"
    else:
        content_type, _ = await run_in_threadpool(_staff.inspect_file, content, max_pages=100)
    try:
        document = await run_in_threadpool(library_files.save, staff, content, filename, content_type, kind,
                                         label.strip(), payer.strip(), effective_from or None, effective_to or None)
    except Exception:
        raise HTTPException(503, "The clinic document could not be saved. Please try again.") from None
    if kind != 'era_835':
        document['extraction_summary'] = {'status': 'reading', 'started_at': staff_store.now()}
        await run_in_threadpool(staff_store.save_library_extraction, document['id'], staff['practice_id'],
                               {'summary': document['extraction_summary']})
        background_tasks.add_task(library_files.extract_after_upload, document)
    return JSONResponse({**public_library(document), **({"era": era} if era else {})}, status_code=201, headers=NO_STORE)

@router.get('/library/{document_id}/text')
async def library_text(document_id: str, request: Request, background_tasks: BackgroundTasks, retry: bool = False):
    staff = await _staff.require_staff(request)
    document = await run_in_threadpool(staff_store.get_library, document_id, staff['practice_id'])
    if not document:
        raise HTTPException(404, 'Clinic document not found')
    if document['kind'] == 'era_835':
        raise HTTPException(422, 'Use the structured ERA view for remittance documents.')
    headers = {**NO_STORE, 'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"}
    saved = document.get('extraction') or {}
    summary = saved.get('summary') or {}
    if saved.get('version') == library_files.EXTRACTION_VERSION and saved.get('pages') and saved.get('sha256') == document.get('sha256'):
        return Response(library_files.extraction_preview(document, saved), media_type='text/html', headers=headers)
    stale = summary.get('started_at') and datetime.fromisoformat(summary['started_at']) < datetime.now(timezone.utc) - timedelta(minutes=30)
    if (summary.get('status') == 'needs_retry' or stale) and not retry:
        return Response('<h1>Text extraction needs another attempt</h1><p>The original document is saved. '
                        'Complete text is not available yet.</p><a href="text?retry=true">Retry extraction</a> · '
                        '<a href="document?preview=true">Open original</a>', status_code=503, media_type='text/html', headers=headers)
    if summary.get('status') != 'reading' or stale:
        await run_in_threadpool(staff_store.save_library_extraction, document['id'], staff['practice_id'],
                               {'summary': {'status': 'reading', 'started_at': staff_store.now()}})
        background_tasks.add_task(library_files.extract_after_upload, document)
    return Response('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="5;url=text">'
                    '<h1>Reading the document</h1><p>Reading every page and using OCR for scanned or outlined text. '
                    'This page will refresh when the extracted text is ready.</p><a href="document?preview=true">Open original</a>',
                    status_code=202, media_type='text/html', headers=headers)

@router.get("/library/{document_id}/document")
async def library_document(document_id: str, request: Request, preview: bool = False):
    staff = await _staff.require_staff(request)
    document = await run_in_threadpool(staff_store.get_library, document_id, staff["practice_id"])
    if not document:
        raise HTTPException(404, "Clinic document not found")
    content = await run_in_threadpool(object_store.get_object_bytes, document["object_key"])
    content_type = document["content_type"]
    if preview and content_type in library_files.OFFICE_TYPES.values():
        try:
            content = await run_in_threadpool(library_files.office_preview, content, document['filename'])
        except Exception:
            raise HTTPException(422, "This document cannot be previewed. Download the original file.") from None
        content_type = 'text/html'
    if preview and content_type in {"text/csv", "application/edi-x12"}:
        content_type = "text/plain"
    disposition = "inline" if preview else "attachment"
    return Response(content, media_type=content_type, headers={**NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(document['filename'], safe='')}", "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})

@router.get("/library/{document_id}/era")
async def library_era(document_id: str, request: Request):
    staff = await _staff.require_staff(request)
    document = await run_in_threadpool(staff_store.get_library, document_id, staff["practice_id"])
    if not document or document["kind"] != "era_835":
        raise HTTPException(404, "835 ERA not found")
    try:
        return JSONResponse(await run_in_threadpool(era835.parse, await run_in_threadpool(object_store.get_object_bytes, document["object_key"])), headers=NO_STORE)
    except ValueError:
        raise HTTPException(422, "The saved ERA cannot be parsed.") from None
