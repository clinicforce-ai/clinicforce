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


async def _session(request: Request, require_beta: bool = True) -> dict:
    _enabled()
    intake_id = request.headers.get("X-ClinicForce-Intake", "")
    if intake_id:
        from app.api.routers.staff import require_staff
        from app.core import staff_store
        staff = await require_staff(request)
        intake = await run_in_threadpool(staff_store.get_intake, intake_id, staff["practice_id"])
        if not intake:
            raise HTTPException(404, "Insurance intake not found")
        patient = intake.get("patient")
        if not patient:
            raise HTTPException(409, "Confirm the patient before opening insurance review")
        return {"practice_id": staff["practice_id"], "intake_id": intake_id,
                "patient_id": patient["id"], "share_manifest_id": "", "staff": staff, "intake": intake}
    try:
        return golden.open_share_token(request.cookies.get(CLINICIAN_SHARE_COOKIE, ""), require_beta)
    except golden.GoldenCaseError as exc:
        code = status.HTTP_403_FORBIDDEN if require_beta else status.HTTP_401_UNAUTHORIZED
        raise HTTPException(status_code=code, detail=str(exc)) from exc


def _scope(session: dict) -> dict:
    return {"practice_id": session["practice_id"], "intake_id": session["intake_id"],
            "staff_actor": {key: session["staff"][key] for key in ("id", "display_name")}} if session.get("practice_id") else {}


def _response(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers=_NO_STORE)


def _load_case(case_id: str, session: dict) -> dict:
    case = store.get_case(case_id, session["patient_id"], session["share_manifest_id"], **_scope(session))
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
    session = await _session(request, require_beta=False)
    if session.get("practice_id"):
        return {"beta_enabled": True, "expires_in_seconds": CLINICIAN_SHARE_MAX_AGE}
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
    session = await _session(request)
    if session.get("practice_id"):
        raise HTTPException(409, "Add card files through the saved staff intake")
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
            "id": "card",
            "filename": filename, "sha256": hashlib.sha256(content).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(), "reference": "Uploaded insurance card",
            "document_dates": v1_sources.document_dates(pages),
        }
        workflow["evidence_manifest_id"] = golden.evidence_binding()
        case = await run_in_threadpool(
            store.create_case, session["patient_id"], session["share_manifest_id"], golden.evidence_binding(), workflow, **_scope(session),
        )
        return _response(case)
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        log_error("Insurance card OCR", exc)
        raise HTTPException(status_code=503, detail="Insurance card OCR is temporarily unavailable") from exc


@router.post("/staff-case")
async def open_staff_case(request: Request) -> JSONResponse:
    """Resume a document review from the already saved, confirmed card intake."""
    session = await _session(request)
    if not session.get("practice_id"):
        raise HTTPException(403, "Open an intake from the staff workspace")
    intake = session["intake"]
    case_id = intake.get("workflows", {}).get("v1")
    if case_id:
        return _response(await run_in_threadpool(_load_case, case_id, session))
    reviewed = intake["fields"]
    fields = {**intake.get("ocr_fields", {}),
              "member_name": reviewed.get("subscriber_name") or reviewed.get("member_name", ""),
              "member_id": reviewed.get("member_id", ""), "group_number": reviewed.get("group_number", "")}
    if reviewed.get("relationship") in {"self", "18"} and not fields["member_name"]:
        fields["member_name"] = " ".join((intake["patient"]["given_name"], intake["patient"]["family_name"]))
    workflow = documented.pending_case(fields)
    card = next(iter(intake.get("documents", [])), {})
    workflow.update(extraction_version=v1_sources.EXTRACTION_VERSION,
                    ocr_evidence=intake.get("ocr_evidence", {}),
                    staff_confirmed_fields=reviewed,
                    evidence_manifest_id=golden.evidence_binding(),
                    card_source={"id": "card", "filename": card.get("filename", "Staff-confirmed insurance details"),
                                 "sha256": card.get("sha256", ""), "observed_at": card.get("created_at", intake["created_at"]),
                                 "reference": "Staff-confirmed intake; original card retained in patient documents"})
    documented.confirm_intake(workflow, fields)
    workflow["facts"][0]["value"] = "Patient and insurance details confirmed by clinic staff"
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", golden.evidence_binding(), workflow,
                                      expected_intake_updated_at=intake["updated_at"], **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _response(case)


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> JSONResponse:
    return _response(await run_in_threadpool(_load_case, case_id, await _session(request)))


@router.put("/cases/{case_id}/intake")
async def confirm_intake(case_id: str, review: InsuranceIntakeRequest, request: Request) -> JSONResponse:
    session = await _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        if case.get("evidence_manifest_id") != golden.evidence_binding():
            raise golden.GoldenCaseError("The insurance review configuration changed; start a new case")
        fields = case.get("ocr", {})
        if case.get("next_stage") != "intake" or any(value != fields.get(key, "") for key, value in review.model_dump().items()):
            raise golden.GoldenCaseError("Confirm the fields extracted from the uploaded card without changing them.")
        documented.confirm_intake(case, fields)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "intake", **_scope(session))
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/documents/{kind}")
async def upload_evidence(case_id: str, kind: str, request: Request, file: UploadFile = File(...)) -> JSONResponse:
    session = await _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    if session.get("practice_id") and not object_store.enabled():
        raise HTTPException(503, "Document storage is unavailable. The evidence has not been saved; try again later.")
    expected_stage = documented.DOCUMENT_STAGES.get(kind)
    if case.get("source_mode") != "documents" or expected_stage is None or case.get("next_stage") != expected_stage:
        raise HTTPException(status_code=409, detail="This evidence upload is not available at the current insurance step")
    filename = Path(file.filename or f"{kind}.pdf").name
    suffixes = {".csv"} if kind == "fee_schedule" else {".csv", ".pdf", ".jpg", ".jpeg", ".png"} if kind == "self_pay" else {".pdf", ".jpg", ".jpeg", ".png"}
    if Path(filename).suffix.lower() not in suffixes:
        raise HTTPException(status_code=415, detail="Upload a rate CSV" if kind == "fee_schedule" else "Upload a PDF, JPG or PNG")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Insurance evidence files must be 15 MB or smaller")
    object_key: str | None = None
    try:
        if kind == "fee_schedule" or (kind == "self_pay" and Path(filename).suffix.lower() == ".csv"):
            source = await run_in_threadpool(documented.inspect_csv, content, filename)
            source["id"] = kind
        else:
            source = await run_in_threadpool(
                documented.inspect_pdf, content, filename, kind,
                case.get("ocr", {}).get("member_id", "") if kind == "coverage" else "",
            )
        if object_store.enabled():
            object_key = await run_in_threadpool(
                object_store.put_staff_document if session.get("practice_id") else object_store.put_insurance_evidence,
                *([session["practice_id"], case_id] if session.get("practice_id") else [case_id]),
                filename, content, file.content_type or "application/octet-stream",
            )
            source["object_key"] = object_key
        documented.add_document(case, kind, source)
        saved = await run_in_threadpool(
            store.save_case, case, session["patient_id"], session["share_manifest_id"], f"{kind}-uploaded", **_scope(session),
        )
        if saved is None:
            raise golden.GoldenCaseError("The uploaded evidence could not be saved; upload it again")
        return _response(saved)
    except store.StaffCaseConflict as exc:
        if object_key:
            await run_in_threadpool(object_store.delete_object, object_key)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    session = await _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        await run_in_threadpool(documented.set_clinical_context, case, review.clinical_context, review.visit_type)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "clinical-context", **_scope(session))
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/cases/{case_id}/service-lines")
async def confirm_service(case_id: str, selection: InsuranceServiceLinesRequest, request: Request) -> JSONResponse:
    session = await _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        if not case.get("documents", {}).get("fee_schedule"):
            raise golden.GoldenCaseError("Upload the fee schedule before confirming anticipated services")
        documented.select_services(case, selection.codes, selection.quantities)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "service", **_scope(session))
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage}")
async def run_stage(case_id: str, stage: str, request: Request) -> JSONResponse:
    session = await _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        documented.advance(case, stage)
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], stage, **_scope(session))
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
