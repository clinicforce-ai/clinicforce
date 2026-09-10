"""RDS persistence for short-lived insurance beta cases."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.object_store import delete_object
from app.core.patient_store import _connection


CASE_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _purge(connection: Any, now: datetime) -> None:
    expired = connection.execute(
        "SELECT workflow_json FROM insurance_cases WHERE expires_at <= %s", (now.isoformat(),),
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
    connection.execute("DELETE FROM insurance_cases WHERE expires_at <= %s", (now.isoformat(),))


def create_case(patient_id: str, share_manifest_id: str, evidence_manifest_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    now, case_id = _now(), str(uuid4())
    workflow = {**workflow, "id": case_id, "created_at": now.isoformat(), "expires_at": (now + CASE_TTL).isoformat()}
    with _connection() as connection:
        _purge(connection, now)
        connection.execute(
            """INSERT INTO insurance_cases
               (id, patient_id, share_manifest_id, evidence_manifest_id, workflow_json, created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (case_id, patient_id, share_manifest_id, evidence_manifest_id,
             json.dumps(workflow, separators=(",", ":")), now.isoformat(), workflow["expires_at"]),
        )
        connection.execute(
            "INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
            (case_id, "created", now.isoformat()),
        )
    return workflow


def get_case(case_id: str, patient_id: str, share_manifest_id: str) -> dict[str, Any] | None:
    now = _now()
    with _connection() as connection:
        _purge(connection, now)
        row = connection.execute(
            """SELECT workflow_json FROM insurance_cases
               WHERE id = %s AND patient_id = %s AND share_manifest_id = %s AND expires_at > %s""",
            (case_id, patient_id, share_manifest_id, now.isoformat()),
        ).fetchone()
    return json.loads(row["workflow_json"]) if row else None


def save_case(case: dict[str, Any], patient_id: str, share_manifest_id: str, event: str) -> dict[str, Any] | None:
    now = _now()
    with _connection() as connection:
        _purge(connection, now)
        updated = connection.execute(
            """UPDATE insurance_cases SET workflow_json = %s
               WHERE id = %s AND patient_id = %s AND share_manifest_id = %s AND expires_at > %s""",
            (json.dumps(case, separators=(",", ":")), case["id"], patient_id, share_manifest_id, now.isoformat()),
        )
        if updated.rowcount != 1:
            return None
        connection.execute(
            "INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
            (case["id"], event[:80], now.isoformat()),
        )
    return case


def save_v2(case: dict[str, Any], patient_id: str, share_manifest_id: str, event: str, revision: int) -> dict[str, Any] | None:
    """Claim a revision before network I/O; duplicate clicks cannot submit twice."""
    now = _now()
    value = {**case, "revision": revision + 1}
    with _connection() as connection:
        updated = connection.execute(
            """UPDATE insurance_cases SET workflow_json = %s
               WHERE id = %s AND patient_id = %s AND share_manifest_id = %s AND expires_at > %s
               AND workflow_json::jsonb ->> 'revision' = %s
               AND workflow_json::jsonb ->> 'source_mode' = 'availity'""",
            (json.dumps(value, separators=(",", ":")), value["id"], patient_id, share_manifest_id,
             now.isoformat(), str(revision)),
        )
        if updated.rowcount != 1:
            return None
        connection.execute("INSERT INTO insurance_case_events (case_id, stage, created_at) VALUES (%s, %s, %s)",
                           (value["id"], event[:80], now.isoformat()))
    return value


def latest_v2(patient_id: str, share_manifest_id: str, binding: str) -> dict[str, Any] | None:
    with _connection() as connection:
        row = connection.execute(
            """SELECT workflow_json FROM insurance_cases
               WHERE patient_id = %s AND share_manifest_id = %s AND evidence_manifest_id = %s
               AND expires_at > %s AND workflow_json::jsonb ->> 'source_mode' = 'availity'
               ORDER BY created_at DESC LIMIT 1""",
            (patient_id, share_manifest_id, binding, _now().isoformat()),
        ).fetchone()
    return json.loads(row["workflow_json"]) if row else None
