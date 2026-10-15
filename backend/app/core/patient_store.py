"""PostgreSQL/RDS storage for patient accounts and uploaded source documents."""

from __future__ import annotations

import json
import os
import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.logs.logger import log_info
from app.core.patient_ids import _google_patient_id, _is_google_generated_patient
from app.core.patient_models import (
    EpicEndpoint,
    InvalidManifestPin,
    PatientProfile,
    PatientProfileSuggestions,
    StoredDoctorReport,
    StoredDocument,
    StoredDriveConnection,
    StoredDriveCrypto,
    StoredEpicManifest,
    StoredIdmeTransaction,
    StoredPatientDocument,
    StoredRecord,
    IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS,
)


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_schema_lock = Lock()
_schema_initialized = False


def _database_url() -> str:
    """Build a TLS RDS URL from task-injected secret fields when needed."""
    if DATABASE_URL:
        return DATABASE_URL
    host = os.getenv("DB_HOST", "").strip()
    password = os.getenv("DATABASE_PASSWORD", "")
    user = os.getenv("DB_USER", "clinicforce")
    database = os.getenv("DB_NAME", "clinicforce")
    if not host or not password:
        raise RuntimeError("Configure DATABASE_URL or DB_HOST and DATABASE_PASSWORD")
    from urllib.parse import quote
    return f"postgresql://{quote(user)}:{quote(password)}@{host}:5432/{quote(database)}?sslmode=require"


MANIFEST_PIN_MAX_ATTEMPTS = 5


from app.core.patient_schema import _initialize_schema


def _connection() -> psycopg.Connection[Any]:
    """Connect to PostgreSQL, initializing the storage schema only once per task."""
    global _schema_initialized
    connection = psycopg.connect(_database_url(), row_factory=dict_row)
    try:
        if not _schema_initialized:
            with _schema_lock:
                if not _schema_initialized:
                    _initialize_schema(connection)
                    _schema_initialized = True
    except Exception:
        connection.close()
        raise
    return connection


from app.core.patient_accounts import create_patient, get_password_hash, get_patient_profile, get_patient_profile_suggestions, _google_name_hint, set_google_profile_suggestions, update_patient_profile, backfill_patient_profile_from_epic, check_database_connection, get_storage_preference, set_storage_preference


def replace_epic_endpoints(endpoints: list[dict[str, object]]) -> None:
    """Atomically replace public Epic directory metadata after validation."""
    now = datetime.now(timezone.utc).isoformat()
    normalized: list[tuple[str, str, str, str, str, str]] = []
    for endpoint in endpoints:
        identifier, name, fhir_base = endpoint.get("id"), endpoint.get("name"), endpoint.get("fhir_base")
        aliases, addresses = endpoint.get("aliases", []), endpoint.get("addresses", [])
        if not all(isinstance(value, str) and value.strip() for value in (identifier, name, fhir_base)):
            continue
        if not str(fhir_base).startswith("https://"):
            continue
        normalized.append((
            str(identifier), str(name),
            json.dumps([value for value in aliases if isinstance(value, str)], separators=(",", ":")),
            json.dumps([value for value in addresses if isinstance(value, dict)], separators=(",", ":")),
            str(fhir_base).rstrip("/"), now,
        ))
    if not normalized:
        raise ValueError("No valid Epic endpoints supplied")
    with _connection() as connection:
        connection.execute("DELETE FROM epic_endpoints")
        # psycopg exposes batch execution on a cursor, not the connection.
        # Keeping the delete and insert in one connection makes the catalog
        # replacement atomic from the patient-facing search endpoint's view.
        connection.cursor().executemany(
            """INSERT INTO epic_endpoints (id, name, aliases_json, addresses_json, fhir_base, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)""", normalized,
        )


def search_epic_endpoints(query: str, limit: int = 25) -> list[EpicEndpoint]:
    """Search public provider names/aliases without ever using patient data."""
    term = query.strip()
    if len(term) < 2:
        return []
    safe_limit = min(max(limit, 1), 50)
    pattern = f"%{term}%"
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, name, aliases_json, addresses_json, fhir_base FROM epic_endpoints
               WHERE name ILIKE %s OR aliases_json ILIKE %s
               ORDER BY name ASC LIMIT %s""", (pattern, pattern, safe_limit),
        ).fetchall()
    return [
        EpicEndpoint(
            id=str(row["id"]), name=str(row["name"]), aliases=json.loads(row["aliases_json"]),
            addresses=json.loads(row["addresses_json"]), fhir_base=str(row["fhir_base"]),
        ) for row in rows
    ]


def get_epic_endpoint(endpoint_id: str) -> EpicEndpoint | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, name, aliases_json, addresses_json, fhir_base FROM epic_endpoints WHERE id = %s", (endpoint_id,)
        ).fetchone()
    if row is None:
        return None
    return EpicEndpoint(
        id=str(row["id"]), name=str(row["name"]), aliases=json.loads(row["aliases_json"]),
        addresses=json.loads(row["addresses_json"]), fhir_base=str(row["fhir_base"]),
    )


from app.core.patient_documents import _attach_document_id, add_document, list_documents, get_document, get_document_by_id, remove_documents, update_document_locator, save_drive_file_crypto, get_drive_file_crypto, delete_drive_file_crypto, add_pending_drive_deletion, list_pending_drive_deletions, clear_pending_drive_deletion, revoke_patient_manifests, update_document_extraction, list_documents_requiring_extraction


from app.core.patient_records import add_record, list_records, generate_manifest_token, create_manifest, list_epic_manifests, revoke_epic_manifest, get_manifest, verify_manifest_pin, add_doctor_report, get_doctor_report


from app.core.patient_oauth_state import save_drive_oauth_state, save_google_login_state, consume_drive_oauth_state, consume_google_login_state


from app.core.patient_identity import (
    save_idme_oidc_transaction,
    consume_idme_oidc_transaction,
    _lock_idme_identity,
    _add_identity_event,
    record_idme_verification_event,
    create_idme_patient_session,
    link_idme_identity,
    record_idme_tefca_verification,
    patient_has_idme_identity,
    unlink_idme_identity,
)


def upsert_drive_connection(
    patient_id: str, google_subject: str, refresh_token_ciphertext: str, folder_id: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute("""INSERT INTO patient_drive_connections
            (patient_id, google_subject, refresh_token_ciphertext, folder_id, summary_file_id, summary_fingerprint, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NULL, NULL, %s, %s)
            ON CONFLICT (patient_id) DO UPDATE SET google_subject = EXCLUDED.google_subject,
              refresh_token_ciphertext = EXCLUDED.refresh_token_ciphertext, folder_id = EXCLUDED.folder_id,
              summary_file_id = CASE WHEN patient_drive_connections.folder_id = EXCLUDED.folder_id
                  THEN patient_drive_connections.summary_file_id ELSE NULL END,
              summary_fingerprint = CASE WHEN patient_drive_connections.folder_id = EXCLUDED.folder_id
                  THEN patient_drive_connections.summary_fingerprint ELSE NULL END,
              updated_at = EXCLUDED.updated_at""",
            (patient_id, google_subject, refresh_token_ciphertext, folder_id, now, now),
        )
        if _is_google_generated_patient(patient_id, google_subject):
            # Idempotently repair rows written by an older task during a rolling
            # deployment after the new column/default already exists.
            connection.execute(
                "UPDATE patients SET password_login_enabled = FALSE WHERE patient_id = %s",
                (patient_id,),
            )


def get_patient_by_google_subject(google_subject: str) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT patient_id FROM patient_drive_connections WHERE google_subject = %s",
            (google_subject,),
        ).fetchone()
    return str(row["patient_id"]) if row else None


def create_google_patient(
    google_subject: str, password_hash: str, google_given_name: str | None = None, google_family_name: str | None = None,
) -> str:
    """Create a stable, non-guessable local workspace for a Google identity."""
    patient_id = _google_patient_id(google_subject)
    with _connection() as connection:
        connection.execute(
            """INSERT INTO patients
               (patient_id, password_hash, password_login_enabled, google_given_name, google_family_name, created_at)
               VALUES (%s, %s, FALSE, %s, %s, %s) ON CONFLICT (patient_id) DO NOTHING""",
            (patient_id, password_hash, _google_name_hint(google_given_name), _google_name_hint(google_family_name), datetime.now(timezone.utc).isoformat()),
        )
    return patient_id


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_patient_session(patient_id: str) -> str:
    """Persist only a SHA-256 token hash; the raw session exists only in the cookie."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with _connection() as connection:
        connection.execute("DELETE FROM patient_sessions WHERE expires_at < %s", (now.isoformat(),))
        connection.execute(
            "INSERT INTO patient_sessions (session_hash, patient_id, expires_at, created_at) VALUES (%s, %s, %s, %s)",
            (_hash_session_token(token), patient_id, (now + timedelta(days=30)).isoformat(), now.isoformat()),
        )
    return token


def get_patient_session(token: str) -> str | None:
    if not token:
        return None
    with _connection() as connection:
        row = connection.execute(
            "SELECT patient_id FROM patient_sessions WHERE session_hash = %s AND expires_at >= %s",
            (_hash_session_token(token), datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return str(row["patient_id"]) if row else None


def get_recent_patient_session(token: str, max_age_seconds: int = 10 * 60) -> str | None:
    """Resolve a still-valid session only when it was freshly authenticated."""
    if not token:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, max_age_seconds))
    with _connection() as connection:
        row = connection.execute(
            """SELECT patient_id FROM patient_sessions
               WHERE session_hash = %s AND expires_at >= %s AND created_at >= %s""",
            (
                _hash_session_token(token),
                datetime.now(timezone.utc).isoformat(),
                cutoff.isoformat(),
            ),
        ).fetchone()
    return str(row["patient_id"]) if row else None


def revoke_patient_session(token: str) -> None:
    if not token:
        return
    with _connection() as connection:
        connection.execute("DELETE FROM patient_sessions WHERE session_hash = %s", (_hash_session_token(token),))


def get_drive_connection(patient_id: str) -> StoredDriveConnection | None:
    with _connection() as connection:
        row = connection.execute("""SELECT patient_id, google_subject, refresh_token_ciphertext, folder_id, summary_file_id, summary_fingerprint
            FROM patient_drive_connections WHERE patient_id = %s""", (patient_id,)).fetchone()
    return StoredDriveConnection(**dict(row)) if row else None


def set_drive_summary_file(patient_id: str, summary_file_id: str, summary_fingerprint: str) -> bool:
    with _connection() as connection:
        cursor = connection.execute("""UPDATE patient_drive_connections
            SET summary_file_id = %s, summary_fingerprint = %s, updated_at = %s WHERE patient_id = %s""",
            (summary_file_id, summary_fingerprint, datetime.now(timezone.utc).isoformat(), patient_id),
        )
    return cursor.rowcount == 1


def clear_drive_summary_file(patient_id: str) -> bool:
    with _connection() as connection:
        cursor = connection.execute("""UPDATE patient_drive_connections
            SET summary_file_id = NULL, summary_fingerprint = NULL, updated_at = %s WHERE patient_id = %s""",
            (datetime.now(timezone.utc).isoformat(), patient_id),
        )
    return cursor.rowcount == 1
