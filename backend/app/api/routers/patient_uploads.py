"""Account-free, write-only insurance submissions and authenticated clinic link management."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path as ApiPath, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.routers import staff
from app.api.settings import MAX_INSURANCE_CARD_BYTES, PUBLIC_BASE_URL
from app.core import clinic_email, clinic_store, intake_links_store as links, object_store, staff_store
from app.core.request_address import request_address
from app.insurance import v1_sources


router = APIRouter(tags=["patient insurance upload"])
ClinicNpi = Annotated[str, ApiPath(min_length=10, max_length=10, pattern=r"^\d{10}$")]
LegacyClinicCode = Annotated[str, ApiPath(min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")]
UploadCode = Annotated[str, ApiPath(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")]
HEADERS = {**staff.NO_STORE, "Referrer-Policy": "no-referrer"}


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    npi: str = Field(min_length=10, max_length=10, pattern=r"^\d{10}$")


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    patient_id: str | None = Field(default=None, min_length=1, max_length=100)
    intake_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def single_target(self):
        if self.patient_id and self.intake_id:
            raise ValueError("Choose one patient or intake for this request")
        return self


async def call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except links.UploadLinkError as exc:
        raise HTTPException(exc.status, str(exc), headers=HEADERS) from None


async def clinic(npi: str) -> dict:
    value = await run_in_threadpool(clinic_store.get_public_clinic, npi)
    if not value:
        raise HTTPException(404, "Clinic not found. Check the 10-digit NPI or contact the clinic.", headers=HEADERS)
    return value


async def prepared_cards(files: list[UploadFile]) -> list[tuple[str, bytes, str]]:
    if not 1 <= len(files) <= 2:
        raise HTTPException(422, "Upload one or two card files, with at most two pages in total.", headers=HEADERS)
    prepared, pages = [], 0
    for file in files:
        content = await file.read(MAX_INSURANCE_CARD_BYTES + 1)
        if len(content) > MAX_INSURANCE_CARD_BYTES:
            raise HTTPException(413, "Each insurance card file must be 10 MB or smaller.", headers=HEADERS)
        content_type, count = await run_in_threadpool(staff.inspect_file, content)
        pages += count
        if pages > 2:
            raise HTTPException(422, "Upload at most two insurance card pages in total.", headers=HEADERS)
        prepared.append((Path(file.filename or "insurance-card").name[:200], content, content_type))
    return prepared


def card_fields(prepared: list[tuple[str, bytes, str]]) -> dict:
    fields, _, _ = v1_sources.read_card_fields([content for _, content, _ in prepared])
    return {key: fields[key] for key in ("member_name", "birth_date", "member_id") if fields.get(key)}


def public_link(row: dict) -> dict:
    return {"id": row["id"], "npi": row["clinic_code"], "patient_id": row["expected_patient_id"],
            "status": "expired" if row["status"] == "pending" and row["expires_at"] <= staff_store.now() else row["status"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "submitted_at": row["submitted_at"], "intake_id": row["intake_id"]}


def created(row: dict, code: str, *, for_staff: bool = False) -> dict:
    return {**({"id": row["id"]} if for_staff else {}), "upload_code": code,
            "url": f"{PUBLIC_BASE_URL.rstrip('/')}/{row['clinic_code']}/{code}",
            "clinic": {"npi": row["clinic_code"], "name": row["clinic_name"]}, "expires_at": row["expires_at"]}


@router.get("/api/insurance-upload/clinics/{npi}")
async def get_clinic(npi: ClinicNpi):
    value = await clinic(npi)
    return JSONResponse({"clinic": {"npi": value["npi"], "name": value["name"]}}, headers=HEADERS)


@router.post("/api/insurance-upload/start")
async def start(body: Start, request: Request):
    staff.check_origin(request)
    value = await clinic(body.npi)
    await call(links.check_rate, request_address(request), value["id"], "start")
    row, code = await call(links.create_link, value)
    return JSONResponse(created(row, code), status_code=201, headers=HEADERS)


@router.post("/api/staff/upload-links")
async def create_link(body: Create, request: Request):
    actor = await staff.require_staff(request)
    value = await run_in_threadpool(clinic_store.get_for_practice, actor["practice_id"], actor["practice_name"])
    await call(links.check_rate, actor["id"], actor["practice_id"], "staff-link")
    patient_id = body.patient_id
    if body.intake_id:
        try:
            patient_id = await run_in_threadpool(staff_store.patient_for_intake_request, body.intake_id, actor["practice_id"])
        except LookupError:
            raise HTTPException(404, "Insurance intake not found", headers=HEADERS) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc), headers=HEADERS) from None
    row, code = await call(links.create_link, value, actor, patient_id)
    return JSONResponse(created(row, code, for_staff=True), status_code=201, headers=HEADERS)


@router.get("/api/staff/patients/dashboard")
async def patient_dashboard(request: Request):
    actor = await staff.require_staff(request)
    return JSONResponse(await run_in_threadpool(staff_store.patient_dashboard, actor["practice_id"]), headers=HEADERS)


@router.get("/api/staff/upload-links")
async def get_links(request: Request):
    actor = await staff.require_staff(request)
    rows = await call(links.list_links, actor["practice_id"])
    return JSONResponse({"upload_links": [public_link(row) for row in rows]}, headers=HEADERS)


@router.post("/api/staff/upload-links/{link_id}/revoke")
async def revoke(link_id: str, request: Request):
    return JSONResponse(await call(links.revoke, link_id, await staff.require_staff(request)), headers=HEADERS)


@router.get("/api/insurance-upload/{clinic_code}/{upload_code}")
async def get_metadata(clinic_code: LegacyClinicCode, upload_code: UploadCode):
    # Retain active pre-NPI private links until they expire. Public discovery
    # accepts NPIs only, and every new link stores the clinic NPI.
    row = await call(links.metadata, clinic_code, upload_code)
    return JSONResponse({"clinic_name": row["clinic_name"], "expires_at": row["expires_at"], "status": row["status"]}, headers=HEADERS)


@router.post("/api/insurance-upload/{clinic_code}/{upload_code}/read-card")
async def read_patient_card(clinic_code: LegacyClinicCode, upload_code: UploadCode, request: Request,
                            files: list[UploadFile] = File(...)):
    """Suggest member name, birth date and member ID from a card; missing printed fields require manual entry."""
    staff.check_origin(request)
    row = await call(links.metadata, clinic_code, upload_code)
    await call(links.check_rate, request_address(request), row["practice_id"], "read-card")
    prepared = await prepared_cards(files)
    try:
        fields = await asyncio.wait_for(asyncio.to_thread(card_fields, prepared), timeout=20)
    except Exception:
        raise HTTPException(422, "We could not read details from this card. Enter them manually or try a clearer image.", headers=HEADERS) from None
    return JSONResponse({"fields": fields}, headers=HEADERS)


@router.post("/api/insurance-upload/{clinic_code}/{upload_code}")
async def submit(clinic_code: LegacyClinicCode, upload_code: UploadCode, request: Request,
                 files: list[UploadFile] = File(...), given_name: str = Form(..., min_length=1, max_length=100),
                 family_name: str = Form(..., min_length=1, max_length=100), birth_date: date = Form(...),
                 consent: bool = Form(...), member_id: str = Form(default="", max_length=80),
                 submission_id: str | None = Form(default=None, max_length=100),
                 email: str = Form(default="", max_length=254), link_account: bool = Form(default=False)):
    staff.check_origin(request)
    row = await call(links.metadata, clinic_code, upload_code)
    await call(links.check_rate, request_address(request), row["practice_id"], "submit")
    if not consent:
        raise HTTPException(422, "Confirm that you agree to send this information to the clinic.", headers=HEADERS)
    if not given_name.strip() or not family_name.strip() or not date(1850, 1, 1) <= birth_date <= date.today():
        raise HTTPException(422, "Enter the patient's name and a valid date of birth.", headers=HEADERS)
    prepared = await prepared_cards(files)
    fields = {"given_name": given_name.strip(), "family_name": family_name.strip(), "birth_date": birth_date.isoformat()}
    if member_id.strip():
        fields["member_id"] = member_id.strip()
    if email.strip():
        try:
            fields["email"] = clinic_email._address(email)
        except ValueError as exc:
            raise HTTPException(422, str(exc), headers=HEADERS) from None
    from app.api.routers.patient_clinic_account import portal_patient_for_upload
    portal_patient_id = await portal_patient_for_upload(request, fields, link_account)
    try:
        result, is_new = await call(links.submit, clinic_code, upload_code, fields, prepared, submission_id, portal_patient_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "The card could not be saved. Please try again with the same files.", headers=HEADERS) from None
    return JSONResponse(result, status_code=201 if is_new else 200, headers=HEADERS)


@router.post("/api/staff/intakes/{intake_id}/read-card")
async def read_card(intake_id: str, request: Request):
    """Reread saved card images with alternate OCR layout when the primary read misses identity details."""
    actor = await staff.require_staff(request)
    record = await staff.owned_intake(intake_id, actor)
    if record.get("patient") or record.get("workflows"):
        raise HTTPException(409, "Card reading is available before the patient is confirmed.", headers=HEADERS)
    if not record["documents"]:
        raise HTTPException(422, "This intake has no saved card files to read.", headers=HEADERS)
    await call(links.check_rate, actor["id"], actor["practice_id"], "staff-read-card")
    try:
        contents = []
        for document in record["documents"]:
            contents.append(await run_in_threadpool(object_store.get_object_bytes, document["object_key"]))
        fields, evidence, counts = await asyncio.wait_for(
            asyncio.to_thread(v1_sources.read_card_fields, contents), timeout=20)
        evidence_sources, offset = {}, 0
        for document, count in zip(record["documents"], counts):
            evidence_sources.update({number + offset: {"document_id": document["id"], "source_filename": document["filename"], "source_page": number} for number in range(1, count + 1)})
            offset += count
        for item in evidence.values():
            item.update(evidence_sources.get(item.get("page"), {}))
        if fields.get("member_name"):
            fields["subscriber_name"] = fields["member_name"]
    except Exception:
        raise HTTPException(422, "The saved card could not be read automatically. The original is still saved; review it and enter the details.", headers=HEADERS) from None
    result = await call(links.save_card_reading, intake_id, actor, fields, evidence, record["updated_at"])
    return JSONResponse(staff.public(result), headers=HEADERS)
