"""Patient documents backed by the patient database connection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.logs.logger import log_info
from app.core.patient_models import StoredDocument, StoredDriveCrypto, StoredPatientDocument


def _connection():
    from app.core import patient_store
    return patient_store._connection()

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
    """Withdraw combined clinician QR snapshots after a workspace source is removed."""
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE shl_manifests SET revoked_at = %s WHERE patient_id = %s AND source IN ('local', 'ecw_compat') AND revoked_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), patient_id),
        )
    if cursor.rowcount:
        log_info("Secure Sharing", "Revoked %d active patient shares after a workspace change", cursor.rowcount)
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
