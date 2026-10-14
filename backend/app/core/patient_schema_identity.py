"""External identities, authentication events and patient sessions."""

def initialize_identities_and_sessions(cursor) -> None:
    cursor.execute("""CREATE TABLE IF NOT EXISTS idme_oidc_transactions (
        state_hash TEXT PRIMARY KEY,
        browser_hash TEXT NOT NULL,
        intent TEXT NOT NULL CHECK (intent IN ('login', 'link', 'tefca_ias')),
        patient_id TEXT REFERENCES patients(patient_id),
        nonce TEXT NOT NULL,
        pkce_verifier TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""")
    cursor.execute("ALTER TABLE idme_oidc_transactions ADD COLUMN IF NOT EXISTS created_at TEXT")
    cursor.execute("UPDATE idme_oidc_transactions SET created_at = expires_at WHERE created_at IS NULL")
    cursor.execute("ALTER TABLE idme_oidc_transactions ALTER COLUMN created_at SET NOT NULL")
    cursor.execute("ALTER TABLE idme_oidc_transactions DROP CONSTRAINT IF EXISTS idme_oidc_transactions_intent_check")
    cursor.execute("ALTER TABLE idme_oidc_transactions ADD CONSTRAINT idme_oidc_transactions_intent_check CHECK (intent IN ('login', 'link', 'tefca_ias'))")
    cursor.execute("CREATE INDEX IF NOT EXISTS idme_oidc_transactions_expiry_idx ON idme_oidc_transactions (expires_at)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS patient_external_identities (
        provider TEXT NOT NULL,
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        created_at TEXT NOT NULL,
        last_authenticated_at TEXT NOT NULL,
        assurance_level TEXT,
        policy_scope TEXT,
        verified_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        PRIMARY KEY (issuer, subject),
        UNIQUE (provider, patient_id)
    )""")
    cursor.execute("ALTER TABLE patient_external_identities ADD COLUMN IF NOT EXISTS assurance_level TEXT")
    cursor.execute("ALTER TABLE patient_external_identities ADD COLUMN IF NOT EXISTS policy_scope TEXT")
    cursor.execute("ALTER TABLE patient_external_identities ADD COLUMN IF NOT EXISTS verified_at TEXT")
    cursor.execute("ALTER TABLE patient_external_identities ADD COLUMN IF NOT EXISTS last_seen_at TEXT")
    cursor.execute("UPDATE patient_external_identities SET verified_at = created_at WHERE verified_at IS NULL")
    cursor.execute("UPDATE patient_external_identities SET last_seen_at = last_authenticated_at WHERE last_seen_at IS NULL")
    cursor.execute("ALTER TABLE patient_external_identities ALTER COLUMN verified_at SET NOT NULL")
    cursor.execute("ALTER TABLE patient_external_identities ALTER COLUMN last_seen_at SET NOT NULL")
    cursor.execute("""CREATE TABLE IF NOT EXISTS identity_verification_events (
        id BIGSERIAL PRIMARY KEY,
        provider TEXT NOT NULL,
        patient_id TEXT REFERENCES patients(patient_id),
        intent TEXT NOT NULL CHECK (intent IN ('login', 'link', 'unlink', 'tefca_ias')),
        outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
        reason_code TEXT NOT NULL,
        assurance_present BOOLEAN NOT NULL DEFAULT FALSE,
        jti_present BOOLEAN NOT NULL DEFAULT FALSE,
        policy_scope TEXT,
        created_at TEXT NOT NULL
    )""")
    cursor.execute("ALTER TABLE identity_verification_events ADD COLUMN IF NOT EXISTS assurance_present BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE identity_verification_events ADD COLUMN IF NOT EXISTS jti_present BOOLEAN NOT NULL DEFAULT FALSE")
    cursor.execute("ALTER TABLE identity_verification_events ADD COLUMN IF NOT EXISTS policy_scope TEXT")
    cursor.execute("ALTER TABLE identity_verification_events DROP CONSTRAINT IF EXISTS identity_verification_events_intent_check")
    cursor.execute("ALTER TABLE identity_verification_events ADD CONSTRAINT identity_verification_events_intent_check CHECK (intent IN ('login', 'link', 'unlink', 'tefca_ias'))")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS identity_verification_events_patient_idx "
        "ON identity_verification_events (patient_id, id DESC)"
    )
    cursor.execute("""CREATE TABLE IF NOT EXISTS patient_sessions (
        session_hash TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        expires_at TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS patient_sessions_patient_id_idx ON patient_sessions (patient_id)")
