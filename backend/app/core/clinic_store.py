"""Clinic registration and single-use staff invitations; never joins a clinic by code."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from psycopg.errors import UniqueViolation

from app.core import staff_store

_schema_initialized = False
_schema_lock = Lock()
RESERVED_CODES = {"api", "assets", "join", "patient", "staff", "manifest", "upload", "insurance", "healthz", "login", "register", "admin", "favicon", "index"}


def _connection():
    global _schema_initialized
    connection = staff_store._connection()
    try:
        if not _schema_initialized:
            with _schema_lock:
                if not _schema_initialized:
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (8_240_715_305,))
                    connection.execute("""CREATE TABLE IF NOT EXISTS clinics (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_invitations (
                        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL REFERENCES clinics(id),
                        created_by TEXT NOT NULL REFERENCES staff_members(id), token_hash TEXT NOT NULL UNIQUE,
                        role TEXT NOT NULL CHECK (role IN ('staff','admin')), created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL, accepted_at TEXT, revoked_at TEXT,
                        accepted_by TEXT REFERENCES staff_members(id)
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS staff_invitations_practice_idx ON staff_invitations(practice_id,created_at DESC)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS clinic_action_limits (
                        key_hash TEXT PRIMARY KEY, bucket BIGINT NOT NULL, attempts INTEGER NOT NULL
                    )""")
                    connection.commit()
                    _schema_initialized = True
    except Exception:
        connection.close()
        raise
    return connection


def valid_code(code: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9-]{2,39}", code)) and code not in RESERVED_CODES and not code.endswith("-")


def _legacy_clinic() -> None:
    # Reserve the explicitly deployed clinic before accepting public registration.
    try:
        config = json.loads(os.getenv("STAFF_BOOTSTRAP_CREDENTIALS", "{}"))
        if config.get("practice_id") and config.get("practice_name"):
            get_for_practice(str(config["practice_id"]), str(config["practice_name"]))
    except (ValueError, TypeError, AttributeError):
        return


def get_for_practice(practice_id: str, practice_name: str) -> dict:
    code = practice_id.lower() if valid_code(practice_id.lower()) else "clinic-" + hashlib.sha256(practice_id.encode()).hexdigest()[:12]
    with _connection() as connection:
        row = connection.execute("SELECT * FROM clinics WHERE id = %s", (practice_id,)).fetchone()
        if not row:
            connection.execute("INSERT INTO clinics(id,name,code,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (practice_id, practice_name, code, staff_store.now()))
            row = connection.execute("SELECT * FROM clinics WHERE id = %s", (practice_id,)).fetchone()
            if not row:
                code = "clinic-" + hashlib.sha256(practice_id.encode()).hexdigest()[:20]
                connection.execute("INSERT INTO clinics(id,name,code,created_at) VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING", (practice_id, practice_name, code, staff_store.now()))
                row = connection.execute("SELECT * FROM clinics WHERE id = %s", (practice_id,)).fetchone()
    return {key: row[key] for key in ("id", "name", "code")}


def get_clinic(code: str) -> dict | None:
    _legacy_clinic()
    with _connection() as connection:
        row = connection.execute("SELECT id,name,code FROM clinics WHERE code = %s", (code,)).fetchone()
    return dict(row) if row else None


def allow_action(action: str, identifier: str, limit: int, seconds: int) -> bool:
    key = hashlib.sha256((action + ":" + identifier).encode()).hexdigest()
    bucket = int(time.time()) // seconds
    with _connection() as connection:
        row = connection.execute("""INSERT INTO clinic_action_limits(key_hash,bucket,attempts) VALUES (%s,%s,1)
            ON CONFLICT(key_hash) DO UPDATE SET bucket=EXCLUDED.bucket,
            attempts=CASE WHEN clinic_action_limits.bucket=EXCLUDED.bucket THEN clinic_action_limits.attempts+1 ELSE 1 END
            RETURNING attempts""", (key, bucket)).fetchone()
    return row["attempts"] <= limit


def register_clinic(*, clinic_name: str, clinic_code: str, display_name: str, email: str, password: str) -> dict:
    if not valid_code(clinic_code):
        raise ValueError("Choose a clinic code of 3–40 lowercase letters, numbers or hyphens, starting with a letter.")
    _legacy_clinic()
    practice_id, staff_id, stamp = str(uuid4()), str(uuid4()), staff_store.now()
    password_hash = staff_store.hash_password(password)
    try:
        with _connection() as connection:
            connection.execute("INSERT INTO clinics(id,name,code,created_at) VALUES (%s,%s,%s,%s)", (practice_id, clinic_name, clinic_code, stamp))
            row = connection.execute(f"""INSERT INTO staff_members(id,username,display_name,practice_id,practice_name,role,password_hash,created_at)
                VALUES (%s,%s,%s,%s,%s,'admin',%s,%s) RETURNING {staff_store.STAFF_FIELDS}""", (staff_id, email, display_name, practice_id, clinic_name, password_hash, stamp)).fetchone()
    except UniqueViolation:
        raise ValueError("This email or clinic code is already in use. Sign in, use your clinic invitation, or choose a different code.") from None
    return dict(row)


def invitation_status(row: dict) -> str:
    return "accepted" if row.get("accepted_at") else "revoked" if row.get("revoked_at") else "expired" if row["expires_at"] <= staff_store.now() else "open"


def create_invitation(staff: dict, role: str) -> tuple[dict, str]:
    get_for_practice(staff["practice_id"], staff["practice_name"])
    token, invite_id = secrets.token_urlsafe(32), str(uuid4())
    moment = datetime.now(timezone.utc)
    with _connection() as connection:
        row = connection.execute("""INSERT INTO staff_invitations(id,practice_id,created_by,token_hash,role,created_at,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,role,created_at,expires_at""", (invite_id, staff["practice_id"], staff["id"], staff_store._hash(token), role, moment.isoformat(), (moment+timedelta(days=7)).isoformat())).fetchone()
    return {**dict(row), "status": "open"}, token


def get_invitation(token: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    with _connection() as connection:
        row = connection.execute("""SELECT i.*,c.name AS clinic_name FROM staff_invitations i
            JOIN clinics c ON c.id=i.practice_id WHERE token_hash=%s""", (staff_store._hash(token),)).fetchone()
    return dict(row) if row else None


def list_invitations(practice_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute("SELECT id,role,created_at,expires_at,accepted_at,revoked_at FROM staff_invitations WHERE practice_id=%s ORDER BY created_at DESC LIMIT 100", (practice_id,)).fetchall()
    return [{**dict(row), "status": invitation_status(row)} for row in rows]


def revoke_invitation(invite_id: str, practice_id: str) -> bool:
    with _connection() as connection:
        result = connection.execute("UPDATE staff_invitations SET revoked_at=COALESCE(revoked_at,%s) WHERE id=%s AND practice_id=%s AND accepted_at IS NULL", (staff_store.now(), invite_id, practice_id))
    return result.rowcount == 1


def accept_invitation(token: str, *, display_name: str, email: str, password: str) -> dict:
    password_hash = staff_store.hash_password(password)
    try:
        with _connection() as connection:
            row = connection.execute("""SELECT i.*,c.name AS clinic_name FROM staff_invitations i JOIN clinics c ON c.id=i.practice_id
                WHERE token_hash=%s FOR UPDATE OF i""", (staff_store._hash(token),)).fetchone()
            if not row or invitation_status(row) != "open":
                raise ValueError("This invitation is expired, revoked or already used. Ask your clinic administrator for a new invitation.")
            member = connection.execute(f"""INSERT INTO staff_members(id,username,display_name,practice_id,practice_name,role,password_hash,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {staff_store.STAFF_FIELDS}""", (str(uuid4()), email, display_name, row["practice_id"], row["clinic_name"], row["role"], password_hash, staff_store.now())).fetchone()
            connection.execute("UPDATE staff_invitations SET accepted_at=%s,accepted_by=%s WHERE id=%s", (staff_store.now(), member["id"], row["id"]))
    except UniqueViolation:
        raise ValueError("This email already has a staff account. Sign in with that account or use a different staff email.") from None
    return dict(member)


def validate_invitation_url(invite_id: str, practice_id: str, url: str, public_base: str) -> dict | None:
    try:
        target, base = urlsplit(url), urlsplit(public_base)
    except (ValueError, TypeError):
        return None
    match = re.fullmatch(r"/join/([A-Za-z0-9_-]{43})", target.path)
    if (target.scheme,target.netloc) != (base.scheme,base.netloc) or target.query or target.fragment or not match:
        return None
    row = get_invitation(match[1])
    return row if row and row["id"] == invite_id and row["practice_id"] == practice_id and invitation_status(row) == "open" else None
