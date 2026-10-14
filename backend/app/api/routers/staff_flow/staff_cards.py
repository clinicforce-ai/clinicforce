"""Staff cards routes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core import object_store, staff_store
from app.insurance import ecw_backend, v1_sources
from app.logs.collection_audit import collection_event
from app.api.schemas.staff import EcwLookup

from app.api.routers import staff as _staff

router = _staff.router
NO_STORE = _staff.NO_STORE
MAX_INSURANCE_CARD_BYTES = _staff.MAX_INSURANCE_CARD_BYTES

@router.post("/intakes/ecw-lookup", responses={
    200: {"description": "selection_required with up to eight unconfirmed candidates (possibly empty); no intake or visit read yet."},
    201: {"description": "Verified/reused clinic intake and eCW patient/appointments."},
    409: {"description": "The selected patient's details changed; repeat the search and confirmation."},
    422: {"description": "Missing or invalid date of birth, or neither a full name nor an eCW ACCT number."},
})
async def create_ecw_lookup(value: EcwLookup, request: Request):
    """Confirm an eCW patient and upcoming visits.

    HTTP 200 selection_required returns up to eight unconfirmed similar patients
    (or an empty list); no intake, visits, or payer request is created. Submit the
    selected ID, canonical demographics and identity_confirmed=true to reverify.
    HTTP 201 returns the verified intake and visits. Timeout/auth failures remain errors.

    ACCT plus DOB can find a patient without supplied names. The returned
    secondary identifier is verified and staff must confirm the candidate before
    any encounters are read or an intake is saved.

    Explicit selection can refresh a linked clinic record only for its existing
    eCW ID. Saved insurance snapshots are preserved; cached identity approvals
    are cleared when demographics change. Account lookup uses identifier+DOB;
    bounded name-only fallback requires both returned names to match locally.

    The shared reader has a 24-second deadline. HTTP 502 errors identify the
    failed lookup phase; X-ClinicForce-Request-ID joins the safe stage logs in
    CloudWatch. No patient or appointment is inferred from a timed-out read.
    """
    staff = await _staff.require_staff(request)
    started = time.monotonic()
    collection_event('ecw_lookup_started', practice_id=staff['practice_id'], staff_id=staff['id'], stage='patient_search')
    if not ecw_backend.configured():
        collection_event('ecw_lookup_blocked', practice_id=staff['practice_id'], stage='patient_search', reason_code='ecw_not_configured')
        raise HTTPException(503, "The eClinicalWorks practice connection is not configured.")
    try:
        matched = await asyncio.wait_for(ecw_backend.lookup_patient_and_appointments(
            {"patient_first_name": value.given_name, "patient_middle_initial": value.middle_initial,
             "patient_last_name": value.family_name, "patient_birth_date": value.birth_date.isoformat(),
             "ecw_account_number": value.ecw_account_number,
             **({"ecw_patient_id": value.selected_patient_id} if value.selected_patient_id else {})},
            {"timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York")},
            allow_suggestions=not bool(value.selected_patient_id),
        ), timeout=26)
    except asyncio.TimeoutError:
        collection_event('ecw_lookup_failed', practice_id=staff['practice_id'], stage='patient_search', reason_code='timeout', duration_ms=int((time.monotonic() - started) * 1000))
        raise HTTPException(504, "eClinicalWorks did not respond to the patient search in time. Retry the lookup.") from None
    except ecw_backend.EcwPatientSelectionRequired as exc:
        return JSONResponse({"status": "selection_required", "candidates": exc.candidates}, headers=NO_STORE)
    except ecw_backend.EcwBackendError as exc:
        collection_event('ecw_lookup_failed', practice_id=staff['practice_id'], stage=exc.stage or 'patient_search', reason_code=exc.reason_code or 'ecw_error', duration_ms=int((time.monotonic() - started) * 1000))
        raise HTTPException(409 if value.selected_patient_id and exc.reason_code in {"candidate_changed", "no_match"} else 502, str(exc)) from exc
    fields = {"given_name": matched["patient"]["given_name"], "middle_initial": value.middle_initial,
              "family_name": matched["patient"]["family_name"], "birth_date": matched["patient"]["birth_date"],
              "ecw_patient_id": matched["patient"]["id"],
              "ecw_account_number": matched['patient'].get('ecw_account_number')}
    intake = await run_in_threadpool(staff_store.find_ecw_intake, staff["practice_id"], fields["ecw_patient_id"])
    reused = bool(intake)
    if intake and intake.get('patient') and not value.selected_patient_id:
        saved = intake['patient']
        current = {"name": [{"given": [fields['given_name']], "family": fields['family_name']}], "birthDate": fields['birth_date']}
        if not ecw_backend._patient_matches(current, {"patient_first_name": saved['given_name'],
                "patient_last_name": saved['family_name'], "patient_birth_date": str(saved['birth_date'])}, family_suffix=True):
            return JSONResponse({"status": "selection_required", "candidates": [{**matched['patient'],
                "differences": [label for key, label in (("given_name", "Saved first name"), ("family_name", "Saved last name"),
                                                         ("birth_date", "Saved date of birth")) if str(saved[key]) != str(fields[key])]}]}, headers=NO_STORE)
    if not intake:
        intake = await run_in_threadpool(staff_store.create_intake, str(uuid4()), staff, [], "ecw_lookup", None, fields)
    elif intake.get('patient'):
        if value.selected_patient_id:
            try:
                await run_in_threadpool(staff_store.confirm_ecw_demographics, intake['id'], staff, fields)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        else:
            await run_in_threadpool(staff_store.save_ecw_account_number, intake['patient']['id'], staff['practice_id'],
                                    fields['ecw_patient_id'], fields['ecw_account_number'])
        intake = await run_in_threadpool(staff_store.get_intake, intake['id'], staff['practice_id'])
    collection_event('ecw_lookup_completed', practice_id=staff['practice_id'], intake_id=intake['id'],
                     staff_id=staff['id'], stage='patient_search', outcome='reused' if reused else 'created',
                     count=len(matched['appointments']), duration_ms=int((time.monotonic() - started) * 1000))
    return JSONResponse({**_staff.public(intake), "ecw_match": {"patient": {key: matched["patient"][key]
                         for key in ("given_name", "family_name", "birth_date", "ecw_account_number") if key in matched['patient']},
                         "appointments": matched["appointments"]}}, status_code=201, headers=NO_STORE)

@router.post("/intakes")
async def upload_cards(request: Request, files: list[UploadFile] = File(default=[]), full_name: str = Form(default="", max_length=200),
                       member_id: str = Form(default="", max_length=80), birth_date: str = Form(default="", max_length=10)):
    """Save card files, propose cited OCR fields, and retain manual entry when a card omits details."""
    staff = await _staff.require_staff(request)
    manual_fields = _staff.manual_card_fields(full_name, member_id, birth_date)
    if len(files) > 2:
        raise HTTPException(422, "Upload one insurance card PDF or up to two card images")
    if not files and not all(manual_fields.get(key) for key in ("given_name", "family_name", "member_id", "birth_date")):
        raise HTTPException(422, "Upload an insurance card or enter the patient name, insurance ID, and date of birth")
    if files and not object_store.enabled():
        raise HTTPException(503, "Card storage is unavailable. Your card has not been saved.")
    prepared, pages = [], 0
    for file in files:
        content = await file.read(MAX_INSURANCE_CARD_BYTES + 1)
        if len(content) > MAX_INSURANCE_CARD_BYTES:
            raise HTTPException(413, "Each insurance card file must be 10 MB or smaller")
        content_type, count = await run_in_threadpool(_staff.inspect_file, content)
        pages += count
        if pages > 2:
            raise HTTPException(422, "Upload at most two insurance card pages in total")
        prepared.append((Path(file.filename or "insurance-card").name[:200], content, content_type))
    intake_id, documents = str(uuid4()), []
    try:
        for filename, content, content_type in prepared:
            suffix = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[content_type]
            key = await run_in_threadpool(object_store.put_staff_document, staff["practice_id"], intake_id, "source" + suffix, content, content_type)
            documents.append({"id": str(uuid4()), "filename": filename, "content_type": content_type,
                              "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "created_at": staff_store.now(), "object_key": key})
        intake = await run_in_threadpool(staff_store.create_intake, intake_id, staff, documents,
                                         "insurance_card" if files else "manual_entry", None, manual_fields)
    except Exception:
        for document in documents:
            try:
                await run_in_threadpool(object_store.delete_object, document["object_key"])
            except Exception:
                pass
        raise HTTPException(503, "The card could not be saved. Please try again.") from None
    if not files:
        return JSONResponse(_staff.public(intake), status_code=201, headers=NO_STORE)
    try:
        fields, evidence, counts = await asyncio.wait_for(
            asyncio.to_thread(v1_sources.read_card_fields, [content for _, content, _ in prepared]), timeout=20)
        offset, source_pages = 0, {}
        for index, count in enumerate(counts):
            source_pages.update({number + offset: {"document_id": documents[index]["id"],
                "source_filename": documents[index]["filename"], "source_page": number} for number in range(1, count + 1)})
            offset += count
        for item in evidence.values():
            item.update(source_pages.get(item.get("page"), {}))
        warning = None if fields.get("member_id") or manual_fields.get("member_id") else "Card saved. Enter the missing details below before starting verification."
        if fields.get("member_name"):
            fields["subscriber_name"] = fields["member_name"]
        intake = await run_in_threadpool(staff_store.save_extraction, intake_id, staff["practice_id"], fields, evidence, warning, manual_fields)
    except Exception:
        warning = "Card saved. Automatic reading is unavailable; review the original and enter the details below."
        try:
            intake = await run_in_threadpool(staff_store.save_extraction, intake_id, staff["practice_id"], {}, {}, warning, manual_fields)
        except Exception:
            intake["warning"] = warning
    return JSONResponse(_staff.public(intake), status_code=201, headers=NO_STORE)
