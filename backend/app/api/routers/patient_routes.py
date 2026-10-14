"""Patient routes for the ClinicForce API."""
from __future__ import annotations

from app.api import application as _api

@_api.app.post("/api/patient/register")
async def register_patient(request: _api.PatientCredentials) -> _api.JSONResponse:
    """Create a local patient account for the persistent document workspace."""
    patient_id, password = _api._validate_patient_credentials(request.patient_id, request.password)
    if len(password) < 12:
        raise _api.HTTPException(status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must contain at least 12 characters")
    created = await _api.asyncio.to_thread(_api.create_patient, patient_id, _api.hash_password(password))
    if not created:
        raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail="A patient account with this ID already exists")
    response = _api.JSONResponse(_api._workspace_payload(patient_id, [], [], "s3", False))
    await _api._set_patient_session(response, patient_id)
    return response


@_api.app.post("/api/patient/login")
async def login_patient(request: _api.PatientCredentials) -> _api.JSONResponse:
    patient_id = await _api._authenticate_patient(request.patient_id, request.password)
    response = _api.JSONResponse(await _api._load_workspace(patient_id))
    await _api._set_patient_session(response, patient_id)
    return response


@_api.app.get("/api/patient/session")
async def get_current_patient_session(request: _api.Request) -> dict[str, _api.Any]:
    patient_id = await _api._authenticate_patient(None, None, request)
    return await _api._load_workspace(patient_id)


@_api.app.get("/api/patient/profile")
async def get_patient_profile_endpoint(request: _api.Request) -> dict[str, str | None]:
    patient_id = await _api._authenticate_patient(None, None, request)
    profile, suggestions = await _api.asyncio.gather(
        _api.asyncio.to_thread(_api.get_patient_profile, patient_id),
        _api.asyncio.to_thread(_api.get_patient_profile_suggestions, patient_id),
    )
    return _api._patient_profile_payload(profile, suggestions)


@_api.app.put("/api/patient/profile")
async def save_patient_profile(request: _api.PatientProfileRequest, http_request: _api.Request) -> dict[str, _api.Any]:
    """Save the minimum canonical demographics required for external-EHR matching."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    profile = await _api.asyncio.to_thread(
        _api.update_patient_profile,
        patient_id,
        request.given_name,
        request.family_name,
        request.birth_date.isoformat(),
        request.administrative_gender,
    )
    if profile is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Patient account not found")
    suggestions = await _api.asyncio.to_thread(_api.get_patient_profile_suggestions, patient_id)
    return _api._patient_profile_payload(profile, suggestions)


@_api.app.post("/api/patient/logout")
async def logout_patient(request: _api.Request) -> _api.Response:
    await _api.asyncio.to_thread(_api.revoke_patient_session, request.cookies.get(_api.PATIENT_SESSION_COOKIE, ""))
    _api.epic.clear_live_session(request.cookies.get(_api.EPIC_LIVE_SESSION_COOKIE, ""))
    _api.ecw.clear_live_session(request.cookies.get(_api.ECW_LIVE_SESSION_COOKIE, ""))
    response = _api.Response(status_code=_api.status.HTTP_204_NO_CONTENT)
    response.delete_cookie(_api.PATIENT_SESSION_COOKIE, path="/")
    response.delete_cookie(
        _api.IDME_OAUTH_STATE_COOKIE,
        path=_api.IDME_OAUTH_STATE_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    _api._clear_epic_cookies(response)
    _api._clear_ecw_cookies(response)
    return response


@_api.app.get("/api/patient/epic/endpoints")
async def epic_endpoints(request: _api.Request, q: str = "") -> dict[str, list[dict[str, _api.Any]]]:
    """Search the locally mirrored, non-patient Epic hospital catalog."""
    await _api._authenticate_patient(None, None, request)
    endpoints = await _api.asyncio.to_thread(_api.search_epic_endpoints, q, 25)
    return {"endpoints": [
        {"id": endpoint.id, "name": endpoint.name, "aliases": endpoint.aliases, "addresses": endpoint.addresses}
        for endpoint in endpoints
    ]}


@_api.app.post("/api/patient/epic/authorize")
async def authorize_epic(request: _api.EpicAuthorizeRequest, http_request: _api.Request) -> _api.JSONResponse:
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    endpoint = await _api.asyncio.to_thread(_api.get_epic_endpoint, request.endpoint_id)
    if endpoint is None:
        raise _api.HTTPException(status_code=_api.status.HTTP_404_NOT_FOUND, detail="Choose a hospital from the ClinicForce Epic directory")
    try:
        started = await _api.epic.begin_authorization(
            endpoint.fhir_base, endpoint.name, patient_id, f"{_api.PUBLIC_BASE_URL}/api/patient/epic/callback", popup=request.popup,
        )
    except _api.epic.EpicError as exc:
        raise _api._epic_error(exc) from exc
    response = _api.JSONResponse({"authorization_url": started.authorization_url})
    _api._set_epic_cookie(response, _api.EPIC_OAUTH_STATE_COOKIE, started.state_cookie, _api.epic.OAUTH_STATE_MAX_AGE_SECONDS)
    return response


@_api.app.get("/api/patient/ecw/jwks")
async def ecw_jwks() -> dict[str, list[dict[str, _api.Any]]]:
    """Public JWKS registered in healow for private_key_jwt authentication."""
    try:
        return _api.ecw.public_jwks()
    except _api.ecw.EcwError as exc:
        raise _api.HTTPException(status_code=_api.status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@_api.app.get("/.well-known/jwks.json", include_in_schema=False)
async def ecw_registered_jwks() -> dict[str, list[dict[str, _api.Any]]]:
    """Serve the stable public JWKS URL registered with eClinicalWorks/healow."""
    return await _api.ecw_jwks()


@_api.app.post("/api/patient/ecw/authorize")
async def authorize_ecw(request: _api.EcwAuthorizeRequest, http_request: _api.Request) -> _api.JSONResponse:
    """Resolve one OEM code to its eCW FHIR endpoint and start SMART + PKCE."""
    patient_id = await _api._authenticate_patient(request.patient_id, request.password, http_request)
    try:
        started = await _api.ecw.begin_authorization(
            request.practice_code, patient_id, f"{_api.PUBLIC_BASE_URL}/api/patient/ecw/callback", popup=request.popup,
        )
    except _api.ecw.EcwError as exc:
        raise _api.HTTPException(status_code=_api.status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    response = _api.JSONResponse({"authorization_url": started.authorization_url})
    _api._set_epic_cookie(response, _api.ECW_OAUTH_STATE_COOKIE, started.state_cookie, _api.ecw.OAUTH_STATE_MAX_AGE_SECONDS)
    return response


@_api.app.get("/api/patient/epic/callback")
async def epic_callback(request: _api.Request, state: str = "", code: str = "", error: str = "") -> _api.Response:
    """Exchange a SMART code server-side; tokens never reach the browser."""
    state_cookie = request.cookies.get(_api.EPIC_OAUTH_STATE_COOKIE, "")
    popup = _api.epic.popup_requested(state_cookie)
    try:
        patient_id = await _api._authenticate_patient(None, None, request)
        if error or not code:
            raise _api.epic.EpicError("Epic authorization was cancelled or denied")
        live_session_id, popup = await _api.epic.complete_authorization(
            state_cookie, state, code, f"{_api.PUBLIC_BASE_URL}/api/patient/epic/callback", patient_id,
        )
    except (_api.HTTPException, _api.epic.EpicError):
        if popup:
            response = _api._epic_popup_result(False)
            _api._clear_epic_cookies(response)
            return response
        response = _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?epic=error", status_code=_api.status.HTTP_303_SEE_OTHER)
        _api._clear_epic_cookies(response)
        return response
    if popup:
        response = _api._epic_popup_result(True)
    else:
        response = _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?epic=connected", status_code=_api.status.HTTP_303_SEE_OTHER)
    response.delete_cookie(_api.EPIC_OAUTH_STATE_COOKIE, path="/")
    _api._set_epic_cookie(response, _api.EPIC_LIVE_SESSION_COOKIE, live_session_id, _api.epic.LIVE_SESSION_MAX_AGE_SECONDS)
    return response


@_api.app.get("/api/patient/ecw/callback")
async def ecw_callback(request: _api.Request, state: str = "", code: str = "", error: str = "") -> _api.Response:
    """Complete the confidential SMART authorization code + PKCE exchange."""
    state_cookie = request.cookies.get(_api.ECW_OAUTH_STATE_COOKIE, "")
    popup = _api.ecw.popup_requested(state_cookie)
    try:
        patient_id = await _api._authenticate_patient(None, None, request)
        if error or not code:
            raise _api.ecw.EcwError("eClinicalWorks authorization was cancelled or denied")
        live_session_id, popup = await _api.ecw.complete_authorization(
            state_cookie, state, code, f"{_api.PUBLIC_BASE_URL}/api/patient/ecw/callback", patient_id,
        )
    except (_api.HTTPException, _api.ecw.EcwError):
        if popup:
            response = _api._ecw_popup_result(False)
            _api._clear_ecw_cookies(response)
            return response
        response = _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?ecw=error", status_code=_api.status.HTTP_303_SEE_OTHER)
        _api._clear_ecw_cookies(response)
        return response
    if popup:
        response = _api._ecw_popup_result(True)
    else:
        response = _api.RedirectResponse(f"{_api.PUBLIC_BASE_URL}/?ecw=connected", status_code=_api.status.HTTP_303_SEE_OTHER)
    response.delete_cookie(_api.ECW_OAUTH_STATE_COOKIE, path="/")
    _api._set_epic_cookie(response, _api.ECW_LIVE_SESSION_COOKIE, live_session_id, _api.ecw.LIVE_SESSION_MAX_AGE_SECONDS)
    return response


@_api.app.get("/api/patient/ecw/record")
async def ecw_record(request: _api.Request) -> dict[str, _api.Any]:
    """Fetch an authorized eCW record into a short-lived review snapshot only."""
    patient_id = await _api._authenticate_patient(None, None, request)
    session = await _api._live_ecw_session(request, patient_id)
    try:
        bundle, availability = await _api.ecw.fetch_record(session)
    except _api.ecw.EcwError as exc:
        raise _api._ecw_error(exc) from exc
    _api.log_info("eClinicalWorks", "Retrieved %d FHIR resources for patient review from practice %s", len(bundle["entry"]), session.practice_code)
    return {"bundle": bundle, "availability": availability, "source": _api.ecw.source_metadata(session)}


@_api.app.post("/api/patient/ecw/import")
async def import_ecw_record(request: _api.Request) -> _api.JSONResponse:
    """Persist the last reviewed eCW snapshot only after an explicit confirmation."""
    patient_id = await _api._authenticate_patient(None, None, request)
    session = await _api._live_ecw_session(request, patient_id)
    if session.review_bundle is None:
        raise _api.HTTPException(
            status_code=_api.status.HTTP_409_CONFLICT,
            detail="Review the eClinicalWorks data before importing it into ClinicForce",
        )
    normalized = _api._normalize_ecw_bundle(session.review_bundle, patient_id, session.practice_code)
    if not normalized["entry"]:
        raise _api.HTTPException(
            status_code=_api.status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="eClinicalWorks returned no supported clinical resources to import",
        )
    try:
        await _api.asyncio.to_thread(
            _api.add_record,
            patient_id,
            "ecw",
            f"eClinicalWorks / healow import from practice {session.practice_code}",
            normalized,
        )
    except Exception as exc:
        raise _api.IntegrationFailure("eClinicalWorks import", exc) from exc
    _api.log_info("eClinicalWorks", "Imported %d user-confirmed FHIR resources from practice %s", len(normalized["entry"]), session.practice_code)
    _api.ecw.clear_live_session(request.cookies.get(_api.ECW_LIVE_SESSION_COOKIE, ""))
    response = _api.JSONResponse(await _api._load_workspace(patient_id))
    response.delete_cookie(_api.ECW_LIVE_SESSION_COOKIE, path="/")
    return response


@_api.app.delete("/api/patient/ecw/live-session")
async def disconnect_ecw(request: _api.Request) -> _api.Response:
    """Discard the short-lived token and unconfirmed review snapshot."""
    patient_id = await _api._authenticate_patient(None, None, request)
    await _api._live_ecw_session(request, patient_id)
    _api.ecw.clear_live_session(request.cookies.get(_api.ECW_LIVE_SESSION_COOKIE, ""))
    response = _api.Response(status_code=_api.status.HTTP_204_NO_CONTENT)
    response.delete_cookie(_api.ECW_LIVE_SESSION_COOKIE, path="/")
    return response


@_api.app.get("/api/patient/epic/record")
async def epic_record(request: _api.Request) -> dict[str, _api.Any]:
    patient_id = await _api._authenticate_patient(None, None, request)
    session = await _api._live_epic_session(request, patient_id)
    try:
        bundle, availability = await _api.epic.fetch_record(session)
    except _api.epic.EpicError as exc:
        raise _api._epic_error(exc) from exc
    await _api._backfill_profile_from_live_epic(patient_id, bundle)
    return {"bundle": bundle, "availability": availability, "source": _api.epic.source_metadata(session)}


@_api.app.get("/api/patient/epic/resources/{resource_type}")
async def epic_resource(resource_type: str, request: _api.Request) -> dict[str, _api.Any]:
    patient_id = await _api._authenticate_patient(None, None, request)
    session = await _api._live_epic_session(request, patient_id)
    try:
        bundle = await _api.epic.fetch_resource_type(session, resource_type)
    except _api.epic.EpicError as exc:
        raise _api._epic_error(exc) from exc
    return {"bundle": bundle, "source": _api.epic.source_metadata(session)}


@_api.app.get("/api/patient/epic/documents/{document_id}")
async def epic_document(document_id: str, request: _api.Request) -> _api.Response:
    patient_id = await _api._authenticate_patient(None, None, request)
    session = await _api._live_epic_session(request, patient_id)
    try:
        content, filename = await _api.epic.stream_document(session, document_id)
    except _api.epic.EpicError as exc:
        raise _api._epic_error(exc) from exc
    return _api.Response(content=content, media_type="application/pdf", headers=_api._download_headers(filename))


@_api.app.delete("/api/patient/epic/live-session")
async def disconnect_epic(request: _api.Request) -> _api.Response:
    patient_id = await _api._authenticate_patient(None, None, request)
    # Check ownership before removal so a forged cookie cannot affect another account.
    await _api._live_epic_session(request, patient_id)
    _api.epic.clear_live_session(request.cookies.get(_api.EPIC_LIVE_SESSION_COOKIE, ""))
    response = _api.Response(status_code=_api.status.HTTP_204_NO_CONTENT)
    response.delete_cookie(_api.EPIC_LIVE_SESSION_COOKIE, path="/")
    return response
