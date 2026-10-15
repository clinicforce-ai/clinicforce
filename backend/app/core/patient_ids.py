"""Deterministic local identifiers for linked patient accounts."""

import hashlib


def _google_patient_id(google_subject: str) -> str:
    return "google-" + hashlib.sha256(google_subject.encode("utf-8")).hexdigest()[:20]


def _is_google_generated_patient(patient_id: str, google_subject: str | None) -> bool:
    return bool(google_subject) and patient_id == _google_patient_id(str(google_subject))
