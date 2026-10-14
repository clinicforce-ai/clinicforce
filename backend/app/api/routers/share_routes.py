"""Share routes for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

@_api.app.post("/api/patient/generate-link")
async def generate_patient_link(request: _api.GeneratePatientLinkRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    """Create an encrypted FHIR snapshot and QR, expiring after three hours by default.

    Standard mode requires a four-digit PIN and opens the ClinicForce viewer.
    ecw_compat creates a bare, no-passcode SMART Health Link for a receiving EHR's
    QR importer. It does not write to eCW or confirm import; the receiver resolves
    POST /manifest/{token} and decrypts the returned JWE with the QR-carried key.
    """
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    protected_share = request.share_mode == "standard"
    if protected_share and (not request.passcode or len(request.passcode) != 4):
        raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a four-digit sharing passcode")
    if not protected_share and request.passcode is not None:
        raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The eClinicalMobile compatibility QR cannot include a passcode")
    # Reconcile patient-managed Drive deletions before this snapshot is encrypted.
    await _api._load_workspace(patient_id)
    documents, records = await _api.asyncio.gather(
        _api.asyncio.to_thread(_api.list_documents, patient_id), _api.asyncio.to_thread(_api.list_records, patient_id)
    )
    try:
        await _api._ensure_documents_extracted(patient_id, documents)
        documents = await _api.asyncio.to_thread(_api.list_documents, patient_id)
        workspace_bundle = _api._combine_document_bundles(documents, records)

        # MyChart is deliberately fetched only at the moment the patient creates
        # the one clinician QR. ClinicForce never stores Epic clinical resources,
        # tokens, or document attachments; the separate patient profile may retain
        # only the minimum matching demographics explicitly backfilled below.
        epic_snapshot: dict[str, _api.Any] | None = None
        profile = await _api.asyncio.to_thread(_api.get_patient_profile, patient_id)
        if http_request.cookies.get(_api.EPIC_LIVE_SESSION_COOKIE):
            session = await _api._live_epic_session(http_request, patient_id)
            try:
                epic_bundle, _ = await _api.epic.fetch_record(session)
            except _api.epic.EpicError as exc:
                raise _api._epic_error(exc) from exc
            profile = await _api._backfill_profile_from_live_epic(patient_id, epic_bundle)
            if epic_bundle.get("entry"):
                epic_snapshot = _api._epic_share_snapshot(epic_bundle, _api.epic.source_metadata(session))

        fhir_bundle = _api.build_exchange_bundle(
            patient_id, documents, records, epic_snapshot, profile,
            ecw_compat=(request.share_mode == "ecw_compat")
        )
        # The exchange bundle always contains Patient.  Do not issue a QR that
        # has no clinical/document content beyond that identity shell.
        if len(fhir_bundle["entry"]) == 1:
            raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No verified clinical facts are available to share")
        exchange_patient = fhir_bundle["entry"][0]["resource"]
        if not _api._has_matchable_patient_demographics(exchange_patient):
            raise _api.HTTPException(
                status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Complete the patient's legal name and date of birth in the demographic profile before creating an external-EHR share.",
            )
        shl_uri, qr_code_base64, manifest_id = await _api.asyncio.to_thread(
            _api._create_upload_shl,
            patient_id,
            fhir_bundle,
            request.passcode if protected_share else None,
            "local" if protected_share else "ecw_compat",
            protected_share,
        )
        # Drive patients receive one stable, replaced-on-new-QR PDF in their own
        # ClinicForce folder. S3 patients retain the existing non-persisted report flow.
        if await _api.asyncio.to_thread(_api.get_storage_preference, patient_id) == "google_drive":
            connection = await _api.asyncio.to_thread(_api.get_drive_connection, patient_id)
            if connection is None:
                raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="Reconnect Google Drive before creating a share")
            # The stable Drive summary remains limited to the patient workspace.
            # Live Epic data is visible only in the encrypted QR snapshot that the
            # patient has just chosen to share with a clinician.
            fingerprint = _api.hashlib.sha256(_api.json.dumps(workspace_bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            summary_exists = bool(connection.summary_file_id) and await _api.asyncio.to_thread(
                _api.google_drive.file_exists, connection, connection.summary_file_id
            )
            summary_crypto = (
                await _api.asyncio.to_thread(_api.get_drive_file_crypto, patient_id, connection.summary_file_id)
                if summary_exists and connection.summary_file_id else None
            )
            if connection.summary_fingerprint != fingerprint or not summary_exists or summary_crypto is None:
                # The stored Drive PDF keeps report/page citations but deliberately
                # omits QR-bound download capabilities. The current clinician
                # dashboard adds those just-in-time after QR/PIN verification.
                # This lets the one Drive PDF remain unchanged for an unchanged
                # patient record while preserving access control for source files.
                summary = await _api.summarize_fhir_bundle(workspace_bundle)
                report_bytes = await _api.run_in_threadpool(_api.render_doctor_pdf_bytes, summary)
                previous_summary_file_id = connection.summary_file_id
                summary_file_id = await _api._replace_encrypted_drive_summary(patient_id, connection, report_bytes)
                await _api.asyncio.to_thread(_api.set_drive_summary_file, patient_id, summary_file_id, fingerprint)
                if previous_summary_file_id and previous_summary_file_id != summary_file_id:
                    await _api._retire_plaintext_drive_file(patient_id, connection, previous_summary_file_id)
        _api.log_info(
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
    except _api.HTTPException:
        raise
    except Exception as exc:
        raise _api.IntegrationFailure("Secure Sharing", exc) from exc


@_api.app.post("/api/patient/documents/{document_id}/download")
async def download_patient_document(document_id: int, request: _api.PatientCredentials, http_request: _api.Request) -> _api.Response:
    """Return one saved source PDF after re-authenticating the patient."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    document = await _api.asyncio.to_thread(_api.get_document, patient_id, document_id)
    if document is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        content = await _api._read_document_bytes(patient_id, document)
    except Exception as exc:
        raise _api.IntegrationFailure("Document storage", exc) from exc
    return _api.Response(
        content=content,
        media_type="application/pdf",
        headers=_api._download_headers(document.filename),
    )


@_api.app.post("/api/patient/documents/bulk-download")
async def bulk_download_patient_documents(request: _api.DocumentBatchRequest, http_request: _api.Request) -> _api.Response:
    """Return patient-selected, decrypted source PDFs as one private ZIP download."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    documents = await _api.asyncio.gather(*[
        _api.asyncio.to_thread(_api.get_document, patient_id, document_id) for document_id in request.document_ids
    ])
    if any(document is None for document in documents):
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="One or more selected reports no longer exist")
    try:
        contents = await _api.asyncio.gather(*[
            _api._read_document_bytes(patient_id, document) for document in documents if document is not None
        ])
        if sum(len(content) for content in contents) > _api.MAX_BULK_DOWNLOAD_BYTES:
            raise _api.HTTPException(
                status_code=_api.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Selected reports exceed the 150 MB bulk-download limit",
            )
        archive = _api.BytesIO()
        used_names: set[str] = set()
        with _api.ZipFile(archive, "w", compression=_api.ZIP_DEFLATED) as zip_file:
            for document, content in zip(documents, contents, strict=True):
                assert document is not None
                zip_file.writestr(_api._zip_entry_name(document.filename, used_names), content)
    except _api.HTTPException:
        raise
    except Exception as exc:
        raise _api.IntegrationFailure("Document storage", exc) from exc
    return _api.Response(content=archive.getvalue(), media_type="application/zip", headers=_api._zip_download_headers())


async def _delete_patient_document(patient_id: str, document: _api.Any) -> None:
    """Remove one RDS-owned document from its backing store and envelope registry."""
    if getattr(document, "storage_provider", "s3") == "google_drive":
        connection = await _api.asyncio.to_thread(_api.get_drive_connection, patient_id)
        if connection is None:
            raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="Reconnect Google Drive before deleting this report")
        await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, document.s3_key)
        await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, document.s3_key)
    else:
        await _api.asyncio.to_thread(_api.delete_s3_object, document.s3_key)
    removed = await _api.asyncio.to_thread(_api.remove_documents, patient_id, [document.id])
    if not removed:
        raise RuntimeError("Document was not removed from ClinicForce")


@_api.app.delete("/api/patient/documents/{document_id}")
async def delete_patient_document(document_id: int, request: _api.PatientCredentials, http_request: _api.Request) -> dict[str, _api.Any]:
    """Delete a patient-selected source from its chosen storage and revoke old QR shares."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    document = await _api.asyncio.to_thread(_api.get_document, patient_id, document_id)
    if document is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Report not found")
    try:
        await _api._delete_patient_document(patient_id, document)
        await _api.asyncio.to_thread(_api.revoke_patient_manifests, patient_id)
    except _api.HTTPException:
        raise
    except Exception as exc:
        raise _api.IntegrationFailure("Document deletion", exc) from exc
    workspace = await _api._load_workspace(patient_id)
    workspace["drive_sync_message"] = "Report deleted. Existing QR codes were revoked."
    return workspace


@_api.app.post("/api/patient/documents/bulk-delete")
async def bulk_delete_patient_documents(request: _api.DocumentBatchRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    """Delete all selected reports and invalidate QR manifests once at the end."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    documents = await _api.asyncio.gather(*[
        _api.asyncio.to_thread(_api.get_document, patient_id, document_id) for document_id in request.document_ids
    ])
    if any(document is None for document in documents):
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="One or more selected reports no longer exist")
    try:
        for document in documents:
            assert document is not None
            await _api._delete_patient_document(patient_id, document)
        await _api.asyncio.to_thread(_api.revoke_patient_manifests, patient_id)
    except _api.HTTPException:
        raise
    except Exception as exc:
        # A successful deletion changes the record set even if a later one fails,
        # so invalidate existing shares before surfacing the recoverable error.
        await _api.asyncio.to_thread(_api.revoke_patient_manifests, patient_id)
        raise _api.IntegrationFailure("Document deletion", exc) from exc
    workspace = await _api._load_workspace(patient_id)
    count = len(documents)
    workspace["drive_sync_message"] = f"{count} report{' was' if count == 1 else 's were'} deleted. Existing QR codes were revoked."
    return workspace


@_api.app.get("/api/doctor/documents/{document_id}/download")
async def download_doctor_document(document_id: int, token: str) -> _api.Response:
    """Download a cited source only if it belongs to the QR manifest's patient."""
    try:
        manifest_id = _api.verify_document_download_token(token, document_id)
    except ValueError as exc:
        raise _api.HTTPException(status_code=_api.status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    document = await _api.asyncio.to_thread(_api.get_document_by_id, document_id)
    if document is None:
        # Do not distinguish a stale/deleted ID from an unauthorized one.
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Source report is no longer available")
    manifest = await _api.asyncio.to_thread(_api.get_manifest, manifest_id)
    if manifest is None or not manifest.get("patient_id"):
        raise _api.HTTPException(status_code=_api.status.HTTP_403_FORBIDDEN, detail="The shared record is no longer available")
    patient_id = str(manifest["patient_id"])
    owned_document = await _api.asyncio.to_thread(_api.get_document, patient_id, document_id)
    if owned_document is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_403_FORBIDDEN, detail="This source is not part of the shared record")
    try:
        content = await _api._read_document_bytes(patient_id, owned_document)
    except Exception as exc:
        raise _api.IntegrationFailure("Document storage", exc) from exc
    return _api.Response(content=content, media_type="application/pdf", headers=_api._download_headers(owned_document.filename))
