"""Authorize patient Drive access and manage encrypted OAuth tokens."""
from __future__ import annotations

import base64
import os
from typing import Any
from urllib.parse import urlencode

import boto3
import httpx

from app.core.patient_store import StoredDriveConnection


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_TOKEN_KMS_KEY_ID = os.getenv("GOOGLE_TOKEN_KMS_KEY_ID", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
# Full Drive access is required only because a patient may place a new PDF in
# the ClinicForce folder using the Drive UI and ask ClinicForce to import it.
# The backend only lists direct children of the per-patient app folder.
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_TOKEN_KMS_KEY_ID)


def _callback_url() -> str:
    return f"{PUBLIC_BASE_URL}/api/patient/google-drive/callback"


def authorization_url(state: str) -> str:
    if not enabled():
        raise RuntimeError("Google Drive is not configured")
    query = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _callback_url(),
        "response_type": "code",
        # ``profile`` lets us offer Google-provided names as editable form
        # suggestions. Date of birth is deliberately not requested from Google.
        "scope": f"openid profile {_DRIVE_SCOPE}",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def _encrypt(value: str) -> str:
    if not GOOGLE_TOKEN_KMS_KEY_ID:
        raise RuntimeError("GOOGLE_TOKEN_KMS_KEY_ID must be configured")
    result = boto3.client("kms").encrypt(KeyId=GOOGLE_TOKEN_KMS_KEY_ID, Plaintext=value.encode("utf-8"))
    return base64.b64encode(result["CiphertextBlob"]).decode("ascii")


def _decrypt(value: str) -> str:
    result = boto3.client("kms").decrypt(CiphertextBlob=base64.b64decode(value))
    return result["Plaintext"].decode("utf-8")


def _token_response(data: dict[str, str]) -> dict[str, Any]:
    response = httpx.post("https://oauth2.googleapis.com/token", data=data, timeout=20.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload.get("access_token"), str):
        raise RuntimeError("Google did not return an access token")
    return payload


def _raise_for_google_error(response: httpx.Response, operation: str) -> None:
    """Raise an actionable Drive error without exposing OAuth credentials."""
    if response.is_success:
        return
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = error.get("message") if isinstance(error, dict) else None
        reason = ""
        if isinstance(error, dict) and isinstance(error.get("errors"), list) and error["errors"]:
            first = error["errors"][0]
            if isinstance(first, dict) and isinstance(first.get("reason"), str):
                reason = first["reason"]
        detail = ": ".join(part for part in (reason, message) if isinstance(part, str) and part)
    except (ValueError, TypeError):
        detail = ""
    if detail:
        raise RuntimeError(f"Google Drive {operation} failed ({response.status_code}): {detail}")
    response.raise_for_status()


def _access_token(connection: StoredDriveConnection) -> str:
    tokens = _token_response({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": _decrypt(connection.refresh_token_ciphertext),
        "grant_type": "refresh_token",
    })
    return str(tokens["access_token"])
