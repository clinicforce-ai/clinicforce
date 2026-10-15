"""Patient Drive and encrypted-file storage tables."""

def initialize_drive(cursor, is_google_generated_patient) -> None:
    cursor.execute("""CREATE TABLE IF NOT EXISTS patient_drive_connections (
        patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id),
        google_subject TEXT NOT NULL,
        refresh_token_ciphertext TEXT NOT NULL,
        folder_id TEXT NOT NULL,
        summary_file_id TEXT,
        summary_fingerprint TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    cursor.execute("ALTER TABLE patient_drive_connections ADD COLUMN IF NOT EXISTS summary_fingerprint TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS patient_drive_connections_google_subject_idx ON patient_drive_connections (google_subject)")
    # Old images can still create a Google-only row during a rolling deploy
    # after the new column exists, letting its TRUE default leak through.
    # Repair the exact deterministic Google identity invariant on every
    # schema initialization; prefix lookalikes remain ordinary local users.
    google_only_ids = [
        str(row["patient_id"])
        for row in cursor.execute(
            """SELECT patients.patient_id, patient_drive_connections.google_subject
               FROM patients JOIN patient_drive_connections USING (patient_id)"""
        ).fetchall()
        if is_google_generated_patient(str(row["patient_id"]), str(row["google_subject"]))
    ]
    if google_only_ids:
        cursor.executemany(
            "UPDATE patients SET password_login_enabled = FALSE WHERE patient_id = %s",
            [(patient_id,) for patient_id in google_only_ids],
        )
    cursor.execute("""CREATE TABLE IF NOT EXISTS drive_file_crypto (
        file_id TEXT PRIMARY KEY,
        patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        artifact_id TEXT NOT NULL UNIQUE,
        purpose TEXT NOT NULL CHECK (purpose IN ('source', 'summary')),
        encrypted_data_key TEXT NOT NULL,
        nonce TEXT NOT NULL,
        plaintext_sha256 TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS drive_file_crypto_patient_id_idx ON drive_file_crypto (patient_id)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS drive_pending_deletions (
        patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        file_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (patient_id, file_id)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS drive_oauth_states (
        state TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        expires_at TEXT NOT NULL
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS google_login_states (
        state TEXT PRIMARY KEY, expires_at TEXT NOT NULL
    )""")
