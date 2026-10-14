"""Patient-authorized Google Drive storage.

Google access is optional.  Raw PDFs never fall back to S3 when a patient has
chosen Drive; the backend keeps only encrypted OAuth credentials and Drive IDs.
All Drive calls run in a FastAPI worker thread.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import boto3
import httpx

from app.core.patient_store import StoredDriveConnection
from app.core.google_drive_auth import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_TOKEN_KMS_KEY_ID,
    PUBLIC_BASE_URL,
    _DRIVE_SCOPE,
    enabled,
    _callback_url,
    authorization_url,
    _encrypt,
    _decrypt,
    _token_response,
    _raise_for_google_error,
    _access_token,
)


@dataclass(frozen=True)
class DriveFolderFile:
    file_id: str
    filename: str
    mime_type: str
    size: int | None
    encrypted: bool


def complete_authorization(code: str) -> tuple[str, str, str, str | None, str | None]:
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
    profile_data = profile.json()
    subject = profile_data.get("sub")
    if not isinstance(subject, str) or not subject:
        raise RuntimeError("Google did not return an account identifier")
    folder_id = _find_or_create_folder(headers)
    given_name = profile_data.get("given_name")
    family_name = profile_data.get("family_name")
    return (
        subject,
        _encrypt(refresh_token),
        folder_id,
        given_name if isinstance(given_name, str) else None,
        family_name if isinstance(family_name, str) else None,
    )




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


def list_folder_files(connection: StoredDriveConnection) -> list[DriveFolderFile]:
    """List direct ClinicForce-folder children for the patient-initiated import flow."""
    headers = {"Authorization": f"Bearer {_access_token(connection)}"}
    query = f"'{connection.folder_id}' in parents and trashed = false"
    files: list[DriveFolderFile] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {
            "q": query,
            "fields": "nextPageToken,files(id,name,mimeType,size,appProperties)",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        response = httpx.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params, timeout=20.0)
        _raise_for_google_error(response, "folder reconciliation")
        payload = response.json()
        for item in payload.get("files", []):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            properties = item.get("appProperties") if isinstance(item.get("appProperties"), dict) else {}
            size = item.get("size")
            files.append(DriveFolderFile(
                file_id=item["id"],
                filename=str(item.get("name") or "Drive document"),
                mime_type=str(item.get("mimeType") or ""),
                size=int(size) if isinstance(size, str) and size.isdigit() else None,
                encrypted=properties.get("clinicforce_encrypted") == "v1",
            ))
        page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        if not isinstance(page_token, str) or not page_token:
            return files


def _multipart(metadata: dict[str, Any], content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "clinicforce_drive_upload"
    payload = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() +
        json.dumps(metadata, separators=(",", ":")).encode() +
        f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode() + content +
        f"\r\n--{boundary}--\r\n".encode()
    )
    return payload, boundary


def put_encrypted_blob(connection: StoredDriveConnection, content: bytes, kind: str) -> str:
    """Upload opaque encrypted bytes; no patient filename or PDF MIME type reaches Drive."""
    access_token = _access_token(connection)
    payload, boundary = _multipart({
        "name": f"clinicforce-{uuid4().hex}.cforce",
        "parents": [connection.folder_id],
        "mimeType": "application/octet-stream",
        "appProperties": {"clinicforce_kind": kind, "clinicforce_encrypted": "v1"},
    }, content, "application/octet-stream")
    response = httpx.post("https://www.googleapis.com/upload/drive/v3/files", headers={
        "Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}",
    }, params={"uploadType": "multipart", "fields": "id"}, content=payload, timeout=60.0)
    _raise_for_google_error(response, "encrypted upload")
    file_id = response.json().get("id")
    if not isinstance(file_id, str):
        raise RuntimeError("Google did not return a file ID")
    return file_id


def replace_encrypted_blob(connection: StoredDriveConnection, file_id: str, content: bytes) -> bool:
    """Replace an existing encrypted artifact in place; False means it was deleted."""
    access_token = _access_token(connection)
    response = httpx.patch(
        f"https://www.googleapis.com/upload/drive/v3/files/{file_id}",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/octet-stream"},
        params={"uploadType": "media", "fields": "id"}, content=content, timeout=60.0,
    )
    if response.status_code == 404:
        return False
    _raise_for_google_error(response, "encrypted replacement")
    return True


def get_file_bytes(connection: StoredDriveConnection, file_id: str) -> bytes:
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
