"""Clinician-only insurance beta endpoints."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.schemas import InsuranceClinicalContextRequest, InsuranceIntakeRequest, InsuranceServiceLinesRequest
from app.api.settings import (
    CLINICIAN_SHARE_COOKIE, CLINICIAN_SHARE_MAX_AGE, MAX_INSURANCE_CARD_BYTES, MAX_UPLOAD_BYTES,
    PUBLIC_BASE_URL, insurance_beta_enabled,
)
from app.core import object_store
from app.core.patient_store import get_patient_profile
from app.insurance import documented, golden, store, v1_sources
from app.logs.logger import log_error


router = APIRouter(prefix="/api/doctor/insurance", tags=["doctor-insurance"])
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
def _enabled() -> None:
    if not insurance_beta_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance beta is not enabled")


def _session(request: Request, require_beta: bool = True) -> dict:
    _enabled()
    try:
        return golden.open_share_token(request.cookies.get(CLINICIAN_SHARE_COOKIE, ""), require_beta)
    except golden.GoldenCaseError as exc:
        code = status.HTTP_403_FORBIDDEN if require_beta else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=code, detail=str(exc)) from exc


def _response(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers=_NO_STORE)


def _load_case(case_id: str, session: dict) -> dict:
    case = store.get_case(case_id, session["patient_id"], session["share_manifest_id"])
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance case not found or expired")
    if case.get("source_mode") == "availity":
        raise HTTPException(status_code=409, detail="Use the Beta V2 workflow for this Availity case")
    if case.get("extraction_version") != v1_sources.EXTRACTION_VERSION:
        raise HTTPException(status_code=409, detail="Start a new V1 review and upload your files again so every value has source evidence.")
    return case


def _saved(case: dict | None) -> dict:
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance case not found or expired")
    return case


@router.post("/beta")
async def enable_beta(request: Request, response: Response) -> dict:
    session = _session(request, require_beta=False)
    profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
    if profile is None:
        raise HTTPException(status_code=422, detail="The opened QR patient no longer has a matching ClinicForce profile")
    token = golden.create_share_token(
        session["patient_id"], session["share_manifest_id"], beta_enabled=True,
    )
    response.set_cookie(
        CLINICIAN_SHARE_COOKIE, token, max_age=CLINICIAN_SHARE_MAX_AGE, httponly=True,
        secure=PUBLIC_BASE_URL.startswith("https://"), samesite="lax", path="/api/doctor",
    )
    response.headers.update(_NO_STORE)
    return {"beta_enabled": True, "expires_in_seconds": CLINICIAN_SHARE_MAX_AGE}


@router.post("/cases")
async def create_case(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    session = _session(request)
    filename = Path(file.filename or "insurance-card").name
    if Path(filename).suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=415, detail="Upload the insurance card as a PDF, JPG or PNG")
    content = await file.read(MAX_INSURANCE_CARD_BYTES + 1)
    if len(content) > MAX_INSURANCE_CARD_BYTES:
        raise HTTPException(status_code=413, detail="Insurance card files must be 10 MB or smaller")
    try:
        pages = await run_in_threadpool(v1_sources.read_pages, content, 2)
        fields, evidence = v1_sources.card_fields(pages)
        workflow = documented.pending_case(fields)
        workflow.update(ocr_evidence=evidence, extraction_version=v1_sources.EXTRACTION_VERSION)
        workflow["card_source"] = {
            "filename": filename, "sha256": hashlib.sha256(content).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(), "reference": "Uploaded insurance card",
        }
        workflow["evidence_manifest_id"] = golden.evidence_binding()
        case = await run_in_threadpool(
            store.create_case, session["patient_id"], session["share_manifest_id"], golden.evidence_binding(), workflow,
        )
        return _response(case)
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        log_error("Insurance card OCR", exc)
        raise HTTPException(status_code=503, detail="Insurance card OCR is temporarily unavailable") from exc


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> JSONResponse:
    return _response(await run_in_threadpool(_load_case, case_id, _session(request)))


@router.put("/cases/{case_id}/intake")
async def confirm_intake(case_id: str, review: InsuranceIntakeRequest, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        if case.get("evidence_manifest_id") != golden.evidence_binding():
            raise golden.GoldenCaseError("The insurance review configuration changed; start a new case")
        fields = case.get("ocr", {})
        if case.get("next_stage") != "intake" or any(value != fields.get(key, "") for key, value in review.model_dump().items()):
            raise golden.GoldenCaseError("Confirm the fields extracted from the uploaded card without changing them.")
        documented.confirm_intake(case, fields)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "intake")
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/documents/{kind}")
async def upload_evidence(case_id: str, kind: str, request: Request, file: UploadFile = File(...)) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    expected_stage = documented.DOCUMENT_STAGES.get(kind)
    if case.get("source_mode") != "documents" or expected_stage is None or case.get("next_stage") != expected_stage:
        raise HTTPException(status_code=409, detail="This evidence upload is not available at the current insurance step")
    filename = Path(file.filename or f"{kind}.pdf").name
    suffixes = {".csv"} if kind == "fee_schedule" else {".pdf", ".jpg", ".jpeg", ".png"}
    if Path(filename).suffix.lower() not in suffixes:
        raise HTTPException(status_code=415, detail="Upload a rate CSV" if kind == "fee_schedule" else "Upload a PDF, JPG or PNG")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Insurance evidence files must be 15 MB or smaller")
    object_key: str | None = None
    try:
        if kind == "fee_schedule":
            source = await run_in_threadpool(documented.inspect_csv, content, filename)
        else:
            source = await run_in_threadpool(
                documented.inspect_pdf, content, filename, kind,
                case.get("ocr", {}).get("member_id", "") if kind == "coverage" else "",
            )
        if object_store.enabled():
            object_key = await run_in_threadpool(
                object_store.put_insurance_evidence, case_id, filename, content, file.content_type or "application/octet-stream",
            )
            source["object_key"] = object_key
        documented.add_document(case, kind, source)
        saved = await run_in_threadpool(
            store.save_case, case, session["patient_id"], session["share_manifest_id"], f"{kind}-uploaded",
        )
        if saved is None:
            raise golden.GoldenCaseError("The uploaded evidence could not be saved; upload it again")
        return _response(saved)
    except golden.GoldenCaseError as exc:
        if object_key:
            await run_in_threadpool(object_store.delete_object, object_key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except golden.OCRServiceError as exc:
        raise HTTPException(status_code=503, detail="Document OCR is temporarily unavailable; try again.") from exc


@router.put("/cases/{case_id}/clinical-context")
async def request_cpt_suggestions(
    case_id: str, review: InsuranceClinicalContextRequest, request: Request,
) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        await run_in_threadpool(documented.set_clinical_context, case, review.clinical_context, review.visit_type)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "clinical-context")
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/cases/{case_id}/service-lines")
async def confirm_service(case_id: str, selection: InsuranceServiceLinesRequest, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        if not case.get("documents", {}).get("fee_schedule"):
            raise golden.GoldenCaseError("Upload the fee schedule before confirming anticipated services")
        documented.select_services(case, selection.codes)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "service")
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage}")
async def run_stage(case_id: str, stage: str, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        documented.advance(case, stage)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], stage)
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
