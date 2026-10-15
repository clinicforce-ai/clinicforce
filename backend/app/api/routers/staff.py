"""Authenticated practice intake; an insurance card never grants account access."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import mimetypes
import os
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4
from urllib.parse import quote

import fitz
from PIL import Image
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.settings import ALLOWED_ORIGINS, CLINICIAN_SHARE_COOKIE, MAX_INSURANCE_CARD_BYTES, MAX_UPLOAD_BYTES, PUBLIC_BASE_URL
from app.core import clinic_email, object_store, staff_store, staff_deletions, library_files
from app.core.patient_store import get_patient_profile
from app.core.request_address import request_address
from app.insurance import commercial, commercial_store, ecw_backend, era835, golden, missing_rates, store, v1_sources
from app.logs.collection_audit import collection_event


router = APIRouter(prefix="/api/staff", tags=["staff"])
STAFF_COOKIE = "clinicforce_staff_session"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


from app.api.schemas.staff import (
    Model,
    MissingRateGap,
    MissingRatesResponse,
    Login,
    Member,
    Patient,
    IntakeSearch,
    PatientSearch,
    EcwLookup,
)


def check_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in {PUBLIC_BASE_URL, *(value.rstrip("/") for value in ALLOWED_ORIGINS)}:
        raise HTTPException(403, "Staff changes must come from the ClinicForce site")


async def require_staff(request: Request) -> dict:
    check_origin(request)
    staff = await run_in_threadpool(staff_store.get_session, request.cookies.get(STAFF_COOKIE, ""))
    if not staff:
        raise HTTPException(401, "Sign in to your clinic workspace")
    return staff


def public(intake: dict) -> dict:
    value = {key: item for key, item in intake.items() if key not in {"created_by", "source_portal_patient_id"}}
    for key in ('patient', 'expected_patient', 'auto_patient'):
        if isinstance(value.get(key), dict):
            value[key] = {k: v for k, v in value[key].items() if k != 'insurance_identities'}
    value["documents"] = [{key: item for key, item in document.items() if key != "object_key"} for document in value.get("documents", [])]
    value["workflow_documents"] = []
    return value


def workflow_sources(intake: dict) -> list[tuple[str, str, dict]]:
    """Resolve saved engine attachments through the same practice and patient boundary."""
    from app.insurance import store
    if not intake.get("patient"):
        return []
    documents = []
    for version in ("v1", "v2"):
        case_id = intake.get("workflows", {}).get(version)
        if not case_id:
            continue
        case = store.get_case(case_id, intake["patient"]["id"], "", practice_id=intake["practice_id"], intake_id=intake["id"])
        if not case or case.get("source_mode") != ("documents" if version == "v1" else "availity"):
            continue
        sources = case.get("documents" if version == "v1" else "sources", {})
        for kind, source in sources.items():
            if isinstance(source, dict) and isinstance(source.get("object_key"), str) and source["object_key"]:
                documents.append((version, kind, {**source, "observed_at": source.get("observed_at") or source.get("created_at") or case.get("created_at")}))
    return documents


def workflow_metadata(intake: dict, documents: list[tuple[str, str, dict]]) -> list[dict]:
    return [{"id": f"{version}:{kind}", "version": version, "kind": kind,
             "filename": source.get("filename") or "Insurance document",
             "content_type": source.get("content_type") or mimetypes.guess_type(source.get("filename", ""))[0] or "application/octet-stream",
             "observed_at": source.get("observed_at"),
             "download_url": f"/api/staff/intakes/{quote(intake['id'], safe='')}/workflow-documents/{version}/{quote(kind, safe='')}"}
            for version, kind, source in documents]


async def owned_intake(intake_id: str, staff: dict) -> dict:
    intake = await run_in_threadpool(staff_store.get_intake, intake_id, staff["practice_id"])
    if not intake:
        raise HTTPException(404, "Insurance intake not found")
    return intake


@router.post("/login")
async def login(body: Login, request: Request):
    check_origin(request)
    try:
        result = await run_in_threadpool(staff_store.authenticate, body.username, body.password, request_address(request))
    except RuntimeError:
        raise HTTPException(503, "Staff sign-in is not available. Contact your clinic administrator.") from None
    if not result:
        raise HTTPException(401, "Unable to sign in. Check your details or try again later.")
    staff, token = result
    response = JSONResponse({"staff": staff}, headers=NO_STORE)
    response.set_cookie(STAFF_COOKIE, token, httponly=True, secure=PUBLIC_BASE_URL.startswith("https://"),
                        samesite="lax", path="/api", max_age=staff_store.SESSION_SECONDS)
    return response


@router.get("/session")
async def session(request: Request):
    return JSONResponse({"staff": await require_staff(request)}, headers=NO_STORE)


@router.post("/logout")
async def logout(request: Request):
    check_origin(request)
    await run_in_threadpool(staff_store.logout, request.cookies.get(STAFF_COOKIE, ""))
    response = Response(status_code=204, headers=NO_STORE)
    response.delete_cookie(STAFF_COOKIE, path="/api")
    return response


@router.post("/members")
async def create_member(body: Member, request: Request):
    staff = await require_staff(request)
    if staff["role"] != "admin":
        raise HTTPException(403, "Only a clinic administrator can add staff")
    member = await run_in_threadpool(staff_store.create_member, staff, body.username, body.display_name, body.password, body.role)
    if not member:
        raise HTTPException(409, "This username is unavailable")
    return JSONResponse({"staff": member}, status_code=201, headers=NO_STORE)


def inspect_file(content: bytes, *, max_pages: int = 2) -> tuple[str, int]:
    """Validate actual bytes without making OCR a prerequisite to saving intake."""
    try:
        if content.startswith(b"%PDF"):
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or not 1 <= len(document) <= max_pages:
                    raise ValueError
                for page in document:
                    if page.rect.width <= 0 or page.rect.height <= 0:
                        raise ValueError
                return "application/pdf", len(document)
        expected = "JPEG" if content.startswith(b"\xff\xd8\xff") else "PNG" if content.startswith(b"\x89PNG\r\n\x1a\n") else None
        if expected:
            # WeasyPrint enables Pillow's permissive truncated-image mode globally.
            # Check the file terminator without changing that process-wide setting.
            terminator = b"\xff\xd9" if expected == "JPEG" else b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
            if not content.rstrip(b"\r\n\t ").endswith(terminator):
                raise ValueError
            with Image.open(BytesIO(content)) as picture:
                if picture.format != expected or picture.width * picture.height > 40_000_000 or getattr(picture, "n_frames", 1) != 1:
                    raise ValueError
                picture.verify()
            with Image.open(BytesIO(content)) as picture:
                picture.load()
            return ("image/jpeg" if expected == "JPEG" else "image/png"), 1
    except Exception:
        pass
    raise HTTPException(422, f"Upload a readable JPG, PNG or unlocked PDF with at most {max_pages} pages.")


@router.get("/intakes")
async def list_intakes(request: Request, status: str = Query(default="", max_length=50), offset: int = Query(default=0, ge=0, le=1_000_000_000)):
    staff = await require_staff(request)
    intakes = await run_in_threadpool(staff_store.list_intakes, staff["practice_id"], "", status, offset)
    return intake_page(intakes, offset)


def intake_page(intakes: list[dict], offset: int) -> JSONResponse:
    return JSONResponse({"intakes": [public(value) for value in intakes[:100]], "has_more": len(intakes) > 100,
                         "next_offset": offset + 100 if len(intakes) > 100 else None}, headers=NO_STORE)


@router.post("/intakes/search")
async def search_intakes(body: IntakeSearch, request: Request):
    staff = await require_staff(request)
    intakes = await run_in_threadpool(staff_store.list_intakes, staff["practice_id"], body.query, body.status, body.offset)
    return intake_page(intakes, body.offset)


def manual_card_fields(full_name: str, member_id: str, birth_date: str) -> dict:
    full_name = " ".join(full_name.split())
    member_id = member_id.strip()
    if any(ord(character) < 32 for character in full_name + member_id) or len(full_name) > 200 or len(member_id) > 80:
        raise HTTPException(422, "Enter a valid patient name and insurance ID")
    fields = {"member_id": member_id} if member_id else {}
    if full_name:
        parts = full_name.split(" ", 1)
        fields["given_name"] = parts[0]
        if len(parts) > 1:
            fields["family_name"] = parts[1]
    if birth_date:
        try:
            parsed = date.fromisoformat(birth_date)
        except ValueError:
            raise HTTPException(422, "Enter a valid date of birth") from None
        if not date(1850, 1, 1) <= parsed <= date.today():
            raise HTTPException(422, "Enter a valid date of birth")
        fields["birth_date"] = parsed.isoformat()
    return fields


from app.api.routers.staff_flow.staff_cards import create_ecw_lookup, upload_cards


@router.post("/intakes/from-share")
async def from_share(request: Request):
    staff = await require_staff(request)
    try:
        share = golden.open_share_token(request.cookies.get(CLINICIAN_SHARE_COOKIE, ""), False)
    except golden.GoldenCaseError:
        raise HTTPException(403, "Open the patient's QR code and PIN before adding this intake") from None
    profile = await run_in_threadpool(get_patient_profile, share["patient_id"])
    if not profile:
        raise HTTPException(422, "The shared patient profile is unavailable")
    fields = {"given_name": profile.given_name or "", "family_name": profile.family_name or "", "birth_date": profile.birth_date or ""}
    intake = await run_in_threadpool(staff_store.create_intake, str(uuid4()), staff, [], "clinicforce_qr", share["patient_id"], fields)
    return JSONResponse(public(intake), status_code=201, headers=NO_STORE)


@router.get("/intakes/{intake_id}")
async def get_intake(intake_id: str, request: Request):
    intake = await owned_intake(intake_id, await require_staff(request))
    documents = await run_in_threadpool(workflow_sources, intake)
    value = public(intake)
    value["workflow_documents"] = workflow_metadata(intake, documents)
    return JSONResponse(value, headers=NO_STORE)


@router.get("/intakes/{intake_id}/workflow-documents/{version}/{kind}")
async def get_workflow_document(intake_id: str, version: Literal["v1", "v2"], kind: str, request: Request):
    intake = await owned_intake(intake_id, await require_staff(request))
    sources = await run_in_threadpool(workflow_sources, intake)
    source = next((document for candidate, name, document in sources if candidate == version and name == kind), None)
    if not source:
        raise HTTPException(404, "Insurance workflow document not found")
    content = await run_in_threadpool(object_store.get_object_bytes, source["object_key"])
    content_type = "application/pdf" if content.startswith(b"%PDF") else "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if content.startswith(b"\xff\xd8\xff") else "text/csv" if str(source.get("filename", "")).lower().endswith(".csv") else "application/octet-stream"
    filename = quote(Path(source.get("filename") or "insurance-document").name, safe="")
    return Response(content, media_type=content_type, headers={**NO_STORE, "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}", "Content-Security-Policy": "sandbox"})


@router.get("/intakes/{intake_id}/documents/{document_id}")
async def get_document(intake_id: str, document_id: str, request: Request):
    intake = await owned_intake(intake_id, await require_staff(request))
    document = next((item for item in intake["documents"] if item["id"] == document_id), None)
    if not document:
        raise HTTPException(404, "Card document not found")
    content = await run_in_threadpool(object_store.get_object_bytes, document["object_key"])
    return Response(content, media_type=document["content_type"], headers={**NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline", "Content-Security-Policy": "sandbox"})


@router.get("/intakes/{intake_id}/cards")
async def patient_cards(intake_id: str, request: Request):
    """List patient-bound ClinicForce cards and eCW health insurance card previews.

    eCW DocumentReference type 64290-0 is required for its cards; government ID
    documents are excluded. An empty result explains the separate eCW grant.
    """
    staff = await require_staff(request)
    intake = await owned_intake(intake_id, staff)
    sources = await run_in_threadpool(staff_store.patient_card_intakes, staff['practice_id'], intake['patient']['id']) if intake.get('patient') else [intake]
    cards = [{"id": item['id'], "title": item['filename'], "content_type": item['content_type'],
              "url": f"/api/staff/intakes/{quote(source['id'], safe='')}/documents/{quote(item['id'], safe='')}"}
             for source in sources for item in source['documents']]
    patient_id = (intake.get('patient') or {}).get('ecw_patient_id') or intake.get('fields', {}).get('ecw_patient_id')
    message = ''
    if patient_id:
        try:
            rows = await ecw_backend.insurance_cards(str(patient_id))
            cards.extend({**item, 'url': f"/api/staff/intakes/{quote(intake_id, safe='')}/ecw-card?document_id={quote(item['id'], safe='')}"} for item in rows)
            if not cards:
                message = 'eCW returned no health insurance card for this patient. Ask eCW to verify DocumentReference type 64290-0 for this practice; government ID access is separate.'
        except Exception:
            message = 'The eCW card connection is unavailable. Any saved ClinicForce cards appear below. Card previews are optional for estimates.'
    return JSONResponse({'cards': cards, 'message': message}, headers=NO_STORE)


@router.get("/intakes/{intake_id}/ecw-card")
async def ecw_card(intake_id: str, document_id: str, request: Request):
    """Read one patient-bound eCW health insurance card image or PDF for preview."""
    intake = await owned_intake(intake_id, await require_staff(request))
    patient_id = (intake.get('patient') or {}).get('ecw_patient_id') or intake.get('fields', {}).get('ecw_patient_id')
    if not patient_id:
        raise HTTPException(404, 'No eCW patient is linked to this intake.')
    try:
        content, mime = await ecw_backend.insurance_cards(str(patient_id), document_id=document_id)
    except Exception as exc:
        raise HTTPException(502, 'The eCW insurance card could not be read.') from exc
    return Response(content, media_type=mime, headers={**NO_STORE, 'Content-Disposition': 'inline', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': 'sandbox'})


@router.put("/intakes/{intake_id}/patient")
async def confirm_patient(intake_id: str, body: Patient, request: Request):
    staff = await require_staff(request)
    try:
        intake = await run_in_threadpool(staff_store.confirm_patient, intake_id, staff, body.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not intake:
        raise HTTPException(404, "Insurance intake not found")
    if intake.get('source') == 'ecw_lookup':
        collection_event('ecw_identity_confirmed', practice_id=staff['practice_id'], intake_id=intake['id'],
                         staff_id=staff['id'], stage='identity', outcome='confirmed')
    return JSONResponse(public(intake), headers=NO_STORE)


@router.post("/patients/search")
async def find_patients(body: PatientSearch, request: Request):
    staff = await require_staff(request)
    patients = await run_in_threadpool(staff_store.find_patients, staff["practice_id"], body.query, body.given_name, body.family_name, body.birth_date)
    return JSONResponse({"patients": patients}, headers=NO_STORE)


from app.api.routers.staff_flow.staff_library import public_library, library, library_missing_rates, retire_library_rate_version, upload_library, library_text, library_document, library_era


async def delete_clinic_record(kind: str, record_id: str, request: Request, intake_id: str | None = None):
    actor = await require_staff(request)
    try:
        result = await run_in_threadpool(staff_deletions.delete_record, kind, record_id, actor, intake_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(result, status_code=202 if result["cleanup_pending"] else 200, headers=NO_STORE)


@router.delete("/intakes/{intake_id}")
async def delete_unconfirmed_intake(intake_id: str, request: Request):
    return await delete_clinic_record("intake", intake_id, request, intake_id)


@router.delete("/patients/{patient_id}")
async def delete_clinic_patient(patient_id: str, request: Request):
    return await delete_clinic_record("patient", patient_id, request)


@router.post("/deletions/retry-cleanup")
async def retry_deleted_files(request: Request):
    actor = await require_staff(request)
    return JSONResponse(await run_in_threadpool(staff_deletions.retry_cleanup, actor), headers=NO_STORE)


@router.delete("/intakes/{intake_id}/checks/{case_id}")
async def delete_insurance_check(intake_id: str, case_id: str, request: Request):
    return await delete_clinic_record("check", case_id, request, intake_id)


@router.get("/intakes/{intake_id}/checks")
async def saved_insurance_checks(intake_id: str, request: Request):
    from app.insurance import store
    actor = await require_staff(request)
    intake = await owned_intake(intake_id, actor)
    records = await run_in_threadpool(store.patient_checks, actor['practice_id'], intake['patient']['id']) if intake.get('patient') else []
    labels = {"commercial_previsit": "Visit estimate", "commercial": "Coded-service estimate",
              "documents": "Document review", "availity": "Connected insurance review"}
    return JSONResponse({"checks": [{"id": case["id"], "created_at": case["created_at"], 'intake_id': record['intake_id'],
        'source_mode': case.get('source_mode'),
        'service_date': case.get('previsit_context', {}).get('result', {}).get('service_date'),
        'visit_type': case.get('previsit_context', {}).get('result', {}).get('visit_type'),
        "label": labels.get(case.get("source_mode"), "Insurance check"),
        "pending": staff_deletions._pending(case)} for record in records for case in [record['case']]]}, headers=NO_STORE)


from app.api.routers.staff_flow.staff_appointments import patient_appointments, saved_insurance_check, owned_saved_check, saved_insurance_check_financials
