"""Document helpers for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

async def _ensure_documents_extracted(patient_id: str, documents: list[_api.Any]) -> None:
    await _api._ensure_documents_extracted_service(
        patient_id, documents, _api.EXTRACTION_VERSION, _api._read_document_bytes,
        _api.extract_pdf_to_fhir_bundle, _api.update_document_extraction,
    )


async def _read_document_bytes(patient_id: str, document: _api.Any) -> bytes:
    return await _api._read_document_bytes_service(
        patient_id, document, _api.get_drive_connection, _api.get_drive_file_crypto,
        _api.decrypt_drive_pdf, _api.get_s3_pdf, _api.google_drive,
    )


async def _store_encrypted_drive_pdf(
    patient_id: str, connection: _api.Any, pdf_bytes: bytes, purpose: _api.Literal["source", "summary"],
) -> str:
    """Upload ciphertext to Drive and persist its KMS envelope before use."""
    encrypted = await _api.asyncio.to_thread(_api.encrypt_drive_pdf, patient_id, purpose, pdf_bytes)
    file_id = await _api.asyncio.to_thread(_api.google_drive.put_encrypted_blob, connection, encrypted.ciphertext, purpose)
    try:
        await _api.asyncio.to_thread(
            _api.save_drive_file_crypto, file_id, patient_id, encrypted.artifact_id, encrypted.purpose,
            encrypted.encrypted_data_key, encrypted.nonce, encrypted.plaintext_sha256, encrypted.algorithm,
        )
    except Exception:
        await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, file_id)
        raise
    return file_id


async def _replace_encrypted_drive_summary(
    patient_id: str, connection: _api.Any, report_bytes: bytes,
) -> str:
    """Create a replacement summary before its old Drive object is removed.

    A fresh object avoids a window where ciphertext has been replaced but RDS
    still holds the old wrapped key.  The caller updates the pointer first and
    then retires the old object.
    """
    return await _api._store_encrypted_drive_pdf(patient_id, connection, report_bytes, "summary")


async def _retire_plaintext_drive_file(patient_id: str, connection: _api.Any, file_id: str) -> None:
    """Delete a plaintext import source, retrying safely on the next workspace load."""
    await _api.asyncio.to_thread(_api.add_pending_drive_deletion, patient_id, file_id)
    try:
        await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, file_id)
    except Exception as exc:
        _api.log_warning("Google Drive", "Encrypted replacement is stored but plaintext cleanup is pending: %s", type(exc).__name__)
        return
    await _api.asyncio.to_thread(_api.clear_pending_drive_deletion, patient_id, file_id)


def _validate_drive_pdf(content: bytes, filename: str) -> None:
    if not content or not content.startswith(b"%PDF-"):
        raise _api.HTTPException(status_code=_api.status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=f"'{filename}' is not a valid PDF file")
    if len(content) > _api.MAX_UPLOAD_BYTES:
        raise _api.HTTPException(status_code=_api.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{filename}' exceeds the 15 MB upload limit")


async def _import_drive_folder_changes(patient_id: str, connection: _api.Any) -> int:
    """Encrypt legacy/new direct-folder PDFs and remove their plaintext originals."""
    documents = await _api.asyncio.to_thread(_api.list_documents, patient_id)
    drive_documents = [doc for doc in documents if getattr(doc, "storage_provider", "s3") == "google_drive"]
    imported = 0

    # Migrate a prior plaintext summary without regenerating its clinical content.
    if connection.summary_file_id and await _api.asyncio.to_thread(
        _api.get_drive_file_crypto, patient_id, connection.summary_file_id
    ) is None:
        legacy_summary = await _api.asyncio.to_thread(_api.google_drive.get_file_bytes, connection, connection.summary_file_id)
        _api._validate_drive_pdf(legacy_summary, "Clinical Summary.pdf")
        encrypted_summary_id = await _api._store_encrypted_drive_pdf(patient_id, connection, legacy_summary, "summary")
        saved = await _api.asyncio.to_thread(
            _api.set_drive_summary_file, patient_id, encrypted_summary_id, connection.summary_fingerprint or "",
        )
        if not saved:
            await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, encrypted_summary_id)
            await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, encrypted_summary_id)
            raise RuntimeError("Google Drive summary changed while its encryption migration was running")
        await _api._retire_plaintext_drive_file(patient_id, connection, connection.summary_file_id)
        imported += 1

    # Migrate previously stored plaintext Drive reports without changing their
    # existing FHIR facts or citation document IDs.
    for document in drive_documents:
        if await _api.asyncio.to_thread(_api.get_drive_file_crypto, patient_id, document.s3_key) is not None:
            continue
        plaintext = await _api.asyncio.to_thread(_api.google_drive.get_file_bytes, connection, document.s3_key)
        _api._validate_drive_pdf(plaintext, document.filename)
        encrypted_file_id = await _api._store_encrypted_drive_pdf(patient_id, connection, plaintext, "source")
        updated = await _api.asyncio.to_thread(_api.update_document_locator, patient_id, document.id, encrypted_file_id)
        if not updated:
            await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, encrypted_file_id)
            await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, encrypted_file_id)
            raise RuntimeError("Document changed while its Drive encryption migration was running")
        await _api._retire_plaintext_drive_file(patient_id, connection, document.s3_key)
        imported += 1

    documents = await _api.asyncio.to_thread(_api.list_documents, patient_id)
    known_ids = {doc.s3_key for doc in documents if getattr(doc, "storage_provider", "s3") == "google_drive"}
    drive_connection = await _api.asyncio.to_thread(_api.get_drive_connection, patient_id)
    if drive_connection and drive_connection.summary_file_id:
        known_ids.add(drive_connection.summary_file_id)
    pending_deletes = set(await _api.asyncio.to_thread(_api.list_pending_drive_deletions, patient_id))
    folder_files = await _api.asyncio.to_thread(_api.google_drive.list_folder_files, connection)
    for item in folder_files:
        if item.file_id in known_ids or item.file_id in pending_deletes or item.encrypted:
            continue
        if item.mime_type != "application/pdf" and not item.filename.lower().endswith(".pdf"):
            continue
        if item.size is not None and item.size > _api.MAX_UPLOAD_BYTES:
            raise _api.HTTPException(status_code=_api.status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"'{item.filename}' exceeds the 15 MB upload limit")
        plaintext = await _api.asyncio.to_thread(_api.google_drive.get_file_bytes, connection, item.file_id)
        _api._validate_drive_pdf(plaintext, item.filename)
        fhir_bundle = await _api.extract_pdf_to_fhir_bundle(plaintext, patient_id, item.filename)
        if not fhir_bundle.get("entry"):
            raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No supported clinical facts could be verified in {item.filename}")
        await _api.post_to_fhir(fhir_bundle)
        encrypted_file_id = await _api._store_encrypted_drive_pdf(patient_id, connection, plaintext, "source")
        try:
            await _api.asyncio.to_thread(_api.add_document, patient_id, item.filename, fhir_bundle, encrypted_file_id, _api.EXTRACTION_VERSION, "google_drive")
        except Exception:
            await _api.asyncio.to_thread(_api.google_drive.delete_file, connection, encrypted_file_id)
            await _api.asyncio.to_thread(_api.delete_drive_file_crypto, patient_id, encrypted_file_id)
            raise
        await _api._retire_plaintext_drive_file(patient_id, connection, item.file_id)
        imported += 1
    if imported:
        await _api.asyncio.to_thread(_api.revoke_patient_manifests, patient_id)
    return imported


@_api.app.exception_handler(_api.IntegrationFailure)
async def handle_integration_failure(_: _api.Request, exc: _api.IntegrationFailure) -> _api.JSONResponse:
    """Return a consistent integration error without exposing uploaded content."""
    _api.log_error(exc.component, exc.cause)
    return _api.JSONResponse(
        status_code=_api.status.HTTP_502_BAD_GATEWAY,
        content={"detail": str(exc)},
    )


async def _build_ingestion_resources(patient_id: str) -> list[dict[str, _api.Any]]:
    return await _api._build_ingestion_resources_service(patient_id)
