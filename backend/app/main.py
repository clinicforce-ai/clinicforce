"""ClinicForce integration endpoints for FHIR ingestion and SMART Health Links."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from html import escape
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

import fitz
from fastapi import File, Form, UploadFile, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.shl import (
    create_shl_uri,
    encrypt_bundle,
    generate_qr_code,
    generate_symmetric_key,
    hash_password,
    hash_pin,
    verify_password,
    verify_pin,
)
from app.logs.logger import log_error, log_info, log_warning
from app.reports.pdf_builder import generate_doctor_pdf
from app.reports.summarizer import summarize_fhir_bundle
from jwcrypto import jwe, jwk
from app.core.object_store import delete_object, enabled as s3_enabled, get_pdf, put_patient_pdf
from app.core.patient_store import add_document, add_record, create_manifest, create_patient, get_document, get_document_by_id, get_manifest, get_password_hash, list_documents, list_records, update_document_extraction
from app.core.source_links import document_download_url, verify_document_download_token
from app.ingestion.bedrock_extractor import EXTRACTION_VERSION, extract_pdf_to_fhir_bundle
from app.ingestion.ccda_parser import parse_ccda_to_fhir_bundle
from app.ingestion.fhir_client import FHIRRequestError, get_patient_records, post_to_fhir
from app.ingestion.pdf_parser import (
    extract_text_from_pdf_bytes,
    parse_pdf_text_with_llm,
    pdf_extraction_to_fhir_bundle,
)
from app.ingestion.wearables import (
    generate_mock_wearable_payload,
    parse_apple_health_heartrate,
    parse_fitbit_steps,
)


PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", PUBLIC_BASE_URL).split(",") if origin.strip()]
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

app = FastAPI(title="ClinicForce")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness endpoint used by the load balancer; it exposes no PHI."""
    return {"status": "ok"}


class IntegrationFailure(Exception):
    """Records which integration component caused a recoverable route failure."""

    def __init__(self, component: str, cause: Exception) -> None:
        self.component = component
        self.cause = cause
        super().__init__(f"{component}: {cause}")


class RequestModel(BaseModel):
    """Strict JSON contract shared by every browser-facing endpoint."""

    model_config = ConfigDict(extra="forbid")


class ManifestRequest(RequestModel):
    passcode: str = Field(min_length=1, max_length=128)

    @field_validator("passcode")
    @classmethod
    def normalize_passcode(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Passcode is required")
        return value


class IntegrationTestRequest(RequestModel):
    patient_id: str = Field(min_length=1, max_length=128)
    passcode: str = Field(min_length=1, max_length=128)


class DoctorSummaryRequest(RequestModel):
    jwe_payload: str = Field(min_length=1, max_length=2_000_000)
    key: str = Field(min_length=1, max_length=256)


class PatientCredentials(RequestModel):
    patient_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("patient_id")
    @classmethod
    def normalize_patient_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Patient ID is required")
        return value


class GeneratePatientLinkRequest(PatientCredentials):
    passcode: str = Field(min_length=4, max_length=128)

    @field_validator("passcode")
    @classmethod
    def normalize_link_passcode(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 4:
            raise ValueError("Choose a sharing passcode of at least four characters")
        return value


class ManualRecordRequest(PatientCredentials):
    category: Literal["hospital", "lab", "vital", "condition", "allergy", "medication"]
    hospital: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    value: str | None = Field(default=None, max_length=256)
    unit: str | None = Field(default=None, max_length=64)
    date: str | None = Field(default=None, max_length=32)
    details: str | None = Field(default=None, max_length=4_000)
    dosage: str | None = Field(default=None, max_length=512)


def _create_upload_shl(patient_id: str | None, fhir_bundle: dict[str, Any], passcode: str) -> tuple[str, str, str]:
    """Create and retain the encrypted SHL payload outside the event loop."""
    key = generate_symmetric_key()
    key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    encrypted_jwe = encrypt_bundle(fhir_bundle, key_bytes)
    manifest_id = create_manifest(patient_id, encrypted_jwe, hash_pin(passcode))
    shl_uri = create_shl_uri(
        f"{PUBLIC_BASE_URL}/manifest/{manifest_id}", key, "ClinicForce records"
    )
    return shl_uri, generate_qr_code(shl_uri), str(manifest_id)


def _mask_passcode(passcode: str) -> str:
    return "*" * len(passcode)


def _validate_patient_credentials(patient_id: str, password: str) -> tuple[str, str]:
    normalized_id = patient_id.strip()
    if not normalized_id or not password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Patient ID and password are required")
    return normalized_id, password


async def _authenticate_patient(patient_id: str, password: str) -> str:
    normalized_id, password = _validate_patient_credentials(patient_id, password)
    stored_hash = await asyncio.to_thread(get_password_hash, normalized_id)
    if stored_hash is None or not verify_password(password, stored_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid patient ID or password")
    return normalized_id


def _document_payload(document: Any) -> dict[str, Any]:
    return {
        "id": document.id,
        "filename": document.filename,
        "uploaded_at": document.uploaded_at,
        "resource_count": len(document.fhir_bundle.get("entry", [])),
        "extraction_version": getattr(document, "extraction_version", None),
    }


def _combine_document_bundles(documents: list[Any], records: list[Any] | None = None) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for document in documents:
        # A few early records predate persisted citation document IDs.  Add the ID
        # as the share bundle is assembled, so every original PDF cited in a newly
        # generated SHL can receive its own download capability.
        bundle = deepcopy(document.fhir_bundle)
        for entry in bundle.get("entry", []):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                continue
            for extension in resource.get("extension", []):
                if not isinstance(extension, dict) or extension.get("url") != "https://clinicforce.example/fhir/StructureDefinition/source-citation":
                    continue
                children = extension.setdefault("extension", [])
                if isinstance(children, list) and not any(
                    isinstance(child, dict) and child.get("url") == "document_id" for child in children
                ):
                    children.append({"url": "document_id", "valuePositiveInt": document.id})
        entries.extend(entry for entry in bundle.get("entry", []) if isinstance(entry, dict))
    for record in records or []:
        entries.extend(entry for entry in record.fhir_bundle.get("entry", []) if isinstance(entry, dict))
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def _with_source_download_links(summary: dict[str, Any]) -> dict[str, Any]:
    """Attach a signed source URL only to facts that have an original PDF citation."""
    rendered = deepcopy(summary)
    for section in ("vitals", "labs", "medications", "conditions", "allergies", "care_events"):
        for item in rendered.get(section, []):
            citation = item.get("citation") if isinstance(item, dict) else None
            document_id = citation.get("document_id") if isinstance(citation, dict) else None
            if isinstance(document_id, int) and document_id > 0:
                citation["download_url"] = document_download_url(PUBLIC_BASE_URL, document_id)
    return rendered


def _download_headers(filename: str) -> dict[str, str]:
    """Prevent filename header injection and make all source links download PDFs."""
    safe_filename = "".join(char if char.isalnum() or char in "._-" else "_" for char in Path(filename).name) or "source.pdf"
    if not safe_filename.lower().endswith(".pdf"):
        safe_filename += ".pdf"
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_filename)}",
        "Cache-Control": "no-store, private",
        "X-Content-Type-Options": "nosniff",
    }


def _record_payload(record: Any) -> dict[str, Any]:
    return {"id": record.id, "category": record.category, "label": record.label, "created_at": record.created_at}


def _workspace_payload(patient_id: str, documents: list[Any], records: list[Any]) -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "documents": [_document_payload(document) for document in documents],
        "records": [_record_payload(record) for record in records],
    }


async def _ensure_documents_extracted(patient_id: str, documents: list[Any]) -> None:
    """Upgrade legacy document facts from their encrypted S3 source before sharing.

    This is cached per document by extraction version.  It intentionally reads only
    RDS-mapped keys for the authenticated patient, never lists the S3 bucket.
    """
    for document in documents:
        if getattr(document, "extraction_version", None) == EXTRACTION_VERSION:
            continue
        pdf_bytes = await asyncio.to_thread(get_pdf, document.s3_key)
        bundle = await extract_pdf_to_fhir_bundle(pdf_bytes, patient_id, document.filename)
        if not bundle.get("entry"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No supported clinical facts could be verified in {document.filename}",
            )
        updated = await asyncio.to_thread(
            update_document_extraction,
            patient_id,
            document.id,
            bundle,
            EXTRACTION_VERSION,
        )
        if not updated:
            raise RuntimeError(f"Document {document.id} was no longer available for extraction")


def _manual_record_bundle(request: ManualRecordRequest, patient_id: str) -> tuple[str, dict[str, Any]]:
    """Create only standard FHIR R4 resources from patient-entered form values."""
    name = (request.name or "").strip()
    hospital = (request.hospital or "").strip()
    details = (request.details or "").strip()
    date = (request.date or "").strip()
    patient = {"reference": f"Patient/{patient_id}"}
    resource: dict[str, Any]
    label: str
    if request.category == "hospital":
        if not hospital:
            raise HTTPException(status_code=422, detail="Enter the hospital or clinic name")
        resource = {
            "resourceType": "Encounter", "status": "finished", "subject": patient,
            "serviceProvider": {"display": hospital},
            **({"period": {"start": date}} if date else {}),
            **({"reasonCode": [{"text": details}]} if details else {}),
        }
        label = hospital
    elif request.category in {"lab", "vital"}:
        if not name or not (request.value or "").strip():
            raise HTTPException(status_code=422, detail="Enter a test or measurement name and its result")
        raw_value = request.value.strip()
        value: dict[str, Any]
        try:
            numeric_value = float(raw_value)
            if not numeric_value == numeric_value or numeric_value in (float("inf"), float("-inf")):
                raise ValueError
            value = {"valueQuantity": {"value": numeric_value, **({"unit": request.unit.strip()} if (request.unit or "").strip() else {})}}
        except ValueError:
            value = {"valueString": raw_value}
        resource = {
            "resourceType": "Observation", "status": "final", "subject": patient,
            "category": [{"text": "Vital Signs" if request.category == "vital" else "Laboratory"}],
            "code": {"text": name}, **value,
            **({"effectiveDateTime": date} if date else {}),
            **({"note": [{"text": details}]} if details else {}),
        }
        label = name
    elif request.category == "condition":
        if not name:
            raise HTTPException(status_code=422, detail="Enter the condition name")
        resource = {"resourceType": "Condition", "subject": patient, "code": {"text": name}, **({"note": [{"text": details}]} if details else {})}
        label = name
    elif request.category == "allergy":
        if not name:
            raise HTTPException(status_code=422, detail="Enter the allergy or intolerance")
        resource = {"resourceType": "AllergyIntolerance", "patient": patient, "code": {"text": name}, **({"note": [{"text": details}]} if details else {})}
        label = name
    else:
        if not name:
            raise HTTPException(status_code=422, detail="Enter the medication name")
        resource = {"resourceType": "MedicationStatement", "status": "active", "subject": patient, "medicationCodeableConcept": {"text": name}}
        if (request.dosage or "").strip():
            resource["dosage"] = [{"text": request.dosage.strip()}]
        if details:
            resource["note"] = [{"text": details}]
        label = name
    return label, {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": resource}]}


@app.exception_handler(IntegrationFailure)
async def handle_integration_failure(_: Request, exc: IntegrationFailure) -> JSONResponse:
    """Return a consistent integration error without exposing uploaded content."""
    log_error(exc.component, exc.cause)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc)},
    )


def _decrypt_jwe_payload(jwe_payload: str, key: str) -> dict[str, Any]:
    """Decrypt a compact A256GCM JWE with its SHL base64url symmetric key."""
    key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    if len(key_bytes) != 32:
        raise ValueError("A256GCM requires a 32-byte symmetric key")
    token = jwe.JWE()
    token.deserialize(jwe_payload)
    token.decrypt(jwk.JWK(kty="oct", k=key))
    bundle = json.loads(token.payload.decode("utf-8"))
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("Decrypted JWE payload is not a FHIR Bundle")
    return bundle


def _make_demo_pdf(text: str) -> bytes:
    """Create a tiny local fixture PDF; invoked in a worker to protect the event loop."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    result = document.tobytes()
    document.close()
    return result


def _demo_ccda(patient_id: str) -> str:
    safe_id = escape(patient_id, quote=True)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <recordTarget><patientRole><id extension="{safe_id}"/><patient><name><given>ClinicForce</given><family>Demo</family></name><administrativeGenderCode code="UN"/></patient></patientRole></recordTarget>
  <component><structuredBody><component><section><title>Problems</title><entry><observation><code code="38341003" codeSystem="2.16.840.1.113883.6.96" displayName="Hypertension"/></observation></entry></section></component>
  <component><section><title>Allergies</title><entry><observation><code code="91936005" codeSystem="2.16.840.1.113883.6.96" displayName="Allergy to penicillin"/></observation></entry></section></component></structuredBody></component>
</ClinicalDocument>'''


def _resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle.get("entry", []) if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)]


def _merge_pdf_extractions(extractions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Combine per-file extraction results without duplicating repeated clinical facts."""
    merged: dict[str, list[dict[str, Any]]] = {"conditions": [], "allergies": [], "medications": []}
    seen: dict[str, set[str]] = {key: set() for key in merged}
    for extraction in extractions:
        for key, items in merged.items():
            values = extraction.get(key, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if marker not in seen[key]:
                    seen[key].add(marker)
                    items.append(item)
    return merged


def _replace_patient_reference(value: Any, original_id: str, assigned_id: str) -> None:
    """Update references after a FHIR server assigns an ID to POSTed Patient."""
    if isinstance(value, dict):
        if value.get("reference") == f"Patient/{original_id}":
            value["reference"] = f"Patient/{assigned_id}"
        for child in value.values():
            _replace_patient_reference(child, original_id, assigned_id)
    elif isinstance(value, list):
        for child in value:
            _replace_patient_reference(child, original_id, assigned_id)


async def _build_ingestion_resources(patient_id: str) -> list[dict[str, Any]]:
    """Run each ingestion adapter against local deterministic integration fixtures."""
    log_info("Ingestion", "Generating mock Apple Health and Fitbit payloads")
    wearable = generate_mock_wearable_payload()
    heart_rate, steps = await asyncio.gather(
        parse_apple_health_heartrate(wearable["apple_health"], patient_id),
        parse_fitbit_steps(wearable["fitbit"], patient_id),
    )

    log_info("Ingestion", "Creating fixture PDF and extracting text with PyMuPDF")
    pdf_bytes = await asyncio.to_thread(
        _make_demo_pdf, "Patient reports no additional symptoms in this demo form."
    )
    pdf_text = await extract_text_from_pdf_bytes(pdf_bytes)
    log_info("Ingestion", "Parsing extracted PDF text locally")
    pdf_bundle = pdf_extraction_to_fhir_bundle(
        await parse_pdf_text_with_llm(pdf_text, "demo-intake.pdf"), patient_id
    )

    log_info("Ingestion", "Parsing local C-CDA XML fixture")
    ccda_bundle = await parse_ccda_to_fhir_bundle(_demo_ccda(patient_id))
    resources = _resources(ccda_bundle) + [heart_rate, steps] + _resources(pdf_bundle)
    # Create the Patient before resources that reference it.
    resources.sort(key=lambda resource: resource.get("resourceType") != "Patient")
    counts: dict[str, int] = {}
    for resource in resources:
        resource_type = resource.get("resourceType", "Unknown")
        counts[resource_type] = counts.get(resource_type, 0) + 1
    log_info("Ingestion", "FHIR R4 resources produced: %s", counts)
    return resources


@app.post("/api/patient/register")
async def register_patient(request: PatientCredentials) -> dict[str, Any]:
    """Create a local patient account for the persistent document workspace."""
    patient_id, password = _validate_patient_credentials(request.patient_id, request.password)
    if len(password) < 12:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must contain at least 12 characters")
    created = await asyncio.to_thread(create_patient, patient_id, hash_password(password))
    if not created:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A patient account with this ID already exists")
    return {"patient_id": patient_id, "documents": [], "records": []}


@app.post("/api/patient/login")
async def login_patient(request: PatientCredentials) -> dict[str, Any]:
    patient_id = await _authenticate_patient(request.patient_id, request.password)
    documents, records = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id), asyncio.to_thread(list_records, patient_id)
    )
    return _workspace_payload(patient_id, documents, records)


@app.post("/api/patient/upload")
async def upload_patient_pdf(
    file: list[UploadFile] | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    patient_id: str = Form(...),
    password: str = Form(...),
) -> dict[str, Any]:
    """Ingest one or more PDFs, accepting both ``file`` and ``files`` form keys.

    Supporting both keys makes the API resilient to standard FormData helpers while
    preserving the original browser contract. Source bytes never enter PostgreSQL.
    """
    patient_id = await _authenticate_patient(patient_id, password)
    if not s3_enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Document storage is unavailable")
    uploaded_files = [*(file or []), *(files or [])]
    if not uploaded_files or any(
        not uploaded.filename or not uploaded.filename.lower().endswith(".pdf")
        for uploaded in uploaded_files
    ):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload one or more PDF files")

    try:
        for uploaded in uploaded_files:
            try:
                pdf_bytes = await uploaded.read()
            finally:
                await uploaded.close()
            if not pdf_bytes:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Uploaded PDF '{uploaded.filename}' is empty")
            if len(pdf_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{uploaded.filename}' exceeds the 15 MB upload limit")
            if not pdf_bytes.startswith(b"%PDF-"):
                raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"'{uploaded.filename}' is not a valid PDF file")
            log_info("Ingestion", "Received an in-memory PDF upload")
            s3_key = await asyncio.to_thread(put_patient_pdf, patient_id, uploaded.filename, pdf_bytes, "source")
            try:
                fhir_bundle = await extract_pdf_to_fhir_bundle(pdf_bytes, patient_id, uploaded.filename)
                if not fhir_bundle.get("entry"):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"No supported clinical facts could be verified in {uploaded.filename}",
                    )
                log_info("Ingestion", "Generated %d citation-backed FHIR resources", len(fhir_bundle.get("entry", [])))
                log_info("Database", "Posting uploaded FHIR resources to local HAPI as a transaction")
                await post_to_fhir(fhir_bundle)
                await asyncio.to_thread(add_document, patient_id, uploaded.filename, fhir_bundle, s3_key, EXTRACTION_VERSION)
            except Exception:
                await asyncio.to_thread(delete_object, s3_key)
                raise
    except HTTPException:
        raise
    except Exception as exc:
        component = "Database" if isinstance(exc, FHIRRequestError) else "Ingestion"
        raise IntegrationFailure(component, exc) from exc

    documents, records = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id), asyncio.to_thread(list_records, patient_id)
    )
    return _workspace_payload(patient_id, documents, records)


@app.post("/api/patient/records")
async def save_patient_record(request: ManualRecordRequest) -> dict[str, Any]:
    """Save a structured patient-entered record as FHIR R4 for dashboard and sharing."""
    patient_id = await _authenticate_patient(request.patient_id, request.password)
    label, fhir_bundle = _manual_record_bundle(request, patient_id)
    try:
        await post_to_fhir(fhir_bundle)
        await asyncio.to_thread(add_record, patient_id, request.category, label, fhir_bundle)
    except Exception as exc:
        raise IntegrationFailure("FHIR persistence", exc) from exc
    documents, records = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id), asyncio.to_thread(list_records, patient_id)
    )
    return _workspace_payload(patient_id, documents, records)


@app.post("/api/patient/generate-link")
async def generate_patient_link(request: GeneratePatientLinkRequest) -> dict[str, str]:
    patient_id = await _authenticate_patient(request.patient_id, request.password)
    if not request.passcode or len(request.passcode) < 4:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a sharing passcode of at least four characters")
    documents, records = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id), asyncio.to_thread(list_records, patient_id)
    )
    if not documents and not records:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Add a PDF or a health record before generating a secure link")
    try:
        await _ensure_documents_extracted(patient_id, documents)
        documents = await asyncio.to_thread(list_documents, patient_id)
        fhir_bundle = _combine_document_bundles(documents, records)
        if not fhir_bundle["entry"]:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No verified clinical facts are available to share")
        shl_uri, qr_code_base64, manifest_id = await asyncio.to_thread(_create_upload_shl, patient_id, fhir_bundle, request.passcode)
        log_info("Secure Sharing", "Stored encrypted manifest %s for %d documents and %d structured records", manifest_id, len(documents), len(records))
        return {"shl_uri": shl_uri, "qr_code_base64": qr_code_base64}
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationFailure("Secure Sharing", exc) from exc


@app.post("/api/patient/documents/{document_id}/download")
async def download_patient_document(document_id: int, request: PatientCredentials) -> Response:
    """Return one saved source PDF after re-authenticating the patient."""
    patient_id = await _authenticate_patient(request.patient_id, request.password)
    document = await asyncio.to_thread(get_document, patient_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        content = await asyncio.to_thread(get_pdf, document.s3_key)
    except Exception as exc:
        raise IntegrationFailure("Document storage", exc) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers=_download_headers(document.filename),
    )


@app.get("/api/doctor/documents/{document_id}/download")
async def download_doctor_document(document_id: int, token: str) -> Response:
    """Download exactly one cited original PDF after capability verification."""
    try:
        verify_document_download_token(token, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    document = await asyncio.to_thread(get_document_by_id, document_id)
    if document is None:
        # Do not distinguish a stale/deleted ID from an unauthorized one.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source report is no longer available")
    try:
        content = await asyncio.to_thread(get_pdf, document.s3_key)
    except Exception as exc:
        raise IntegrationFailure("Document storage", exc) from exc
    return Response(content=content, media_type="application/pdf", headers=_download_headers(document.filename))



@app.post("/api/test/integration")
async def test_integration(request: IntegrationTestRequest) -> dict[str, Any]:
    """Exercise local ingestion, HAPI persistence, and encrypted SHL creation."""
    try:
        try:
            resources = await _build_ingestion_resources(request.patient_id)
        except Exception as exc:
            raise IntegrationFailure("Ingestion", exc) from exc

        try:
            log_info("Database", "Posting %d FHIR resources to local HAPI", len(resources))
            fhir_responses = []
            patient = next((resource for resource in resources if resource.get("resourceType") == "Patient"), None)
            if patient is None:
                raise ValueError("Ingestion did not produce a Patient resource")
            patient_response = await post_to_fhir(patient)
            fhir_responses.append(patient_response)
            assigned_patient_id = patient_response.get("id")
            if not isinstance(assigned_patient_id, str) or not assigned_patient_id:
                raise ValueError(f"HAPI Patient create response did not include an id: {patient_response}")
            log_info("Database", "HAPI Patient response: %s", patient_response)
            for resource in (item for item in resources if item is not patient):
                _replace_patient_reference(resource, request.patient_id, assigned_patient_id)
                response = await post_to_fhir(resource)
                fhir_responses.append(response)
                log_info("Database", "HAPI %s response: %s", resource["resourceType"], response)
            fhir_bundle = await get_patient_records(assigned_patient_id)
            log_info("Database", "Retrieved patient bundle with %d entries", len(fhir_bundle.get("entry", [])))
        except Exception as exc:
            raise IntegrationFailure("Database", exc) from exc

        try:
            log_info("Secure Sharing", "Generating a 32-byte AES-256-GCM key and encrypting FHIR bundle")
            key = generate_symmetric_key()
            key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
            encrypted_jwe = encrypt_bundle(fhir_bundle, key_bytes)
            manifest_id = create_manifest(None, encrypted_jwe, hash_pin(request.passcode))
            shl_uri = create_shl_uri(
                f"{PUBLIC_BASE_URL}/manifest/{manifest_id}", key, "ClinicForce records"
            )
            qr_code_base64 = generate_qr_code(shl_uri)
            log_info("Secure Sharing", "Stored encrypted manifest %s and rendered PNG QR code", manifest_id)
        except Exception as exc:
            raise IntegrationFailure("Secure Sharing", exc) from exc

        return {
            "fhir_post_status": fhir_responses,
            "shl_uri": shl_uri,
            "qr_code_base64": qr_code_base64,
            "manifest_id": str(manifest_id),
        }
    except IntegrationFailure as exc:
        log_error(exc.component, exc.cause)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@app.post("/manifest/{manifest_id}")
async def retrieve_manifest(manifest_id: UUID, request: ManifestRequest) -> dict:
    """Return an encrypted SHL payload after successful passcode verification."""
    log_info("Secure Sharing", "Manifest lookup for %s with passcode=%s", manifest_id, _mask_passcode(request.passcode))
    manifest = await asyncio.to_thread(get_manifest, str(manifest_id))
    if manifest is None or not verify_pin(request.passcode, manifest["hashed_pin"]):
        log_warning("Secure Sharing", "Invalid manifest credentials for %s", manifest_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid manifest ID or passcode",
        )

    return {
        "files": [
            {
                "contentType": "application/fhir+json",
                "embedded": manifest["encrypted_jwe"],
            }
        ]
    }


@app.post("/api/doctor/decrypt-and-summarize")
async def decrypt_and_summarize(request: DoctorSummaryRequest) -> dict[str, Any]:
    """Decrypt an SHL payload, locally summarize it, and render a doctor PDF."""
    try:
        log_info("Clinical Summary", "Decrypting clinician-provided JWE payload")
        fhir_bundle = await asyncio.to_thread(_decrypt_jwe_payload, request.jwe_payload, request.key)
        log_info("Clinical Summary", "Generating deterministic clinical narrative")
        summary = _with_source_download_links(await summarize_fhir_bundle(fhir_bundle))
        log_info("Clinical Summary", "Rendering local WeasyPrint clinical PDF")
        report_path = f"/tmp/clinical_summary_{uuid4()}.pdf"
        await run_in_threadpool(generate_doctor_pdf, summary, report_path)
        try:
            report_bytes = await asyncio.to_thread(Path(report_path).read_bytes)
        finally:
            await asyncio.to_thread(Path(report_path).unlink, missing_ok=True)
        return {
            "summary": summary,
            # The caller already proved possession of the SHL decryption key.
            # Avoid a reusable unauthenticated report URL and shared task file.
            "pdf_url": "data:application/pdf;base64," + base64.b64encode(report_bytes).decode("ascii"),
        }
    except Exception as exc:
        failure = IntegrationFailure("Clinical Summary", exc)
        log_error(failure.component, failure.cause)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(failure)) from exc
