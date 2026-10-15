"""Patient records backed by the patient database connection."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.logs.logger import log_info
from app.core.patient_models import InvalidManifestPin, StoredDoctorReport, StoredEpicManifest, StoredRecord


def _connection():
    from app.core import patient_store
    return patient_store._connection()

MANIFEST_PIN_MAX_ATTEMPTS = 5

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

def generate_manifest_token() -> str:
    """Return the 256-bit, URL-safe public identifier required by SHL."""
    return secrets.token_urlsafe(32)

def create_manifest(
    patient_id: str | None,
    encrypted_jwe: str,
    hashed_pin: str | None,
    source: str = "local",
    expires_at: str | None = None,
) -> str:
    if source not in {"local", "epic", "ecw_compat"}:
        raise ValueError("Unknown manifest source")
    manifest_id = generate_manifest_token()
    with _connection() as connection:
        if patient_id:
            # The no-PIN compatibility QR is a separately disclosed test
            # artifact, so generating either format must not silently revoke
            # the other.  Standard shares retain the legacy behavior of
            # superseding old standard and old Epic-only shares.
            if source == "epic":
                predicate = "source = 'epic'"
            elif source == "ecw_compat":
                predicate = "source = 'ecw_compat'"
            else:
                predicate = "source IN ('local', 'epic')"
            superseded = connection.execute(
                f"UPDATE shl_manifests SET revoked_at = %s WHERE patient_id = %s AND {predicate} AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), patient_id),
            )
        connection.execute("""INSERT INTO shl_manifests
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, created_at, source, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (manifest_id, patient_id, encrypted_jwe, hashed_pin, datetime.now(timezone.utc).isoformat(), source, expires_at))
    if patient_id and superseded.rowcount:
        log_info("Secure Sharing", "Revoked %d superseded patient shares", superseded.rowcount)
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
    if cursor.rowcount:
        log_info("Secure Sharing", "Revoked one patient-requested share")
    return cursor.rowcount == 1

def get_manifest(manifest_id: str) -> dict[str, str | None] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        row = connection.execute(
            """SELECT patient_id, encrypted_jwe, hashed_pin FROM shl_manifests
               WHERE manifest_id = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)""",
            (manifest_id, now),
        ).fetchone()
    return dict(row) if row else None

def verify_manifest_pin(manifest_id: str, passcode: str) -> dict[str, str | None] | None:
    """Verify a manifest PIN with a race-safe, lifetime brute-force limit."""
    from app.core.shl import verify_pin

    now = datetime.now(timezone.utc)
    with _connection() as connection:
        # Row locking serializes verification while the failed-attempt UPDATE below
        # performs its increment atomically. This prevents parallel requests from
        # observing and consuming the same remaining attempt.
        row = connection.execute("""SELECT patient_id, encrypted_jwe, hashed_pin, failed_pin_attempts
            FROM shl_manifests
            WHERE manifest_id = %s AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > %s)
            FOR UPDATE""", (manifest_id, now.isoformat())).fetchone()
        if row is None:
            return None
        if int(row["failed_pin_attempts"] or 0) >= MANIFEST_PIN_MAX_ATTEMPTS:
            return None
        # A manifest created without a hash represents an SHL without the
        # ``P`` flag.  There is no PIN to validate and the receiver's request
        # must be allowed to continue without consuming the PIN attempt cap.
        if row["hashed_pin"] is None:
            return {key: row[key] for key in ("patient_id", "encrypted_jwe", "hashed_pin")}
        if verify_pin(passcode, row["hashed_pin"]):
            return {key: row[key] for key in ("patient_id", "encrypted_jwe", "hashed_pin")}
        incremented = connection.execute("""UPDATE shl_manifests
            SET failed_pin_attempts = failed_pin_attempts + 1,
                revoked_at = CASE WHEN failed_pin_attempts + 1 >= %s THEN %s ELSE revoked_at END,
                pin_locked_until = NULL
            WHERE manifest_id = %s AND revoked_at IS NULL
            RETURNING failed_pin_attempts""", (
                MANIFEST_PIN_MAX_ATTEMPTS, now.isoformat(), manifest_id,
            )).fetchone()
        if incremented is None:
            return None
        remaining_attempts = max(0, MANIFEST_PIN_MAX_ATTEMPTS - int(incremented["failed_pin_attempts"]))
    raise InvalidManifestPin(remaining_attempts)

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
