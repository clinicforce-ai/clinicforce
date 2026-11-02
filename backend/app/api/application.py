"""ClinicForce integration endpoints for FHIR ingestion and SMART Health Links."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
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
from app.logs.collection_audit import request_id as collection_request_id
from app.logs.collection_audit import collection_event
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
    CLINICIAN_SHARE_COOKIE, CLINICIAN_SHARE_MAX_AGE, IDME_OAUTH_STATE_COOKIE, IDME_OAUTH_STATE_COOKIE_PATH, MAX_UPLOAD_BYTES, PATIENT_SESSION_COOKIE,
    PATIENT_SESSION_MAX_AGE, PUBLIC_BASE_URL, SHL_SHARE_TTL_SECONDS,
    SOURCE_TAGS as _SOURCE_TAGS, insurance_beta_enabled,
)
from app.api.developer_docs import OPENAPI_DESCRIPTION, enrich_openapi, render_docs, docs_sessions, guard_docs_execution
from app.insurance import golden as insurance_golden


_epic_endpoint_sync_task: asyncio.Task[None] | None = None


app = FastAPI(
    title="ClinicForce API",
    description=OPENAPI_DESCRIPTION,
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.middleware("http")(guard_docs_execution)


@app.middleware("http")
async def collection_audit_request_id(request: Request, call_next):
    token = collection_request_id.set(str(uuid4()))
    started = time.monotonic()
    path = request.url.path
    share_stage = ('share_create' if path == '/api/patient/generate-link' else
                   'manifest_fetch' if path.startswith('/manifest/') else
                   'share_summary' if path == '/api/doctor/decrypt-and-summarize' else None)
    stage = ('patient_search' if path == '/api/staff/intakes/ecw-lookup' else
             'appointment_list' if path == '/api/doctor/insurance/commercial/appointments' else
             'previsit' if path == '/api/doctor/insurance/commercial/appointments/previsit' else
             'identity' if path.startswith('/api/staff/intakes/') and path.endswith('/patient') else None)
    try:
        response = await call_next(request)
        if share_stage or path.startswith(("/api/staff/intakes", "/api/doctor/insurance/commercial")):
            response.headers["X-ClinicForce-Request-ID"] = collection_request_id.get()
        if share_stage:
            response.headers['Cache-Control'] = 'no-store'
            collection_event('sharing_http_completed', stage=share_stage,
                             outcome='success' if response.status_code < 400 else 'error',
                             http_status=response.status_code, duration_ms=int((time.monotonic() - started) * 1000),
                             cloudfront_request_id=request.headers.get('x-amz-cf-id'))
        if stage:
            collection_event('collection_http_completed', stage=stage, outcome='success' if response.status_code < 400 else 'error',
                             http_status=response.status_code, duration_ms=int((time.monotonic() - started) * 1000))
        return response
    except Exception:
        if share_stage:
            collection_event('sharing_http_failed', stage=share_stage, outcome='error',
                             reason_code='unhandled_error', duration_ms=int((time.monotonic() - started) * 1000),
                             cloudfront_request_id=request.headers.get('x-amz-cf-id'))
        if stage:
            collection_event('collection_http_failed', stage=stage, outcome='error',
                             reason_code='unhandled_error', duration_ms=int((time.monotonic() - started) * 1000))
        raise
    finally:
        collection_request_id.reset(token)


@app.get("/api/docs/session", include_in_schema=False)
async def swagger_session(request: Request):
    return JSONResponse(await docs_sessions(request), headers={"Cache-Control": "no-store"})


@app.get("/api/docs", include_in_schema=False)
async def swagger_docs():
    return render_docs()


@app.get("/api/redoc", include_in_schema=False)
async def redoc_docs():
    return RedirectResponse(url="/api/docs", status_code=308)


app.include_router(insurance_router)
from app.api.routers.insurance_v2 import router as insurance_v2_router
app.include_router(insurance_v2_router)
from app.api.routers.commercial_estimator import router as commercial_estimator_router
app.include_router(commercial_estimator_router)
from app.api.routers.staff import router as staff_router, from_share as staff_intake_from_share
app.include_router(staff_router)
from app.api.routers.staff_registration import router as staff_registration_router
from app.api.routers.patient_uploads import router as patient_uploads_router
from app.api.routers.patient_clinic_account import router as patient_clinic_account_router
from app.api.routers.idme import router as idme_router
from app.api.routers.public_entry import install_access_log_filter, router as public_entry_router
app.include_router(staff_registration_router)
app.include_router(patient_uploads_router)
app.include_router(patient_clinic_account_router)
app.include_router(idme_router)
app.add_api_route("/api/doctor/insurance/staff-intake", staff_intake_from_share, methods=["POST"])
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
    install_access_log_filter()
    if EPIC_ENDPOINT_SYNC_ENABLED:
        _epic_endpoint_sync_task = asyncio.create_task(_epic_endpoint_sync_loop())


@app.on_event("shutdown")
async def stop_epic_endpoint_sync() -> None:
    global _epic_endpoint_sync_task
    if _epic_endpoint_sync_task is not None:
        _epic_endpoint_sync_task.cancel()
        _epic_endpoint_sync_task = None


from app.api.services.patient_helpers import (
    _create_upload_shl,
    _mask_passcode,
    _validate_patient_credentials,
    _set_patient_session,
    _set_epic_cookie,
    _clear_epic_cookies,
    _clear_ecw_cookies,
    _epic_popup_result,
    _ecw_popup_result,
    _live_epic_session,
    _live_ecw_session,
    _epic_error,
    _ecw_error,
    _authenticate_patient,
    handle_database_unavailable,
    _backfill_profile_from_live_epic,
    _with_source_download_links,
    _load_workspace,
)


from app.api.services.document_helpers import (
    _ensure_documents_extracted,
    _read_document_bytes,
    _store_encrypted_drive_pdf,
    _replace_encrypted_drive_summary,
    _retire_plaintext_drive_file,
    _validate_drive_pdf,
    _import_drive_folder_changes,
    handle_integration_failure,
    _build_ingestion_resources,
)


from app.api.routers.patient_routes import (
    register_patient,
    login_patient,
    get_current_patient_session,
    get_patient_profile_endpoint,
    save_patient_profile,
    logout_patient,
    epic_endpoints,
    authorize_epic,
    ecw_jwks,
    ecw_registered_jwks,
    authorize_ecw,
    epic_callback,
    ecw_callback,
    ecw_record,
    import_ecw_record,
    disconnect_ecw,
    epic_record,
    epic_resource,
    epic_document,
    disconnect_epic,
)


from app.api.routers.import_routes import (
    change_storage_preference,
    connect_google_drive,
    start_google_login,
    google_drive_callback,
    upload_patient_pdf,
    import_google_drive_changes,
    save_patient_record,
    import_external_shl,
)


from app.api.routers.share_routes import (
    generate_patient_link,
    download_patient_document,
    bulk_download_patient_documents,
    _delete_patient_document,
    delete_patient_document,
    bulk_delete_patient_documents,
    download_doctor_document,
)



from app.api.routers.doctor_routes import (
    test_integration,
    retrieve_manifest,
    decrypt_and_summarize,
)


# Pretty public links must follow the concrete API and health routes.
app.include_router(public_entry_router)


_original_openapi = app.openapi


def guided_openapi() -> dict:
    return enrich_openapi(_original_openapi())


app.openapi = guided_openapi
