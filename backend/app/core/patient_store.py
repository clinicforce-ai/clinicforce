"""PostgreSQL/RDS storage for patient accounts and uploaded source documents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


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


def _connection() -> psycopg.Connection[Any]:
    """Connect to the ClinicForce PostgreSQL database configured by the deployment."""
    connection = psycopg.connect(_database_url(), row_factory=dict_row)
    with connection.cursor() as cursor:
        cursor.execute("""CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
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
        cursor.execute("""CREATE TABLE IF NOT EXISTS shl_manifests (
            manifest_id TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(patient_id),
            encrypted_jwe TEXT NOT NULL, hashed_pin TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        cursor.execute("""CREATE TABLE IF NOT EXISTS doctor_reports (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            filename TEXT NOT NULL, s3_key TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
    connection.commit()
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
) -> int:
    if not s3_key:
        raise ValueError("A document must have an S3 object key")
    with _connection() as connection:
        parameters = (patient_id, filename, datetime.now(timezone.utc).isoformat(), s3_key, "{}", extraction_version)
        cursor = connection.execute("""INSERT INTO patient_documents
            (patient_id, filename, uploaded_at, s3_key, fhir_bundle_json, extraction_version)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""", parameters)
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
            """SELECT id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version
               FROM patient_documents WHERE patient_id = %s ORDER BY id DESC""",
            (patient_id,),
        ).fetchall()
    return [
        StoredDocument(
            id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
            fhir_bundle=json.loads(row["fhir_bundle_json"]), s3_key=row["s3_key"],
            extraction_version=row["extraction_version"],
        )
        for row in rows
    ]


def get_document(patient_id: str, document_id: int) -> StoredDocument | None:
    with _connection() as connection:
        row = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version
               FROM patient_documents WHERE patient_id = %s AND id = %s""",
            (patient_id, document_id),
        ).fetchone()
    if row is None:
        return None
    return StoredDocument(
        id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
        fhir_bundle=json.loads(row["fhir_bundle_json"]), s3_key=row["s3_key"],
        extraction_version=row["extraction_version"],
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
            """SELECT id, patient_id, filename, uploaded_at, fhir_bundle_json, s3_key, extraction_version
               FROM patient_documents
               WHERE extraction_version IS DISTINCT FROM %s
               ORDER BY patient_id, id""",
            (extraction_version,),
        ).fetchall()
    return [
        StoredPatientDocument(
            id=row["id"], patient_id=row["patient_id"], filename=row["filename"],
            uploaded_at=row["uploaded_at"], fhir_bundle=json.loads(row["fhir_bundle_json"]),
            s3_key=row["s3_key"], extraction_version=row["extraction_version"],
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
        connection.execute("""INSERT INTO shl_manifests
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, created_at) VALUES (%s, %s, %s, %s, %s)""",
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, datetime.now(timezone.utc).isoformat()))
    return manifest_id


def get_manifest(manifest_id: str) -> dict[str, str] | None:
    with _connection() as connection:
        row = connection.execute("SELECT patient_id, encrypted_jwe, hashed_pin FROM shl_manifests WHERE manifest_id = %s", (manifest_id,)).fetchone()
    return dict(row) if row else None


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
