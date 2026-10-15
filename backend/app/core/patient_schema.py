"""Patient storage schema initialization for existing deployments."""
from __future__ import annotations

from typing import Any

import psycopg

from app.core.patient_schema_records import initialize_records_and_shares
from app.core.patient_schema_drive import initialize_drive
from app.core.patient_schema_identity import initialize_identities_and_sessions
from app.core.patient_schema_insurance import initialize_insurance
from app.core.patient_ids import _is_google_generated_patient


def _initialize_schema(connection: psycopg.Connection[Any]) -> None:
    """Run the one-time, cross-process-safe storage migration.

    Schema DDL takes exclusive PostgreSQL locks.  Running it for every request can
    deadlock when endpoints fetch documents and records concurrently, so it is
    guarded both in-process and across ECS tasks.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (8_240_715_301,))
        initialize_records_and_shares(cursor)
        initialize_drive(cursor, _is_google_generated_patient)
        initialize_identities_and_sessions(cursor)
        initialize_insurance(cursor)
        # This is public provider-directory metadata only.  It intentionally has
        # no patient reference and must never be used to store Epic chart data.
        cursor.execute("""CREATE TABLE IF NOT EXISTS epic_endpoints (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            addresses_json TEXT NOT NULL,
            fhir_base TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS epic_endpoints_name_idx ON epic_endpoints (name)")
    connection.commit()
