"""Patient credential and cookie-session policy."""

import asyncio
from collections.abc import Callable

from fastapi import HTTPException, Request, status


def validate_credentials(patient_id: str | None, password: str | None) -> tuple[str, str]:
    normalized_id = (patient_id or "").strip()
    if not normalized_id or not password:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Patient ID and password are required")
    return normalized_id, password


async def authenticate_patient(
    patient_id: str | None,
    password: str | None,
    request: Request | None,
    session_cookie: str,
    public_base_url: str,
    get_patient_session: Callable[[str], str | None],
    get_password_hash: Callable[[str], str | None],
    verify_password: Callable[[str, str], bool],
    log_rejection: Callable[[str, str], None],
) -> str:
    """Authorize a matching session cookie or a password credential pair."""
    token = ""
    if request is not None:
        token = request.cookies.get(session_cookie, "")
        session_patient_id = await asyncio.to_thread(get_patient_session, token)
        if session_patient_id:
            origin = request.headers.get("origin", "").rstrip("/")
            if origin and origin != public_base_url:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Patient session requests must come from ClinicForce")
            if patient_id and patient_id.strip() != session_patient_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Patient session does not match this workspace")
            return session_patient_id
        if not patient_id and not password:
            log_rejection(request.url.path, "absent" if token else "absent")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to open your workspace")
    normalized_id, supplied_password = validate_credentials(patient_id, password)
    stored_hash = await asyncio.to_thread(get_password_hash, normalized_id)
    if stored_hash is None or not verify_password(supplied_password, stored_hash):
        if request is not None:
            log_rejection(request.url.path, "rejected")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid patient ID or password")
    return normalized_id
