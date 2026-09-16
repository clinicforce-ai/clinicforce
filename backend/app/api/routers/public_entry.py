"""Pretty, private link entry points; register after the application's API routes."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.core import clinic_store, intake_links_store


router = APIRouter(include_in_schema=False)
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def _not_found() -> HTTPException:
    return HTTPException(404, "This clinic link was not found. Check the link or contact the clinic.", headers=PRIVATE_HEADERS)


def _redirect(**query: str) -> RedirectResponse:
    return RedirectResponse("/?" + urlencode(query), status_code=303, headers=PRIVATE_HEADERS)


@router.api_route("/join/{token}", methods=["GET", "HEAD"])
async def staff_invitation_entry(token: str):
    if not TOKEN_PATTERN.fullmatch(token) or not await run_in_threadpool(clinic_store.get_invitation, token):
        raise _not_found()
    # The form explains expired/accepted invitations without consuming them on GET.
    return _redirect(join=token)


@router.api_route("/{clinic_code}/{token}", methods=["GET", "HEAD"])
async def patient_upload_entry(clinic_code: str, token: str):
    if not (clinic_store.valid_npi(clinic_code) or clinic_store.valid_legacy_code(clinic_code)) or not TOKEN_PATTERN.fullmatch(token):
        raise _not_found()
    try:
        await run_in_threadpool(intake_links_store.metadata, clinic_code, token)
    except intake_links_store.UploadLinkError as error:
        if error.status != 410:
            raise _not_found() from None
        # The private form can explain expiration/revocation; it never exposes a card.
    return _redirect(clinic=clinic_code, upload=token)


@router.api_route("/{clinic_code}", methods=["GET", "HEAD"])
async def clinic_entry(clinic_code: str):
    if not clinic_store.valid_npi(clinic_code) or not await run_in_threadpool(clinic_store.get_public_clinic, clinic_code):
        raise _not_found()
    return _redirect(clinic=clinic_code)


def _redacted_target(target: str) -> str:
    parsed = urlsplit(target)
    path = parsed.path
    parts = path.strip("/").split("/")
    if (len(parts) == 2 and (parts[0] == "join" or clinic_store.valid_npi(parts[0]) or clinic_store.valid_legacy_code(parts[0]))
            and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", parts[1])):
        path = "/" + parts[0] + "/[redacted]" + ("/" if path.endswith("/") else "")
    elif ((len(parts) == 4 and parts[:2] == ["api", "insurance-upload"] and (clinic_store.valid_npi(parts[2]) or clinic_store.valid_legacy_code(parts[2])))
          or (len(parts) == 4 and parts[:3] == ["api", "staff", "invitations"])):
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", parts[3]):
            path = "/" + "/".join(parts[:3]) + "/[redacted]" + ("/" if path.endswith("/") else "")
    query = parsed.query
    if path.rstrip("/") == "/api/patient/idme/callback":
        query = ""
    values = parse_qsl(query, keep_blank_values=True)
    if any(key.lower() in {"clinic", "upload", "join"} for key, _ in values):
        query = urlencode([(key, "[redacted]" if key.lower() in {"clinic", "upload", "join"} else value) for key, value in values])
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, parsed.fragment))


class PrivateLinkAccessFilter(logging.Filter):
    """Keep new link secrets out of Uvicorn access logs without suppressing requests."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn access records: client, method, path-with-query, HTTP version, status.
        if isinstance(record.args, tuple) and len(record.args) == 5 and isinstance(record.args[2], str):
            values = list(record.args)
            try:
                values[2] = _redacted_target(values[2])
            except ValueError:
                values[2] = "[invalid request target]"
            record.args = tuple(values)
        return True


def install_access_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, PrivateLinkAccessFilter) for item in logger.filters):
        logger.addFilter(PrivateLinkAccessFilter())
