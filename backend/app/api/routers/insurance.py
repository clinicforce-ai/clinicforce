"""Local-only clinician insurance-review endpoints."""

from hashlib import sha256
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.schemas import InsuranceDemoStageRequest, InsuranceEstimateDemoRequest, InsuranceV2DocumentReviewRequest, InsuranceV2StageRequest
from app.api.settings import MAX_INSURANCE_CARD_BYTES, MAX_INSURANCE_EVIDENCE_BYTES, insurance_demo_enabled
from app.insurance import demo as insurance_demo
from app.insurance import local_v2
from app.ingestion.pdf_parser import extract_text_from_pdf_bytes

router = APIRouter(prefix="/api/clinician/insurance", tags=["clinician-insurance"])
_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _require_local_insurance_mode() -> None:
    if not insurance_demo_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance verification is not enabled")


async def _local_v2_document(kind: str, upload: UploadFile | None) -> dict | None:
    if upload is None:
        return None
    filename = Path(upload.filename or "insurance-document.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Local V2 accepts PDF printouts only")
    content = await upload.read(MAX_INSURANCE_EVIDENCE_BYTES + 1)
    if len(content) > MAX_INSURANCE_EVIDENCE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Insurance evidence documents must be 25 MB or smaller")
    try:
        text = await extract_text_from_pdf_bytes(content)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The uploaded file is not a readable PDF printout") from exc
    try:
        document = await run_in_threadpool(local_v2.extract_document, kind, filename, text)
        document["content_sha256"] = sha256(content).hexdigest()
        return document
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/local-v2/cases")
async def create_local_v2_case(
    patient_label: str = Form(""), insurance_card: UploadFile | None = File(None),
) -> JSONResponse:
    """Start a local case with only its first required document."""
    _require_local_insurance_mode()
    document = await _local_v2_document("insurance_card", insurance_card)
    try:
        case = await run_in_threadpool(local_v2.create_case, patient_label, [document] if document else [])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return JSONResponse(case, headers=_NO_STORE_HEADERS)


@router.get("/local-v2/cases/{case_id}")
async def get_local_v2_case(case_id: str) -> JSONResponse:
    _require_local_insurance_mode()
    try:
        return JSONResponse(await run_in_threadpool(local_v2.get_case, case_id), headers=_NO_STORE_HEADERS)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/local-v2/cases/{case_id}/documents")
async def add_local_v2_document(case_id: str, kind: str = Form(...), file: UploadFile = File(...)) -> JSONResponse:
    _require_local_insurance_mode()
    if kind not in local_v2.DOCUMENT_KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported insurance document type")
    document = await _local_v2_document(kind, file)
    try:
        return JSONResponse(await run_in_threadpool(local_v2.add_document, case_id, document), headers=_NO_STORE_HEADERS)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/local-v2/cases/{case_id}/documents/{document_id}")
async def review_local_v2_document(case_id: str, document_id: str, request: InsuranceV2DocumentReviewRequest) -> JSONResponse:
    _require_local_insurance_mode()
    try:
        return JSONResponse(await run_in_threadpool(local_v2.review_document, case_id, document_id, request.fields), headers=_NO_STORE_HEADERS)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/local-v2/cases/{case_id}/stages/{stage}")
async def run_local_v2_stage(case_id: str, stage: str, request: InsuranceV2StageRequest) -> JSONResponse:
    _require_local_insurance_mode()
    try:
        result = await run_in_threadpool(local_v2.run_stage, case_id, stage, request.service)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return JSONResponse(result, headers=_NO_STORE_HEADERS)


@router.post("/demo-card")
async def verify_demo_insurance_card(
    file: UploadFile = File(...),
) -> JSONResponse:
    """Exercise the UI without retaining a card or contacting a payer."""
    if not insurance_demo_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance verification is not enabled")
    filename = Path(file.filename or "insurance-card").name
    if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".pdf"}:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a JPG, PNG, or PDF insurance card")
    content = await file.read(MAX_INSURANCE_CARD_BYTES + 1)
    if len(content) > MAX_INSURANCE_CARD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Insurance card files must be 10 MB or smaller")
    try:
        insurance_demo.validate_local_card_upload(content)
        payload = insurance_demo.verification()
    except insurance_demo.InsuranceCardContentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except insurance_demo.LocalCaseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    payload["card_scan"] = {"filename": filename, "status": "document_evidence_applied", "retention": "Not stored. No OCR or payer request was made."}
    return JSONResponse(payload, headers=_NO_STORE_HEADERS)


@router.post("/demo-estimate")
async def estimate_demo_service(request: InsuranceEstimateDemoRequest) -> JSONResponse:
    """Return the document-backed, review-gated estimate state."""
    if not insurance_demo_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance verification is not enabled")
    try:
        payload = insurance_demo.service_estimate(
            reason=request.reason, anticipated_service=request.anticipated_service,
            referral_status=request.referral_status, authorization_status=request.authorization_status,
        )
    except insurance_demo.LocalCaseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JSONResponse(payload, headers=_NO_STORE_HEADERS)


@router.post("/demo-stage")
async def run_demo_insurance_stage(request: InsuranceDemoStageRequest) -> JSONResponse:
    """Run one reviewed local flowchart stage without contacting an external system."""
    if not insurance_demo_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance verification is not enabled")
    try:
        payload = insurance_demo.workbench_stage(
            request.stage, reason=request.reason, anticipated_service=request.anticipated_service,
            referral_status=request.referral_status, authorization_status=request.authorization_status,
        )
    except insurance_demo.LocalCaseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return JSONResponse(payload, headers=_NO_STORE_HEADERS)
