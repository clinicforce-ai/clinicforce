"""Patient-authorized Google Drive storage.

Google access is optional.  Raw PDFs never fall back to S3 when a patient has
chosen Drive; the backend keeps only encrypted OAuth credentials and Drive IDs.
All Drive calls run in a FastAPI worker thread.
"""

from __future__ import annotations

import base64
import json
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
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


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
        "scope": f"openid {_DRIVE_SCOPE}",
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


def complete_authorization(code: str) -> tuple[str, str, str]:
    """Exchange a callback code and create the patient-visible ClinicForce folder."""
    if not enabled():
        raise RuntimeError("Google Drive is not configured")
    tokens = _token_response({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": _callback_url(),
        "grant_type": "authorization_code",
    })
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("Google did not grant offline Drive access; reconnect and approve Drive access")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    profile = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers, timeout=20.0)
    profile.raise_for_status()
    subject = profile.json().get("sub")
    if not isinstance(subject, str) or not subject:
        raise RuntimeError("Google did not return an account identifier")
    folder_id = _find_or_create_folder(headers)
    return subject, _encrypt(refresh_token), folder_id


def _access_token(connection: StoredDriveConnection) -> str:
    tokens = _token_response({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": _decrypt(connection.refresh_token_ciphertext),
        "grant_type": "refresh_token",
    })
    return str(tokens["access_token"])


def _find_or_create_folder(headers: dict[str, str]) -> str:
    query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false and appProperties has { key='clinicforce_kind' and value='root' }"
    response = httpx.get("https://www.googleapis.com/drive/v3/files", headers=headers, params={"q": query, "fields": "files(id)"}, timeout=20.0)
    _raise_for_google_error(response, "folder lookup")
    files = response.json().get("files", [])
    if files and isinstance(files[0].get("id"), str):
        return files[0]["id"]
    created = httpx.post("https://www.googleapis.com/drive/v3/files", headers={**headers, "Content-Type": "application/json"}, json={
        "name": "ClinicForce",
        "mimeType": "application/vnd.google-apps.folder",
        "appProperties": {"clinicforce_kind": "root"},
    }, params={"fields": "id"}, timeout=20.0)
    _raise_for_google_error(created, "folder creation")
    folder_id = created.json().get("id")
    if not isinstance(folder_id, str):
        raise RuntimeError("Google did not create the ClinicForce folder")
    return folder_id


def existing_file_ids(connection: StoredDriveConnection, file_ids: list[str]) -> set[str]:
    """Return the app-created Drive files that still exist and are not trashed."""
    ids = list(dict.fromkeys(file_id for file_id in file_ids if file_id))
    found: set[str] = set()
    # Drive's search DSL does not support ``id =`` clauses. Check each RDS-mapped
    # ID directly so file IDs containing punctuation are always handled safely.
    access_token = _access_token(connection)
    headers = {"Authorization": f"Bearer {access_token}"}
    for file_id in ids:
        response = httpx.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers=headers,
            params={"fields": "id,trashed"},
            timeout=20.0,
        )
        if response.status_code == 404:
            continue
        _raise_for_google_error(response, "file reconciliation")
        item = response.json()
        if isinstance(item, dict) and isinstance(item.get("id"), str) and not item.get("trashed", False):
            found.add(item["id"])
    return found


def file_exists(connection: StoredDriveConnection, file_id: str) -> bool:
    """Check a known Drive artifact without downloading its PDF content."""
    response = httpx.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {_access_token(connection)}"},
        params={"fields": "id"},
        timeout=20.0,
    )
    if response.status_code == 404:
        return False
    _raise_for_google_error(response, "file lookup")
    return True


def file_is_in_folder(connection: StoredDriveConnection, file_id: str) -> bool:
    """Return whether a Drive file is still present in this patient's folder."""
    response = httpx.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {_access_token(connection)}"},
        params={"fields": "id,trashed,parents"},
        timeout=20.0,
    )
    if response.status_code == 404:
        return False
    _raise_for_google_error(response, "file location lookup")
    item = response.json()
    parents = item.get("parents", []) if isinstance(item, dict) else []
    return (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and not item.get("trashed", False)
        and connection.folder_id in parents
    )


def ensure_summary_in_folder(connection: StoredDriveConnection, file_id: str) -> None:
    """Place an existing summary in the connected ClinicForce folder.

    This covers patients who connected a new folder after a summary had already
    been generated.  The file ID and contents remain stable; only its Drive
    location and visible metadata are corrected.
    """
    response = httpx.patch(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {_access_token(connection)}", "Content-Type": "application/json"},
        params={"addParents": connection.folder_id, "fields": "id,parents"},
        json={"name": "Clinical Summary.pdf", "appProperties": {"clinicforce_kind": "clinical_summary"}},
        timeout=20.0,
    )
    _raise_for_google_error(response, "summary folder update")


def _multipart(metadata: dict[str, Any], content: bytes) -> tuple[bytes, str]:
    boundary = "clinicforce_drive_upload"
    payload = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() +
        json.dumps(metadata, separators=(",", ":")).encode() +
        f"\r\n--{boundary}\r\nContent-Type: application/pdf\r\n\r\n".encode() + content +
        f"\r\n--{boundary}--\r\n".encode()
    )
    return payload, boundary


def put_pdf(connection: StoredDriveConnection, filename: str, content: bytes, kind: str) -> str:
    access_token = _access_token(connection)
    payload, boundary = _multipart({
        "name": filename,
        "parents": [connection.folder_id],
        "mimeType": "application/pdf",
        "appProperties": {"clinicforce_kind": kind},
    }, content)
    response = httpx.post("https://www.googleapis.com/upload/drive/v3/files", headers={
        "Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}",
    }, params={"uploadType": "multipart", "fields": "id"}, content=payload, timeout=60.0)
    response.raise_for_status()
    file_id = response.json().get("id")
    if not isinstance(file_id, str):
        raise RuntimeError("Google did not return a file ID")
    return file_id


def replace_summary(connection: StoredDriveConnection, content: bytes) -> str:
    """Keep exactly one patient-visible summary file by replacing its content in place."""
    access_token = _access_token(connection)
    if connection.summary_file_id:
        response = httpx.patch(
            f"https://www.googleapis.com/upload/drive/v3/files/{connection.summary_file_id}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/pdf"},
            params={"uploadType": "media", "fields": "id"}, content=content, timeout=60.0,
        )
        if response.status_code != 404:
            _raise_for_google_error(response, "summary replacement")
            if not file_is_in_folder(connection, connection.summary_file_id):
                ensure_summary_in_folder(connection, connection.summary_file_id)
            return connection.summary_file_id
    return put_pdf(connection, "Clinical Summary.pdf", content, "clinical_summary")


def get_pdf(connection: StoredDriveConnection, file_id: str) -> bytes:
    response = httpx.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {_access_token(connection)}"}, params={"alt": "media"}, timeout=60.0,
    )
    response.raise_for_status()
    return response.content


def delete_file(connection: StoredDriveConnection, file_id: str) -> None:
    response = httpx.delete(f"https://www.googleapis.com/drive/v3/files/{file_id}", headers={"Authorization": f"Bearer {_access_token(connection)}"}, timeout=20.0)
    if response.status_code not in {204, 404}:
        response.raise_for_status()
