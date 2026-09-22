"""RDS persistence for temporary patient shares and durable practice reviews."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.object_store import delete_object
from app.core.patient_store import _connection
from app.insurance.golden import GoldenCaseError


CASE_TTL = timedelta(hours=24)


class StaffCaseConflict(GoldenCaseError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _purge(connection: Any, now: datetime) -> None:
    expired = connection.execute(
        "SELECT workflow_json FROM insurance_cases WHERE practice_id IS NULL AND expires_at <= %s", (now.isoformat(),),
    ).fetchall()
    for row in expired:
        try:
            workflow = json.loads(row["workflow_json"])
            for source in workflow.get("documents", {}).values():
                if isinstance(source, dict) and isinstance(source.get("object_key"), str):
                    delete_object(source["object_key"])
        except Exception:
            # A lifecycle rule also removes case PDFs. Expiry must never be held by S3 cleanup.
            pass
    connection.execute("DELETE FROM insurance_cases WHERE practice_id IS NULL AND expires_at <= %s", (now.isoformat(),))


def _owner(patient_id: str, share_manifest_id: str, practice_id: str | None = None, intake_id: str | None = None, staff_actor: dict | None = None) -> tuple[str, tuple]:
    if practice_id:
        if not intake_id:
            raise ValueError("A practice review must belong to an intake")
        return "practice_id = %s AND practice_patient_id = %s AND intake_id = %s", (practice_id, patient_id, intake_id)
    return "practice_id IS NULL AND patient_id = %s AND share_manifest_id = %s AND expires_at > %s", (patient_id, share_manifest_id, _now().isoformat())


def _staff_progress(connection: Any, case: dict, event: str, scope: dict, stamp: str) -> None:
    if not scope.get("practice_id"):
        return
    actor = scope.get("staff_actor") or {}
    version = {"availity": "Connected review", "commercial": "Commercial estimate"}.get(case.get("source_mode"), "Document review")
    activity = {"id": str(uuid4()), "action": f"{version}: {event.replace('-', ' ')}"[:100], "created_at": stamp,
                "actor": actor.get("display_name", "Clinic staff"), "actor_id": actor.get("id")}
    status = "reviewed" if case.get("source_mode") == "documents" and case.get("status") == "complete" else "in_progress"
    connection.execute(
        """UPDATE staff_intakes SET status = %s, events = events || %s::jsonb, updated_at = %s
           WHERE id = %s AND practice_id = %s""",
        (status, json.dumps([activity]), stamp, scope["intake_id"], scope["practice_id"]),
    )


def create_case(patient_id: str, share_manifest_id: str, evidence_manifest_id: str, workflow: dict[str, Any], *, practice_id: str | None = None, intake_id: str | None = None, expected_intake_updated_at: str | None = None, staff_actor: dict | None = None) -> dict[str, Any]:
    now, case_id = _now(), str(uuid4())
    _owner(patient_id, share_manifest_id, practice_id, intake_id)
    workflow = {**workflow, "revision": workflow.get("revision", 0), "id": case_id, "created_at": now.isoformat(), "expires_at": None if practice_id else (now + CASE_TTL).isoformat()}
    with _connection() as connection:
        _purge(connection, now)
        if practice_id:
            # The same row lock guards staff confirmation. Claim and attach the case
            # together, so two starts cannot fork payer transactions or stale identity.
            intake = connection.execute(
                "SELECT patient_id,workflows,updated_at FROM staff_intakes WHERE id = %s AND practice_id = %s FOR UPDATE",
                (intake_id, practice_id),
            ).fetchone()
            if not intake or not intake["patient_id"] or intake["patient_id"] != patient_id:
                raise StaffCaseConflict("The intake patient changed. Reopen the intake and confirm the correct patient.")
            version = {"documents": "v1", "availity": "v2", "commercial": "commercial"}.get(workflow.get("source_mode"))
            if not version:
                raise StaffCaseConflict("Unknown staff insurance workflow")
            existing_id = intake["workflows"].get(version)
            if existing_id:
                existing = connection.execute(
                    """SELECT workflow_json,evidence_manifest_id FROM insurance_cases
                       WHERE id = %s AND practice_id = %s AND practice_patient_id = %s AND intake_id = %s""",
                    (existing_id, practice_id, patient_id, intake_id),
                ).fetchone()
                if not existing or existing["evidence_manifest_id"] != evidence_manifest_id:
                    raise StaffCaseConflict("The existing review configuration changed. Start a new intake for a new review.")
                return json.loads(existing["workflow_json"])
            if expected_intake_updated_at and intake["updated_at"] != expected_intake_updated_at:
                raise StaffCaseConflict("The intake details changed while opening this review. Reopen the intake and try again.")
        connection.execute(
            """INSERT INTO insurance_cases
               (id, patient_id, share_manifest_id, evidence_manifest_id, workflow_json, created_at, expires_at,
                practice_id, practice_patient_id, intake_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (case_id, None if practice_id else patient_id, share_manifest_id, evidence_manifest_id,
             json.dumps(workflow, separators=(",", ":")), now.isoformat(), workflow["expires_at"],
             practice_id, patient_id if practice_id else None, intake_id),
        )
        connection.execute(
            "INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
            (case_id, "created", now.isoformat()),
        )
        if practice_id:
            connection.execute(
                """UPDATE staff_intakes SET workflows = workflows || %s::jsonb, updated_at = %s
                   WHERE id = %s AND practice_id = %s""",
                (json.dumps({version: case_id}), now.isoformat(), intake_id, practice_id),
            )
            _staff_progress(connection, workflow, "started", {"practice_id": practice_id, "intake_id": intake_id, "staff_actor": staff_actor}, now.isoformat())
    return workflow


def get_case(case_id: str, patient_id: str, share_manifest_id: str, **scope) -> dict[str, Any] | None:
    now = _now()
    owner, values = _owner(patient_id, share_manifest_id, **scope)
    with _connection() as connection:
        _purge(connection, now)
        row = connection.execute(
            f"SELECT workflow_json FROM insurance_cases WHERE id = %s AND {owner}",
            (case_id, *values),
        ).fetchone()
    return json.loads(row["workflow_json"]) if row else None


def save_case(case: dict[str, Any], patient_id: str, share_manifest_id: str, event: str, **scope) -> dict[str, Any] | None:
    now = _now()
    owner, values = _owner(patient_id, share_manifest_id, **scope)
    revision = case.get("revision", 0)
    value = {**case, "revision": revision + 1}
    with _connection() as connection:
        _purge(connection, now)
        updated = connection.execute(
            f"""UPDATE insurance_cases SET workflow_json = %s WHERE id = %s AND {owner}
                AND COALESCE(workflow_json::jsonb ->> 'revision', '0') = %s""",
            (json.dumps(value, separators=(",", ":")), case["id"], *values, str(revision)),
        )
        if updated.rowcount != 1:
            if connection.execute(f"SELECT 1 FROM insurance_cases WHERE id = %s AND {owner}", (case["id"], *values)).fetchone():
                raise StaffCaseConflict("This review changed in another request. Reopen the review before continuing.")
            return None
        connection.execute(
            "INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
            (case["id"], event[:80], now.isoformat()),
        )
        _staff_progress(connection, value, event, scope, now.isoformat())
    return value


def save_v2(case: dict[str, Any], patient_id: str, share_manifest_id: str, event: str, revision: int, **scope) -> dict[str, Any] | None:
    """Claim a revision before network I/O; duplicate clicks cannot submit twice."""
    now = _now()
    value = {**case, "revision": revision + 1}
    owner, values = _owner(patient_id, share_manifest_id, **scope)
    with _connection() as connection:
        updated = connection.execute(
            f"""UPDATE insurance_cases SET workflow_json = %s
               WHERE id = %s AND {owner}
               AND workflow_json::jsonb ->> 'revision' = %s
               AND workflow_json::jsonb ->> 'source_mode' = 'availity'""",
            (json.dumps(value, separators=(",", ":")), value["id"], *values, str(revision)),
        )
        if updated.rowcount != 1:
            return None
        connection.execute("INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
                           (value["id"], event[:80], now.isoformat()))
        _staff_progress(connection, value, event, scope, now.isoformat())
    return value


def latest_v2(patient_id: str, share_manifest_id: str, binding: str, **scope) -> dict[str, Any] | None:
    owner, values = _owner(patient_id, share_manifest_id, **scope)
    with _connection() as connection:
        row = connection.execute(
            f"""SELECT workflow_json FROM insurance_cases
               WHERE {owner} AND evidence_manifest_id = %s
               AND workflow_json::jsonb ->> 'source_mode' = 'availity'
               ORDER BY created_at DESC LIMIT 1""",
            (*values, binding),
        ).fetchone()
    return json.loads(row["workflow_json"]) if row else None
