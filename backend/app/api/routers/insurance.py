"""Clinician-only golden insurance beta endpoints."""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.schemas import InsuranceEvidenceRequest, InsuranceIntakeRequest, InsuranceServiceLinesRequest
from app.api.settings import (
    CLINICIAN_SHARE_COOKIE, CLINICIAN_SHARE_MAX_AGE, MAX_INSURANCE_CARD_BYTES, MAX_UPLOAD_BYTES,
    PUBLIC_BASE_URL, insurance_beta_enabled,
)
from app.core.patient_store import get_patient_profile
from app.insurance import documented, golden, store


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
        conditions=session.get("conditions", []),
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
        raise HTTPException(status_code=415, detail="Upload a PDF, JPG, or PNG insurance card")
    content = await file.read(MAX_INSURANCE_CARD_BYTES + 1)
    if len(content) > MAX_INSURANCE_CARD_BYTES:
        raise HTTPException(status_code=413, detail="Insurance card files must be 10 MB or smaller")
    try:
        manifest = await run_in_threadpool(golden.load_manifest)
        profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
        fields = await run_in_threadpool(golden.read_card, content)
        if golden.patient_matches(profile, manifest):
            workflow = golden.pending_case(fields)
        else:
            if not documented.card_matches_patient(fields, profile):
                raise golden.GoldenCaseError("The member name on the card must match the patient opened from the QR")
            workflow = documented.pending_case(fields, session.get("conditions", []), manifest.get("rate_lines", []))
        workflow["evidence_manifest_id"] = manifest["manifest_id"]
        case = await run_in_threadpool(
            store.create_case, session["patient_id"], session["share_manifest_id"], manifest["manifest_id"], workflow,
        )
        return _response(case)
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Insurance card OCR is temporarily unavailable") from exc


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> JSONResponse:
    return _response(await run_in_threadpool(_load_case, case_id, _session(request)))


@router.put("/cases/{case_id}/intake")
async def confirm_intake(case_id: str, review: InsuranceIntakeRequest, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        manifest = await run_in_threadpool(golden.load_manifest)
        if case.get("evidence_manifest_id") != manifest["manifest_id"]:
            raise golden.GoldenCaseError("The reviewed evidence bundle changed; start a new case")
        if case.get("source_mode") == "golden":
            golden.unlock_case(case, manifest, review.model_dump())
        else:
            profile = await run_in_threadpool(get_patient_profile, session["patient_id"])
            documented.confirm_intake(case, profile, review.model_dump())
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "intake")
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/documents/{kind}")
async def upload_evidence(case_id: str, kind: str, request: Request, file: UploadFile = File(...)) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    if case.get("source_mode") != "documents" or case.get("next_stage") != "eligibility":
        raise HTTPException(status_code=409, detail="Evidence uploads are available during document-backed eligibility review")
    filename = Path(file.filename or f"{kind}.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Upload a PDF evidence document")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Evidence PDFs must be 15 MB or smaller")
    try:
        source = await run_in_threadpool(
            documented.inspect_pdf, content, filename, kind,
            case.get("ocr", {}).get("member_id", "") if kind == "eligibility" else "",
        )
        documented.add_document(case, kind, source)
        saved = await run_in_threadpool(
            store.save_case, case, session["patient_id"], session["share_manifest_id"], f"{kind}-uploaded",
        )
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/cases/{case_id}/evidence")
async def confirm_evidence(
    case_id: str, evidence: InsuranceEvidenceRequest, request: Request,
) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    if case.get("source_mode") != "documents" or case.get("next_stage") != "eligibility":
        raise HTTPException(status_code=409, detail="Document-backed eligibility is not ready for confirmation")
    try:
        documented.confirm_evidence(case, evidence.model_dump())
        saved = await run_in_threadpool(
            store.save_case, case, session["patient_id"], session["share_manifest_id"], "eligibility",
        )
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/cases/{case_id}/service-lines")
async def confirm_service(case_id: str, selection: InsuranceServiceLinesRequest, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        manifest = await run_in_threadpool(golden.load_manifest)
        if case.get("source_mode") == "golden":
            if len(selection.codes) != 1:
                raise golden.GoldenCaseError("Choose one anticipated office-visit code for the golden case")
            golden.select_service(case, selection.codes[0], manifest)
        else:
            documented.select_services(case, selection.codes, manifest["rates_source"])
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], "service")
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/cases/{case_id}/stages/{stage}")
async def run_stage(case_id: str, stage: str, request: Request) -> JSONResponse:
    session = _session(request)
    case = await run_in_threadpool(_load_case, case_id, session)
    try:
        manifest = await run_in_threadpool(golden.load_manifest)
        if case.get("source_mode") == "golden":
            golden.advance(case, stage, manifest)
        else:
            documented.advance(case, stage, manifest["rates_source"])
        saved = await run_in_threadpool(store.save_case, case, session["patient_id"], session["share_manifest_id"], stage)
        return _response(_saved(saved))
    except golden.GoldenCaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
