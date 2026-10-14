"""Import routes for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

@_api.app.post("/api/patient/storage-preference")
async def change_storage_preference(request: _api.StoragePreferenceRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    if request.storage_provider == "google_drive":
        if not _api.google_drive.enabled():
            raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Drive storage is not configured")
        if await _api.asyncio.to_thread(_api.get_drive_connection, patient_id) is None:
            raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="Connect Google Drive before selecting it")
    await _api.asyncio.to_thread(_api.set_storage_preference, patient_id, request.storage_provider)
    return await _api._load_workspace(patient_id)


@_api.app.post("/api/patient/google-drive/connect")
async def connect_google_drive(request: _api.PatientCredentials, http_request: _api.Request) -> dict[str, str]:
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    if not _api.google_drive.enabled():
        raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google Drive integration is not configured")
    state = await _api.asyncio.to_thread(_api.save_drive_oauth_state, patient_id)
    return {"authorization_url": _api.google_drive.authorization_url(state)}


@_api.app.post("/api/patient/google-login/start")
async def start_google_login() -> dict[str, str]:
    if not _api.google_drive.enabled():
        raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google sign-in is not configured")
    state = await _api.asyncio.to_thread(_api.save_google_login_state)
    return {"authorization_url": _api.google_drive.authorization_url(state)}


@_api.app.get("/api/patient/google-drive/callback")
async def google_drive_callback(state: str = "", code: str = "", error: str = "") -> _api.RedirectResponse:
    """Finish OAuth and return to the portal; browser code never receives tokens."""
    google_login = await _api.asyncio.to_thread(_api.consume_google_login_state, state)
    patient_id = None if google_login else await _api.asyncio.to_thread(_api.consume_drive_oauth_state, state)
    if not google_login and patient_id is None:
        return _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?drive=error", status_code=_api.status.HTTP_303_SEE_OTHER)
    if error or not code:
        return _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?drive=cancelled", status_code=_api.status.HTTP_303_SEE_OTHER)
    try:
        subject, encrypted_refresh_token, folder_id, google_given_name, google_family_name = await _api.asyncio.to_thread(_api.google_drive.complete_authorization, code)
        if google_login:
            patient_id = await _api.asyncio.to_thread(_api.get_patient_by_google_subject, subject)
            if patient_id is None:
                patient_id = await _api.asyncio.to_thread(
                    _api.create_google_patient, subject, _api.hash_password(_api.secrets.token_urlsafe(48)), google_given_name, google_family_name,
                )
            else:
                await _api.asyncio.to_thread(_api.set_google_profile_suggestions, patient_id, google_given_name, google_family_name)
        assert patient_id is not None
        await _api.asyncio.to_thread(_api.upsert_drive_connection, patient_id, subject, encrypted_refresh_token, folder_id)
        await _api.asyncio.to_thread(_api.set_storage_preference, patient_id, "google_drive")
    except Exception as exc:
        _api.log_error("Google Drive", exc)
        return _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?drive=error", status_code=_api.status.HTTP_303_SEE_OTHER)
    response = _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?patient=google&drive=connected", status_code=_api.status.HTTP_303_SEE_OTHER)
    await _api._set_patient_session(response, patient_id)
    return response


@_api.app.post("/api/patient/upload")
async def upload_patient_pdf(
    http_request: _api.Request,
    file: list[_api.UploadFile] | None = _api.File(default=None),
    files: list[_api.UploadFile] | None = _api.File(default=None),
    patient_id: str | None = _api.Form(default=None),
    password: str | None = _api.Form(default=None),
) -> dict[str, _api.Any]:
    """Ingest one or more PDFs, accepting both ``file`` and ``files`` form keys.

    Supporting both keys makes the API resilient to standard FormData helpers while
    preserving the original browser contract. Source bytes never enter PostgreSQL.
    """
    patient_id = await _api._authenticate_patient(patient_id, password, http_request)
    storage_provider, drive_connection = await _api.asyncio.gather(
        _api.asyncio.to_thread(_api.get_storage_preference, patient_id), _api.asyncio.to_thread(_api.get_drive_connection, patient_id)
    )
    if storage_provider == "google_drive" and drive_connection is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="Connect Google Drive before uploading there")
    if storage_provider == "s3" and not _api.s3_enabled():
        raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail="Document storage is unavailable")
    uploaded_files = [*(file or []), *(files or [])]
    if not uploaded_files or any(
        not uploaded.filename or not uploaded.filename.lower().endswith(".pdf")
        for uploaded in uploaded_files
    ):
        raise _api.HTTPException(status_code=_api.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload one or more PDF files")

    try:
        for uploaded in uploaded_files:
            try:
                pdf_bytes = await uploaded.read()
            finally:
                await uploaded.close()
            if not pdf_bytes:
                raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Uploaded PDF '{uploaded.filename}' is empty")
            if len(pdf_bytes) > _api.MAX_UPLOAD_BYTES:
                raise _api.HTTPException(status_code=_api.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{uploaded.filename}' exceeds the 15 MB upload limit")
            if not pdf_bytes.startswith(b"%PDF-"):
                raise _api.HTTPException(status_code=_api.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"'{uploaded.filename}' is not a valid PDF file")
            _api.log_info("Ingestion", "Received an in-memory PDF upload")
            locator: str | None = None
            try:
                fhir_bundle = await _api.extract_pdf_to_fhir_bundle(pdf_bytes, patient_id, uploaded.filename)
                if not fhir_bundle.get("entry"):
                    raise _api.HTTPException(
                        status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"No supported clinical facts could be verified in {uploaded.filename}",
                    )
                _api.log_info("Ingestion", "Generated %d citation-backed FHIR resources", len(fhir_bundle.get("entry", [])))
                _api.log_info("Database", "Posting uploaded FHIR resources to local HAPI as a transaction")
                await _api.post_to_fhir(fhir_bundle)
                locator = (
                    await _api._store_encrypted_drive_pdf(patient_id, drive_connection, pdf_bytes, "source")
                    if storage_provider == "google_drive"
                    else await _api.asyncio.to_thread(_api.put_patient_pdf, patient_id, uploaded.filename, pdf_bytes, "source")
                )
                await _api.asyncio.to_thread(_api.add_document, patient_id, uploaded.filename, fhir_bundle, locator, _api.EXTRACTION_VERSION, storage_provider)
            except Exception:
                if storage_provider == "google_drive" and locator:
                    await _api.asyncio.to_thread(_api.google_drive.delete_file, drive_connection, locator)
                    await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, locator)
                elif locator:
                    await _api.asyncio.to_thread(_api.delete_s3_object, locator)
                raise
    except _api.HTTPException:
        raise
    except Exception as exc:
        component = "Database" if isinstance(exc, _api.FHIRRequestError) else "Ingestion"
        raise _api.IntegrationFailure(component, exc) from exc

    return await _api._load_workspace(patient_id)


@_api.app.post("/api/patient/google-drive/import")
async def import_google_drive_changes(request: _api.PatientCredentials, http_request: _api.Request) -> dict[str, _api.Any]:
    """Import direct-folder PDFs selected by the patient into encrypted Drive storage."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    storage_provider, connection = await _api.asyncio.gather(
        _api.asyncio.to_thread(_api.get_storage_preference, patient_id), _api.asyncio.to_thread(_api.get_drive_connection, patient_id),
    )
    if storage_provider != "google_drive" or connection is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="Connect and select Google Drive before importing Drive files")
    try:
        imported = await _api._import_drive_folder_changes(patient_id, connection)
    except _api.HTTPException:
        raise
    except Exception as exc:
        raise _api.IntegrationFailure("Google Drive import", exc) from exc
    workspace = await _api._load_workspace(patient_id)
    workspace["drive_sync_message"] = (
        f"{imported} Drive PDF{' was' if imported == 1 else 's were'} encrypted and imported."
        if imported else "No new Drive PDFs need importing."
    )
    return workspace


@_api.app.post("/api/patient/records")
async def save_patient_record(request: _api.ManualRecordRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    """Save a structured patient-entered record as FHIR R4 for dashboard and sharing."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    label, fhir_bundle = _api._manual_record_bundle(request, patient_id)
    try:
        await _api.post_to_fhir(fhir_bundle)
        await _api.asyncio.to_thread(_api.add_record, patient_id, request.category, label, fhir_bundle)
    except Exception as exc:
        raise _api.IntegrationFailure("FHIR persistence", exc) from exc
    return await _api._load_workspace(patient_id)


@_api.app.post("/api/patient/external-shl-import")
async def import_external_shl(request: _api.ExternalShlImportRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    """Persist a user-confirmed external SHL bundle, never its key, PIN, or JWE."""
    patient_id = await _api._authenticate_patient(None, None, http_request)
    try:
        serialized = _api.json.dumps(request.bundle, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Imported clinical data must be valid JSON") from exc
    if len(serialized.encode("utf-8")) > 5 * 1024 * 1024:
        raise _api.HTTPException(status_code=_api.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Imported clinical data exceeds the 5 MB limit")
    normalized = _api._normalize_external_shl_bundle(request.bundle, patient_id, request.source_host)
    if not normalized["entry"]:
        raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The shared file contains no supported FHIR R4 resources to import")
    try:
        await _api.asyncio.to_thread(
            _api.add_record,
            patient_id,
            "external-shl",
            f"External SMART Health Link from {request.source_host}",
            normalized,
        )
    except Exception as exc:
        raise _api.IntegrationFailure("External SHL import", exc) from exc
    _api.log_info("External SHL", "Imported %d user-confirmed external FHIR resources from %s", len(normalized["entry"]), request.source_host)
    return await _api._load_workspace(patient_id)
