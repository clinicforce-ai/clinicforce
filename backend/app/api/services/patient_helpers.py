"""Patient helpers for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

def _create_upload_shl(
    patient_id: str | None,
    fhir_bundle: dict[str, _api.Any],
    passcode: str | None,
    source: str = "local",
    require_passcode: bool = True,
) -> tuple[str, str, str]:
    return _api._create_upload_shl_service(
        patient_id, fhir_bundle, passcode, source, require_passcode,
        _api.PUBLIC_BASE_URL, _api.SHL_SHARE_TTL_SECONDS, _api.generate_symmetric_key,
        _api.encrypt_bundle, _api.hash_pin, _api.create_manifest, _api.create_shl_uri, _api.generate_qr_code,
    )


def _mask_passcode(passcode: str) -> str:
    return "*" * len(passcode)


def _validate_patient_credentials(patient_id: str | None, password: str | None) -> tuple[str, str]:
    return _api._validate_patient_credentials_service(patient_id, password)


async def _set_patient_session(response: _api.Response, patient_id: str) -> None:
    session_token = await _api.asyncio.to_thread(_api.create_patient_session, patient_id)
    response.set_cookie(
        key=_api.PATIENT_SESSION_COOKIE,
        value=session_token,
        max_age=_api.PATIENT_SESSION_MAX_AGE,
        httponly=True,
        secure=_api.PUBLIC_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _set_epic_cookie(response: _api.Response, name: str, value: str, max_age: int) -> None:
    """Keep opaque OAuth/session handles out of JavaScript and persistent storage."""
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=_api.PUBLIC_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _clear_epic_cookies(response: _api.Response) -> None:
    response.delete_cookie(_api.EPIC_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(_api.EPIC_LIVE_SESSION_COOKIE, path="/")


def _clear_ecw_cookies(response: _api.Response) -> None:
    response.delete_cookie(_api.ECW_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(_api.ECW_LIVE_SESSION_COOKIE, path="/")


def _epic_popup_result(connected: bool) -> _api.HTMLResponse:
    return _api._popup_result("epic", connected, _api.PUBLIC_BASE_URL)


def _ecw_popup_result(connected: bool) -> _api.HTMLResponse:
    return _api._popup_result("ecw", connected, _api.PUBLIC_BASE_URL)


async def _live_epic_session(request: _api.Request, patient_id: str) -> _api.epic.LiveEpicSession:
    return await _api._live_session(request, patient_id, _api.EPIC_LIVE_SESSION_COOKIE, _api.epic.get_live_session, _api.epic.EpicError)


async def _live_ecw_session(request: _api.Request, patient_id: str) -> _api.ecw.LiveEcwSession:
    return await _api._live_session(request, patient_id, _api.ECW_LIVE_SESSION_COOKIE, _api.ecw.get_live_session, _api.ecw.EcwError)


def _epic_error(exc: _api.epic.EpicError) -> _api.HTTPException:
    return _api._integration_error(exc)


def _ecw_error(exc: _api.ecw.EcwError) -> _api.HTTPException:
    return _api._integration_error(exc, _api.status.HTTP_401_UNAUTHORIZED)


async def _authenticate_patient(patient_id: str | None, password: str | None, request: _api.Request | None = None) -> str:
    def log_rejection(path: str, credentials: str) -> None:
        cookie = "present" if request and request.cookies.get(_api.PATIENT_SESSION_COOKIE) else "absent"
        _api.log_warning("Patient authentication", "%s rejected: session cookie=%s, credentials=%s", path, cookie, credentials)

    return await _api._authenticate_patient_service(
        patient_id, password, request, _api.PATIENT_SESSION_COOKIE, _api.PUBLIC_BASE_URL,
        _api.get_patient_session, _api.get_password_hash, _api.verify_password, log_rejection,
    )


@_api.app.exception_handler(_api.psycopg.OperationalError)
async def handle_database_unavailable(_: _api.Request, exc: _api.psycopg.OperationalError) -> _api.JSONResponse:
    """Avoid exposing database failures or returning an unhelpful generic 500."""
    _api.log_error("Database", exc)
    return _api.JSONResponse(
        status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Patient data is temporarily unavailable. Please try again shortly."},
    )


async def _backfill_profile_from_live_epic(patient_id: str, bundle: dict[str, _api.Any]) -> _api.PatientProfile | None:
    """Persist only missing matching demographics from an already-authorized Epic response."""
    patient_resource = _api._first_patient_resource(bundle)
    if patient_resource is None:
        return await _api.asyncio.to_thread(_api.get_patient_profile, patient_id)
    return await _api.asyncio.to_thread(_api.backfill_patient_profile_from_epic, patient_id, patient_resource)


def _with_source_download_links(summary: dict[str, _api.Any], manifest_id: str) -> dict[str, _api.Any]:
    return _api._with_source_download_links_service(summary, manifest_id, _api.PUBLIC_BASE_URL)


async def _load_workspace(patient_id: str) -> dict[str, _api.Any]:
    documents, records, storage_provider, connection = await _api.asyncio.gather(
        _api.asyncio.to_thread(_api.list_documents, patient_id),
        _api.asyncio.to_thread(_api.list_records, patient_id),
        _api.asyncio.to_thread(_api.get_storage_preference, patient_id),
        _api.asyncio.to_thread(_api.get_drive_connection, patient_id),
    )
    pending_import_count = 0
    sync_message: str | None = None
    access_upgrade_required = False
    if storage_provider == "google_drive" and connection is not None:
        drive_documents = [document for document in documents if getattr(document, "storage_provider", "s3") == "google_drive"]
        if drive_documents:
            existing_ids = await _api.asyncio.to_thread(
                _api.google_drive.existing_file_ids, connection, [document.s3_key for document in drive_documents]
            )
            missing_document_ids = [document.id for document in drive_documents if document.s3_key not in existing_ids]
            if missing_document_ids:
                removed = await _api.asyncio.to_thread(_api.remove_documents, patient_id, missing_document_ids)
                if removed:
                    for document in drive_documents:
                        if document.id in missing_document_ids:
                            await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, document.s3_key)
                await _api.asyncio.to_thread(_api.revoke_patient_manifests, patient_id)
                _api.log_warning("Google Drive", "Removed %d stale Drive document mappings after patient-side deletion", removed)
                documents = await _api.asyncio.to_thread(_api.list_documents, patient_id)
                sync_message = f"{removed} deleted Drive report{' was' if removed == 1 else 's were'} removed from ClinicForce. Existing QR codes were revoked."
        try:
            folder_files = await _api.asyncio.to_thread(_api.google_drive.list_folder_files, connection)
            known_ids = {document.s3_key for document in documents if getattr(document, "storage_provider", "s3") == "google_drive"}
            if connection.summary_file_id:
                known_ids.add(connection.summary_file_id)
            pending_delete_ids = set(await _api.asyncio.to_thread(_api.list_pending_drive_deletions, patient_id))
            for file_id in pending_delete_ids:
                try:
                    await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, file_id)
                    await _api.asyncio.to_thread(_api.clear_pending_drive_deletion, patient_id, file_id)
                except Exception as exc:
                    _api.log_warning("Google Drive", "Could not remove already-imported plaintext file: %s", type(exc).__name__)
            external_pdf_count = sum(
                1 for item in folder_files
                if item.file_id not in known_ids and item.file_id not in pending_delete_ids
                and not item.encrypted and (item.mime_type == "application/pdf" or item.filename.lower().endswith(".pdf"))
            )
            legacy_count = 0
            for document in documents:
                if getattr(document, "storage_provider", "s3") != "google_drive":
                    continue
                if await _api.asyncio.to_thread(_api.get_drive_file_crypto, patient_id, document.s3_key) is None:
                    legacy_count += 1
            if connection.summary_file_id and await _api.asyncio.to_thread(
                _api.get_drive_file_crypto, patient_id, connection.summary_file_id
            ) is None:
                legacy_count += 1
            pending_import_count = external_pdf_count + legacy_count
        except Exception as exc:
            # Existing Drive artifacts remain usable with the original narrow grant.
            # The new folder-import feature becomes available after the patient
            # consents to the upgraded Drive scope.
            access_upgrade_required = True
            _api.log_warning("Google Drive", "Folder import reconciliation unavailable: %s", type(exc).__name__)
    return _api._workspace_payload(
        patient_id, documents, records, storage_provider, connection is not None,
        pending_import_count, sync_message, access_upgrade_required,
    )
