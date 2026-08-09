"""Signed, least-privilege source-document download capabilities.

The original PDFs remain private S3 objects.  A clinician who has decrypted a
patient's SMART Health Link receives an expiring, single-document capability
for each cited fact; it cannot enumerate or access any other patient document.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import quote


DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    value = os.getenv("SOURCE_LINK_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("SOURCE_LINK_SECRET must be configured with at least 32 characters")
    return value.encode("utf-8")


def create_document_download_token(document_id: int, *, now: int | None = None, ttl_seconds: int | None = None) -> str:
    """Create an authenticated, expiring capability for one source document."""
    if document_id < 1:
        raise ValueError("A valid document ID is required")
    issued_at = int(time.time() if now is None else now)
    lifetime = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if lifetime < 60:
        raise ValueError("Source-link lifetime must be at least one minute")
    payload = _base64url(json.dumps(
        {"v": 1, "document_id": document_id, "exp": issued_at + lifetime},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8"))
    signature = _base64url(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_document_download_token(token: str, document_id: int, *, now: int | None = None) -> None:
    """Verify a source capability without leaking whether a document exists."""
    try:
        payload, signature = token.split(".", 1)
        expected = _base64url(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid source download link")
        decoded: Any = json.loads(_decode_base64url(payload))
        if not isinstance(decoded, dict) or decoded.get("v") != 1:
            raise ValueError("Invalid source download link")
        if decoded.get("document_id") != document_id or not isinstance(decoded.get("exp"), int):
            raise ValueError("Invalid source download link")
        if decoded["exp"] < int(time.time() if now is None else now):
            raise ValueError("This source download link has expired")
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        if isinstance(exc, ValueError) and str(exc) in {"Invalid source download link", "This source download link has expired"}:
            raise
        raise ValueError("Invalid source download link") from exc


def document_download_url(base_url: str, document_id: int) -> str:
    """Return an absolute attachment URL suitable for the dashboard and PDF."""
    token = create_document_download_token(document_id)
    return f"{base_url.rstrip('/')}/api/doctor/documents/{document_id}/download?token={quote(token, safe='')}"
