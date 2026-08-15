"""PostgreSQL/RDS storage for patient accounts and uploaded source documents."""

from __future__ import annotations

import json
import os
import hashlib
import secrets
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


@dataclass(frozen=True)
class StoredDriveCrypto:
    file_id: str
    patient_id: str
    artifact_id: str
    purpose: str
    encrypted_data_key: str
    nonce: str
    plaintext_sha256: str
    algorithm: str


@dataclass(frozen=True)
class EpicEndpoint:
    id: str
    name: str
    aliases: list[str]
    addresses: list[dict[str, Any]]
    fhir_base: str


@dataclass(frozen=True)
class StoredEpicManifest:
    manifest_id: str
    created_at: str


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
        cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'local'")
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
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS patient_drive_connections_google_subject_idx ON patient_drive_connections (google_subject)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS drive_file_crypto (
            file_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            artifact_id TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL CHECK (purpose IN ('source', 'summary')),
            encrypted_data_key TEXT NOT NULL,
            nonce TEXT NOT NULL,
            plaintext_sha256 TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS drive_file_crypto_patient_id_idx ON drive_file_crypto (patient_id)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS drive_pending_deletions (
            patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            file_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (patient_id, file_id)
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS drive_oauth_states (
            state TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            expires_at TEXT NOT NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS google_login_states (
            state TEXT PRIMARY KEY, expires_at TEXT NOT NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS patient_sessions (
            session_hash TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS patient_sessions_patient_id_idx ON patient_sessions (patient_id)")
        # This is public provider-directory metadata only.  It intentionally has
        # no patient reference and must never be used to store Epic chart data.
        cursor.execute("""CREATE TABLE IF NOT EXISTS epic_endpoints (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            addresses_json TEXT NOT NULL,
            fhir_base TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS epic_endpoints_name_idx ON epic_endpoints (name)")
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
        connection.executemany(
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


def remove_documents(patient_id: str, document_ids: list[int]) -> int:
    """Remove Drive artifact mappings after the patient deletes source files.

    The clinical facts remain only until this mapping is removed; new workspaces and
    QR snapshots therefore cannot continue to present a source that no longer exists
    in the patient's selected storage provider.
    """
    ids = sorted({document_id for document_id in document_ids if document_id > 0})
    if not ids:
        return 0
    with _connection() as connection:
        cursor = connection.execute(
            "DELETE FROM patient_documents WHERE patient_id = %s AND id = ANY(%s)",
            (patient_id, ids),
        )
    return cursor.rowcount


def update_document_locator(patient_id: str, document_id: int, drive_file_id: str) -> bool:
    """Atomically point an existing Drive document at its encrypted replacement."""
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE patient_documents SET s3_key = %s WHERE patient_id = %s AND id = %s AND storage_provider = 'google_drive'",
            (drive_file_id, patient_id, document_id),
        )
    return cursor.rowcount == 1


def save_drive_file_crypto(
    file_id: str, patient_id: str, artifact_id: str, purpose: str,
    encrypted_data_key: str, nonce: str, plaintext_sha256: str, algorithm: str,
) -> None:
    if purpose not in {"source", "summary"}:
        raise ValueError("Unsupported Drive artifact purpose")
    with _connection() as connection:
        connection.execute("""INSERT INTO drive_file_crypto
            (file_id, patient_id, artifact_id, purpose, encrypted_data_key, nonce, plaintext_sha256, algorithm, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (file_id) DO UPDATE SET artifact_id = EXCLUDED.artifact_id,
              purpose = EXCLUDED.purpose, encrypted_data_key = EXCLUDED.encrypted_data_key,
              nonce = EXCLUDED.nonce, plaintext_sha256 = EXCLUDED.plaintext_sha256,
              algorithm = EXCLUDED.algorithm, created_at = EXCLUDED.created_at""",
            (file_id, patient_id, artifact_id, purpose, encrypted_data_key, nonce, plaintext_sha256, algorithm,
             datetime.now(timezone.utc).isoformat()),
        )


def get_drive_file_crypto(patient_id: str, file_id: str) -> StoredDriveCrypto | None:
    with _connection() as connection:
        row = connection.execute("""SELECT file_id, patient_id, artifact_id, purpose, encrypted_data_key,
            nonce, plaintext_sha256, algorithm FROM drive_file_crypto
            WHERE patient_id = %s AND file_id = %s""", (patient_id, file_id)).fetchone()
    return StoredDriveCrypto(**dict(row)) if row else None


def delete_drive_file_crypto(patient_id: str, file_id: str) -> None:
    with _connection() as connection:
        connection.execute("DELETE FROM drive_file_crypto WHERE patient_id = %s AND file_id = %s", (patient_id, file_id))


def add_pending_drive_deletion(patient_id: str, file_id: str) -> None:
    with _connection() as connection:
        connection.execute("""INSERT INTO drive_pending_deletions (patient_id, file_id, created_at)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (patient_id, file_id, datetime.now(timezone.utc).isoformat()))


def list_pending_drive_deletions(patient_id: str) -> list[str]:
    with _connection() as connection:
        rows = connection.execute("SELECT file_id FROM drive_pending_deletions WHERE patient_id = %s", (patient_id,)).fetchall()
    return [str(row["file_id"]) for row in rows]


def clear_pending_drive_deletion(patient_id: str, file_id: str) -> None:
    with _connection() as connection:
        connection.execute("DELETE FROM drive_pending_deletions WHERE patient_id = %s AND file_id = %s", (patient_id, file_id))


def revoke_patient_manifests(patient_id: str) -> int:
    """Withdraw local-record QR snapshots after a local source is removed.

    Epic snapshots have their own explicit revoke control and must not be altered
    by changes to unrelated uploaded/manual records.
    """
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE shl_manifests SET revoked_at = %s WHERE patient_id = %s AND source = 'local' AND revoked_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), patient_id),
        )
    return cursor.rowcount


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


def create_manifest(patient_id: str | None, encrypted_jwe: str, hashed_pin: str, source: str = "local") -> str:
    if source not in {"local", "epic"}:
        raise ValueError("Unknown manifest source")
    manifest_id = str(uuid4())
    with _connection() as connection:
        if patient_id:
            # Existing local QR behavior remains one-current-link-per-patient;
            # a new Epic share does not revoke unrelated local data sharing.
            predicate = "source = 'epic'" if source == "epic" else "source = 'local'"
            connection.execute(
                f"UPDATE shl_manifests SET revoked_at = %s WHERE patient_id = %s AND {predicate} AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), patient_id),
            )
        connection.execute("""INSERT INTO shl_manifests
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, created_at, source) VALUES (%s, %s, %s, %s, %s, %s)""",
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, datetime.now(timezone.utc).isoformat(), source))
    return manifest_id


def list_epic_manifests(patient_id: str) -> list[StoredEpicManifest]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT manifest_id, created_at FROM shl_manifests
               WHERE patient_id = %s AND source = 'epic' AND revoked_at IS NULL
               ORDER BY created_at DESC""", (patient_id,)
        ).fetchall()
    return [StoredEpicManifest(manifest_id=str(row["manifest_id"]), created_at=str(row["created_at"])) for row in rows]


def revoke_epic_manifest(patient_id: str, manifest_id: str) -> bool:
    with _connection() as connection:
        cursor = connection.execute(
            """UPDATE shl_manifests SET revoked_at = %s
               WHERE patient_id = %s AND manifest_id = %s AND source = 'epic' AND revoked_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), patient_id, manifest_id),
        )
    return cursor.rowcount == 1


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


def save_google_login_state() -> str:
    """Create a single-use OAuth state for passwordless Google patient sign-in."""
    state = str(uuid4())
    now = datetime.now(timezone.utc)
    with _connection() as connection:
        connection.execute("DELETE FROM google_login_states WHERE expires_at < %s", (now.isoformat(),))
        connection.execute(
            "INSERT INTO google_login_states (state, expires_at) VALUES (%s, %s)",
            (state, (now + timedelta(minutes=10)).isoformat()),
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


def consume_google_login_state(state: str) -> bool:
    """Consume a Google sign-in state exactly once."""
    with _connection() as connection:
        row = connection.execute(
            "DELETE FROM google_login_states WHERE state = %s AND expires_at >= %s RETURNING state",
            (state, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return row is not None


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


def get_patient_by_google_subject(google_subject: str) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT patient_id FROM patient_drive_connections WHERE google_subject = %s",
            (google_subject,),
        ).fetchone()
    return str(row["patient_id"]) if row else None


def create_google_patient(google_subject: str, password_hash: str) -> str:
    """Create a stable, non-guessable local workspace for a Google identity."""
    digest = hashlib.sha256(google_subject.encode("utf-8")).hexdigest()[:20]
    patient_id = f"google-{digest}"
    with _connection() as connection:
        connection.execute(
            "INSERT INTO patients (patient_id, password_hash, created_at) VALUES (%s, %s, %s) ON CONFLICT (patient_id) DO NOTHING",
            (patient_id, password_hash, datetime.now(timezone.utc).isoformat()),
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
