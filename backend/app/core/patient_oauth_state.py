"""Patient oauth state backed by the patient database connection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


def _connection():
    from app.core import patient_store
    return patient_store._connection()

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
