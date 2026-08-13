"""PostgreSQL/RDS storage for patient accounts and uploaded source documents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


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


@dataclass(frozen=True)
class StoredDocument:
    id: int
    filename: str
    uploaded_at: str
    fhir_bundle: dict[str, Any]
    s3_key: str
    storage_provider: str = "s3"
    extraction_version: str | None = None


@dataclass(frozen=True)
class StoredRecord:
    id: int
    category: str
    label: str
    created_at: str
    fhir_bundle: dict[str, Any]


@dataclass(frozen=True)
class StoredDoctorReport:
    id: str
    patient_id: str
    filename: str
    s3_key: str


@dataclass(frozen=True)
class StoredPatientDocument(StoredDocument):
    patient_id: str = ""


@dataclass(frozen=True)
class StoredDriveConnection:
    patient_id: str
    google_subject: str
    refresh_token_ciphertext: str
    folder_id: str
    summary_file_id: str | None
    summary_fingerprint: str | None


def _initialize_schema(connection: psycopg.Connection[Any]) -> None:
    """Run the one-time, cross-process-safe storage migration.

    Schema DDL takes exclusive PostgreSQL locks.  Running it for every request can
    deadlock when endpoints fetch documents and records concurrently, so it is
    guarded both in-process and across ECS tasks.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (8_240_715_301,))
        cursor.execute("""CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            storage_preference TEXT NOT NULL DEFAULT 's3'
        )""")
        cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS storage_preference TEXT NOT NULL DEFAULT 's3'")
        cursor.execute("""DO $$ BEGIN
            ALTER TABLE patients ADD CONSTRAINT patients_storage_preference_check
                CHECK (storage_preference IN ('s3', 'google_drive'));
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS patient_records (
            id BIGSERIAL PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            category TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL, fhir_bundle_json TEXT NOT NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS patient_documents (
            id BIGSERIAL PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, s3_key TEXT NOT NULL,
            fhir_bundle_json TEXT NOT NULL, extraction_version TEXT
        )""")
        # PostgreSQL does not automatically index foreign-key columns. These are the
        # predicates used on every workspace load and secure document download.
        cursor.execute("CREATE INDEX IF NOT EXISTS patient_documents_patient_id_idx ON patient_documents (patient_id, id DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS patient_records_patient_id_idx ON patient_records (patient_id, id DESC)")
        # Earlier development builds allowed a BYTEA fallback. Production documents
        # must never be kept in PostgreSQL; refuse an unsafe schema conversion rather
        # than silently deleting a legacy binary object.
        has_legacy_column = cursor.execute("""
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = 'patient_documents'
                AND column_name = 'pdf_bytes'
            ) AS exists
        """).fetchone()["exists"]
        if has_legacy_column:
            binary_count = cursor.execute(
                "SELECT COUNT(*) AS count FROM patient_documents WHERE pdf_bytes IS NOT NULL"
            ).fetchone()["count"]
            if binary_count:
                raise RuntimeError(
                    "Refusing to remove legacy database document bytes; migrate them to S3 first"
                )
            cursor.execute("ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS patient_documents_check")
            cursor.execute("ALTER TABLE patient_documents DROP COLUMN pdf_bytes")
        cursor.execute("ALTER TABLE patient_documents ALTER COLUMN s3_key SET NOT NULL")
        cursor.execute("ALTER TABLE patient_documents ADD COLUMN IF NOT EXISTS extraction_version TEXT")
        cursor.execute("ALTER TABLE patient_documents ADD COLUMN IF NOT EXISTS storage_provider TEXT NOT NULL DEFAULT 's3'")
        cursor.execute("""DO $$ BEGIN
            ALTER TABLE patient_documents ADD CONSTRAINT patient_documents_storage_provider_check
                CHECK (storage_provider IN ('s3', 'google_drive'));
        EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS shl_manifests (
            manifest_id TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(patient_id),
            encrypted_jwe TEXT NOT NULL, hashed_pin TEXT NOT NULL, created_at TEXT NOT NULL,
            failed_pin_attempts INTEGER NOT NULL DEFAULT 0, pin_locked_until TEXT, revoked_at TEXT
        )""")
        cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS failed_pin_attempts INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS pin_locked_until TEXT")
        cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS revoked_at TEXT")
        cursor.execute("""CREATE TABLE IF NOT EXISTS doctor_reports (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            filename TEXT NOT NULL, s3_key TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS patient_drive_connections (
            patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id),
            google_subject TEXT NOT NULL,
            refresh_token_ciphertext TEXT NOT NULL,
            folder_id TEXT NOT NULL,
            summary_file_id TEXT,
            summary_fingerprint TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        cursor.execute("ALTER TABLE patient_drive_connections ADD COLUMN IF NOT EXISTS summary_fingerprint TEXT")
        cursor.execute("""CREATE TABLE IF NOT EXISTS drive_oauth_states (
            state TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            expires_at TEXT NOT NULL
        )""")
    connection.commit()


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


def create_patient(patient_id: str, password_hash: str) -> bool:
    with _connection() as connection:
        try:
            connection.execute(
                "INSERT INTO patients (patient_id, password_hash, created_at) VALUES (%s, %s, %s)",
                (patient_id, password_hash, datetime.now(timezone.utc).isoformat()),
            )
        except psycopg.IntegrityError:
            return False
    return True


def get_password_hash(patient_id: str) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT password_hash FROM patients WHERE patient_id = %s", (patient_id,)
        ).fetchone()
    return row["password_hash"] if row else None


def get_storage_preference(patient_id: str) -> str:
    with _connection() as connection:
        row = connection.execute("SELECT storage_preference FROM patients WHERE patient_id = %s", (patient_id,)).fetchone()
    return str(row["storage_preference"]) if row else "s3"


def set_storage_preference(patient_id: str, storage_provider: str) -> bool:
    if storage_provider not in {"s3", "google_drive"}:
        raise ValueError("Unsupported storage provider")
    with _connection() as connection:
        cursor = connection.execute("UPDATE patients SET storage_preference = %s WHERE patient_id = %s", (storage_provider, patient_id))
    return cursor.rowcount == 1


def _attach_document_id(fhir_bundle: dict[str, Any], document_id: int) -> None:
    """Attach the persisted artifact ID to each citation extension in the bundle."""
    for entry in fhir_bundle.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        for extension in resource.get("extension", []):
            if not isinstance(extension, dict) or extension.get("url") != "https://clinicforce.example/fhir/StructureDefinition/source-citation":
                continue
            children = extension.setdefault("extension", [])
            if isinstance(children, list) and not any(
                isinstance(child, dict) and child.get("url") == "document_id" for child in children
            ):
                children.append({"url": "document_id", "valuePositiveInt": document_id})


def add_document(
    patient_id: str,
    filename: str,
    fhir_bundle: dict[str, Any],
    s3_key: str,
    extraction_version: str | None = None,
    storage_provider: str = "s3",
) -> int:
    if not s3_key or storage_provider not in {"s3", "google_drive"}:
        raise ValueError("A document must have a valid storage locator and provider")
    with _connection() as connection:
        parameters = (patient_id, filename, datetime.now(timezone.utc).isoformat(), s3_key, "{}", extraction_version, storage_provider)
        cursor = connection.execute("""INSERT INTO patient_documents
            (patient_id, filename, uploaded_at, s3_key, fhir_bundle_json, extraction_version, storage_provider)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""", parameters)
        document_id = int(cursor.fetchone()["id"])
        _attach_document_id(fhir_bundle, document_id)
        connection.execute(
            "UPDATE patient_documents SET fhir_bundle_json = %s WHERE id = %s",
            (json.dumps(fhir_bundle, separators=(",", ":")), document_id),
        )
    return document_id


def list_documents(patient_id: str) -> list[StoredDocument]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version, storage_provider
               FROM patient_documents WHERE patient_id = %s ORDER BY id DESC""",
            (patient_id,),
        ).fetchall()
    return [
        StoredDocument(
            id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
            fhir_bundle=json.loads(row["fhir_bundle_json"]), s3_key=row["s3_key"],
            extraction_version=row["extraction_version"], storage_provider=row["storage_provider"],
        )
        for row in rows
    ]


def get_document(patient_id: str, document_id: int) -> StoredDocument | None:
    with _connection() as connection:
        row = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version, storage_provider
               FROM patient_documents WHERE patient_id = %s AND id = %s""",
            (patient_id, document_id),
        ).fetchone()
    if row is None:
        return None
    return StoredDocument(
        id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
        fhir_bundle=json.loads(row["fhir_bundle_json"]), s3_key=row["s3_key"],
        extraction_version=row["extraction_version"], storage_provider=row["storage_provider"],
    )


def get_document_by_id(document_id: int) -> StoredDocument | None:
    """Fetch one document by its internal ID after a signed capability is verified."""
    with _connection() as connection:
        row = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version, storage_provider
               FROM patient_documents WHERE id = %s""",
            (document_id,),
        ).fetchone()
    if row is None:
        return None
    return StoredDocument(
        id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
        fhir_bundle=json.loads(row["fhir_bundle_json"]), s3_key=row["s3_key"],
        extraction_version=row["extraction_version"], storage_provider=row["storage_provider"],
    )


def update_document_extraction(
    patient_id: str,
    document_id: int,
    fhir_bundle: dict[str, Any],
    extraction_version: str,
) -> bool:
    """Replace a legacy extraction while retaining the original S3 source object."""
    _attach_document_id(fhir_bundle, document_id)
    with _connection() as connection:
        cursor = connection.execute(
            """UPDATE patient_documents
               SET fhir_bundle_json = %s, extraction_version = %s
               WHERE patient_id = %s AND id = %s""",
            (json.dumps(fhir_bundle, separators=(",", ":")), extraction_version, patient_id, document_id),
        )
    return cursor.rowcount == 1


def list_documents_requiring_extraction(extraction_version: str) -> list[StoredPatientDocument]:
    """List metadata-only document rows needing an explicit migration job."""
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, patient_id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version, storage_provider
               FROM patient_documents
               WHERE extraction_version IS DISTINCT FROM %s
               ORDER BY patient_id, id""",
            (extraction_version,),
        ).fetchall()
    return [
        StoredPatientDocument(
            id=row["id"], patient_id=row["patient_id"], filename=row["filename"],
            uploaded_at=row["uploaded_at"], fhir_bundle=json.loads(row["fhir_bundle_json"]),
            s3_key=row["s3_key"], extraction_version=row["extraction_version"], storage_provider=row["storage_provider"],
        )
        for row in rows
    ]


def add_record(patient_id: str, category: str, label: str, fhir_bundle: dict[str, Any]) -> None:
    with _connection() as connection:
        connection.execute(
            """INSERT INTO patient_records
               (patient_id, category, label, created_at, fhir_bundle_json)
               VALUES (%s, %s, %s, %s, %s)""",
            (patient_id, category, label, datetime.now(timezone.utc).isoformat(),
             json.dumps(fhir_bundle, separators=(",", ":"))),
        )


def list_records(patient_id: str) -> list[StoredRecord]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, category, label, created_at, fhir_bundle_json
               FROM patient_records WHERE patient_id = %s ORDER BY id DESC""",
            (patient_id,),
        ).fetchall()
    return [
        StoredRecord(
            id=row["id"], category=row["category"], label=row["label"],
            created_at=row["created_at"], fhir_bundle=json.loads(row["fhir_bundle_json"]),
        )
        for row in rows
    ]


def create_manifest(patient_id: str | None, encrypted_jwe: str, hashed_pin: str) -> str:
    manifest_id = str(uuid4())
    with _connection() as connection:
        if patient_id:
            connection.execute("UPDATE shl_manifests SET revoked_at = %s WHERE patient_id = %s AND revoked_at IS NULL", (datetime.now(timezone.utc).isoformat(), patient_id))
        connection.execute("""INSERT INTO shl_manifests
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, created_at) VALUES (%s, %s, %s, %s, %s)""",
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, datetime.now(timezone.utc).isoformat()))
    return manifest_id


def get_manifest(manifest_id: str) -> dict[str, str] | None:
    with _connection() as connection:
        row = connection.execute("SELECT patient_id, encrypted_jwe, hashed_pin FROM shl_manifests WHERE manifest_id = %s AND revoked_at IS NULL", (manifest_id,)).fetchone()
    return dict(row) if row else None


def verify_manifest_pin(manifest_id: str, passcode: str) -> dict[str, str] | None:
    """Verify a manifest PIN with persistent brute-force protection.

    A valid in-person visit remains unlimited; only five consecutive wrong PINs
    pause attempts for five minutes.
    """
    from app.core.shl import verify_pin

    now = datetime.now(timezone.utc)
    with _connection() as connection:
        row = connection.execute("""SELECT patient_id, encrypted_jwe, hashed_pin, failed_pin_attempts, pin_locked_until
            FROM shl_manifests WHERE manifest_id = %s AND revoked_at IS NULL""", (manifest_id,)).fetchone()
        if row is None:
            return None
        locked_until = row["pin_locked_until"]
        if isinstance(locked_until, str) and locked_until > now.isoformat():
            return None
        if verify_pin(passcode, row["hashed_pin"]):
            connection.execute("UPDATE shl_manifests SET failed_pin_attempts = 0, pin_locked_until = NULL WHERE manifest_id = %s", (manifest_id,))
            return {key: row[key] for key in ("patient_id", "encrypted_jwe", "hashed_pin")}
        attempts = int(row["failed_pin_attempts"] or 0) + 1
        lock_until = (now + timedelta(minutes=5)).isoformat() if attempts >= 5 else None
        connection.execute("UPDATE shl_manifests SET failed_pin_attempts = %s, pin_locked_until = %s WHERE manifest_id = %s", (attempts, lock_until, manifest_id))
    return None


def add_doctor_report(patient_id: str, filename: str, s3_key: str) -> str:
    report_id = str(uuid4())
    with _connection() as connection:
        connection.execute("""INSERT INTO doctor_reports (id, patient_id, filename, s3_key, created_at)
            VALUES (%s, %s, %s, %s, %s)""", (report_id, patient_id, filename, s3_key, datetime.now(timezone.utc).isoformat()))
    return report_id


def get_doctor_report(report_id: str) -> StoredDoctorReport | None:
    with _connection() as connection:
        row = connection.execute("SELECT id, patient_id, filename, s3_key FROM doctor_reports WHERE id = %s", (report_id,)).fetchone()
    return StoredDoctorReport(**dict(row)) if row else None


def save_drive_oauth_state(patient_id: str) -> str:
    """Persist a short-lived, single-use OAuth CSRF state for a logged-in patient."""
    state = str(uuid4())
    now = datetime.now(timezone.utc)
    with _connection() as connection:
        connection.execute("DELETE FROM drive_oauth_states WHERE expires_at < %s", (now.isoformat(),))
        connection.execute(
            "INSERT INTO drive_oauth_states (state, patient_id, expires_at) VALUES (%s, %s, %s)",
            (state, patient_id, (now + timedelta(minutes=10)).isoformat()),
        )
    return state


def consume_drive_oauth_state(state: str) -> str | None:
    """Return the connected patient once, rejecting expired or replayed callbacks."""
    with _connection() as connection:
        row = connection.execute(
            "DELETE FROM drive_oauth_states WHERE state = %s AND expires_at >= %s RETURNING patient_id",
            (state, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return row["patient_id"] if row else None


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
              summary_file_id = NULL, summary_fingerprint = NULL, updated_at = EXCLUDED.updated_at""",
            (patient_id, google_subject, refresh_token_ciphertext, folder_id, now, now),
        )


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
