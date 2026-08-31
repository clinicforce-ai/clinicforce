"""FHIR reference rewriting utilities."""

from typing import Any


def replace_patient_reference(value: Any, original_id: str, assigned_id: str) -> None:
    """Rewrite nested references after the server assigns a Patient ID."""
    if isinstance(value, dict):
        if value.get("reference") == f"Patient/{original_id}":
            value["reference"] = f"Patient/{assigned_id}"
        for child in value.values():
            replace_patient_reference(child, original_id, assigned_id)
    elif isinstance(value, list):
        for child in value:
            replace_patient_reference(child, original_id, assigned_id)
