"""Patient identity verification events, sessions and account links."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from app.core.patient_models import StoredIdmeTransaction, IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS
from app.core.patient_ids import _is_google_generated_patient


def _connection():
    from app.core import patient_store
    return patient_store._connection()

def _hash_session_token(value):
    from app.core import patient_store
    return patient_store._hash_session_token(value)

def save_idme_oidc_transaction(
    intent: str,
    patient_id: str | None,
    state: str,
    browser_token: str,
    nonce: str,
    pkce_verifier: str,
) -> None:
    """Persist only hashed browser/state handles for one short-lived OIDC callback."""
    if intent not in {"login", "link", "tefca_ias"} or (
        (intent in {"link", "tefca_ias"}) != (patient_id is not None)
    ):
        raise ValueError("Invalid ID.me transaction intent")
    now = datetime.now(timezone.utc)
    with _connection() as connection:
        connection.execute("DELETE FROM idme_oidc_transactions WHERE expires_at < %s", (now.isoformat(),))
        connection.execute(
            """INSERT INTO idme_oidc_transactions
               (state_hash, browser_hash, intent, patient_id, nonce, pkce_verifier, created_at, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                _hash_session_token(state),
                _hash_session_token(browser_token),
                intent,
                patient_id,
                nonce,
                pkce_verifier,
                now.isoformat(),
                (now + timedelta(seconds=IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS)).isoformat(),
            ),
        )

def consume_idme_oidc_transaction(state: str, browser_token: str) -> StoredIdmeTransaction | None:
    """Atomically consume a valid transaction bound to the initiating browser."""
    if not state or not browser_token:
        return None
    with _connection() as connection:
        row = connection.execute(
            """DELETE FROM idme_oidc_transactions
               WHERE state_hash = %s AND browser_hash = %s AND expires_at >= %s
               RETURNING intent, patient_id, nonce, pkce_verifier""",
            (
                _hash_session_token(state),
                _hash_session_token(browser_token),
                datetime.now(timezone.utc).isoformat(),
            ),
        ).fetchone()
    return StoredIdmeTransaction(**dict(row)) if row else None

def _lock_idme_identity(connection: psycopg.Connection[Any], issuer: str, subject: str) -> None:
    # A transaction-scoped lock keeps first-login creation deterministic without
    # exposing the identity or relying on a process-local lock across ECS tasks.
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{issuer}\n{subject}",))

def _add_identity_event(
    connection: psycopg.Connection[Any],
    patient_id: str | None,
    intent: str,
    outcome: str,
    reason_code: str,
    assurance_present: bool = False,
    jti_present: bool = False,
    policy_scope: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO identity_verification_events
           (provider, patient_id, intent, outcome, reason_code, assurance_present,
            jti_present, policy_scope, created_at)
           VALUES ('idme', %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            patient_id,
            intent,
            outcome,
            reason_code,
            assurance_present,
            jti_present,
            policy_scope,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

def record_idme_verification_event(
    patient_id: str | None, intent: str, outcome: str, reason_code: str,
) -> None:
    """Record bounded reason codes only; never persist tokens, claims, or subjects."""
    if intent not in {"login", "link", "tefca_ias"} or outcome not in {"success", "failure"}:
        raise ValueError("Invalid ID.me audit event")
    allowed_reasons = {
        "provider_denied",
        "session_changed",
        "verification_failed",
        "identity_not_linked",
        "verified",
        "test_patient_not_allowed",
    }
    if reason_code not in allowed_reasons:
        raise ValueError("Invalid ID.me audit reason")
    with _connection() as connection:
        _add_identity_event(connection, patient_id, intent, outcome, reason_code)

def create_idme_patient_session(
    issuer: str,
    subject: str,
    allowed_patient_ids: frozenset[str] | None = None,
) -> tuple[str, str] | None:
    """Authenticate one linked identity and issue its session atomically."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with _connection() as connection:
        # Unlink takes the same advisory lock and deletes every session in its
        # transaction. This prevents an in-flight callback from recreating a
        # session immediately after the user disconnects the identity.
        _lock_idme_identity(connection, issuer, subject)
        existing = connection.execute(
            """SELECT patient_id FROM patient_external_identities
               WHERE provider = 'idme' AND issuer = %s AND subject = %s""",
            (issuer, subject),
        ).fetchone()
        if existing is None:
            return None
        patient_id = str(existing["patient_id"])
        if allowed_patient_ids is not None and patient_id not in allowed_patient_ids:
            _add_identity_event(connection, patient_id, "login", "failure", "test_patient_not_allowed")
            return None
        connection.execute(
            """UPDATE patient_external_identities SET last_authenticated_at = %s, last_seen_at = %s
               WHERE provider = 'idme' AND issuer = %s AND subject = %s""",
            (now.isoformat(), now.isoformat(), issuer, subject),
        )
        connection.execute("DELETE FROM patient_sessions WHERE expires_at < %s", (now.isoformat(),))
        connection.execute(
            """INSERT INTO patient_sessions (session_hash, patient_id, expires_at, created_at)
               VALUES (%s, %s, %s, %s)""",
            (
                _hash_session_token(token),
                patient_id,
                (now + timedelta(days=30)).isoformat(),
                now.isoformat(),
            ),
        )
        _add_identity_event(connection, patient_id, "login", "success", "authenticated")
    return patient_id, token

def link_idme_identity(
    patient_id: str,
    session_token: str,
    issuer: str,
    subject: str,
    assurance_level: str | None = None,
    policy_scope: str | None = None,
) -> bool:
    """Link an unused identity only while the initiating patient session is valid."""
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        # Identity first, patient second is the shared lock order with login and
        # unlink. This avoids a patient-row/advisory-lock deadlock under races.
        _lock_idme_identity(connection, issuer, subject)
        patient = connection.execute(
            "SELECT patient_id FROM patients WHERE patient_id = %s FOR UPDATE", (patient_id,)
        ).fetchone()
        if patient is None:
            return False
        session = connection.execute(
            """SELECT 1 FROM patient_sessions
               WHERE session_hash = %s AND patient_id = %s AND expires_at >= %s""",
            (_hash_session_token(session_token), patient_id, now),
        ).fetchone()
        if session is None:
            _add_identity_event(connection, patient_id, "link", "failure", "session_changed")
            return False
        identity = connection.execute(
            """SELECT patient_id FROM patient_external_identities
               WHERE provider = 'idme' AND issuer = %s AND subject = %s""",
            (issuer, subject),
        ).fetchone()
        if identity is not None:
            if str(identity["patient_id"]) != patient_id:
                _add_identity_event(connection, patient_id, "link", "failure", "identity_conflict")
                return False
            connection.execute(
                """UPDATE patient_external_identities
                   SET last_authenticated_at = %s, last_seen_at = %s,
                       assurance_level = %s, policy_scope = %s, verified_at = %s
                   WHERE provider = 'idme' AND issuer = %s AND subject = %s""",
                (now, now, assurance_level, policy_scope, now, issuer, subject),
            )
            _add_identity_event(connection, patient_id, "link", "success", "already_linked")
            return True
        already_linked = connection.execute(
            """SELECT 1 FROM patient_external_identities
               WHERE provider = 'idme' AND patient_id = %s""",
            (patient_id,),
        ).fetchone()
        if already_linked is not None:
            _add_identity_event(connection, patient_id, "link", "failure", "different_identity_already_linked")
            return False
        connection.execute(
            """INSERT INTO patient_external_identities
               (provider, issuer, subject, patient_id, created_at, last_authenticated_at,
                assurance_level, policy_scope, verified_at, last_seen_at)
               VALUES ('idme', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (issuer, subject, patient_id, now, now, assurance_level, policy_scope, now, now),
        )
        _add_identity_event(connection, patient_id, "link", "success", "linked")
    return True

def record_idme_tefca_verification(
    patient_id: str,
    assurance_level: str | None,
    policy_scope: str | None,
    jti_present: bool,
) -> None:
    """Record only safe verification metadata; raw claims and tokens are discarded."""
    with _connection() as connection:
        _add_identity_event(
            connection,
            patient_id,
            "tefca_ias",
            "success",
            "verified",
            assurance_present=assurance_level is not None,
            jti_present=jti_present,
            policy_scope=policy_scope,
        )

def patient_has_idme_identity(patient_id: str) -> bool:
    with _connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM patient_external_identities WHERE provider = 'idme' AND patient_id = %s",
            (patient_id,),
        ).fetchone()
    return row is not None

def unlink_idme_identity(patient_id: str, google_login_available: bool = False) -> str:
    """Remove ID.me only when another usable local/Google sign-in remains."""
    with _connection() as connection:
        identity = connection.execute(
            """SELECT issuer, subject FROM patient_external_identities
               WHERE provider = 'idme' AND patient_id = %s""",
            (patient_id,),
        ).fetchone()
        if identity is None:
            return "missing"
        _lock_idme_identity(connection, str(identity["issuer"]), str(identity["subject"]))
        patient = connection.execute(
            """SELECT patients.password_login_enabled, patient_drive_connections.google_subject
               FROM patients LEFT JOIN patient_drive_connections USING (patient_id)
               WHERE patients.patient_id = %s FOR UPDATE OF patients""",
            (patient_id,),
        ).fetchone()
        if patient is None:
            return "missing"
        # Recheck after both locks in case a preceding transaction removed it.
        identity_still_linked = connection.execute(
            "SELECT 1 FROM patient_external_identities WHERE provider = 'idme' AND patient_id = %s",
            (patient_id,),
        ).fetchone()
        if identity_still_linked is None:
            return "missing"
        google_subject = patient["google_subject"]
        password_login_usable = bool(patient["password_login_enabled"]) and not (
            _is_google_generated_patient(patient_id, google_subject)
        )
        if not password_login_usable and not (
            google_subject is not None and google_login_available
        ):
            _add_identity_event(connection, patient_id, "unlink", "failure", "last_login")
            return "last_login"
        connection.execute(
            "DELETE FROM patient_external_identities WHERE provider = 'idme' AND patient_id = %s",
            (patient_id,),
        )
        # An ID.me-originated session is intentionally indistinguishable from
        # other patient sessions. Revoke all of them so disconnecting a
        # compromised identity actually ends access on every device.
        connection.execute("DELETE FROM patient_sessions WHERE patient_id = %s", (patient_id,))
        _add_identity_event(connection, patient_id, "unlink", "success", "unlinked")
    return "unlinked"
