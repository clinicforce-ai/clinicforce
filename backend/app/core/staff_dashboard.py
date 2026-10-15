"""Practice patient dashboard grouping and counts."""

from __future__ import annotations

from collections.abc import Callable


def patient_dashboard(practice_id: str, *, connection: Callable, hash_value: Callable[[str], str]) -> dict:
    """Only explicit practice records/shares; never enumerate the portal patient table."""
    with connection() as db:
        patients = db.execute("SELECT * FROM staff_patients WHERE practice_id=%s", (practice_id,)).fetchall()
        pending_cleanup = db.execute("SELECT count(*) AS count FROM staff_deletions WHERE practice_id=%s AND object_keys <> '[]'::jsonb", (practice_id,)).fetchone()["count"]
        intakes = db.execute("""SELECT id,patient_id,auto_patient_id,source_portal_patient_id,fields,status,source,created_at,updated_at,
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
        public_id = "portal:" + hash_value(practice_id + ":" + key)[:24] if key.startswith("portal:") else key
        rows.append({"id": public_id, "clinic_patient_id": patient.get("id"), "identity_status": patient.get("identity_status", "unverified"),
            **{name: patient.get(name) or fields.get(name) or "" for name in ("given_name", "family_name", "birth_date")},
            "email": patient.get("email") or next((item["fields"].get("email") for item in history if item["fields"].get("email")), None),
            "ecw_account_number": patient.get("ecw_account_number"),
            "account_status": account, "card_status": "received" if has_card else "missing", "latest_intake_id": latest.get("id"),
            "intake_ids": [item["id"] for item in history], "intake_count": len(history),
            "updated_at": latest.get("updated_at") or patient["created_at"],
            "intakes": [{name: item[name] for name in ("id", "status", "created_at", "updated_at", "source")} for item in history]})
    rows.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    return {"patients": rows, "counts": counts, "pending_file_cleanup": pending_cleanup}
