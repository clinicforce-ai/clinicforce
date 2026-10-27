"""Insurance cases and practice-scoped commercial configuration."""

def initialize_insurance(cursor) -> None:
    cursor.execute("""CREATE TABLE IF NOT EXISTS insurance_cases (
        id TEXT PRIMARY KEY, patient_id TEXT NOT NULL REFERENCES patients(patient_id),
        share_manifest_id TEXT NOT NULL, evidence_manifest_id TEXT NOT NULL,
        workflow_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    )""")
    # Staff-owned reviews are durable and independent of patient login/share credentials.
    cursor.execute("ALTER TABLE insurance_cases ALTER COLUMN patient_id DROP NOT NULL")
    cursor.execute("ALTER TABLE insurance_cases ALTER COLUMN expires_at DROP NOT NULL")
    cursor.execute("ALTER TABLE insurance_cases ADD COLUMN IF NOT EXISTS practice_id TEXT")
    cursor.execute("ALTER TABLE insurance_cases ADD COLUMN IF NOT EXISTS practice_patient_id TEXT")
    cursor.execute("ALTER TABLE insurance_cases ADD COLUMN IF NOT EXISTS intake_id TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS insurance_cases_practice_idx ON insurance_cases (practice_id, intake_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS insurance_cases_expiry_idx ON insurance_cases (expires_at)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS insurance_case_events (
        id BIGSERIAL PRIMARY KEY, case_id TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    cursor.execute("ALTER TABLE insurance_case_events ADD COLUMN IF NOT EXISTS snapshot_json TEXT")
    # Commercial-estimator configuration is practice-scoped.  No member IDs,
    # card images, or raw payer responses belong in these relational records.
    cursor.execute("""CREATE TABLE IF NOT EXISTS practice_cash_schedules (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, actor_id TEXT NOT NULL,
        filename TEXT NOT NULL, sha256 TEXT NOT NULL, effective_from TEXT NOT NULL,
        rows_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS practice_cash_schedules_scope_idx ON practice_cash_schedules (practice_id,status,effective_from)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_payer_connections (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, display_name TEXT NOT NULL,
        stedi_payer_id TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL,
        aliases_json TEXT NOT NULL, rules_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE (practice_id, stedi_payer_id)
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_payer_connections_practice_idx ON commercial_payer_connections (practice_id, status)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_provider_networks (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, payer_connection_id TEXT NOT NULL REFERENCES commercial_payer_connections(id),
        billing_npi TEXT NOT NULL, rendering_npi TEXT NOT NULL, organization_name TEXT NOT NULL DEFAULT '', tin TEXT NOT NULL, location_id TEXT NOT NULL,
        place_of_service TEXT NOT NULL, plan_matcher TEXT NOT NULL, network_name TEXT NOT NULL,
        effective_from TEXT NOT NULL, effective_to TEXT, status TEXT NOT NULL, evidence_reference TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    cursor.execute("ALTER TABLE commercial_provider_networks ADD COLUMN IF NOT EXISTS organization_name TEXT NOT NULL DEFAULT ''")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_provider_networks_practice_idx ON commercial_provider_networks (practice_id, payer_connection_id, status)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_estimation_profiles (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, payer_connection_id TEXT NOT NULL REFERENCES commercial_payer_connections(id),
        provider_network_id TEXT NOT NULL REFERENCES commercial_provider_networks(id), service_category TEXT NOT NULL,
        plan_matcher TEXT NOT NULL, status TEXT NOT NULL, rules_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_estimation_profiles_practice_idx ON commercial_estimation_profiles (practice_id, status)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_rate_versions (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, payer_connection_id TEXT NOT NULL REFERENCES commercial_payer_connections(id),
        source_type TEXT NOT NULL, status TEXT NOT NULL, source_reference TEXT NOT NULL, sha256 TEXT NOT NULL,
        effective_from TEXT NOT NULL, effective_to TEXT, change_ceiling_bps INTEGER NOT NULL,
        created_at TEXT NOT NULL, activated_at TEXT
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_rate_versions_practice_idx ON commercial_rate_versions (practice_id, payer_connection_id, status)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_rate_lines (
        id BIGSERIAL PRIMARY KEY, version_id TEXT NOT NULL REFERENCES commercial_rate_versions(id) ON DELETE CASCADE,
        row_number INTEGER NOT NULL, code TEXT NOT NULL, description TEXT NOT NULL, modifier TEXT NOT NULL,
        unit_rule TEXT NOT NULL, allowed_cents INTEGER NOT NULL, cash_cents INTEGER, network_name TEXT NOT NULL,
        plan_matcher TEXT NOT NULL, place_of_service TEXT NOT NULL, provider_npi TEXT NOT NULL, tin TEXT NOT NULL,
        effective_from TEXT NOT NULL, effective_to TEXT
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_rate_lines_match_idx ON commercial_rate_lines (version_id, code, network_name, place_of_service)")
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_cpt_requirements (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, payer_key TEXT NOT NULL,
        product_key TEXT NOT NULL, cpt TEXT NOT NULL, network_tier TEXT NOT NULL,
        place_of_service TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
        referral_required BOOLEAN, prior_authorization_required BOOLEAN,
        source_label TEXT NOT NULL, source_url TEXT, updated_at TEXT NOT NULL,
        CHECK (referral_required IS NOT NULL OR prior_authorization_required IS NOT NULL),
        UNIQUE (practice_id,payer_key,product_key,cpt,network_tier,place_of_service,effective_from)
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_cpt_requirements_scope_idx ON commercial_cpt_requirements (practice_id, payer_key, product_key, cpt, effective_from)")
    # Published Choice Plus access rule; authorization remains unknown until a
    # code- and member-specific source settles it. Expire with the 2026 guide.
    for cpt in ('99212', '99213', '99214', '99215'):
        cursor.execute('''INSERT INTO commercial_cpt_requirements
                (id,practice_id,payer_key,product_key,cpt,network_tier,place_of_service,
                 effective_from,effective_to,referral_required,prior_authorization_required,
                 source_label,source_url,updated_at)
                VALUES (%s,'*','unitedhealthcare',%s,%s,'in_network','11',
                        '2026-01-01','2026-12-31',FALSE,NULL,
                        'UnitedHealthcare 2026 commercial product overview',
                        'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf',
                        '2026-10-26') ON CONFLICT (id) DO NOTHING''',
                (f'uhc-choice-plus-{cpt}-2026', 'choiceplus', cpt))
    cursor.execute("""CREATE TABLE IF NOT EXISTS commercial_eligibility_evidence (
        id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, case_id TEXT NOT NULL REFERENCES insurance_cases(id),
        context_hash TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, response_reference TEXT NOT NULL,
        requested_dos TEXT NOT NULL, payer_checked_at TEXT NOT NULL, normalized_json TEXT NOT NULL,
        raw_object_key TEXT, expires_at TEXT, created_at TEXT NOT NULL
    )""")
    cursor.execute("CREATE INDEX IF NOT EXISTS commercial_eligibility_evidence_case_idx ON commercial_eligibility_evidence (practice_id, case_id, created_at DESC)")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS commercial_pending_evidence_context_idx ON commercial_eligibility_evidence (practice_id, context_hash) WHERE status = 'pending'")
