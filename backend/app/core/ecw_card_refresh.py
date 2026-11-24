"""Shared, patient-bound eCW card refresh queue and short-lived preview metadata."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.core import staff_store


FRESH_HOURS = 18  # S3 expires insurance-cases objects after one day.
RETRY_MINUTES = 5
LEASE_SECONDS = 300
MIN_SCAN_SPACING_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh(row: dict, moment: datetime) -> bool:
    return bool(row.get('checked_at') and row['checked_at'] > moment - timedelta(hours=FRESH_HOURS))


def linked_practices() -> dict[str, set[str]]:
    """Confirmed practice-to-eCW links for scheduled near-term warmup."""
    with staff_store._connection() as connection:
        rows = connection.execute("""SELECT DISTINCT practice_id,ecw_patient_id FROM staff_patients
            WHERE identity_status='confirmed' AND ecw_patient_id IS NOT NULL""").fetchall()
    linked: dict[str, set[str]] = {}
    for row in rows:
        linked.setdefault(row['practice_id'], set()).add(row['ecw_patient_id'])
    return linked


def enqueue(practice_id: str, patient_id: str, *, force: bool = False, priority: int = 10) -> dict:
    """Deduplicate refreshes across requests and app tasks; preserve last good result."""
    moment = _now()
    with staff_store._connection() as connection:
        connection.execute("""INSERT INTO staff_ecw_card_refresh (practice_id,ecw_patient_id,status,priority,updated_at)
            VALUES (%s,%s,'queued',%s,%s) ON CONFLICT DO NOTHING""", (practice_id, patient_id, priority, moment))
        row = connection.execute("""SELECT * FROM staff_ecw_card_refresh
            WHERE practice_id=%s AND ecw_patient_id=%s FOR UPDATE""", (practice_id, patient_id)).fetchone()
        running = row['status'] == 'running' and row['lease_until'] and row['lease_until'] > moment
        queued = row['status'] == 'queued'
        recent_failure = row['status'] == 'failed' and row['updated_at'] > moment - timedelta(minutes=RETRY_MINUTES)
        if queued and row['priority'] < priority:
            connection.execute("""UPDATE staff_ecw_card_refresh SET priority=%s
                WHERE practice_id=%s AND ecw_patient_id=%s""", (priority, practice_id, patient_id))
            row = {**row, 'priority': priority}
        if not (running or queued or (not force and (_fresh(row, moment) or recent_failure))):
            connection.execute("""UPDATE staff_ecw_card_refresh SET status='queued',priority=%s,updated_at=%s,lease_until=NULL
                WHERE practice_id=%s AND ecw_patient_id=%s""", (priority, moment, practice_id, patient_id))
            row = {**row, 'status': 'queued', 'priority': priority, 'updated_at': moment}
    return dict(row)


def enqueue_many(practice_id: str, patient_ids: set[str]) -> None:
    if not patient_ids:
        return
    moment = _now()
    with staff_store._connection() as connection:
        for patient_id in patient_ids:
            connection.execute("""INSERT INTO staff_ecw_card_refresh AS current
                (practice_id,ecw_patient_id,status,priority,updated_at) VALUES (%s,%s,'queued',0,%s)
                ON CONFLICT (practice_id,ecw_patient_id) DO UPDATE
                SET status='queued',priority=0,updated_at=EXCLUDED.updated_at,lease_until=NULL
                WHERE (current.status='ready' AND (current.checked_at IS NULL OR current.checked_at<%s))
                   OR (current.status='failed' AND current.updated_at<%s)
                   OR (current.status='running' AND current.lease_until<%s)""",
                (practice_id, patient_id, moment, moment - timedelta(hours=FRESH_HOURS),
                 moment - timedelta(minutes=RETRY_MINUTES), moment))


def status(practice_id: str, patient_id: str) -> dict | None:
    with staff_store._connection() as connection:
        row = connection.execute("""SELECT * FROM staff_ecw_card_refresh
            WHERE practice_id=%s AND ecw_patient_id=%s""", (practice_id, patient_id)).fetchone()
    return dict(row) if row else None


def current_or_enqueue(practice_id: str, patient_id: str) -> dict:
    row = status(practice_id, patient_id)
    moment = _now()
    if row and (_fresh(row, moment) or (row['status'] == 'queued' and row['priority'] >= 10) or
                (row['status'] == 'running' and row['lease_until'] and row['lease_until'] > moment) or
                (row['status'] == 'failed' and row['updated_at'] > moment - timedelta(minutes=RETRY_MINUTES))):
        return row
    return enqueue(practice_id, patient_id)


def claim() -> dict | None:
    """One global eCW scan at a time; expired leases recover after a task restart."""
    moment = _now()
    with staff_store._connection() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (8_240_715_303,))
        active = connection.execute("""SELECT 1 FROM staff_ecw_card_refresh
            WHERE status='running' AND lease_until>%s LIMIT 1""", (moment,)).fetchone()
        if active:
            return None
        last_completed = connection.execute("""SELECT max(finished_at) AS finished_at FROM staff_ecw_card_refresh
            WHERE finished_at IS NOT NULL""").fetchone()['finished_at']
        if last_completed and last_completed > moment - timedelta(seconds=MIN_SCAN_SPACING_SECONDS):
            return None
        row = connection.execute("""SELECT * FROM staff_ecw_card_refresh
            WHERE status='queued' OR (status='running' AND lease_until<=%s)
            ORDER BY priority DESC,updated_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (moment,)).fetchone()
        if not row:
            return None
        lease_until = moment + timedelta(seconds=LEASE_SECONDS)
        connection.execute("""UPDATE staff_ecw_card_refresh SET status='running',updated_at=%s,lease_until=%s
            WHERE practice_id=%s AND ecw_patient_id=%s""",
            (moment, lease_until, row['practice_id'], row['ecw_patient_id']))
    return {**dict(row), 'lease_until': lease_until}


def complete(job: dict, cards: list[dict], *, failed: bool = False) -> list[str]:
    """Publish only completed scans and return replaced S3 keys for cleanup."""
    moment = _now()
    with staff_store._connection() as connection:
        row = connection.execute("""SELECT cards FROM staff_ecw_card_refresh WHERE practice_id=%s AND ecw_patient_id=%s
            AND status='running' AND lease_until=%s FOR UPDATE""",
            (job['practice_id'], job['ecw_patient_id'], job['lease_until'])).fetchone()
        if not row:
            return [card['object_key'] for card in cards]
        if failed:
            connection.execute("""UPDATE staff_ecw_card_refresh SET status='failed',updated_at=%s,finished_at=%s,lease_until=NULL
                WHERE practice_id=%s AND ecw_patient_id=%s AND lease_until=%s""",
                (moment, moment, job['practice_id'], job['ecw_patient_id'], job['lease_until']))
            return [card['object_key'] for card in cards]
        old = [card['object_key'] for card in row['cards']]
        connection.execute("""UPDATE staff_ecw_card_refresh SET status='ready',cards=%s::jsonb,checked_at=%s,
            updated_at=%s,finished_at=%s,lease_until=NULL WHERE practice_id=%s AND ecw_patient_id=%s AND lease_until=%s""",
            (json.dumps(cards), moment, moment, moment, job['practice_id'], job['ecw_patient_id'], job['lease_until']))
    return old
