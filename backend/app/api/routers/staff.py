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
from app.insurance import commercial, commercial_store, ecw_backend, era835, golden, v1_sources
from app.logs.collection_audit import collection_event


router = APIRouter(prefix="/api/staff", tags=["staff"])
STAFF_COOKIE = "clinicforce_staff_session"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._+\-]*$")
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def normalized_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Member(Login):
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["staff", "admin"] = "staff"

    @field_validator("display_name")
    @classmethod
    def valid_display_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a staff name without control characters")
        return value


class Patient(Model):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    member_id: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=254)
    payer: str = Field(default="", max_length=200)
    group_number: str = Field(default="", max_length=80)
    relationship: Literal["self", "spouse", "child", "other", "unknown", "18", "01", "19", "G8"]
    subscriber_name: str = Field(default="", max_length=200)
    subscriber_birth_date: date | None = None
    patient_id: str | None = Field(default=None, max_length=100)
    identity_confirmed: Literal[True]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return clinic_email._address(value) if value else ""

    @model_validator(mode="after")
    def subscriber_identified(self):
        if self.relationship not in {"self", "18", "unknown"} and not self.subscriber_name:
            raise ValueError("Enter the policyholder name when the patient is not the policyholder")
        return self

    @field_validator("subscriber_birth_date", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value

    @field_validator("birth_date", "subscriber_birth_date")
    @classmethod
    def plausible_date(cls, value):
        if value and not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid birth date that is not in the future")
        return value


class IntakeSearch(Model):
    query: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=50)
    offset: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)


class PatientSearch(Model):
    query: str = Field(default="", max_length=200)
    given_name: str = Field(default="", max_length=100)
    family_name: str = Field(default="", max_length=100)
    birth_date: str = Field(default="", max_length=10)


class EcwLookup(Model):
    given_name: str = Field(min_length=1, max_length=100)
    middle_initial: str = Field(default="", max_length=10)
    family_name: str = Field(min_length=1, max_length=100)
    birth_date: date

    @field_validator("birth_date")
    @classmethod
    def plausible_birth_date(cls, value):
        if not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid date of birth that is not in the future")
        return value


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


@router.post("/intakes/ecw-lookup")
async def create_ecw_lookup(value: EcwLookup, request: Request):
    """Confirm an eCW patient and upcoming visits.

    The shared reader has a 24-second deadline. HTTP 502 errors identify the
    failed lookup phase; X-ClinicForce-Request-ID joins the safe stage logs in
    CloudWatch. No patient or appointment is inferred from a timed-out read.
    """
    staff = await require_staff(request)
    started = time.monotonic()
    collection_event('ecw_lookup_started', practice_id=staff['practice_id'], staff_id=staff['id'], stage='patient_search')
    if not ecw_backend.configured():
        collection_event('ecw_lookup_blocked', practice_id=staff['practice_id'], stage='patient_search', reason_code='ecw_not_configured')
        raise HTTPException(503, "The eClinicalWorks practice connection is not configured.")
    try:
        matched = await asyncio.wait_for(ecw_backend.lookup_patient_and_appointments(
            {"patient_first_name": value.given_name, "patient_middle_initial": value.middle_initial,
             "patient_last_name": value.family_name, "patient_birth_date": value.birth_date.isoformat()},
            {"timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York")},
        ), timeout=26)
    except asyncio.TimeoutError:
        collection_event('ecw_lookup_failed', practice_id=staff['practice_id'], stage='patient_search', reason_code='timeout', duration_ms=int((time.monotonic() - started) * 1000))
        raise HTTPException(504, "eClinicalWorks did not respond to the patient search in time. Retry the lookup.") from None
    except ecw_backend.EcwBackendError as exc:
        collection_event('ecw_lookup_failed', practice_id=staff['practice_id'], stage=exc.stage or 'patient_search', reason_code=exc.reason_code or 'ecw_error', duration_ms=int((time.monotonic() - started) * 1000))
        raise HTTPException(502, str(exc)) from exc
    fields = {"given_name": value.given_name, "middle_initial": value.middle_initial,
              "family_name": value.family_name, "birth_date": value.birth_date.isoformat(),
              "ecw_patient_id": matched["patient"]["id"],
              "ecw_account_number": matched['patient'].get('ecw_account_number')}
    intake = await run_in_threadpool(staff_store.find_ecw_intake, staff["practice_id"], fields["ecw_patient_id"])
    reused = bool(intake)
    if not intake:
        intake = await run_in_threadpool(staff_store.create_intake, str(uuid4()), staff, [], "ecw_lookup", None, fields)
    elif intake.get('patient'):
        await run_in_threadpool(staff_store.save_ecw_account_number, intake['patient']['id'], staff['practice_id'],
                                fields['ecw_patient_id'], fields['ecw_account_number'])
        intake = await run_in_threadpool(staff_store.get_intake, intake['id'], staff['practice_id'])
    collection_event('ecw_lookup_completed', practice_id=staff['practice_id'], intake_id=intake['id'],
                     staff_id=staff['id'], stage='patient_search', outcome='reused' if reused else 'created',
                     count=len(matched['appointments']), duration_ms=int((time.monotonic() - started) * 1000))
    return JSONResponse({**public(intake), "ecw_match": {"patient": {key: matched["patient"][key]
                         for key in ("given_name", "family_name", "birth_date", "ecw_account_number") if key in matched['patient']},
                         "appointments": matched["appointments"]}}, status_code=201, headers=NO_STORE)


@router.post("/intakes")
async def upload_cards(request: Request, files: list[UploadFile] = File(default=[]), full_name: str = Form(default="", max_length=200),
                       member_id: str = Form(default="", max_length=80), birth_date: str = Form(default="", max_length=10)):
    """Save card files, propose cited OCR fields, and retain manual entry when a card omits details."""
    staff = await require_staff(request)
    manual_fields = manual_card_fields(full_name, member_id, birth_date)
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
        content_type, count = await run_in_threadpool(inspect_file, content)
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
        return JSONResponse(public(intake), status_code=201, headers=NO_STORE)
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
    return JSONResponse(public(intake), status_code=201, headers=NO_STORE)


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
                message = 'The current eCW connection did not return a card preview. A card saved in eCW folders may still be available there. Card previews are optional for estimates.'
        except Exception:
            message = 'The eCW card connection is unavailable. Any saved ClinicForce cards appear below. Card previews are optional for estimates.'
    return JSONResponse({'cards': cards, 'message': message}, headers=NO_STORE)


@router.get("/intakes/{intake_id}/ecw-card")
async def ecw_card(intake_id: str, document_id: str, request: Request):
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


def public_library(document: dict) -> dict:
    return {key: value for key, value in document.items() if key not in {"object_key", "created_by", "extraction"}}


@router.get("/library")
async def library(request: Request):
    staff = await require_staff(request)
    documents = await run_in_threadpool(staff_store.list_library, staff["practice_id"])
    usage = await run_in_threadpool(library_files.estimator_links, staff['practice_id'], documents)
    return JSONResponse({"documents": [{**public_library(value), 'estimator_uses': usage['links'][value['id']]} for value in documents]}, headers=NO_STORE)


@router.post("/library/rate-versions/{version_id}/retire")
async def retire_library_rate_version(version_id: str, request: Request):
    staff = await require_staff(request)
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
    staff = await require_staff(request)
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
        content_type, _ = await run_in_threadpool(inspect_file, content, max_pages=100)
    try:
        document = await run_in_threadpool(library_files.save, staff, content, filename, content_type, kind,
                                         label.strip(), payer.strip(), effective_from or None, effective_to or None)
    except Exception:
        raise HTTPException(503, "The clinic document could not be saved. Please try again.") from None
    if content_type in {'application/pdf', 'image/png', 'image/jpeg'}:
        await run_in_threadpool(staff_store.save_library_extraction, document['id'], staff['practice_id'],
                               {'summary': {'status': 'reading', 'started_at': staff_store.now()}})
        background_tasks.add_task(library_files.extract_after_upload, document)
    return JSONResponse({**public_library(document), **({"era": era} if era else {})}, status_code=201, headers=NO_STORE)


@router.get('/library/{document_id}/text')
async def library_text(document_id: str, request: Request, background_tasks: BackgroundTasks, retry: bool = False):
    staff = await require_staff(request)
    document = await run_in_threadpool(staff_store.get_library, document_id, staff['practice_id'])
    if not document:
        raise HTTPException(404, 'Clinic document not found')
    if document['content_type'] not in {'application/pdf', 'image/png', 'image/jpeg'}:
        raise HTTPException(422, 'Extracted page text is available for PDFs and images.')
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
    staff = await require_staff(request)
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
    staff = await require_staff(request)
    document = await run_in_threadpool(staff_store.get_library, document_id, staff["practice_id"])
    if not document or document["kind"] != "era_835":
        raise HTTPException(404, "835 ERA not found")
    try:
        return JSONResponse(await run_in_threadpool(era835.parse, await run_in_threadpool(object_store.get_object_bytes, document["object_key"])), headers=NO_STORE)
    except ValueError:
        raise HTTPException(422, "The saved ERA cannot be parsed.") from None


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


@router.get('/patients/{patient_id}/appointments')
async def patient_appointments(patient_id: str, request: Request, include_ecw: bool = False):
    from app.insurance import store
    from app.api.routers.commercial_estimator import _ecw_practice_config
    actor = await require_staff(request)
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
    actor = await require_staff(request)
    intake = await owned_intake(intake_id, actor)
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
