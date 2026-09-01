"""ClinicForce integration endpoints for FHIR ingestion and SMART Health Links."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from html import escape
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
import psycopg
from fastapi import File, Form, UploadFile, FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.shl import (
    create_shl_uri,
    encrypt_bundle,
    generate_qr_code,
    generate_symmetric_key,
    hash_password,
    hash_pin,
    verify_password,
)
from app.logs.logger import log_error, log_info, log_warning
from app.reports.pdf_builder import render_doctor_pdf_bytes
from app.reports.summarizer import summarize_fhir_bundle
from jwcrypto import jwe, jwk
from app.core.object_store import (
    delete_object as delete_s3_object,
    enabled as s3_enabled,
    get_pdf as get_s3_pdf,
    presign_shl_payload,
    put_patient_pdf,
    put_shl_payload,
)
from app.core import google_drive
from app.core.drive_crypto import decrypt_pdf as decrypt_drive_pdf, encrypt_pdf as encrypt_drive_pdf
from app.core.patient_store import (
    add_document, add_pending_drive_deletion, add_record, clear_pending_drive_deletion, consume_drive_oauth_state, consume_google_login_state,
    backfill_patient_profile_from_epic, check_database_connection, create_google_patient, create_manifest, create_patient, create_patient_session,
    delete_drive_file_crypto, get_document, get_document_by_id, get_drive_connection, get_drive_file_crypto, get_manifest, get_password_hash,
    get_patient_by_google_subject, get_patient_profile, get_patient_session, get_storage_preference, list_documents, list_records,
    list_pending_drive_deletions, remove_documents, revoke_patient_manifests, revoke_patient_session, save_drive_file_crypto,
    save_drive_oauth_state, save_google_login_state, set_drive_summary_file, set_storage_preference, update_document_extraction,
    update_document_locator,
    InvalidManifestPin, PatientProfile, PatientProfileSuggestions, get_patient_profile_suggestions,
    set_google_profile_suggestions, update_patient_profile, upsert_drive_connection, verify_manifest_pin,
    get_epic_endpoint, search_epic_endpoints,
)
from app.core.source_links import document_download_url, verify_document_download_token
from app.ingestion.bedrock_extractor import EXTRACTION_VERSION, extract_pdf_to_fhir_bundle
from app.ingestion.ccda_parser import parse_ccda_to_fhir_bundle
from app.ingestion.fhir_client import FHIRRequestError, get_patient_records, post_to_fhir
from app.ingestion import ecw, epic
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
from app.api.errors import IntegrationFailure
from app.api.presenters.summary import with_source_download_links as _with_source_download_links_service
from app.api.presenters.workspace import record_payload as _record_payload, workspace_payload as _workspace_payload
from app.api.presenters.oauth import popup_result as _popup_result
from app.api.presenters.profile import patient_profile_payload as _patient_profile_payload
from app.api.services.authentication import (
    authenticate_patient as _authenticate_patient_service,
    validate_credentials as _validate_patient_credentials_service,
)
from app.api.services.document_extraction import ensure_documents_extracted as _ensure_documents_extracted_service
from app.api.services.sharing import create_upload_shl as _create_upload_shl_service
from app.api.services.manual_records import manual_record_bundle as _manual_record_bundle
from app.api.services.integration_fixture import build_ingestion_resources as _build_ingestion_resources_service
from app.api.services.jwe_payload import decrypt_jwe_payload as _decrypt_jwe_payload
from app.api.services.document_reading import read_document_bytes as _read_document_bytes_service
from app.api.services.ehr_sessions import integration_error as _integration_error, live_session as _live_session
from app.api.routers import insurance_router, system_router
from app.domain.fhir.tags import append_meta_tag as _append_meta_tag, tag_resource_source as _tag_resource_source
from app.domain.fhir.bundles import (
    combine_document_bundles as _combine_document_bundles,
    document_payload as _document_payload,
)
from app.domain.fhir.epic_snapshot import epic_share_snapshot as _epic_share_snapshot
from app.domain.fhir.patient import (
    exchange_patient as _exchange_patient,
    first_patient_resource as _first_patient_resource,
    patient_reference as _exchange_patient_reference,
)
from app.domain.fhir.resources import prepare_exchange_resource as _prepare_exchange_resource
from app.domain.fhir.imports import (
    normalize_ecw_bundle as _normalize_ecw_bundle,
    normalize_external_shl_bundle as _normalize_external_shl_bundle,
)
from app.domain.fhir.shares import build_exchange_bundle
from app.domain.fhir.validation import has_matchable_patient_demographics as _has_matchable_patient_demographics
from app.domain.fhir.extractions import merge_pdf_extractions as _merge_pdf_extractions
from app.domain.fhir.references import replace_patient_reference as _replace_patient_reference
from app.domain.documents.filenames import (
    download_headers as _download_headers,
    zip_download_headers as _zip_download_headers,
    zip_entry_name as _zip_entry_name,
)
from app.api.schemas import (
    DoctorSummaryRequest, DocumentBatchRequest, EcwAuthorizeRequest, EpicAuthorizeRequest,
    ExternalShlImportRequest, GeneratePatientLinkRequest,
    IntegrationTestRequest, ManifestRequest, ManualRecordRequest, PatientCredentials,
    PatientProfileRequest, StoragePreferenceRequest,
)
from app.scripts.sync_epic_endpoints import sync_once as sync_epic_endpoint_catalog
from app.api.settings import (
    ALLOWED_ORIGINS, ECW_IMPORT_RESOURCE_TYPES as _ECW_IMPORT_RESOURCE_TYPES,
    ECW_LIVE_SESSION_COOKIE, ECW_OAUTH_STATE_COOKIE, EPIC_ENDPOINT_SYNC_ENABLED,
    EPIC_ENDPOINT_SYNC_INTERVAL_SECONDS, EPIC_LIVE_SESSION_COOKIE, EPIC_OAUTH_STATE_COOKIE,
    EXCHANGE_RESOURCE_TYPES as _EXCHANGE_RESOURCE_TYPES, FHIR_SOURCE_TAG_SYSTEM,
    FHIR_VERIFICATION_TAG_SYSTEM, MAX_BULK_DOWNLOAD_BYTES, MAX_BULK_DOWNLOAD_DOCUMENTS,
    CLINICIAN_SHARE_COOKIE, CLINICIAN_SHARE_MAX_AGE, MAX_UPLOAD_BYTES, PATIENT_SESSION_COOKIE,
    PATIENT_SESSION_MAX_AGE, PUBLIC_BASE_URL, SHL_SHARE_TTL_SECONDS,
    SOURCE_TAGS as _SOURCE_TAGS, insurance_beta_enabled,
)
from app.insurance import golden as insurance_golden


_epic_endpoint_sync_task: asyncio.Task[None] | None = None


app = FastAPI(title="ClinicForce")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(insurance_router)
app.include_router(system_router)


async def _epic_endpoint_sync_loop() -> None:
    """Refresh Epic's public directory outside patient request handling."""
    while True:
        try:
            await sync_epic_endpoint_catalog()
        except Exception as exc:
            # The catalog is non-patient metadata. Do not let a temporary Epic
            # directory outage block the health-record service or leak details.
            log_warning("Epic endpoint catalog", "Weekly sync failed: %s", type(exc).__name__)
        await asyncio.sleep(EPIC_ENDPOINT_SYNC_INTERVAL_SECONDS)


@app.on_event("startup")
async def start_epic_endpoint_sync() -> None:
    global _epic_endpoint_sync_task
    if EPIC_ENDPOINT_SYNC_ENABLED:
        _epic_endpoint_sync_task = asyncio.create_task(_epic_endpoint_sync_loop())


@app.on_event("shutdown")
async def stop_epic_endpoint_sync() -> None:
    global _epic_endpoint_sync_task
    if _epic_endpoint_sync_task is not None:
        _epic_endpoint_sync_task.cancel()
        _epic_endpoint_sync_task = None


def _create_upload_shl(
    patient_id: str | None,
    fhir_bundle: dict[str, Any],
    passcode: str | None,
    source: str = "local",
    require_passcode: bool = True,
) -> tuple[str, str, str]:
    return _create_upload_shl_service(
        patient_id, fhir_bundle, passcode, source, require_passcode,
        PUBLIC_BASE_URL, SHL_SHARE_TTL_SECONDS, generate_symmetric_key,
        encrypt_bundle, hash_pin, create_manifest, create_shl_uri, generate_qr_code,
    )


def _mask_passcode(passcode: str) -> str:
    return "*" * len(passcode)


def _validate_patient_credentials(patient_id: str | None, password: str | None) -> tuple[str, str]:
    return _validate_patient_credentials_service(patient_id, password)


async def _set_patient_session(response: Response, patient_id: str) -> None:
    session_token = await asyncio.to_thread(create_patient_session, patient_id)
    response.set_cookie(
        key=PATIENT_SESSION_COOKIE,
        value=session_token,
        max_age=PATIENT_SESSION_MAX_AGE,
        httponly=True,
        secure=PUBLIC_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _set_epic_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    """Keep opaque OAuth/session handles out of JavaScript and persistent storage."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=PUBLIC_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _clear_epic_cookies(response: Response) -> None:
    response.delete_cookie(EPIC_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(EPIC_LIVE_SESSION_COOKIE, path="/")


def _clear_ecw_cookies(response: Response) -> None:
    response.delete_cookie(ECW_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(ECW_LIVE_SESSION_COOKIE, path="/")


def _epic_popup_result(connected: bool) -> HTMLResponse:
    return _popup_result("epic", connected, PUBLIC_BASE_URL)


def _ecw_popup_result(connected: bool) -> HTMLResponse:
    return _popup_result("ecw", connected, PUBLIC_BASE_URL)


async def _live_epic_session(request: Request, patient_id: str) -> epic.LiveEpicSession:
    return await _live_session(request, patient_id, EPIC_LIVE_SESSION_COOKIE, epic.get_live_session, epic.EpicError)


async def _live_ecw_session(request: Request, patient_id: str) -> ecw.LiveEcwSession:
    return await _live_session(request, patient_id, ECW_LIVE_SESSION_COOKIE, ecw.get_live_session, ecw.EcwError)


def _epic_error(exc: epic.EpicError) -> HTTPException:
    return _integration_error(exc)


def _ecw_error(exc: ecw.EcwError) -> HTTPException:
    return _integration_error(exc, status.HTTP_401_UNAUTHORIZED)


async def _authenticate_patient(patient_id: str | None, password: str | None, request: Request | None = None) -> str:
    def log_rejection(path: str, credentials: str) -> None:
        cookie = "present" if request and request.cookies.get(PATIENT_SESSION_COOKIE) else "absent"
        log_warning("Patient authentication", "%s rejected: session cookie=%s, credentials=%s", path, cookie, credentials)

    return await _authenticate_patient_service(
        patient_id, password, request, PATIENT_SESSION_COOKIE, PUBLIC_BASE_URL,
        get_patient_session, get_password_hash, verify_password, log_rejection,
    )


@app.exception_handler(psycopg.OperationalError)
async def handle_database_unavailable(_: Request, exc: psycopg.OperationalError) -> JSONResponse:
    """Avoid exposing database failures or returning an unhelpful generic 500."""
    log_error("Database", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Patient data is temporarily unavailable. Please try again shortly."},
    )


async def _backfill_profile_from_live_epic(patient_id: str, bundle: dict[str, Any]) -> PatientProfile | None:
    """Persist only missing matching demographics from an already-authorized Epic response."""
    patient_resource = _first_patient_resource(bundle)
    if patient_resource is None:
        return await asyncio.to_thread(get_patient_profile, patient_id)
    return await asyncio.to_thread(backfill_patient_profile_from_epic, patient_id, patient_resource)


def _with_source_download_links(summary: dict[str, Any], manifest_id: str) -> dict[str, Any]:
    return _with_source_download_links_service(summary, manifest_id, PUBLIC_BASE_URL)


async def _load_workspace(patient_id: str) -> dict[str, Any]:
    documents, records, storage_provider, connection = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id),
        asyncio.to_thread(list_records, patient_id),
        asyncio.to_thread(get_storage_preference, patient_id),
        asyncio.to_thread(get_drive_connection, patient_id),
    )
    pending_import_count = 0
    sync_message: str | None = None
    access_upgrade_required = False
    if storage_provider == "google_drive" and connection is not None:
        drive_documents = [document for document in documents if getattr(document, "storage_provider", "s3") == "google_drive"]
        if drive_documents:
            existing_ids = await asyncio.to_thread(
                google_drive.existing_file_ids, connection, [document.s3_key for document in drive_documents]
            )
            missing_document_ids = [document.id for document in drive_documents if document.s3_key not in existing_ids]
            if missing_document_ids:
                removed = await asyncio.to_thread(remove_documents, patient_id, missing_document_ids)
                if removed:
                    for document in drive_documents:
                        if document.id in missing_document_ids:
                            await asyncio.to_thread(delete_drive_file_crypto, patient_id, document.s3_key)
                await asyncio.to_thread(revoke_patient_manifests, patient_id)
                log_warning("Google Drive", "Removed %d stale Drive document mappings after patient-side deletion", removed)
                documents = await asyncio.to_thread(list_documents, patient_id)
                sync_message = f"{removed} deleted Drive report{' was' if removed == 1 else 's were'} removed from ClinicForce. Existing QR codes were revoked."
        try:
            folder_files = await asyncio.to_thread(google_drive.list_folder_files, connection)
            known_ids = {document.s3_key for document in documents if getattr(document, "storage_provider", "s3") == "google_drive"}
            if connection.summary_file_id:
                known_ids.add(connection.summary_file_id)
            pending_delete_ids = set(await asyncio.to_thread(list_pending_drive_deletions, patient_id))
            for file_id in pending_delete_ids:
                try:
                    await asyncio.to_thread(google_drive.delete_file, connection, file_id)
                    await asyncio.to_thread(clear_pending_drive_deletion, patient_id, file_id)
                except Exception as exc:
                    log_warning("Google Drive", "Could not remove already-imported plaintext file: %s", type(exc).__name__)
            external_pdf_count = sum(
                1 for item in folder_files
                if item.file_id not in known_ids and item.file_id not in pending_delete_ids
                and not item.encrypted and (item.mime_type == "application/pdf" or item.filename.lower().endswith(".pdf"))
            )
            legacy_count = 0
            for document in documents:
                if getattr(document, "storage_provider", "s3") != "google_drive":
                    continue
                if await asyncio.to_thread(get_drive_file_crypto, patient_id, document.s3_key) is None:
                    legacy_count += 1
            if connection.summary_file_id and await asyncio.to_thread(
                get_drive_file_crypto, patient_id, connection.summary_file_id
            ) is None:
                legacy_count += 1
            pending_import_count = external_pdf_count + legacy_count
        except Exception as exc:
            # Existing Drive artifacts remain usable with the original narrow grant.
            # The new folder-import feature becomes available after the patient
            # consents to the upgraded Drive scope.
            access_upgrade_required = True
            log_warning("Google Drive", "Folder import reconciliation unavailable: %s", type(exc).__name__)
    return _workspace_payload(
        patient_id, documents, records, storage_provider, connection is not None,
        pending_import_count, sync_message, access_upgrade_required,
    )


async def _ensure_documents_extracted(patient_id: str, documents: list[Any]) -> None:
    await _ensure_documents_extracted_service(
        patient_id, documents, EXTRACTION_VERSION, _read_document_bytes,
        extract_pdf_to_fhir_bundle, update_document_extraction,
    )


async def _read_document_bytes(patient_id: str, document: Any) -> bytes:
    return await _read_document_bytes_service(
        patient_id, document, get_drive_connection, get_drive_file_crypto,
        decrypt_drive_pdf, get_s3_pdf, google_drive,
    )


async def _store_encrypted_drive_pdf(
    patient_id: str, connection: Any, pdf_bytes: bytes, purpose: Literal["source", "summary"],
) -> str:
    """Upload ciphertext to Drive and persist its KMS envelope before use."""
    encrypted = await asyncio.to_thread(encrypt_drive_pdf, patient_id, purpose, pdf_bytes)
    file_id = await asyncio.to_thread(google_drive.put_encrypted_blob, connection, encrypted.ciphertext, purpose)
    try:
        await asyncio.to_thread(
            save_drive_file_crypto, file_id, patient_id, encrypted.artifact_id, encrypted.purpose,
            encrypted.encrypted_data_key, encrypted.nonce, encrypted.plaintext_sha256, encrypted.algorithm,
        )
    except Exception:
        await asyncio.to_thread(google_drive.delete_file, connection, file_id)
        raise
    return file_id


async def _replace_encrypted_drive_summary(
    patient_id: str, connection: Any, report_bytes: bytes,
) -> str:
    """Create a replacement summary before its old Drive object is removed.

    A fresh object avoids a window where ciphertext has been replaced but RDS
    still holds the old wrapped key.  The caller updates the pointer first and
    then retires the old object.
    """
    return await _store_encrypted_drive_pdf(patient_id, connection, report_bytes, "summary")


async def _retire_plaintext_drive_file(patient_id: str, connection: Any, file_id: str) -> None:
    """Delete a plaintext import source, retrying safely on the next workspace load."""
    await asyncio.to_thread(add_pending_drive_deletion, patient_id, file_id)
    try:
        await asyncio.to_thread(google_drive.delete_file, connection, file_id)
    except Exception as exc:
        log_warning("Google Drive", "Encrypted replacement is stored but plaintext cleanup is pending: %s", type(exc).__name__)
        return
    await asyncio.to_thread(clear_pending_drive_deletion, patient_id, file_id)


def _validate_drive_pdf(content: bytes, filename: str) -> None:
    if not content or not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"'{filename}' is not a valid PDF file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{filename}' exceeds the 15 MB upload limit")


async def _import_drive_folder_changes(patient_id: str, connection: Any) -> int:
    """Encrypt legacy/new direct-folder PDFs and remove their plaintext originals."""
    documents = await asyncio.to_thread(list_documents, patient_id)
    drive_documents = [doc for doc in documents if getattr(doc, "storage_provider", "s3") == "google_drive"]
    imported = 0

    # Migrate a prior plaintext summary without regenerating its clinical content.
    if connection.summary_file_id and await asyncio.to_thread(
        get_drive_file_crypto, patient_id, connection.summary_file_id
    ) is None:
        legacy_summary = await asyncio.to_thread(google_drive.get_file_bytes, connection, connection.summary_file_id)
        _validate_drive_pdf(legacy_summary, "Clinical Summary.pdf")
        encrypted_summary_id = await _store_encrypted_drive_pdf(patient_id, connection, legacy_summary, "summary")
        saved = await asyncio.to_thread(
            set_drive_summary_file, patient_id, encrypted_summary_id, connection.summary_fingerprint or "",
        )
        if not saved:
            await asyncio.to_thread(google_drive.delete_file, connection, encrypted_summary_id)
            await asyncio.to_thread(delete_drive_file_crypto, patient_id, encrypted_summary_id)
            raise RuntimeError("Google Drive summary changed while its encryption migration was running")
        await _retire_plaintext_drive_file(patient_id, connection, connection.summary_file_id)
        imported += 1

    # Migrate previously stored plaintext Drive reports without changing their
    # existing FHIR facts or citation document IDs.
    for document in drive_documents:
        if await asyncio.to_thread(get_drive_file_crypto, patient_id, document.s3_key) is not None:
            continue
        plaintext = await asyncio.to_thread(google_drive.get_file_bytes, connection, document.s3_key)
        _validate_drive_pdf(plaintext, document.filename)
        encrypted_file_id = await _store_encrypted_drive_pdf(patient_id, connection, plaintext, "source")
        updated = await asyncio.to_thread(update_document_locator, patient_id, document.id, encrypted_file_id)
        if not updated:
            await asyncio.to_thread(google_drive.delete_file, connection, encrypted_file_id)
            await asyncio.to_thread(delete_drive_file_crypto, patient_id, encrypted_file_id)
            raise RuntimeError("Document changed while its Drive encryption migration was running")
        await _retire_plaintext_drive_file(patient_id, connection, document.s3_key)
        imported += 1

    documents = await asyncio.to_thread(list_documents, patient_id)
    known_ids = {doc.s3_key for doc in documents if getattr(doc, "storage_provider", "s3") == "google_drive"}
    drive_connection = await asyncio.to_thread(get_drive_connection, patient_id)
    if drive_connection and drive_connection.summary_file_id:
        known_ids.add(drive_connection.summary_file_id)
    pending_deletes = set(await asyncio.to_thread(list_pending_drive_deletions, patient_id))
    folder_files = await asyncio.to_thread(google_drive.list_folder_files, connection)
    for item in folder_files:
        if item.file_id in known_ids or item.file_id in pending_deletes or item.encrypted:
            continue
        if item.mime_type != "application/pdf" and not item.filename.lower().endswith(".pdf"):
            continue
        if item.size is not None and item.size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{item.filename}' exceeds the 15 MB upload limit")
        plaintext = await asyncio.to_thread(google_drive.get_file_bytes, connection, item.file_id)
        _validate_drive_pdf(plaintext, item.filename)
        fhir_bundle = await extract_pdf_to_fhir_bundle(plaintext, patient_id, item.filename)
        if not fhir_bundle.get("entry"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No supported clinical facts could be verified in {item.filename}")
        await post_to_fhir(fhir_bundle)
        encrypted_file_id = await _store_encrypted_drive_pdf(patient_id, connection, plaintext, "source")
        try:
            await asyncio.to_thread(add_document, patient_id, item.filename, fhir_bundle, encrypted_file_id, EXTRACTION_VERSION, "google_drive")
        except Exception:
            await asyncio.to_thread(google_drive.delete_file, connection, encrypted_file_id)
            await asyncio.to_thread(delete_drive_file_crypto, patient_id, encrypted_file_id)
            raise
        await _retire_plaintext_drive_file(patient_id, connection, item.file_id)
        imported += 1
    if imported:
        await asyncio.to_thread(revoke_patient_manifests, patient_id)
    return imported


@app.exception_handler(IntegrationFailure)
async def handle_integration_failure(_: Request, exc: IntegrationFailure) -> JSONResponse:
    """Return a consistent integration error without exposing uploaded content."""
    log_error(exc.component, exc.cause)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc)},
    )


async def _build_ingestion_resources(patient_id: str) -> list[dict[str, Any]]:
    return await _build_ingestion_resources_service(patient_id)


@app.post("/api/patient/register")
async def register_patient(request: PatientCredentials) -> JSONResponse:
    """Create a local patient account for the persistent document workspace."""
    patient_id, password = _validate_patient_credentials(request.patient_id, request.password)
    if len(password) < 12:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must contain at least 12 characters")
    created = await asyncio.to_thread(create_patient, patient_id, hash_password(password))
    if not created:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A patient account with this ID already exists")
    response = JSONResponse(_workspace_payload(patient_id, [], [], "s3", False))
    await _set_patient_session(response, patient_id)
    return response


@app.post("/api/patient/login")
async def login_patient(request: PatientCredentials) -> JSONResponse:
    patient_id = await _authenticate_patient(request.patient_id, request.password)
    response = JSONResponse(await _load_workspace(patient_id))
    await _set_patient_session(response, patient_id)
    return response


@app.get("/api/patient/session")
async def get_current_patient_session(request: Request) -> dict[str, Any]:
    patient_id = await _authenticate_patient(None, None, request)
    return await _load_workspace(patient_id)


@app.get("/api/patient/profile")
async def get_patient_profile_endpoint(request: Request) -> dict[str, str | None]:
    patient_id = await _authenticate_patient(None, None, request)
    profile, suggestions = await asyncio.gather(
        asyncio.to_thread(get_patient_profile, patient_id),
        asyncio.to_thread(get_patient_profile_suggestions, patient_id),
    )
    return _patient_profile_payload(profile, suggestions)


@app.put("/api/patient/profile")
async def save_patient_profile(request: PatientProfileRequest, http_request: Request) -> dict[str, Any]:
    """Save the minimum canonical demographics required for external-EHR matching."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    profile = await asyncio.to_thread(
        update_patient_profile,
        patient_id,
        request.given_name,
        request.family_name,
        request.birth_date.isoformat(),
        request.administrative_gender,
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient account not found")
    suggestions = await asyncio.to_thread(get_patient_profile_suggestions, patient_id)
    return _patient_profile_payload(profile, suggestions)


@app.post("/api/patient/logout")
async def logout_patient(request: Request) -> Response:
    await asyncio.to_thread(revoke_patient_session, request.cookies.get(PATIENT_SESSION_COOKIE, ""))
    epic.clear_live_session(request.cookies.get(EPIC_LIVE_SESSION_COOKIE, ""))
    ecw.clear_live_session(request.cookies.get(ECW_LIVE_SESSION_COOKIE, ""))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(PATIENT_SESSION_COOKIE, path="/")
    _clear_epic_cookies(response)
    _clear_ecw_cookies(response)
    return response


@app.get("/api/patient/epic/endpoints")
async def epic_endpoints(request: Request, q: str = "") -> dict[str, list[dict[str, Any]]]:
    """Search the locally mirrored, non-patient Epic hospital catalog."""
    await _authenticate_patient(None, None, request)
    endpoints = await asyncio.to_thread(search_epic_endpoints, q, 25)
    return {"endpoints": [
        {"id": endpoint.id, "name": endpoint.name, "aliases": endpoint.aliases, "addresses": endpoint.addresses}
        for endpoint in endpoints
    ]}


@app.post("/api/patient/epic/authorize")
async def authorize_epic(request: EpicAuthorizeRequest, http_request: Request) -> JSONResponse:
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    endpoint = await asyncio.to_thread(get_epic_endpoint, request.endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Choose a hospital from the ClinicForce Epic directory")
    try:
        started = await epic.begin_authorization(
            endpoint.fhir_base, endpoint.name, patient_id, f"{PUBLIC_BASE_URL}/api/patient/epic/callback", popup=request.popup,
        )
    except epic.EpicError as exc:
        raise _epic_error(exc) from exc
    response = JSONResponse({"authorization_url": started.authorization_url})
    _set_epic_cookie(response, EPIC_OAUTH_STATE_COOKIE, started.state_cookie, epic.OAUTH_STATE_MAX_AGE_SECONDS)
    return response


@app.get("/api/patient/ecw/jwks")
async def ecw_jwks() -> dict[str, list[dict[str, Any]]]:
    """Public JWKS registered in healow for private_key_jwt authentication."""
    try:
        return ecw.public_jwks()
    except ecw.EcwError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.get("/.well-known/jwks.json", include_in_schema=False)
async def ecw_registered_jwks() -> dict[str, list[dict[str, Any]]]:
    """Serve the stable public JWKS URL registered with eClinicalWorks/healow."""
    return await ecw_jwks()


@app.post("/api/patient/ecw/authorize")
async def authorize_ecw(request: EcwAuthorizeRequest, http_request: Request) -> JSONResponse:
    """Resolve one OEM code to its eCW FHIR endpoint and start SMART + PKCE."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    try:
        started = await ecw.begin_authorization(
            request.practice_code, patient_id, f"{PUBLIC_BASE_URL}/api/patient/ecw/callback", popup=request.popup,
        )
    except ecw.EcwError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    response = JSONResponse({"authorization_url": started.authorization_url})
    _set_epic_cookie(response, ECW_OAUTH_STATE_COOKIE, started.state_cookie, ecw.OAUTH_STATE_MAX_AGE_SECONDS)
    return response


@app.get("/api/patient/epic/callback")
async def epic_callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
    """Exchange a SMART code server-side; tokens never reach the browser."""
    state_cookie = request.cookies.get(EPIC_OAUTH_STATE_COOKIE, "")
    popup = epic.popup_requested(state_cookie)
    try:
        patient_id = await _authenticate_patient(None, None, request)
        if error or not code:
            raise epic.EpicError("Epic authorization was cancelled or denied")
        live_session_id, popup = await epic.complete_authorization(
            state_cookie, state, code, f"{PUBLIC_BASE_URL}/api/patient/epic/callback", patient_id,
        )
    except (HTTPException, epic.EpicError):
        if popup:
            response = _epic_popup_result(False)
            _clear_epic_cookies(response)
            return response
        response = RedirectResponse(f"{PUBLIC_BASE_URL}/?epic=error", status_code=status.HTTP_303_SEE_OTHER)
        _clear_epic_cookies(response)
        return response
    if popup:
        response = _epic_popup_result(True)
    else:
        response = RedirectResponse(f"{PUBLIC_BASE_URL}/?epic=connected", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(EPIC_OAUTH_STATE_COOKIE, path="/")
    _set_epic_cookie(response, EPIC_LIVE_SESSION_COOKIE, live_session_id, epic.LIVE_SESSION_MAX_AGE_SECONDS)
    return response


@app.get("/api/patient/ecw/callback")
async def ecw_callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
    """Complete the confidential SMART authorization code + PKCE exchange."""
    state_cookie = request.cookies.get(ECW_OAUTH_STATE_COOKIE, "")
    popup = ecw.popup_requested(state_cookie)
    try:
        patient_id = await _authenticate_patient(None, None, request)
        if error or not code:
            raise ecw.EcwError("eClinicalWorks authorization was cancelled or denied")
        live_session_id, popup = await ecw.complete_authorization(
            state_cookie, state, code, f"{PUBLIC_BASE_URL}/api/patient/ecw/callback", patient_id,
        )
    except (HTTPException, ecw.EcwError):
        if popup:
            response = _ecw_popup_result(False)
            _clear_ecw_cookies(response)
            return response
        response = RedirectResponse(f"{PUBLIC_BASE_URL}/?ecw=error", status_code=status.HTTP_303_SEE_OTHER)
        _clear_ecw_cookies(response)
        return response
    if popup:
        response = _ecw_popup_result(True)
    else:
        response = RedirectResponse(f"{PUBLIC_BASE_URL}/?ecw=connected", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(ECW_OAUTH_STATE_COOKIE, path="/")
    _set_epic_cookie(response, ECW_LIVE_SESSION_COOKIE, live_session_id, ecw.LIVE_SESSION_MAX_AGE_SECONDS)
    return response


@app.get("/api/patient/ecw/record")
async def ecw_record(request: Request) -> dict[str, Any]:
    """Fetch an authorized eCW record into a short-lived review snapshot only."""
    patient_id = await _authenticate_patient(None, None, request)
    session = await _live_ecw_session(request, patient_id)
    try:
        bundle, availability = await ecw.fetch_record(session)
    except ecw.EcwError as exc:
        raise _ecw_error(exc) from exc
    log_info("eClinicalWorks", "Retrieved %d FHIR resources for patient review from practice %s", len(bundle["entry"]), session.practice_code)
    return {"bundle": bundle, "availability": availability, "source": ecw.source_metadata(session)}


@app.post("/api/patient/ecw/import")
async def import_ecw_record(request: Request) -> JSONResponse:
    """Persist the last reviewed eCW snapshot only after an explicit confirmation."""
    patient_id = await _authenticate_patient(None, None, request)
    session = await _live_ecw_session(request, patient_id)
    if session.review_bundle is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review the eClinicalWorks data before importing it into ClinicForce",
        )
    normalized = _normalize_ecw_bundle(session.review_bundle, patient_id, session.practice_code)
    if not normalized["entry"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="eClinicalWorks returned no supported clinical resources to import",
        )
    try:
        await asyncio.to_thread(
            add_record,
            patient_id,
            "ecw",
            f"eClinicalWorks / healow import from practice {session.practice_code}",
            normalized,
        )
    except Exception as exc:
        raise IntegrationFailure("eClinicalWorks import", exc) from exc
    log_info("eClinicalWorks", "Imported %d user-confirmed FHIR resources from practice %s", len(normalized["entry"]), session.practice_code)
    ecw.clear_live_session(request.cookies.get(ECW_LIVE_SESSION_COOKIE, ""))
    response = JSONResponse(await _load_workspace(patient_id))
    response.delete_cookie(ECW_LIVE_SESSION_COOKIE, path="/")
    return response


@app.delete("/api/patient/ecw/live-session")
async def disconnect_ecw(request: Request) -> Response:
    """Discard the short-lived token and unconfirmed review snapshot."""
    patient_id = await _authenticate_patient(None, None, request)
    await _live_ecw_session(request, patient_id)
    ecw.clear_live_session(request.cookies.get(ECW_LIVE_SESSION_COOKIE, ""))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(ECW_LIVE_SESSION_COOKIE, path="/")
    return response


@app.get("/api/patient/epic/record")
async def epic_record(request: Request) -> dict[str, Any]:
    patient_id = await _authenticate_patient(None, None, request)
    session = await _live_epic_session(request, patient_id)
    try:
        bundle, availability = await epic.fetch_record(session)
    except epic.EpicError as exc:
        raise _epic_error(exc) from exc
    await _backfill_profile_from_live_epic(patient_id, bundle)
    return {"bundle": bundle, "availability": availability, "source": epic.source_metadata(session)}


@app.get("/api/patient/epic/resources/{resource_type}")
async def epic_resource(resource_type: str, request: Request) -> dict[str, Any]:
    patient_id = await _authenticate_patient(None, None, request)
    session = await _live_epic_session(request, patient_id)
    try:
        bundle = await epic.fetch_resource_type(session, resource_type)
    except epic.EpicError as exc:
        raise _epic_error(exc) from exc
    return {"bundle": bundle, "source": epic.source_metadata(session)}


@app.get("/api/patient/epic/documents/{document_id}")
async def epic_document(document_id: str, request: Request) -> Response:
    patient_id = await _authenticate_patient(None, None, request)
    session = await _live_epic_session(request, patient_id)
    try:
        content, filename = await epic.stream_document(session, document_id)
    except epic.EpicError as exc:
        raise _epic_error(exc) from exc
    return Response(content=content, media_type="application/pdf", headers=_download_headers(filename))


@app.delete("/api/patient/epic/live-session")
async def disconnect_epic(request: Request) -> Response:
    patient_id = await _authenticate_patient(None, None, request)
    # Check ownership before removal so a forged cookie cannot affect another account.
    await _live_epic_session(request, patient_id)
    epic.clear_live_session(request.cookies.get(EPIC_LIVE_SESSION_COOKIE, ""))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(EPIC_LIVE_SESSION_COOKIE, path="/")
    return response


@app.post("/api/patient/storage-preference")
async def change_storage_preference(request: StoragePreferenceRequest, http_request: Request) -> dict[str, Any]:
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    if request.storage_provider == "google_drive":
        if not google_drive.enabled():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Drive storage is not configured")
        if await asyncio.to_thread(get_drive_connection, patient_id) is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect Google Drive before selecting it")
    await asyncio.to_thread(set_storage_preference, patient_id, request.storage_provider)
    return await _load_workspace(patient_id)


@app.post("/api/patient/google-drive/connect")
async def connect_google_drive(request: PatientCredentials, http_request: Request) -> dict[str, str]:
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    if not google_drive.enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Drive integration is not configured")
    state = await asyncio.to_thread(save_drive_oauth_state, patient_id)
    return {"authorization_url": google_drive.authorization_url(state)}


@app.post("/api/patient/google-login/start")
async def start_google_login() -> dict[str, str]:
    if not google_drive.enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google sign-in is not configured")
    state = await asyncio.to_thread(save_google_login_state)
    return {"authorization_url": google_drive.authorization_url(state)}


@app.get("/api/patient/google-drive/callback")
async def google_drive_callback(state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Finish OAuth and return to the portal; browser code never receives tokens."""
    google_login = await asyncio.to_thread(consume_google_login_state, state)
    patient_id = None if google_login else await asyncio.to_thread(consume_drive_oauth_state, state)
    if not google_login and patient_id is None:
        return RedirectResponse(f"{PUBLIC_BASE_URL}/?drive=error", status_code=status.HTTP_303_SEE_OTHER)
    if error or not code:
        return RedirectResponse(f"{PUBLIC_BASE_URL}/?drive=cancelled", status_code=status.HTTP_303_SEE_OTHER)
    try:
        subject, encrypted_refresh_token, folder_id, google_given_name, google_family_name = await asyncio.to_thread(google_drive.complete_authorization, code)
        if google_login:
            patient_id = await asyncio.to_thread(get_patient_by_google_subject, subject)
            if patient_id is None:
                patient_id = await asyncio.to_thread(
                    create_google_patient, subject, hash_password(secrets.token_urlsafe(48)), google_given_name, google_family_name,
                )
            else:
                await asyncio.to_thread(set_google_profile_suggestions, patient_id, google_given_name, google_family_name)
        assert patient_id is not None
        await asyncio.to_thread(upsert_drive_connection, patient_id, subject, encrypted_refresh_token, folder_id)
        await asyncio.to_thread(set_storage_preference, patient_id, "google_drive")
    except Exception as exc:
        log_error("Google Drive", exc)
        return RedirectResponse(f"{PUBLIC_BASE_URL}/?drive=error", status_code=status.HTTP_303_SEE_OTHER)
    response = RedirectResponse(f"{PUBLIC_BASE_URL}/?patient=google&drive=connected", status_code=status.HTTP_303_SEE_OTHER)
    await _set_patient_session(response, patient_id)
    return response


@app.post("/api/patient/upload")
async def upload_patient_pdf(
    http_request: Request,
    file: list[UploadFile] | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    patient_id: str | None = Form(default=None),
    password: str | None = Form(default=None),
) -> dict[str, Any]:
    """Ingest one or more PDFs, accepting both ``file`` and ``files`` form keys.

    Supporting both keys makes the API resilient to standard FormData helpers while
    preserving the original browser contract. Source bytes never enter PostgreSQL.
    """
    patient_id = await _authenticate_patient(patient_id, password, http_request)
    storage_provider, drive_connection = await asyncio.gather(
        asyncio.to_thread(get_storage_preference, patient_id), asyncio.to_thread(get_drive_connection, patient_id)
    )
    if storage_provider == "google_drive" and drive_connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect Google Drive before uploading there")
    if storage_provider == "s3" and not s3_enabled():
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
            locator: str | None = None
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
                locator = (
                    await _store_encrypted_drive_pdf(patient_id, drive_connection, pdf_bytes, "source")
                    if storage_provider == "google_drive"
                    else await asyncio.to_thread(put_patient_pdf, patient_id, uploaded.filename, pdf_bytes, "source")
                )
                await asyncio.to_thread(add_document, patient_id, uploaded.filename, fhir_bundle, locator, EXTRACTION_VERSION, storage_provider)
            except Exception:
                if storage_provider == "google_drive" and locator:
                    await asyncio.to_thread(google_drive.delete_file, drive_connection, locator)
                    await asyncio.to_thread(delete_drive_file_crypto, patient_id, locator)
                elif locator:
                    await asyncio.to_thread(delete_s3_object, locator)
                raise
    except HTTPException:
        raise
    except Exception as exc:
        component = "Database" if isinstance(exc, FHIRRequestError) else "Ingestion"
        raise IntegrationFailure(component, exc) from exc

    return await _load_workspace(patient_id)


@app.post("/api/patient/google-drive/import")
async def import_google_drive_changes(request: PatientCredentials, http_request: Request) -> dict[str, Any]:
    """Import direct-folder PDFs selected by the patient into encrypted Drive storage."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    storage_provider, connection = await asyncio.gather(
        asyncio.to_thread(get_storage_preference, patient_id), asyncio.to_thread(get_drive_connection, patient_id),
    )
    if storage_provider != "google_drive" or connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect and select Google Drive before importing Drive files")
    try:
        imported = await _import_drive_folder_changes(patient_id, connection)
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationFailure("Google Drive import", exc) from exc
    workspace = await _load_workspace(patient_id)
    workspace["drive_sync_message"] = (
        f"{imported} Drive PDF{' was' if imported == 1 else 's were'} encrypted and imported."
        if imported else "No new Drive PDFs need importing."
    )
    return workspace


@app.post("/api/patient/records")
async def save_patient_record(request: ManualRecordRequest, http_request: Request) -> dict[str, Any]:
    """Save a structured patient-entered record as FHIR R4 for dashboard and sharing."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    label, fhir_bundle = _manual_record_bundle(request, patient_id)
    try:
        await post_to_fhir(fhir_bundle)
        await asyncio.to_thread(add_record, patient_id, request.category, label, fhir_bundle)
    except Exception as exc:
        raise IntegrationFailure("FHIR persistence", exc) from exc
    return await _load_workspace(patient_id)


@app.post("/api/patient/external-shl-import")
async def import_external_shl(request: ExternalShlImportRequest, http_request: Request) -> dict[str, Any]:
    """Persist a user-confirmed external SHL bundle, never its key, PIN, or JWE."""
    patient_id = await _authenticate_patient(None, None, http_request)
    try:
        serialized = json.dumps(request.bundle, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Imported clinical data must be valid JSON") from exc
    if len(serialized.encode("utf-8")) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Imported clinical data exceeds the 5 MB limit")
    normalized = _normalize_external_shl_bundle(request.bundle, patient_id, request.source_host)
    if not normalized["entry"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The shared file contains no supported FHIR R4 resources to import")
    try:
        await asyncio.to_thread(
            add_record,
            patient_id,
            "external-shl",
            f"External SMART Health Link from {request.source_host}",
            normalized,
        )
    except Exception as exc:
        raise IntegrationFailure("External SHL import", exc) from exc
    log_info("External SHL", "Imported %d user-confirmed external FHIR resources from %s", len(normalized["entry"]), request.source_host)
    return await _load_workspace(patient_id)


@app.post("/api/patient/generate-link")
async def generate_patient_link(request: GeneratePatientLinkRequest, http_request: Request) -> dict[str, Any]:
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    protected_share = request.share_mode == "standard"
    if protected_share and (not request.passcode or len(request.passcode) != 4):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a four-digit sharing passcode")
    if not protected_share and request.passcode is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The eClinicalMobile compatibility QR cannot include a passcode")
    # Reconcile patient-managed Drive deletions before this snapshot is encrypted.
    await _load_workspace(patient_id)
    documents, records = await asyncio.gather(
        asyncio.to_thread(list_documents, patient_id), asyncio.to_thread(list_records, patient_id)
    )
    try:
        await _ensure_documents_extracted(patient_id, documents)
        documents = await asyncio.to_thread(list_documents, patient_id)
        workspace_bundle = _combine_document_bundles(documents, records)

        # MyChart is deliberately fetched only at the moment the patient creates
        # the one clinician QR. ClinicForce never stores Epic clinical resources,
        # tokens, or document attachments; the separate patient profile may retain
        # only the minimum matching demographics explicitly backfilled below.
        epic_snapshot: dict[str, Any] | None = None
        profile = await asyncio.to_thread(get_patient_profile, patient_id)
        if http_request.cookies.get(EPIC_LIVE_SESSION_COOKIE):
            session = await _live_epic_session(http_request, patient_id)
            try:
                epic_bundle, _ = await epic.fetch_record(session)
            except epic.EpicError as exc:
                raise _epic_error(exc) from exc
            profile = await _backfill_profile_from_live_epic(patient_id, epic_bundle)
            if epic_bundle.get("entry"):
                epic_snapshot = _epic_share_snapshot(epic_bundle, epic.source_metadata(session))

        fhir_bundle = build_exchange_bundle(
            patient_id, documents, records, epic_snapshot, profile,
            ecw_compat=(request.share_mode == "ecw_compat")
        )
        # The exchange bundle always contains Patient.  Do not issue a QR that
        # has no clinical/document content beyond that identity shell.
        if len(fhir_bundle["entry"]) == 1:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No verified clinical facts are available to share")
        exchange_patient = fhir_bundle["entry"][0]["resource"]
        if not _has_matchable_patient_demographics(exchange_patient):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Complete the patient's legal name and date of birth in the demographic profile before creating an external-EHR share.",
            )
        shl_uri, qr_code_base64, manifest_id = await asyncio.to_thread(
            _create_upload_shl,
            patient_id,
            fhir_bundle,
            request.passcode if protected_share else None,
            "local" if protected_share else "ecw_compat",
            protected_share,
        )
        # Drive patients receive one stable, replaced-on-new-QR PDF in their own
        # ClinicForce folder. S3 patients retain the existing non-persisted report flow.
        if await asyncio.to_thread(get_storage_preference, patient_id) == "google_drive":
            connection = await asyncio.to_thread(get_drive_connection, patient_id)
            if connection is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reconnect Google Drive before creating a share")
            # The stable Drive summary remains limited to the patient workspace.
            # Live Epic data is visible only in the encrypted QR snapshot that the
            # patient has just chosen to share with a clinician.
            fingerprint = hashlib.sha256(json.dumps(workspace_bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            summary_exists = bool(connection.summary_file_id) and await asyncio.to_thread(
                google_drive.file_exists, connection, connection.summary_file_id
            )
            summary_crypto = (
                await asyncio.to_thread(get_drive_file_crypto, patient_id, connection.summary_file_id)
                if summary_exists and connection.summary_file_id else None
            )
            if connection.summary_fingerprint != fingerprint or not summary_exists or summary_crypto is None:
                # The stored Drive PDF keeps report/page citations but deliberately
                # omits QR-bound download capabilities. The current clinician
                # dashboard adds those just-in-time after QR/PIN verification.
                # This lets the one Drive PDF remain unchanged for an unchanged
                # patient record while preserving access control for source files.
                summary = await summarize_fhir_bundle(workspace_bundle)
                report_bytes = await run_in_threadpool(render_doctor_pdf_bytes, summary)
                previous_summary_file_id = connection.summary_file_id
                summary_file_id = await _replace_encrypted_drive_summary(patient_id, connection, report_bytes)
                await asyncio.to_thread(set_drive_summary_file, patient_id, summary_file_id, fingerprint)
                if previous_summary_file_id and previous_summary_file_id != summary_file_id:
                    await _retire_plaintext_drive_file(patient_id, connection, previous_summary_file_id)
        log_info(
            "Secure Sharing",
            "Created encrypted clinician share mode=%s for %d documents, %d structured records, Epic=%s",
            request.share_mode,
            len(documents),
            len(records),
            bool(epic_snapshot),
        )
        return {
            "shl_uri": shl_uri,
            "qr_code_base64": qr_code_base64,
            "epic_included": bool(epic_snapshot),
            "share_mode": request.share_mode,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationFailure("Secure Sharing", exc) from exc


@app.post("/api/patient/documents/{document_id}/download")
async def download_patient_document(document_id: int, request: PatientCredentials, http_request: Request) -> Response:
    """Return one saved source PDF after re-authenticating the patient."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    document = await asyncio.to_thread(get_document, patient_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        content = await _read_document_bytes(patient_id, document)
    except Exception as exc:
        raise IntegrationFailure("Document storage", exc) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers=_download_headers(document.filename),
    )


@app.post("/api/patient/documents/bulk-download")
async def bulk_download_patient_documents(request: DocumentBatchRequest, http_request: Request) -> Response:
    """Return patient-selected, decrypted source PDFs as one private ZIP download."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    documents = await asyncio.gather(*[
        asyncio.to_thread(get_document, patient_id, document_id) for document_id in request.document_ids
    ])
    if any(document is None for document in documents):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more selected reports no longer exist")
    try:
        contents = await asyncio.gather(*[
            _read_document_bytes(patient_id, document) for document in documents if document is not None
        ])
        if sum(len(content) for content in contents) > MAX_BULK_DOWNLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Selected reports exceed the 150 MB bulk-download limit",
            )
        archive = BytesIO()
        used_names: set[str] = set()
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
            for document, content in zip(documents, contents, strict=True):
                assert document is not None
                zip_file.writestr(_zip_entry_name(document.filename, used_names), content)
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationFailure("Document storage", exc) from exc
    return Response(content=archive.getvalue(), media_type="application/zip", headers=_zip_download_headers())


async def _delete_patient_document(patient_id: str, document: Any) -> None:
    """Remove one RDS-owned document from its backing store and envelope registry."""
    if getattr(document, "storage_provider", "s3") == "google_drive":
        connection = await asyncio.to_thread(get_drive_connection, patient_id)
        if connection is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reconnect Google Drive before deleting this report")
        await asyncio.to_thread(google_drive.delete_file, connection, document.s3_key)
        await asyncio.to_thread(delete_drive_file_crypto, patient_id, document.s3_key)
    else:
        await asyncio.to_thread(delete_s3_object, document.s3_key)
    removed = await asyncio.to_thread(remove_documents, patient_id, [document.id])
    if not removed:
        raise RuntimeError("Document was not removed from ClinicForce")


@app.delete("/api/patient/documents/{document_id}")
async def delete_patient_document(document_id: int, request: PatientCredentials, http_request: Request) -> dict[str, Any]:
    """Delete a patient-selected source from its chosen storage and revoke old QR shares."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    document = await asyncio.to_thread(get_document, patient_id, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        await _delete_patient_document(patient_id, document)
        await asyncio.to_thread(revoke_patient_manifests, patient_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise IntegrationFailure("Document deletion", exc) from exc
    workspace = await _load_workspace(patient_id)
    workspace["drive_sync_message"] = "Report deleted. Existing QR codes were revoked."
    return workspace


@app.post("/api/patient/documents/bulk-delete")
async def bulk_delete_patient_documents(request: DocumentBatchRequest, http_request: Request) -> dict[str, Any]:
    """Delete all selected reports and invalidate QR manifests once at the end."""
    patient_id = await _authenticate_patient(request.patient_id, request.password, http_request)
    documents = await asyncio.gather(*[
        asyncio.to_thread(get_document, patient_id, document_id) for document_id in request.document_ids
    ])
    if any(document is None for document in documents):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more selected reports no longer exist")
    try:
        for document in documents:
            assert document is not None
            await _delete_patient_document(patient_id, document)
        await asyncio.to_thread(revoke_patient_manifests, patient_id)
    except HTTPException:
        raise
    except Exception as exc:
        # A successful deletion changes the record set even if a later one fails,
        # so invalidate existing shares before surfacing the recoverable error.
        await asyncio.to_thread(revoke_patient_manifests, patient_id)
        raise IntegrationFailure("Document deletion", exc) from exc
    workspace = await _load_workspace(patient_id)
    count = len(documents)
    workspace["drive_sync_message"] = f"{count} report{' was' if count == 1 else 's were'} deleted. Existing QR codes were revoked."
    return workspace


@app.get("/api/doctor/documents/{document_id}/download")
async def download_doctor_document(document_id: int, token: str) -> Response:
    """Download a cited source only if it belongs to the QR manifest's patient."""
    try:
        manifest_id = verify_document_download_token(token, document_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    document = await asyncio.to_thread(get_document_by_id, document_id)
    if document is None:
        # Do not distinguish a stale/deleted ID from an unauthorized one.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source report is no longer available")
    manifest = await asyncio.to_thread(get_manifest, manifest_id)
    if manifest is None or not manifest.get("patient_id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The shared record is no longer available")
    patient_id = str(manifest["patient_id"])
    owned_document = await asyncio.to_thread(get_document, patient_id, document_id)
    if owned_document is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This source is not part of the shared record")
    try:
        content = await _read_document_bytes(patient_id, owned_document)
    except Exception as exc:
        raise IntegrationFailure("Document storage", exc) from exc
    return Response(content=content, media_type="application/pdf", headers=_download_headers(owned_document.filename))



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
            shl_uri, qr_code_base64, manifest_id = _create_upload_shl(
                None, fhir_bundle, request.passcode
            )
            log_info("Secure Sharing", "Created encrypted test share")
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
async def retrieve_manifest(manifest_id: str, request: ManifestRequest) -> dict[str, Any]:
    """Return an encrypted SHL payload after successful passcode verification."""
    log_info("Secure Sharing", "SHL manifest fetch attempt")
    try:
        manifest = await asyncio.to_thread(verify_manifest_pin, manifest_id, request.passcode or "")
    except InvalidManifestPin as exc:
        log_warning("Secure Sharing", "SHL manifest fetch rejected: invalid passcode")
        if exc.remaining_attempts == 0:
            log_warning("Secure Sharing", "SHL share permanently disabled after lifetime PIN limit")
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"remainingAttempts": exc.remaining_attempts})
    if manifest is None:
        log_warning("Secure Sharing", "SHL manifest fetch rejected: unavailable")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shared record is no longer available")

    file: dict[str, str] = {"contentType": "application/fhir+json"}
    max_embedded = request.embeddedLengthMax
    if max_embedded is None or len(manifest["encrypted_jwe"]) <= max_embedded:
        file["embedded"] = manifest["encrypted_jwe"]
    else:
        try:
            object_key = await asyncio.to_thread(put_shl_payload, manifest_id, manifest["encrypted_jwe"])
            file["location"] = await asyncio.to_thread(presign_shl_payload, object_key)
        except Exception as exc:
            log_error("Secure Sharing", exc)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Shared record is temporarily unavailable") from exc

    return {
        "files": [
            file
        ]
    }


@app.post("/api/doctor/decrypt-and-summarize")
async def decrypt_and_summarize(request: DoctorSummaryRequest, response: Response) -> dict[str, Any]:
    """Build a dashboard from the stored QR manifest, never browser-supplied FHIR."""
    try:
        try:
            manifest = await asyncio.to_thread(verify_manifest_pin, request.manifest_id, request.passcode)
        except InvalidManifestPin as exc:
            if exc.remaining_attempts == 0:
                log_warning("Secure Sharing", "SHL share permanently disabled after lifetime PIN limit")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shared-record credentials") from exc
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid shared-record credentials")
        log_info("Clinical Summary", "Decrypting server-stored QR manifest")
        fhir_bundle = await asyncio.to_thread(_decrypt_jwe_payload, manifest["encrypted_jwe"], request.key)
        log_info("Clinical Summary", "Generating deterministic clinical narrative")
        summary = _with_source_download_links(await summarize_fhir_bundle(fhir_bundle), str(request.manifest_id))
        patient_id = manifest.get("patient_id")
        connection = await asyncio.to_thread(get_drive_connection, str(patient_id)) if patient_id else None
        storage_provider = (
            await asyncio.to_thread(get_storage_preference, str(patient_id)) if connection else "s3"
        )
        if storage_provider == "google_drive" and connection and connection.summary_file_id:
            try:
                crypto = await asyncio.to_thread(get_drive_file_crypto, str(patient_id), connection.summary_file_id)
                if crypto is None:
                    raise RuntimeError("Drive summary is awaiting encryption migration")
                ciphertext = await asyncio.to_thread(google_drive.get_file_bytes, connection, connection.summary_file_id)
                report_bytes = await asyncio.to_thread(decrypt_drive_pdf, str(patient_id), crypto, ciphertext)
            except Exception as exc:
                # A patient may delete their Drive summary after making the QR. The
                # doctor can still view that already-authorized QR snapshot; no
                # Drive or S3 plaintext fallback is persisted.
                log_warning("Google Drive", "Stored summary unavailable for clinician view: %s", type(exc).__name__)
                report_bytes = await run_in_threadpool(render_doctor_pdf_bytes, summary)
        else:
            report_bytes = await run_in_threadpool(render_doctor_pdf_bytes, summary)
        if patient_id and insurance_beta_enabled():
            conditions = [
                " · ".join(value for value in (item.get("name"), item.get("details")) if isinstance(value, str) and value.strip())
                for item in summary.get("conditions", [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]
            response.set_cookie(
                CLINICIAN_SHARE_COOKIE,
                insurance_golden.create_share_token(str(patient_id), request.manifest_id, conditions=conditions),
                max_age=CLINICIAN_SHARE_MAX_AGE,
                httponly=True,
                secure=PUBLIC_BASE_URL.startswith("https://"),
                samesite="lax",
                path="/api/doctor",
            )
            response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
        return {
            "summary": summary,
            "pdf_url": "data:application/pdf;base64," + base64.b64encode(report_bytes).decode("ascii"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        failure = IntegrationFailure("Clinical Summary", exc)
        log_error(failure.component, failure.cause)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(failure)) from exc
