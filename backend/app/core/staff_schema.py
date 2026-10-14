"""Initialize practice-owned staff and intake tables."""
from __future__ import annotations


def initialize(connection) -> None:
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
    connection.execute("ALTER TABLE staff_patients ADD COLUMN IF NOT EXISTS ecw_account_number TEXT")
    connection.execute("ALTER TABLE staff_patients ADD COLUMN IF NOT EXISTS insurance_identities JSONB NOT NULL DEFAULT '{}'")
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
    connection.execute("ALTER TABLE staff_library ADD COLUMN IF NOT EXISTS sha256 TEXT")
    connection.execute("ALTER TABLE staff_library ADD COLUMN IF NOT EXISTS extraction JSONB")
    connection.execute("""CREATE TABLE IF NOT EXISTS staff_deletions (
        practice_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
        intake_id TEXT, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
        object_keys JSONB NOT NULL DEFAULT '[]',
        PRIMARY KEY (practice_id,kind,record_id)
    )""")
    connection.commit()
