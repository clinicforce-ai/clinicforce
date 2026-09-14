"""Write-only patient upload links; original files and link consumption commit together."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from app.core import object_store, staff_store


_schema_initialized = False
_schema_lock = Lock()


class UploadLinkError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _connection():
    global _schema_initialized
    connection = staff_store._connection()
    try:
        if not _schema_initialized:
            with _schema_lock:
                if not _schema_initialized:
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (8_240_715_306,))
                    connection.execute("ALTER TABLE staff_intakes ALTER COLUMN created_by DROP NOT NULL")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS upload_link_id TEXT")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS expected_patient_id TEXT REFERENCES staff_patients(id)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS insurance_upload_links (
                        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, clinic_code TEXT NOT NULL,
                        practice_id TEXT NOT NULL, clinic_name TEXT NOT NULL,
                        created_by TEXT REFERENCES staff_members(id), expected_patient_id TEXT REFERENCES staff_patients(id),
                        status TEXT NOT NULL CHECK (status IN ('pending','received','revoked')),
                        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, submitted_at TEXT,
                        intake_id TEXT REFERENCES staff_intakes(id), receipt_id TEXT,
                        submission_hash TEXT, submission_id TEXT, events JSONB NOT NULL DEFAULT '[]'
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS insurance_upload_links_practice_idx ON insurance_upload_links (practice_id,created_at DESC)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS insurance_upload_limits (
                        key_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_started TEXT NOT NULL
                    )""")
                    connection.commit()
                    _schema_initialized = True
    except Exception:
        connection.close()
        raise
    return connection


def check_rate(address: str, practice_id: str, action: str) -> None:
    """Persist bounds across workers/restarts; raw addresses are never stored."""
    moment = datetime.now(timezone.utc)
    cutoff = (moment - timedelta(hours=1)).isoformat()
    # Separate start and submission limits allow a patient to use the link just made.
    staff_action = action.startswith("staff-")
    limits = sorted([(_hash(f"{action}:address:{address}"), 60 if staff_action else 10), (_hash(f"{action}:practice:{practice_id}"), 500 if staff_action else 100)])
    allowed = True
    with _connection() as connection:
        connection.execute("DELETE FROM insurance_upload_limits WHERE window_started < %s", (cutoff,))
        for key, limit in limits:
            row = connection.execute("""INSERT INTO insurance_upload_limits (key_hash,attempts,window_started) VALUES (%s,1,%s)
                ON CONFLICT (key_hash) DO UPDATE SET attempts = insurance_upload_limits.attempts + 1 RETURNING attempts""",
                (key, moment.isoformat())).fetchone()
            allowed = allowed and row["attempts"] <= limit
    if not allowed:
        raise UploadLinkError("Too many upload attempts. Please try again in an hour or contact the clinic.", 429)


def create_link(clinic: dict, actor: dict | None = None, patient_id: str | None = None) -> tuple[dict, str]:
    if not clinic.get("npi"):
        raise UploadLinkError("A clinic administrator must set the clinic's NPI before creating patient upload links.")
    stamp = datetime.now(timezone.utc)
    code = secrets.token_urlsafe(32)
    with _connection() as connection:
        if patient_id and not connection.execute("SELECT id FROM staff_patients WHERE id=%s AND practice_id=%s", (patient_id, clinic["id"])).fetchone():
            raise UploadLinkError("Clinic patient not found", 404)
        if actor and actor["practice_id"] != clinic["id"]:
            raise UploadLinkError("Clinic not found", 404)
        row = connection.execute("""INSERT INTO insurance_upload_links
            (id,token_hash,clinic_code,practice_id,clinic_name,created_by,expected_patient_id,status,created_at,expires_at,events)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s::jsonb) RETURNING *""",
            (str(uuid4()), _hash(code), clinic["npi"], clinic["id"], clinic["name"], actor["id"] if actor else None,
             patient_id, stamp.isoformat(), (stamp + timedelta(hours=72 if actor else 1)).isoformat(),
             json.dumps([staff_store._event("Patient upload link created", actor or "Patient upload")]))).fetchone()
    return dict(row), code


def _usable(row: dict | None) -> dict:
    if not row:
        raise UploadLinkError("This upload link was not found. Check the link or contact the clinic.", 404)
    if row["status"] == "revoked":
        raise UploadLinkError("This upload link is no longer available. Contact the clinic for a new link.", 410)
    if row["status"] == "pending" and row["expires_at"] <= staff_store.now():
        raise UploadLinkError("This upload link has expired. Contact the clinic for a new link.", 410)
    return row


def metadata(clinic_code: str, code: str) -> dict:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM insurance_upload_links WHERE clinic_code=%s AND token_hash=%s", (clinic_code, _hash(code))).fetchone()
    return dict(_usable(row))


def owned_request(link_id: str, practice_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM insurance_upload_links WHERE id=%s AND practice_id=%s", (link_id, practice_id)).fetchone()
    return dict(row) if row else None


def validate_owned_url(link_id: str, practice_id: str, url: str) -> dict | None:
    """Email may send only the exact active write-only URL created for this clinic."""
    from app.api.settings import PUBLIC_BASE_URL
    row = owned_request(link_id, practice_id)
    try:
        parsed, base = urlsplit(url), urlsplit(PUBLIC_BASE_URL)
        parts = parsed.path.split("/")
        valid = row and row["status"] == "pending" and row["expires_at"] > staff_store.now()
        valid = valid and (parsed.scheme, parsed.netloc) == (base.scheme, base.netloc) and not parsed.query and not parsed.fragment
        valid = valid and len(parts) == 3 and parts[1] == row["clinic_code"] and secrets.compare_digest(_hash(parts[2]), row["token_hash"])
        return row if valid else None
    except (ValueError, TypeError):
        return None


def list_links(practice_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute("SELECT * FROM insurance_upload_links WHERE practice_id=%s ORDER BY created_at DESC,id DESC LIMIT 100", (practice_id,)).fetchall()
    return [dict(row) for row in rows]


def revoke(link_id: str, actor: dict) -> dict:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM insurance_upload_links WHERE id=%s AND practice_id=%s FOR UPDATE", (link_id, actor["practice_id"])).fetchone()
        if not row:
            raise UploadLinkError("Upload link not found", 404)
        if row["status"] == "received":
            raise UploadLinkError("This link has already received an upload. The saved intake remains available in your inbox.")
        connection.execute("UPDATE insurance_upload_links SET status='revoked',events=events || %s::jsonb WHERE id=%s",
                           (json.dumps([staff_store._event("Patient upload link revoked", actor)]), link_id))
    return {"id": link_id, "status": "revoked"}


def receipt(row: dict) -> dict:
    return {"receipt_id": row["receipt_id"], "status": "received", "received_at": row["submitted_at"], "clinic_name": row["clinic_name"]}


def submit(clinic_code: str, code: str, fields: dict, prepared: list[tuple[str, bytes, str]], submission_id: str | None = None, source_portal_patient_id: str | None = None) -> tuple[dict, bool]:
    digest = _hash(json.dumps({"fields": fields, "files": [hashlib.sha256(content).hexdigest() for _, content, _ in prepared],
                              "source_portal_patient_id": source_portal_patient_id}, sort_keys=True, separators=(",", ":")))
    documents, intake_id = [], None
    try:
        with _connection() as connection:
            # ponytail: hold one link row while saving at most two originals; an outbox
            # is needed only if object-store latency makes these short transactions costly.
            row = _usable(connection.execute("SELECT * FROM insurance_upload_links WHERE clinic_code=%s AND token_hash=%s FOR UPDATE", (clinic_code, _hash(code))).fetchone())
            if row["status"] == "received":
                if secrets.compare_digest(row["submission_hash"] or "", digest):
                    return receipt(row), False
                raise UploadLinkError("This link has already received an upload. Contact the clinic if you need to send a correction.")
            if not object_store.enabled():
                raise UploadLinkError("Card storage is unavailable. Your card has not been saved. Please try again later.", 503)
            intake_id, stamp = str(uuid4()), staff_store.now()
            for filename, content, content_type in prepared:
                suffix = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[content_type]
                key = object_store.put_staff_document(row["practice_id"], intake_id, "source" + suffix, content, content_type)
                documents.append({"id": str(uuid4()), "filename": Path(filename).name[:200], "content_type": content_type,
                                  "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "created_at": stamp, "object_key": key})
            event = staff_store._event("Patient uploaded insurance card", "Patient upload")
            event["upload_link_id"] = row["id"]
            event["consent"] = "Patient agreed to send insurance information to this clinic"
            expected = connection.execute("SELECT * FROM staff_patients WHERE id=%s AND practice_id=%s", (row["expected_patient_id"], row["practice_id"])).fetchone() if row["expected_patient_id"] else None
            matches_expected = expected and all(str(expected[key]).strip().casefold() == str(fields[key]).strip().casefold() for key in ("given_name", "family_name", "birth_date"))
            auto_patient_id = expected["id"] if matches_expected else str(uuid4())
            if not matches_expected:
                connection.execute("""INSERT INTO staff_patients (id,practice_id,given_name,family_name,birth_date,email,identity_status,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,'unverified',%s)""",
                    (auto_patient_id, row["practice_id"], fields["given_name"], fields["family_name"], fields["birth_date"], fields.get("email"), stamp))
            connection.execute("""INSERT INTO staff_intakes
                (id,practice_id,created_by,status,fields,field_sources,documents,events,warning,created_at,updated_at,source,upload_link_id,expected_patient_id,auto_patient_id,source_portal_patient_id)
                VALUES (%s,%s,NULL,'needs_confirmation',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,'patient_upload',%s,%s,%s,%s)""",
                (intake_id, row["practice_id"], json.dumps(fields), json.dumps({key: "patient_provided" for key in fields}), json.dumps(documents), json.dumps([event]),
                 "Patient-submitted details have not been confirmed. Read the saved card and confirm the patient before starting insurance review.", stamp, stamp, row["id"], row["expected_patient_id"], auto_patient_id, source_portal_patient_id))
            saved = connection.execute("""UPDATE insurance_upload_links SET status='received',submitted_at=%s,intake_id=%s,
                receipt_id=%s,submission_hash=%s,submission_id=%s,events=events || %s::jsonb WHERE id=%s RETURNING *""",
                (stamp, intake_id, str(uuid4()), digest, submission_id, json.dumps([event]), row["id"])).fetchone()
        return receipt(saved), True
    except Exception:
        if documents:
            # A dropped connection during COMMIT may have committed successfully.
            # Never delete originals until the database proves our intake is absent.
            try:
                with _connection() as connection:
                    saved_intake = connection.execute("SELECT id FROM staff_intakes WHERE id=%s", (intake_id,)).fetchone()
                    saved_link = connection.execute("SELECT * FROM insurance_upload_links WHERE clinic_code=%s AND token_hash=%s", (clinic_code, _hash(code))).fetchone()
                if saved_intake:
                    if saved_link and saved_link["intake_id"] == intake_id and saved_link["status"] == "received":
                        return receipt(saved_link), True
                    raise UploadLinkError("Your upload may have been saved. Retry with the same files to check its receipt.", 503)
            except Exception:
                # Leave potentially committed objects intact when recovery cannot be checked.
                raise UploadLinkError("The upload receipt is unavailable. Retry with the same files.", 503) from None
        for document in documents:
            try:
                object_store.delete_object(document["object_key"])
            except Exception:
                pass
        raise


def save_card_reading(intake_id: str, actor: dict, extracted: dict, evidence: dict, expected_updated_at: str) -> dict:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM staff_intakes WHERE id=%s AND practice_id=%s FOR UPDATE", (intake_id, actor["practice_id"])).fetchone()
        if not row:
            raise UploadLinkError("Insurance intake not found", 404)
        if row["patient_id"] or row["workflows"] or row["updated_at"] != expected_updated_at:
            raise UploadLinkError("Patient details changed while the card was being read. Reopen this intake to review the latest details.")
        fields, sources = dict(row["fields"]), dict(row["field_sources"])
        for key, value in extracted.items():
            if key not in {"given_name", "family_name", "birth_date"} and value and not fields.get(key):
                fields[key], sources[key] = value, "card_ocr"
        warning = "Check the card reading and confirm patient details before starting insurance review."
        connection.execute("""UPDATE staff_intakes SET fields=%s::jsonb,field_sources=%s::jsonb,ocr_fields=%s::jsonb,ocr_evidence=%s::jsonb,
            warning=%s,events=events || %s::jsonb,updated_at=%s WHERE id=%s AND practice_id=%s""",
            (json.dumps(fields), json.dumps(sources), json.dumps(extracted), json.dumps(evidence), warning,
             json.dumps([staff_store._event("Saved insurance card read", actor)]), staff_store.now(), intake_id, actor["practice_id"]))
    return staff_store.get_intake(intake_id, actor["practice_id"])
