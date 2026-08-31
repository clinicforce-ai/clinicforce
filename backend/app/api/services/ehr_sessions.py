"""HTTP-safe access to short-lived EHR sessions."""

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, status


async def live_session(
    request: Request,
    patient_id: str,
    cookie_name: str,
    get_live_session: Callable[[str, str], Any],
    error_type: type[Exception],
) -> Any:
    try:
        return get_live_session(request.cookies.get(cookie_name, ""), patient_id)
    except error_type as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def integration_error(exc: Exception, expired_session_status: int | None = None) -> HTTPException:
    if expired_session_status and "session has ended" in str(exc):
        return HTTPException(status_code=expired_session_status, detail=str(exc))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
