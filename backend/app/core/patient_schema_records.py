"""Patient accounts, records, documents, reports and share tables."""

def initialize_records_and_shares(cursor) -> None:
    cursor.execute("""CREATE TABLE IF NOT EXISTS patients (
        patient_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        storage_preference TEXT NOT NULL DEFAULT 's3'
    )""")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS storage_preference TEXT NOT NULL DEFAULT 's3'")
    # Patient demographics are deliberately nullable for existing accounts:
    # ClinicForce must never infer identity details from an uploaded document.
    # Active Epic connections can populate them through the explicit
    # backfill path below; all other existing rows remain empty.
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS given_name TEXT")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS family_name TEXT")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS birth_date DATE")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS administrative_gender TEXT")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS google_given_name TEXT")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS google_family_name TEXT")
    cursor.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS password_login_enabled BOOLEAN NOT NULL DEFAULT TRUE")
    cursor.execute("""DO $$ BEGIN
        ALTER TABLE patients ADD CONSTRAINT patients_administrative_gender_check
            CHECK (administrative_gender IS NULL OR administrative_gender IN ('male', 'female', 'other', 'unknown'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
    cursor.execute("""DO $$ BEGIN
        ALTER TABLE patients ADD CONSTRAINT patients_storage_preference_check
            CHECK (storage_preference IN ('s3', 'google_drive'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS patient_records (
        id BIGSERIAL PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        category TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL, fhir_bundle_json TEXT NOT NULL
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS patient_documents (
        id BIGSERIAL PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, s3_key TEXT NOT NULL,
        fhir_bundle_json TEXT NOT NULL, extraction_version TEXT
    )""")
    # PostgreSQL does not automatically index foreign-key columns. These are the
    # predicates used on every workspace load and secure document download.
    cursor.execute("CREATE INDEX IF NOT EXISTS patient_documents_patient_id_idx ON patient_documents (patient_id, id DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS patient_records_patient_id_idx ON patient_records (patient_id, id DESC)")
    # Earlier development builds allowed a BYTEA fallback. Production documents
    # must never be kept in PostgreSQL; refuse an unsafe schema conversion rather
    # than silently deleting a legacy binary object.
    has_legacy_column = cursor.execute("""
        SELECT EXISTS (
          SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'public' AND table_name = 'patient_documents'
            AND column_name = 'pdf_bytes'
        ) AS exists
    """).fetchone()["exists"]
    if has_legacy_column:
        binary_count = cursor.execute(
            "SELECT COUNT(*) AS count FROM patient_documents WHERE pdf_bytes IS NOT NULL"
        ).fetchone()["count"]
        if binary_count:
            raise RuntimeError(
                "Refusing to remove legacy database document bytes; migrate them to S3 first"
            )
        cursor.execute("ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS patient_documents_check")
        cursor.execute("ALTER TABLE patient_documents DROP COLUMN pdf_bytes")
    cursor.execute("ALTER TABLE patient_documents ALTER COLUMN s3_key SET NOT NULL")
    cursor.execute("ALTER TABLE patient_documents ADD COLUMN IF NOT EXISTS extraction_version TEXT")
    cursor.execute("ALTER TABLE patient_documents ADD COLUMN IF NOT EXISTS storage_provider TEXT NOT NULL DEFAULT 's3'")
    cursor.execute("""DO $$ BEGIN
        ALTER TABLE patient_documents ADD CONSTRAINT patient_documents_storage_provider_check
            CHECK (storage_provider IN ('s3', 'google_drive'));
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS shl_manifests (
        manifest_id TEXT PRIMARY KEY, patient_id TEXT REFERENCES patients(patient_id),
        encrypted_jwe TEXT NOT NULL, hashed_pin TEXT, created_at TEXT NOT NULL,
        failed_pin_attempts INTEGER NOT NULL DEFAULT 0, pin_locked_until TEXT, revoked_at TEXT
    )""")
    cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS failed_pin_attempts INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS pin_locked_until TEXT")
    cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS revoked_at TEXT")
    cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'local'")
    cursor.execute("ALTER TABLE shl_manifests ADD COLUMN IF NOT EXISTS expires_at TEXT")
    # Compatibility-test shares deliberately omit the SHL ``P`` flag and
    # therefore have no PIN to hash.  Existing PIN-bearing shares retain
    # their hashes unchanged.
    cursor.execute("ALTER TABLE shl_manifests ALTER COLUMN hashed_pin DROP NOT NULL")
    cursor.execute("""CREATE TABLE IF NOT EXISTS doctor_reports (
        id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        filename TEXT NOT NULL, s3_key TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
