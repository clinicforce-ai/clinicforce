"""Doctor routes for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

@_api.app.post("/api/test/integration")
async def test_integration(request: _api.IntegrationTestRequest) -> dict[str, _api.Any]:
    """Exercise local ingestion, HAPI persistence, and encrypted SHL creation."""
    try:
        try:
            resources = await _api._build_ingestion_resources(request.patient_id)
        except Exception as exc:
            raise _api.IntegrationFailure("Ingestion", exc) from exc

        try:
            _api.log_info("Database", "Posting %d FHIR resources to local HAPI", len(resources))
            fhir_responses = []
            patient = next((resource for resource in resources if resource.get("resourceType") == "Patient"), None)
            if patient is None:
                raise ValueError("Ingestion did not produce a Patient resource")
            patient_response = await _api.post_to_fhir(patient)
            fhir_responses.append(patient_response)
            assigned_patient_id = patient_response.get("id")
            if not isinstance(assigned_patient_id, str) or not assigned_patient_id:
                raise ValueError(f"HAPI Patient create response did not include an id: {patient_response}")
            _api.log_info("Database", "HAPI Patient response: %s", patient_response)
            for resource in (item for item in resources if item is not patient):
                _api._replace_patient_reference(resource, request.patient_id, assigned_patient_id)
                response = await _api.post_to_fhir(resource)
                fhir_responses.append(response)
                _api.log_info("Database", "HAPI %s response: %s", resource["resourceType"], response)
            fhir_bundle = await _api.get_patient_records(assigned_patient_id)
            _api.log_info("Database", "Retrieved patient bundle with %d entries", len(fhir_bundle.get("entry", [])))
        except Exception as exc:
            raise _api.IntegrationFailure("Database", exc) from exc

        try:
            _api.log_info("Secure Sharing", "Generating a 32-byte AES-256-GCM key and encrypting FHIR bundle")
            shl_uri, qr_code_base64, manifest_id = _api._create_upload_shl(
                None, fhir_bundle, request.passcode
            )
            _api.log_info("Secure Sharing", "Created encrypted test share")
        except Exception as exc:
            raise _api.IntegrationFailure("Secure Sharing", exc) from exc

        return {
            "fhir_post_status": fhir_responses,
            "shl_uri": shl_uri,
            "qr_code_base64": qr_code_base64,
            "manifest_id": str(manifest_id),
        }
    except _api.IntegrationFailure as exc:
        _api.log_error(exc.component, exc.cause)
        raise _api.HTTPException(status_code=_api.status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@_api.app.post("/manifest/{manifest_id}")
async def retrieve_manifest(manifest_id: str, request: _api.ManifestRequest) -> dict[str, _api.Any]:
    """Resolve a SMART Health Link to encrypted FHIR R4 data.

    POST a recipient and, for protected shares, the passcode. ecw_compat needs no
    passcode. Return embedded JWE or a short-lived payload URL when the JWE exceeds
    embeddedLengthMax. 401 means an invalid PIN; 404 means unavailable, expired or
    revoked; 422 means an invalid request; 503 means payload storage is unavailable.
    A 200 response confirms retrieval only, not mobile decryption or chart import.
    """
    _api.log_info("Secure Sharing", "SHL manifest fetch attempt")
    try:
        manifest = await _api.asyncio.to_thread(_api.verify_manifest_pin, manifest_id, request.passcode or "")
    except _api.InvalidManifestPin as exc:
        _api.log_warning("Secure Sharing", "SHL manifest fetch rejected: invalid passcode")
        if exc.remaining_attempts == 0:
            _api.log_warning("Secure Sharing", "SHL share permanently disabled after lifetime PIN limit")
        return _api.JSONResponse(status_code=_api.status.HTTP_401_UNAUTHORIZED, content={"remainingAttempts": exc.remaining_attempts})
    if manifest is None:
        _api.log_warning("Secure Sharing", "SHL manifest fetch rejected: unavailable")
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Shared record is no longer available")

    file: dict[str, str] = {"contentType": "application/fhir+json;fhirVersion=4.0.1"}
    max_embedded = request.embeddedLengthMax
    if max_embedded is None or len(manifest["encrypted_jwe"]) <= max_embedded:
        file["embedded"] = manifest["encrypted_jwe"]
    else:
        try:
            object_key = await _api.asyncio.to_thread(_api.put_shl_payload, manifest_id, manifest["encrypted_jwe"])
            file["location"] = await _api.asyncio.to_thread(_api.presign_shl_payload, object_key)
        except Exception as exc:
            _api.log_error("Secure Sharing", exc)
            raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail="Shared record is temporarily unavailable") from exc

    return {
        "files": [
            file
        ]
    }


@_api.app.post("/api/doctor/decrypt-and-summarize")
async def decrypt_and_summarize(request: _api.DoctorSummaryRequest, response: _api.Response) -> dict[str, _api.Any]:
    """Build a dashboard from the stored QR manifest, never browser-supplied FHIR."""
    try:
        try:
            manifest = await _api.asyncio.to_thread(_api.verify_manifest_pin, request.manifest_id, request.passcode)
        except _api.InvalidManifestPin as exc:
            if exc.remaining_attempts == 0:
                _api.log_warning("Secure Sharing", "SHL share permanently disabled after lifetime PIN limit")
            raise _api.HTTPException(status_code=_api.status.HTTP_401_UNAUTHORIZED, detail="Invalid shared-record credentials") from exc
        if manifest is None:
            raise _api.HTTPException(status_code=_api.status.HTTP_401_UNAUTHORIZED, detail="Invalid shared-record credentials")
        _api.log_info("Clinical Summary", "Decrypting server-stored QR manifest")
        fhir_bundle = await _api.asyncio.to_thread(_api._decrypt_jwe_payload, manifest["encrypted_jwe"], request.key)
        _api.log_info("Clinical Summary", "Generating deterministic clinical narrative")
        summary = _api._with_source_download_links(await _api.summarize_fhir_bundle(fhir_bundle), str(request.manifest_id))
        patient_id = manifest.get("patient_id")
        connection = await _api.asyncio.to_thread(_api.get_drive_connection, str(patient_id)) if patient_id else None
        storage_provider = (
            await _api.asyncio.to_thread(_api.get_storage_preference, str(patient_id)) if connection else "s3"
        )
        if storage_provider == "google_drive" and connection and connection.summary_file_id:
            try:
                crypto = await _api.asyncio.to_thread(_api.get_drive_file_crypto, str(patient_id), connection.summary_file_id)
                if crypto is None:
                    raise RuntimeError("Drive summary is awaiting encryption migration")
                ciphertext = await _api.asyncio.to_thread(_api.google_drive.get_file_bytes, connection, connection.summary_file_id)
                report_bytes = await _api.asyncio.to_thread(_api.decrypt_drive_pdf, str(patient_id), crypto, ciphertext)
            except Exception as exc:
                # A patient may delete their Drive summary after making the QR. The
                # doctor can still view that already-authorized QR snapshot; no
                # Drive or S3 plaintext fallback is persisted.
                _api.log_warning("Google Drive", "Stored summary unavailable for clinician view: %s", type(exc).__name__)
                report_bytes = await _api.run_in_threadpool(_api.render_doctor_pdf_bytes, summary)
        else:
            report_bytes = await _api.run_in_threadpool(_api.render_doctor_pdf_bytes, summary)
        if patient_id and _api.insurance_beta_enabled():
            response.set_cookie(
                _api.CLINICIAN_SHARE_COOKIE,
                _api.insurance_golden.create_share_token(str(patient_id), request.manifest_id),
                max_age=_api.CLINICIAN_SHARE_MAX_AGE,
                httponly=True,
                secure=_api.PUBLIC_BASE_URL.startswith("https://"),
                samesite="lax",
                path="/api/doctor",
            )
            response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
        return {
            "summary": summary,
            "pdf_url": "data:application/pdf;base64," + _api.base64.b64encode(report_bytes).decode("ascii"),
        }
    except _api.HTTPException:
        raise
    except Exception as exc:
        failure = _api.IntegrationFailure("Clinical Summary", exc)
        _api.log_error(failure.component, failure.cause)
        raise _api.HTTPException(status_code=_api.status.HTTP_502_BAD_GATEWAY, detail=str(failure)) from exc
