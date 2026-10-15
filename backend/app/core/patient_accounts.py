"""Patient accounts backed by the patient database connection."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import psycopg

from app.core.patient_models import PatientProfile, PatientProfileSuggestions


def _connection():
    from app.core import patient_store
    return patient_store._connection()

def create_patient(patient_id: str, password_hash: str) -> bool:
    with _connection() as connection:
        try:
            connection.execute(
                "INSERT INTO patients (patient_id, password_hash, created_at) VALUES (%s, %s, %s)",
                (patient_id, password_hash, datetime.now(timezone.utc).isoformat()),
            )
        except psycopg.IntegrityError:
            return False
    return True

def get_password_hash(patient_id: str) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT password_hash FROM patients WHERE patient_id = %s AND password_login_enabled", (patient_id,)
        ).fetchone()
    return row["password_hash"] if row else None

def get_patient_profile(patient_id: str) -> PatientProfile | None:
    """Return only the canonical demographics intentionally stored for matching."""
    with _connection() as connection:
        row = connection.execute(
            """SELECT patient_id, given_name, family_name, birth_date, administrative_gender
               FROM patients WHERE patient_id = %s""",
            (patient_id,),
        ).fetchone()
    if row is None:
        return None
    birth_date = row["birth_date"]
    return PatientProfile(
        patient_id=str(row["patient_id"]),
        given_name=str(row["given_name"]) if row["given_name"] else None,
        family_name=str(row["family_name"]) if row["family_name"] else None,
        birth_date=birth_date.isoformat() if hasattr(birth_date, "isoformat") else (str(birth_date) if birth_date else None),
        administrative_gender=str(row["administrative_gender"]) if row["administrative_gender"] else None,
    )

def get_patient_profile_suggestions(patient_id: str) -> PatientProfileSuggestions:
    """Return optional Google profile names as form suggestions only."""
    with _connection() as connection:
        row = connection.execute(
            "SELECT google_given_name, google_family_name FROM patients WHERE patient_id = %s",
            (patient_id,),
        ).fetchone()
    if row is None:
        return PatientProfileSuggestions(None, None)
    return PatientProfileSuggestions(
        given_name=str(row["google_given_name"]) if row["google_given_name"] else None,
        family_name=str(row["google_family_name"]) if row["google_family_name"] else None,
    )

def _google_name_hint(value: str | None) -> str | None:
    """Keep unverified Google display names bounded before storing a suggestion."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:100] if value else None

def set_google_profile_suggestions(patient_id: str, given_name: str | None, family_name: str | None) -> None:
    """Persist current consented Google display names without treating them as legal names."""
    with _connection() as connection:
        connection.execute(
            """UPDATE patients SET google_given_name = %s, google_family_name = %s
               WHERE patient_id = %s""",
            (_google_name_hint(given_name), _google_name_hint(family_name), patient_id),
        )

def update_patient_profile(
    patient_id: str, given_name: str, family_name: str, birth_date: str, administrative_gender: str,
) -> PatientProfile | None:
    """Persist an explicitly entered canonical demographic profile."""
    if administrative_gender not in {"male", "female", "other", "unknown"}:
        raise ValueError("Unsupported administrative gender")
    with _connection() as connection:
        row = connection.execute(
            """UPDATE patients
               SET given_name = %s, family_name = %s, birth_date = %s, administrative_gender = %s
               WHERE patient_id = %s
               RETURNING patient_id, given_name, family_name, birth_date, administrative_gender""",
            (given_name.strip(), family_name.strip(), birth_date, administrative_gender, patient_id),
        ).fetchone()
    if row is None:
        return None
    returned_birth_date = row["birth_date"]
    return PatientProfile(
        patient_id=str(row["patient_id"]),
        given_name=str(row["given_name"]), family_name=str(row["family_name"]),
        birth_date=returned_birth_date.isoformat() if hasattr(returned_birth_date, "isoformat") else str(returned_birth_date),
        administrative_gender=str(row["administrative_gender"]),
    )

def backfill_patient_profile_from_epic(patient_id: str, patient_resource: dict[str, Any]) -> PatientProfile | None:
    """Fill missing profile fields from a live Epic Patient resource only.

    Existing patient-entered values win. This intentionally stores no Epic token,
    remote identifier, or full chart--only the requested matching demographics.
    """
    if patient_resource.get("resourceType") != "Patient":
        return get_patient_profile(patient_id)
    selected_name: dict[str, Any] | None = None
    for name in patient_resource.get("name", []):
        if isinstance(name, dict) and name.get("use") == "official":
            selected_name = name
            break
        if selected_name is None and isinstance(name, dict):
            selected_name = name
    given_values = selected_name.get("given", []) if selected_name else []
    given_name = " ".join(value.strip() for value in given_values if isinstance(value, str) and value.strip()) or None
    family_name = selected_name.get("family") if selected_name else None
    family_name = family_name.strip() if isinstance(family_name, str) and family_name.strip() else None
    birth_date = patient_resource.get("birthDate")
    birth_date = birth_date.strip() if isinstance(birth_date, str) and birth_date.strip() else None
    try:
        birth_date_value = date.fromisoformat(birth_date) if birth_date else None
    except ValueError:
        # FHIR permits partial dates, but a partial Epic value is not suitable
        # as the canonical matching date stored in this profile.
        birth_date_value = None
    gender = patient_resource.get("gender")
    gender = gender if gender in {"male", "female", "other", "unknown"} else None
    with _connection() as connection:
        row = connection.execute(
            """UPDATE patients
               SET given_name = COALESCE(given_name, %s), family_name = COALESCE(family_name, %s),
                   birth_date = COALESCE(birth_date, %s), administrative_gender = COALESCE(administrative_gender, %s)
               WHERE patient_id = %s
               RETURNING patient_id, given_name, family_name, birth_date, administrative_gender""",
            (given_name, family_name, birth_date_value, gender, patient_id),
        ).fetchone()
    if row is None:
        return None
    returned_birth_date = row["birth_date"]
    return PatientProfile(
        patient_id=str(row["patient_id"]),
        given_name=str(row["given_name"]) if row["given_name"] else None,
        family_name=str(row["family_name"]) if row["family_name"] else None,
        birth_date=returned_birth_date.isoformat() if hasattr(returned_birth_date, "isoformat") else (str(returned_birth_date) if returned_birth_date else None),
        administrative_gender=str(row["administrative_gender"]) if row["administrative_gender"] else None,
    )

def check_database_connection() -> None:
    """Verify that this task can authenticate to and query its PostgreSQL database."""
    with _connection() as connection:
        connection.execute("SELECT 1").fetchone()

def get_storage_preference(patient_id: str) -> str:
    with _connection() as connection:
        row = connection.execute("SELECT storage_preference FROM patients WHERE patient_id = %s", (patient_id,)).fetchone()
    return str(row["storage_preference"]) if row else "s3"

def set_storage_preference(patient_id: str, storage_provider: str) -> bool:
    if storage_provider not in {"s3", "google_drive"}:
        raise ValueError("Unsupported storage provider")
    with _connection() as connection:
        cursor = connection.execute("UPDATE patients SET storage_preference = %s WHERE patient_id = %s", (storage_provider, patient_id))
    return cursor.rowcount == 1
