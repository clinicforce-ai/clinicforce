"""Signed, least-privilege source-document download capabilities.

The original PDFs remain private S3 objects.  A clinician who has decrypted a
patient's SMART Health Link receives a manifest-bound, single-document capability
for each cited fact; it cannot enumerate or access any other patient document.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from typing import Any
from urllib.parse import quote


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _secret() -> bytes:
    value = os.getenv("SOURCE_LINK_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("SOURCE_LINK_SECRET must be configured with at least 32 characters")
    return value.encode("utf-8")


def create_document_download_token(document_id: int, manifest_id: str) -> str:
    """Bind one cited document to the QR manifest that authorized it.

    The patient explicitly requested non-expiring visit links.  Revocation is
    therefore achieved by deleting/revoking the manifest rather than a timer.
    """
    if document_id < 1 or not manifest_id:
        raise ValueError("A valid document and manifest are required")
    payload = _base64url(json.dumps(
        {"v": 2, "document_id": document_id, "manifest_id": manifest_id},
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8"))
    signature = _base64url(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_document_download_token(token: str, document_id: int) -> str:
    """Verify a source capability and return its immutable QR manifest ID."""
    try:
        payload, signature = token.split(".", 1)
        expected = _base64url(hmac.new(_secret(), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid source download link")
        decoded: Any = json.loads(_decode_base64url(payload))
        if not isinstance(decoded, dict) or decoded.get("v") != 2:
            raise ValueError("Invalid source download link")
        manifest_id = decoded.get("manifest_id")
        if decoded.get("document_id") != document_id or not isinstance(manifest_id, str) or not manifest_id:
            raise ValueError("Invalid source download link")
        return manifest_id
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        if isinstance(exc, ValueError) and str(exc) == "Invalid source download link":
            raise
        raise ValueError("Invalid source download link") from exc


def document_download_url(base_url: str, document_id: int, manifest_id: str) -> str:
    """Return an absolute attachment URL suitable for the dashboard and PDF."""
    token = create_document_download_token(document_id, manifest_id)
    return f"{base_url.rstrip('/')}/api/doctor/documents/{document_id}/download?token={quote(token, safe='')}"
