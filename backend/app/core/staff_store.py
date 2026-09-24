"""Practice-owned intake and staff sessions, independent of patient portal accounts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from passlib.hash import pbkdf2_sha256

from app.core.patient_store import _connection as patient_connection
from app.core.shl import hash_password, verify_password


SESSION_SECONDS = 12 * 60 * 60
_schema_initialized = False
_schema_lock = Lock()
_bootstrap_initialized = False
_bootstrap_lock = Lock()
STAFF_FIELDS = "id, username, display_name, practice_id, practice_name, role"
_DUMMY_PASSWORD_HASH = hash_password("unused-staff-login-timing-value")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connection():
    global _schema_initialized
    connection = patient_connection()
    try:
        if not _schema_initialized:
            with _schema_lock:
                if not _schema_initialized:
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (8_240_715_302,))
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_members (
                        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                        practice_id TEXT NOT NULL, practice_name TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('staff', 'admin')), password_hash TEXT NOT NULL,
                        disabled_at TEXT, created_at TEXT NOT NULL
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_sessions (
                        session_hash TEXT PRIMARY KEY, staff_id TEXT NOT NULL REFERENCES staff_members(id),
                        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_login_attempts (
                        key_hash TEXT PRIMARY KEY, attempts INTEGER NOT NULL, window_started TEXT NOT NULL
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_patients (
                        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, given_name TEXT NOT NULL,
                        family_name TEXT NOT NULL, birth_date TEXT NOT NULL,
                        administrative_gender TEXT NOT NULL DEFAULT 'unknown', created_at TEXT NOT NULL
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS staff_patients_practice_idx ON staff_patients (practice_id, family_name, given_name, birth_date)")
                    connection.execute("ALTER TABLE staff_patients ADD COLUMN IF NOT EXISTS email TEXT")
                    connection.execute("ALTER TABLE staff_patients ADD COLUMN IF NOT EXISTS identity_status TEXT NOT NULL DEFAULT 'confirmed'")
                    connection.execute("ALTER TABLE staff_patients ADD COLUMN IF NOT EXISTS ecw_patient_id TEXT")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_intakes (
                        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES staff_members(id),
                        patient_id TEXT REFERENCES staff_patients(id), status TEXT NOT NULL,
                        fields JSONB NOT NULL DEFAULT '{}', ocr_fields JSONB NOT NULL DEFAULT '{}',
                        ocr_evidence JSONB NOT NULL DEFAULT '{}', field_sources JSONB NOT NULL DEFAULT '{}',
                        documents JSONB NOT NULL DEFAULT '[]', workflows JSONB NOT NULL DEFAULT '{}',
                        events JSONB NOT NULL DEFAULT '[]', warning TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )""")
                    connection.execute("CREATE INDEX IF NOT EXISTS staff_intakes_practice_idx ON staff_intakes (practice_id, updated_at DESC)")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'insurance_card'")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS source_portal_patient_id TEXT")
                    connection.execute("ALTER TABLE staff_intakes ALTER COLUMN created_by DROP NOT NULL")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS upload_link_id TEXT")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS expected_patient_id TEXT REFERENCES staff_patients(id)")
                    connection.execute("ALTER TABLE staff_intakes ADD COLUMN IF NOT EXISTS auto_patient_id TEXT REFERENCES staff_patients(id)")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_library (
                        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, kind TEXT NOT NULL,
                        label TEXT NOT NULL, payer TEXT NOT NULL, filename TEXT NOT NULL,
                        content_type TEXT NOT NULL, object_key TEXT NOT NULL, created_at TEXT NOT NULL,
                        effective_from TEXT, effective_to TEXT, created_by TEXT NOT NULL REFERENCES staff_members(id)
                    )""")
                    connection.execute("""CREATE TABLE IF NOT EXISTS staff_deletions (
                        practice_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
                        intake_id TEXT, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
                        object_keys JSONB NOT NULL DEFAULT '[]',
                        PRIMARY KEY (practice_id,kind,record_id)
                    )""")
                    connection.commit()
                    _schema_initialized = True
    except Exception:
        connection.close()
        raise
    return connection


def ensure_bootstrap() -> None:
    """Provision only the explicitly deployed administrator; never reset an existing account."""
    global _bootstrap_initialized
    if _bootstrap_initialized:
        return
    with _bootstrap_lock:
        if _bootstrap_initialized:
            return
        value = os.getenv("STAFF_BOOTSTRAP_CREDENTIALS", "").strip()
        if not value:
            return
        try:
            config = json.loads(value)
            username = str(config["username"]).strip().lower()
            password = config.get("password")
            password_hash = config.get("password_hash")
            practice_id = str(config["practice_id"]).strip()
            practice_name = str(config["practice_name"]).strip()
            display_name = str(config["display_name"]).strip()
            if not username or len(username) > 200 or not all((practice_id, practice_name, display_name)):
                raise ValueError
            if password_hash is not None:
                # Deployment can provision a one-way hash without putting a password
                # in the task definition. Only accept the application's strong scheme.
                if password is not None or not isinstance(password_hash, str) or len(password_hash) > 300:
                    raise ValueError
                parsed = pbkdf2_sha256.from_string(password_hash)
                if not 29_000 <= parsed.rounds <= 2_000_000 or not parsed.checksum or len(parsed.salt) < 16:
                    raise ValueError
            else:
                if not isinstance(password, str) or not 12 <= len(password) <= 256:
                    raise ValueError
                password_hash = hash_password(password)
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Staff administrator setup is invalid") from None
        with _connection() as connection:
            existing = connection.execute("SELECT id FROM staff_members WHERE username = %s", (username,)).fetchone()
            if not existing:
                connection.execute(
                    """INSERT INTO staff_members (id, username, display_name, practice_id, practice_name, role, password_hash, created_at)
                       VALUES (%s,%s,%s,%s,%s,'admin',%s,%s) ON CONFLICT (username) DO NOTHING""",
                    (str(uuid4()), username, display_name, practice_id, practice_name, password_hash, now()),
                )
        _bootstrap_initialized = True


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate(username: str, password: str, address: str) -> tuple[dict, str] | None:
    ensure_bootstrap()
    moment = datetime.now(timezone.utc)
    cutoff = (moment - timedelta(minutes=15)).isoformat()
    username = username.strip().lower()
    user_key = _hash("staff-login-user:" + username)
    with _connection() as connection:
        connection.execute("DELETE FROM staff_login_attempts WHERE window_started < %s", (cutoff,))
        allowed = True
        for key, limit in ((user_key, 8), (_hash("staff-login-address:" + address), 60)):
            row = connection.execute(
                """INSERT INTO staff_login_attempts (key_hash, attempts, window_started) VALUES (%s,1,%s)
                   ON CONFLICT (key_hash) DO UPDATE SET attempts = staff_login_attempts.attempts + 1
                   RETURNING attempts""", (key, moment.isoformat()),
            ).fetchone()
            allowed = allowed and row["attempts"] <= limit
        row = connection.execute(f"SELECT {STAFF_FIELDS}, password_hash FROM staff_members WHERE username = %s AND disabled_at IS NULL", (username,)).fetchone() if allowed else None
    password_matches = verify_password(password, row["password_hash"] if row else _DUMMY_PASSWORD_HASH) if allowed else False
    if not row or not password_matches:
        return None
    staff = {key: value for key, value in row.items() if key != "password_hash"}
    with _connection() as connection:
        connection.execute("DELETE FROM staff_login_attempts WHERE key_hash = %s", (user_key,))
    return staff, create_session(staff)


def create_session(staff: dict) -> str:
    """Issue a session after password verification or successful account creation."""
    moment = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    with _connection() as connection:
        connection.execute("DELETE FROM staff_sessions WHERE expires_at < %s", (moment.isoformat(),))
        connection.execute("INSERT INTO staff_sessions (session_hash, staff_id, created_at, expires_at) VALUES (%s,%s,%s,%s)",
                           (_hash(token), staff["id"], moment.isoformat(), (moment + timedelta(seconds=SESSION_SECONDS)).isoformat()))
    return token


def get_session(token: str) -> dict | None:
    if not token or len(token) > 200:
        return None
    with _connection() as connection:
        row = connection.execute(
            """SELECT m.id,m.username,m.display_name,m.practice_id,m.practice_name,m.role
               FROM staff_sessions s JOIN staff_members m ON m.id = s.staff_id
               WHERE s.session_hash = %s AND s.expires_at > %s AND m.disabled_at IS NULL""", (_hash(token), now()),
        ).fetchone()
    return dict(row) if row else None


def logout(token: str) -> None:
    with _connection() as connection:
        connection.execute("DELETE FROM staff_sessions WHERE session_hash = %s", (_hash(token),))


def create_member(actor: dict, username: str, display_name: str, password: str, role: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute(
            f"""INSERT INTO staff_members (id,username,display_name,practice_id,practice_name,role,password_hash,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING RETURNING {STAFF_FIELDS}""",
            (str(uuid4()), username.lower(), display_name, actor["practice_id"], actor["practice_name"], role, hash_password(password), now()),
        ).fetchone()
    return dict(row) if row else None


def _event(action: str, actor: dict | str | None = None) -> dict:
    return {"id": str(uuid4()), "action": action[:100], "created_at": now(),
            "actor_id": actor.get("id") if isinstance(actor, dict) else None,
            "actor": actor.get("display_name", "Clinic staff") if isinstance(actor, dict) else str(actor or "Clinic staff")[:100]}


def create_intake(intake_id: str, staff: dict, documents: list[dict], source: str = "insurance_card", source_portal_patient_id: str | None = None, fields: dict | None = None) -> dict:
    stamp = now()
    event = "eCW patient lookup started" if source == "ecw_lookup" else "QR patient details received" if source == "clinicforce_qr" else "Manual insurance details received" if source == "manual_entry" else "Insurance card received"
    with _connection() as connection:
        row = connection.execute(
            """INSERT INTO staff_intakes (id,practice_id,created_by,status,documents,events,created_at,updated_at,source,source_portal_patient_id,fields,field_sources)
               VALUES (%s,%s,%s,'needs_confirmation',%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s::jsonb) RETURNING *""",
            (intake_id, staff["practice_id"], staff["id"], json.dumps(documents), json.dumps([_event(event, staff)]), stamp, stamp,
             source, source_portal_patient_id, json.dumps(fields or {}), json.dumps({key: source for key in fields or {}})),
        ).fetchone()
    return {**dict(row), "patient": None}


def get_patient(patient_id: str, practice_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM staff_patients WHERE id = %s AND practice_id = %s", (patient_id, practice_id)).fetchone()
    return dict(row) if row else None


def set_ecw_patient_id(patient_id: str, practice_id: str, ecw_patient_id: str) -> None:
    with _connection() as connection:
        connection.execute(
            "UPDATE staff_patients SET ecw_patient_id=%s WHERE id=%s AND practice_id=%s",
            (ecw_patient_id, patient_id, practice_id),
        )


def get_intake(intake_id: str, practice_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("""SELECT i.*, to_jsonb(p) AS patient, to_jsonb(e) AS expected_patient, to_jsonb(a) AS auto_patient FROM staff_intakes i
            LEFT JOIN staff_patients p ON p.id = i.patient_id AND p.practice_id = i.practice_id
            LEFT JOIN staff_patients e ON e.id = i.expected_patient_id AND e.practice_id = i.practice_id
            LEFT JOIN staff_patients a ON a.id = i.auto_patient_id AND a.practice_id = i.practice_id
            WHERE i.id = %s AND i.practice_id = %s""", (intake_id, practice_id)).fetchone()
    return dict(row) if row else None


def patient_for_intake_request(intake_id: str, practice_id: str) -> str:
    """A staff request may target an already shared, unconfirmed clinic identity."""
    with _connection() as connection:
        row = connection.execute("SELECT * FROM staff_intakes WHERE id=%s AND practice_id=%s FOR UPDATE", (intake_id, practice_id)).fetchone()
        if not row:
            raise LookupError("Insurance intake not found")
        for key in ("patient_id", "auto_patient_id"):
            candidate = connection.execute("SELECT * FROM staff_patients WHERE id=%s AND practice_id=%s", (row[key], practice_id)).fetchone() if row.get(key) else None
            if candidate and (key == "patient_id" or all(str(candidate[name]).strip().casefold() == str(row["fields"].get(name, "")).strip().casefold() for name in ("given_name", "family_name", "birth_date"))):
                return candidate["id"]
        fields = row["fields"]
        if not all(fields.get(key) for key in ("given_name", "family_name", "birth_date")):
            raise ValueError("Confirm the patient's name and date of birth before creating a patient-specific request.")
        patient_id = str(uuid4())
        connection.execute("""INSERT INTO staff_patients (id,practice_id,given_name,family_name,birth_date,email,identity_status,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,'unverified',%s)""",
            (patient_id, practice_id, fields["given_name"], fields["family_name"], fields["birth_date"], fields.get("email"), now()))
        connection.execute("UPDATE staff_intakes SET auto_patient_id=%s WHERE id=%s AND practice_id=%s", (patient_id, intake_id, practice_id))
    return patient_id


def patient_dashboard(practice_id: str) -> dict:
    """Only explicit practice records/shares; never enumerate the portal patient table."""
    with _connection() as connection:
        patients = connection.execute("SELECT * FROM staff_patients WHERE practice_id=%s", (practice_id,)).fetchall()
        pending_cleanup = connection.execute("SELECT count(*) AS count FROM staff_deletions WHERE practice_id=%s AND object_keys <> '[]'::jsonb", (practice_id,)).fetchone()["count"]
        intakes = connection.execute("""SELECT id,patient_id,auto_patient_id,source_portal_patient_id,fields,status,source,created_at,updated_at,
            jsonb_array_length(documents)>0 AS has_card FROM staff_intakes WHERE practice_id=%s ORDER BY updated_at DESC,id DESC""", (practice_id,)).fetchall()
    patient_map = {row["id"]: row for row in patients}
    def matches_snapshot(row, patient_id):
        return all(str(patient_map[patient_id][name]).strip().casefold() == str(row["fields"].get(name, "")).strip().casefold() for name in ("given_name", "family_name", "birth_date"))
    # Explicit shared portal IDs connect a patient's intake history; names/email never do.
    portal_for_patient = {}
    for row in intakes:
        if row["source_portal_patient_id"]:
            for key in ("patient_id", "auto_patient_id"):
                if row[key] in patient_map and matches_snapshot(row, row[key]):
                    portal_for_patient.setdefault(row[key], row["source_portal_patient_id"])
    groups = {}
    def group(key):
        return groups.setdefault(key, {"records": {}, "intakes": []})
    for patient in patients:
        portal = portal_for_patient.get(patient["id"])
        group("portal:" + portal if portal else patient["id"])["records"][patient["id"]] = patient
    for intake in intakes:
        patient_id = intake["patient_id"] if intake["patient_id"] in patient_map else None
        if not patient_id and intake["auto_patient_id"] in patient_map and matches_snapshot(intake, intake["auto_patient_id"]):
            patient_id = intake["auto_patient_id"]
        portal = portal_for_patient.get(patient_id) or (intake["source_portal_patient_id"] if not patient_id or matches_snapshot(intake, patient_id) else None)
        key = "portal:" + portal if portal else patient_id or "intake:" + intake["id"]
        group(key)["intakes"].append(intake)
    rows, counts = [], {"all": 0, "accounts_with_cards": 0, "accounts_without_cards": 0, "card_only": 0, "other": 0}
    for key, value in groups.items():
        history = value["intakes"]
        records = list(value["records"].values())
        records.sort(key=lambda item: (item["identity_status"] != "confirmed", item["created_at"]))
        patient = records[0] if records else {}
        latest = history[0] if history else {}
        fields = latest.get("fields", {})
        has_card = any(item["has_card"] for item in history)
        account = "portal" if key.startswith("portal:") else "automatic" if any(item["source"] == "patient_upload" for item in history) or patient.get("identity_status") == "unverified" or not patient else "clinic"
        bucket = "accounts_with_cards" if account == "portal" and has_card else "accounts_without_cards" if account == "portal" else "card_only" if has_card else "other"
        counts["all"] += 1; counts[bucket] += 1
        public_id = "portal:" + _hash(practice_id + ":" + key)[:24] if key.startswith("portal:") else key
        rows.append({"id": public_id, "clinic_patient_id": patient.get("id"), "identity_status": patient.get("identity_status", "unverified"),
            **{name: patient.get(name) or fields.get(name) or "" for name in ("given_name", "family_name", "birth_date")},
            "email": patient.get("email") or next((item["fields"].get("email") for item in history if item["fields"].get("email")), None),
            "account_status": account, "card_status": "received" if has_card else "missing", "latest_intake_id": latest.get("id"),
            "intake_ids": [item["id"] for item in history], "intake_count": len(history),
            "updated_at": latest.get("updated_at") or patient["created_at"],
            "intakes": [{name: item[name] for name in ("id", "status", "created_at", "updated_at", "source")} for item in history]})
    rows.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    return {"patients": rows, "counts": counts, "pending_file_cleanup": pending_cleanup}


def list_intakes(practice_id: str, query: str = "", status: str = "", offset: int = 0) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute("""SELECT i.*, to_jsonb(p) AS patient FROM staff_intakes i
            LEFT JOIN staff_patients p ON p.id = i.patient_id AND p.practice_id = i.practice_id
            WHERE i.practice_id = %s AND (%s = '' OR i.status = %s)
              AND (%s = '' OR concat_ws(' ', p.given_name,p.family_name,p.birth_date,i.fields->>'member_name',i.fields->>'member_id',i.fields->>'payer') ILIKE %s)
            ORDER BY i.updated_at DESC,i.id DESC LIMIT 101 OFFSET %s""", (practice_id, status, status, query, "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", offset)).fetchall()
    return [dict(row) for row in rows]


def find_patients(practice_id: str, query: str = "", given_name: str = "", family_name: str = "", birth_date: str = "") -> list[dict]:
    if len(query) < 2 and not (given_name and family_name and birth_date):
        return []
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with _connection() as connection:
        rows = connection.execute("""SELECT * FROM staff_patients WHERE practice_id = %s
            AND ((%s <> '' AND concat_ws(' ',given_name,family_name,birth_date) ILIKE %s)
                OR (lower(given_name) = lower(%s) AND lower(family_name) = lower(%s) AND birth_date = %s))
            ORDER BY family_name,given_name LIMIT 30""", (practice_id, query, "%" + escaped + "%", given_name, family_name, birth_date)).fetchall()
    return [dict(row) for row in rows]


def save_extraction(intake_id: str, practice_id: str, fields: dict, evidence: dict, warning: str | None, manual_fields: dict | None = None) -> dict:
    manual_fields = manual_fields or {}
    merged_fields = {**manual_fields, **fields}
    field_sources = {key: "staff_entered" for key in manual_fields}
    field_sources.update({key: "card_ocr" for key in fields})
    with _connection() as connection:
        connection.execute("""UPDATE staff_intakes SET ocr_fields = %s::jsonb, ocr_evidence = %s::jsonb,
            fields = CASE WHEN patient_id IS NULL THEN %s::jsonb ELSE fields END,
            field_sources = CASE WHEN patient_id IS NULL THEN %s::jsonb ELSE field_sources END,
            warning = %s, updated_at = %s WHERE id = %s AND practice_id = %s""",
            (json.dumps(fields), json.dumps(evidence), json.dumps(merged_fields), json.dumps(field_sources), warning, now(), intake_id, practice_id))
    return get_intake(intake_id, practice_id)


def confirm_patient(intake_id: str, staff: dict, data: dict) -> dict | None:
    with _connection() as connection:
        intake = connection.execute("SELECT * FROM staff_intakes WHERE id = %s AND practice_id = %s FOR UPDATE", (intake_id, staff["practice_id"])).fetchone()
        if not intake:
            return None
        if intake["workflows"]:
            raise ValueError("Start a new intake before changing details used by an insurance workflow.")
        if intake["source"] not in {"clinicforce_qr", "ecw_lookup"} and not data.get("member_id"):
            raise ValueError("Confirm the insurance member ID before starting verification.")
        identity_changed = any(str(intake["fields"].get(key, "")).strip().casefold() != str(data[key]).strip().casefold() for key in ("given_name", "family_name", "birth_date"))
        ecw_patient_id = intake["fields"].get("ecw_patient_id") if intake["source"] == "ecw_lookup" else None
        if intake["source"] == "ecw_lookup" and not ecw_patient_id:
            raise ValueError("This intake was not verified in eCW. Start a new eCW lookup before continuing.")
        if ecw_patient_id and identity_changed:
            raise ValueError("The patient details changed after the eCW match. Start a new eCW lookup.")
        portal_patient_id = None if identity_changed else intake["source_portal_patient_id"]
        patient_id = data.get("patient_id")
        if intake.get("expected_patient_id"):
            expected = connection.execute("SELECT * FROM staff_patients WHERE id=%s AND practice_id=%s", (intake["expected_patient_id"], staff["practice_id"])).fetchone()
            if (not expected or (patient_id and patient_id != expected["id"])
                    or any(str(expected[key]).casefold() != str(data[key]).casefold() for key in ("given_name", "family_name", "birth_date"))):
                raise ValueError("These details differ from the patient this upload link was created for. Review the requested patient and uploaded card before confirming.")
            patient_id = expected["id"]
        if not patient_id and intake["patient_id"]:
            previous = connection.execute("SELECT * FROM staff_patients WHERE id = %s AND practice_id = %s", (intake["patient_id"], staff["practice_id"])).fetchone()
            if previous and all(str(previous[key]).casefold() == str(data[key]).casefold() for key in ("given_name", "family_name", "birth_date")):
                patient_id = previous["id"]
        automatic = None
        if intake.get("auto_patient_id"):
            automatic = connection.execute("SELECT * FROM staff_patients WHERE id=%s AND practice_id=%s FOR UPDATE", (intake["auto_patient_id"], staff["practice_id"])).fetchone()
        if not patient_id and automatic and automatic["identity_status"] == "unverified":
            patient_id = automatic["id"]
            connection.execute("""UPDATE staff_patients SET given_name=%s,family_name=%s,birth_date=%s,
                               ecw_patient_id=CASE WHEN %s THEN NULL ELSE ecw_patient_id END
                               WHERE id=%s AND practice_id=%s""",
                               (data["given_name"], data["family_name"], data["birth_date"], identity_changed,
                                patient_id, staff["practice_id"]))
            if identity_changed:
                connection.execute("UPDATE staff_intakes SET source_portal_patient_id=NULL WHERE auto_patient_id=%s AND practice_id=%s", (patient_id, staff["practice_id"]))
        if patient_id:
            patient = connection.execute("SELECT * FROM staff_patients WHERE id = %s AND practice_id = %s", (patient_id, staff["practice_id"])).fetchone()
            if not patient or any(str(patient[key]).casefold() != str(data[key]).casefold() for key in ("given_name", "family_name", "birth_date")):
                raise ValueError("The selected patient does not match these details. Review the patient selection.")
        else:
            patient_id = str(uuid4())
            connection.execute("INSERT INTO staff_patients (id,practice_id,given_name,family_name,birth_date,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                               (patient_id, staff["practice_id"], data["given_name"], data["family_name"], data["birth_date"], now()))
        if ecw_patient_id:
            linked = connection.execute("SELECT id FROM staff_patients WHERE practice_id=%s AND ecw_patient_id=%s AND id<>%s",
                                        (staff["practice_id"], ecw_patient_id, patient_id)).fetchone()
            if linked:
                raise ValueError("This eCW patient is already linked to another clinic record. Select that record instead.")
            existing_link = connection.execute("SELECT ecw_patient_id FROM staff_patients WHERE id=%s AND practice_id=%s",
                                               (patient_id, staff["practice_id"])).fetchone()
            if existing_link and existing_link["ecw_patient_id"] not in {None, ecw_patient_id}:
                raise ValueError("The selected clinic record is linked to a different eCW patient.")
            connection.execute("UPDATE staff_patients SET ecw_patient_id=%s WHERE id=%s AND practice_id=%s",
                               (ecw_patient_id, patient_id, staff["practice_id"]))
        connection.execute("UPDATE staff_patients SET identity_status='confirmed',email=COALESCE(NULLIF(%s,''),email) WHERE id=%s AND practice_id=%s",
                           (data.get("email") or intake["fields"].get("email"), patient_id, staff["practice_id"]))
        if automatic and automatic["identity_status"] == "unverified" and automatic["id"] != patient_id:
            # A staff-selected existing record replaces this provisional identity only
            # within the same clinic, including any pending requests targeting it.
            connection.execute("UPDATE staff_intakes SET auto_patient_id=%s WHERE id=%s AND practice_id=%s", (patient_id, intake_id, staff["practice_id"]))
            links_exist = connection.execute("SELECT to_regclass('insurance_upload_links') AS name").fetchone()["name"]
            same_identity = all(str(automatic[key]).strip().casefold() == str(data[key]).strip().casefold() for key in ("given_name", "family_name", "birth_date"))
            if same_identity:
                connection.execute("UPDATE staff_intakes SET auto_patient_id=%s WHERE auto_patient_id=%s AND practice_id=%s", (patient_id, automatic["id"], staff["practice_id"]))
                connection.execute("UPDATE staff_intakes SET expected_patient_id=%s WHERE expected_patient_id=%s AND practice_id=%s", (patient_id, automatic["id"], staff["practice_id"]))
                if links_exist:
                    connection.execute("UPDATE insurance_upload_links SET expected_patient_id=%s WHERE expected_patient_id=%s AND practice_id=%s", (patient_id, automatic["id"], staff["practice_id"]))
            referenced = connection.execute("SELECT 1 FROM staff_intakes WHERE %s IN (patient_id,auto_patient_id,expected_patient_id) LIMIT 1", (automatic["id"],)).fetchone()
            if links_exist:
                referenced = referenced or connection.execute("SELECT 1 FROM insurance_upload_links WHERE expected_patient_id=%s LIMIT 1", (automatic["id"],)).fetchone()
            if not referenced:
                connection.execute("DELETE FROM staff_patients WHERE id=%s AND practice_id=%s", (automatic["id"], staff["practice_id"]))
        fields = {**intake["fields"], **{k: v for k, v in data.items() if k not in {"patient_id", "identity_confirmed"}}}
        sources = {**intake["field_sources"], **{key: "staff_confirmed" for key in data if key not in {"patient_id", "identity_confirmed"}}}
        connection.execute("""UPDATE staff_intakes SET patient_id = %s, source_portal_patient_id=%s, fields = %s::jsonb,
            field_sources = %s::jsonb, status = 'ready', events = events || %s::jsonb, updated_at = %s
            WHERE id = %s AND practice_id = %s""", (patient_id, portal_patient_id, json.dumps(fields), json.dumps(sources), json.dumps([_event("Patient details confirmed", staff)]), now(), intake_id, staff["practice_id"]))
    return get_intake(intake_id, staff["practice_id"])


def attach_workflow(intake_id: str, practice_id: str, version: str, case_id: str) -> dict | None:
    if version not in {"v1", "v2"}:
        raise ValueError("Unknown insurance workflow")
    with _connection() as connection:
        connection.execute("""UPDATE staff_intakes SET workflows = workflows || %s::jsonb,
            updated_at = %s WHERE id = %s AND practice_id = %s AND patient_id IS NOT NULL""",
            (json.dumps({version: case_id}), now(), intake_id, practice_id))
    return get_intake(intake_id, practice_id)


def add_event(intake_id: str, practice_id: str, action: str, actor: dict | str | None = None) -> None:
    with _connection() as connection:
        connection.execute("UPDATE staff_intakes SET events = events || %s::jsonb, updated_at = %s WHERE id = %s AND practice_id = %s",
                           (json.dumps([_event(action, actor)]), now(), intake_id, practice_id))


def list_library(practice_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute("SELECT * FROM staff_library WHERE practice_id = %s ORDER BY created_at DESC LIMIT 200", (practice_id,)).fetchall()
    return [dict(row) for row in rows]


def get_library(document_id: str, practice_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM staff_library WHERE id = %s AND practice_id = %s", (document_id, practice_id)).fetchone()
    return dict(row) if row else None


def create_library(staff: dict, document: dict) -> dict:
    with _connection() as connection:
        row = connection.execute("""INSERT INTO staff_library
            (id,practice_id,kind,label,payer,filename,content_type,object_key,created_at,effective_from,effective_to,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (document["id"], staff["practice_id"], document["kind"], document["label"], document["payer"], document["filename"],
             document["content_type"], document["object_key"], now(), document.get("effective_from"), document.get("effective_to"), staff["id"])).fetchone()
    return dict(row)
