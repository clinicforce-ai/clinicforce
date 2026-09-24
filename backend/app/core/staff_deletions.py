"""Clinic-scoped deletion with a durable receipt for retryable file cleanup."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.core import intake_links_store, object_store, staff_store


def retry_cleanup(actor: dict) -> dict:
    with staff_store._connection() as db:
        receipts = db.execute("""SELECT kind,record_id,intake_id FROM staff_deletions
            WHERE practice_id=%s AND object_keys <> '[]'::jsonb ORDER BY created_at LIMIT 50""",
            (actor["practice_id"],)).fetchall()
    for receipt in receipts:
        delete_record(receipt["kind"], receipt["record_id"], actor, receipt["intake_id"])
    with staff_store._connection() as db:
        pending = db.execute("SELECT count(*) AS count FROM staff_deletions WHERE practice_id=%s AND object_keys <> '[]'::jsonb",
                             (actor["practice_id"],)).fetchone()["count"]
    return {"pending_file_cleanup": pending}


def _pending(value: Any) -> bool:
    if isinstance(value, dict):
        return any((key in {"request_state", "outcome", "state"} and isinstance(item, str)
                    and item in {"pending", "indeterminate", "submitting"}) or _pending(item)
                   for key, item in value.items())
    return isinstance(value, list) and any(_pending(item) for item in value)


def _owned_keys(value: Any, practice_id: str, record_ids: list[str]) -> set[str]:
    """Delete only originals owned by these records, never shared library sources."""
    prefix = f"{object_store.S3_PREFIX}/staff-insurance/{sha256(practice_id.encode()).hexdigest()}/"
    prefixes = tuple(prefix + object_store._safe_segment(record_id) + "/" for record_id in record_ids)
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("object_key") and isinstance(item, str) and item.startswith(prefixes):
                keys.add(item)
            elif isinstance(item, (dict, list)):
                keys.update(_owned_keys(item, practice_id, record_ids))
    elif isinstance(value, list):
        for item in value:
            keys.update(_owned_keys(item, practice_id, record_ids))
    return keys


def delete_record(kind: str, record_id: str, actor: dict, intake_id: str | None = None) -> dict:
    if kind not in {"patient", "check"} or (kind == "check" and not intake_id):
        raise ValueError("Choose a patient or saved insurance check.")
    practice_id = actor["practice_id"]
    with intake_links_store._connection() as db:
        receipt = db.execute("SELECT * FROM staff_deletions WHERE practice_id=%s AND kind=%s AND record_id=%s",
                             (practice_id, kind, record_id)).fetchone()
        if receipt and receipt["intake_id"] != intake_id:
            raise LookupError("Saved insurance check not found")
        if not receipt:
            if kind == "patient":
                # Upload submission locks its link before attaching an intake.
                db.execute("""SELECT id FROM insurance_upload_links WHERE practice_id=%s
                    AND (expected_patient_id=%s OR intake_id IN
                        (SELECT id FROM staff_intakes WHERE practice_id=%s AND %s IN (patient_id,auto_patient_id)))
                    ORDER BY id FOR UPDATE""", (practice_id, record_id, practice_id, record_id)).fetchall()
            # Match the intake -> case lock order used by insurance create/save.
            intakes = db.execute(
                "SELECT * FROM staff_intakes WHERE practice_id=%s AND " +
                ("%s IN (patient_id,auto_patient_id,expected_patient_id)" if kind == "patient" else "id=%s") +
                " ORDER BY id FOR UPDATE", (practice_id, record_id if kind == "patient" else intake_id)).fetchall()
            if kind == "patient":
                patient = db.execute("SELECT id FROM staff_patients WHERE id=%s AND practice_id=%s FOR UPDATE",
                                     (record_id, practice_id)).fetchone()
                if not patient:
                    raise LookupError("Clinic patient not found")
                owned_intakes = [row for row in intakes if (row["patient_id"] or row["auto_patient_id"]) == record_id]
                intake_ids = [row["id"] for row in owned_intakes]
                cases = db.execute("""SELECT id,workflow_json FROM insurance_cases WHERE practice_id=%s
                    AND (practice_patient_id=%s OR intake_id=ANY(%s)) ORDER BY id FOR UPDATE""",
                                   (practice_id, record_id, intake_ids)).fetchall()
            else:
                if not intakes or not intakes[0]["patient_id"]:
                    raise LookupError("Insurance intake not found")
                owned_intakes, intake_ids = [], []
                cases = db.execute("""SELECT id,workflow_json FROM insurance_cases
                    WHERE id=%s AND practice_id=%s AND intake_id=%s AND practice_patient_id=%s FOR UPDATE""",
                    (record_id, practice_id, intake_id, intakes[0]["patient_id"])).fetchall()
                if not cases:
                    raise LookupError("Saved insurance check not found")
            workflows = [json.loads(row["workflow_json"]) for row in cases]
            if any(_pending(value) for value in workflows):
                raise ValueError("A payer request is still pending or its outcome is unknown. Resolve that request before deleting this record.")
            case_ids = [row["id"] for row in cases]
            evidence = db.execute("SELECT raw_object_key,status FROM commercial_eligibility_evidence WHERE practice_id=%s AND case_id=ANY(%s) FOR UPDATE",
                                  (practice_id, case_ids)).fetchall()
            if any(row["status"] in {"pending", "indeterminate"} for row in evidence):
                raise ValueError("A payer request is unresolved. Confirm its outcome before deleting this record.")
            keys = _owned_keys([*owned_intakes, *workflows, *evidence], practice_id, [*intake_ids, *case_ids])
            db.execute("DELETE FROM commercial_eligibility_evidence WHERE practice_id=%s AND case_id=ANY(%s)", (practice_id, case_ids))
            db.execute("DELETE FROM insurance_case_events WHERE case_id=ANY(%s)", (case_ids,))
            db.execute("DELETE FROM insurance_cases WHERE practice_id=%s AND id=ANY(%s)", (practice_id, case_ids))
            if kind == "patient":
                # Expire upload links and detach foreign keys without touching another patient's intake.
                db.execute("""UPDATE insurance_upload_links SET status='revoked',expected_patient_id=NULL,intake_id=NULL
                    WHERE practice_id=%s AND (expected_patient_id=%s OR intake_id=ANY(%s))""", (practice_id, record_id, intake_ids))
                db.execute("DELETE FROM staff_intakes WHERE practice_id=%s AND id=ANY(%s)", (practice_id, intake_ids))
                db.execute("""UPDATE staff_intakes SET
                    auto_patient_id=CASE WHEN auto_patient_id=%s THEN NULL ELSE auto_patient_id END,
                    expected_patient_id=CASE WHEN expected_patient_id=%s THEN NULL ELSE expected_patient_id END
                    WHERE practice_id=%s AND %s IN (auto_patient_id,expected_patient_id)""",
                    (record_id, record_id, practice_id, record_id))
                db.execute("DELETE FROM staff_patients WHERE id=%s AND practice_id=%s", (record_id, practice_id))
            else:
                remaining = {key: value for key, value in intakes[0]["workflows"].items() if value != record_id}
                db.execute("""UPDATE staff_intakes SET workflows=%s::jsonb,events=events || %s::jsonb,
                    status=%s,updated_at=%s WHERE id=%s AND practice_id=%s""",
                    (json.dumps(remaining), json.dumps([staff_store._event("Insurance check deleted", actor)]),
                     "in_progress" if remaining else "ready", staff_store.now(), intake_id, practice_id))
            db.execute("""INSERT INTO staff_deletions (practice_id,kind,record_id,intake_id,actor_id,created_at,object_keys)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (practice_id, kind, record_id, intake_id, actor["id"], staff_store.now(), json.dumps(sorted(keys))))
    # Record removal commits first. A failed S3 operation remains durably retryable
    # through the same DELETE endpoint; never lose the only references to these keys.
    with staff_store._connection() as db:
        receipt = db.execute("SELECT object_keys FROM staff_deletions WHERE practice_id=%s AND kind=%s AND record_id=%s FOR UPDATE",
                             (practice_id, kind, record_id)).fetchone()
        pending = []
        for key in receipt["object_keys"]:
            try:
                if not object_store.enabled():
                    raise RuntimeError("File storage unavailable")
                object_store.delete_object(key)
            except Exception:
                pending.append(key)
        db.execute("UPDATE staff_deletions SET object_keys=%s::jsonb WHERE practice_id=%s AND kind=%s AND record_id=%s",
                   (json.dumps(pending), practice_id, kind, record_id))
    return {"deleted": True, "cleanup_pending": bool(pending)}
