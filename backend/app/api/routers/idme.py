"""Patient ID.me sign-in and explicit account-link routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.schemas import IdmeStartRequest
from app.api.services.authentication import authenticate_patient
from app.api.settings import (
    IDME_OAUTH_STATE_COOKIE,
    IDME_OAUTH_STATE_COOKIE_PATH,
    PATIENT_SESSION_COOKIE,
    PATIENT_SESSION_MAX_AGE,
    PUBLIC_BASE_URL,
)
from app.core import clinic_store, google_drive, idme as idme_client
from app.core.patient_models import IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS
from app.core.request_address import request_address
from app.core.patient_store import (
    consume_idme_oidc_transaction,
    create_idme_patient_session,
    get_password_hash,
    get_patient_session,
    get_recent_patient_session,
    link_idme_identity,
    patient_has_idme_identity,
    record_idme_verification_event,
    save_idme_oidc_transaction,
    unlink_idme_identity,
)
from app.core.shl import verify_password
from app.logs.logger import log_warning


router = APIRouter(prefix="/api/patient/idme", tags=["patient-idme"])
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


async def _authenticate(request: Request) -> str:
    return await authenticate_patient(
        None,
        None,
        request,
        PATIENT_SESSION_COOKIE,
        PUBLIC_BASE_URL,
        get_patient_session,
        get_password_hash,
        verify_password,
        lambda _path, _credentials: None,
    )


def _set_patient_session(response: Response, patient_id: str, token: str) -> None:
    response.set_cookie(
        key=PATIENT_SESSION_COOKIE,
        value=token,
        max_age=PATIENT_SESSION_MAX_AGE,
        httponly=True,
        secure=PUBLIC_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/",
    )


def _callback_redirect(result: str) -> RedirectResponse:
    response = RedirectResponse(
        f"{PUBLIC_BASE_URL}/?patient&idme={result}",
        status_code=status.HTTP_303_SEE_OTHER,
        headers=_PRIVATE_HEADERS,
    )
    response.delete_cookie(
        IDME_OAUTH_STATE_COOKIE,
        path=IDME_OAUTH_STATE_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


async def _record_callback_failure(patient_id: str | None, intent: str, reason_code: str) -> None:
    try:
        await asyncio.to_thread(
            record_idme_verification_event, patient_id, intent, "failure", reason_code
        )
    except Exception:
        log_warning("ID.me", "Callback audit event could not be stored")


@router.get("/status")
async def idme_status(request: Request) -> JSONResponse:
    patient_id = None
    session_token = request.cookies.get(PATIENT_SESSION_COOKIE, "")
    if session_token:
        patient_id = await asyncio.to_thread(get_patient_session, session_token)
    linked = bool(patient_id and await asyncio.to_thread(patient_has_idme_identity, patient_id))
    auth_recent = bool(
        patient_id
        and await asyncio.to_thread(get_recent_patient_session, session_token) == patient_id
    )
    return JSONResponse(
        {
            "enabled": idme_client.enabled(),
            "configured": idme_client.configured(),
            "linked": linked,
            "auth_recent": auth_recent,
        },
        headers=_PRIVATE_HEADERS,
    )


@router.post("/start")
async def start_idme(request_body: IdmeStartRequest, request: Request) -> JSONResponse:
    if not idme_client.enabled():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ID.me sign-in is not available")
    patient_id = await _authenticate(request) if request_body.intent == "link" else None
    if patient_id is not None:
        recent_patient_id = await asyncio.to_thread(
            get_recent_patient_session, request.cookies.get(PATIENT_SESSION_COOKIE, "")
        )
        if recent_patient_id != patient_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sign in again before linking ID.me",
            )
    if not await asyncio.to_thread(
        clinic_store.allow_action, "idme-authorization-start", request_address(request), 20, 15 * 60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many ID.me sign-in attempts. Please try again later.",
            headers={**_PRIVATE_HEADERS, "Retry-After": str(15 * 60)},
        )
    try:
        started = await idme_client.begin_authorization()
    except idme_client.IdmeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ID.me sign-in is temporarily unavailable") from exc
    await asyncio.to_thread(
        save_idme_oidc_transaction,
        request_body.intent,
        patient_id,
        started.state,
        started.browser_token,
        started.nonce,
        started.pkce_verifier,
    )
    response = JSONResponse({"authorization_url": started.authorization_url}, headers=_PRIVATE_HEADERS)
    response.set_cookie(
        key=IDME_OAUTH_STATE_COOKIE,
        value=started.browser_token,
        max_age=IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path=IDME_OAUTH_STATE_COOKIE_PATH,
    )
    return response


@router.get("/callback")
async def idme_callback(
    request: Request,
    state: str = Query(default="", max_length=256),
    code: str = Query(default="", max_length=8_192),
    error: str = Query(default="", max_length=256),
) -> RedirectResponse:
    browser_token = request.cookies.get(IDME_OAUTH_STATE_COOKIE, "")
    transaction = await asyncio.to_thread(consume_idme_oidc_transaction, state, browser_token)
    if transaction is None:
        return _callback_redirect("error")
    if error or not code:
        await _record_callback_failure(transaction.patient_id, transaction.intent, "provider_denied")
        return _callback_redirect("error")
    try:
        identity = await idme_client.complete_authorization(code, transaction.pkce_verifier, transaction.nonce)
        if transaction.intent == "link":
            session_token = request.cookies.get(PATIENT_SESSION_COOKIE, "")
            if not transaction.patient_id or not session_token:
                await _record_callback_failure(transaction.patient_id, transaction.intent, "session_changed")
                return _callback_redirect("error")
            linked = await asyncio.to_thread(
                link_idme_identity,
                transaction.patient_id,
                session_token,
                identity.issuer,
                identity.subject,
            )
            return _callback_redirect("linked" if linked else "error")
        authenticated = await asyncio.to_thread(
            create_idme_patient_session, identity.issuer, identity.subject
        )
        if authenticated is None:
            await _record_callback_failure(None, transaction.intent, "identity_not_linked")
            return _callback_redirect("not-linked")
        patient_id, session_token = authenticated
    except Exception as exc:
        # Never log callback parameters, tokens, claims, subjects, or provider responses.
        log_warning("ID.me", "Callback failed safely (%s)", type(exc).__name__)
        await _record_callback_failure(transaction.patient_id, transaction.intent, "verification_failed")
        return _callback_redirect("error")
    response = _callback_redirect("connected")
    _set_patient_session(response, patient_id, session_token)
    return response


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_idme(request: Request) -> Response:
    patient_id = await _authenticate(request)
    recent_patient_id = await asyncio.to_thread(
        get_recent_patient_session, request.cookies.get(PATIENT_SESSION_COOKIE, "")
    )
    if recent_patient_id != patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sign in again before disconnecting ID.me",
        )
    result = await asyncio.to_thread(unlink_idme_identity, patient_id, google_drive.enabled())
    if result == "last_login":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Add another sign-in method before disconnecting ID.me",
        )
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=_PRIVATE_HEADERS)
    response.delete_cookie(
        PATIENT_SESSION_COOKIE,
        path="/",
        secure=PUBLIC_BASE_URL.startswith("https://"),
        httponly=True,
        samesite="lax",
    )
    return response
