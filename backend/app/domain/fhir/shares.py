"""FHIR bundle assembly for SMART Health Link shares."""

from typing import Any

from app.core.patient_store import PatientProfile

from .bundles import combine_document_bundles
from .patient import apply_identity_assurance, exchange_patient, patient_reference
from .resources import prepare_exchange_resource
from .tags import tag_resource_source


def uploaded_document_reference(document: Any, patient_id: str) -> dict[str, Any]:
    """Represent an uploaded PDF without exposing bytes or a storage locator."""
    reference = {
        "resourceType": "DocumentReference",
        "id": f"uploaded-document-{document.id}",
        "status": "current",
        "subject": patient_reference(patient_id),
        "type": {"text": "Patient-uploaded clinical document"},
        "date": document.uploaded_at,
        "description": "Patient-uploaded PDF; metadata only. Clinician reconciliation required.",
        "content": [{"attachment": {"contentType": "application/pdf", "title": document.filename}}],
    }
    tag_resource_source(reference, "document-extracted")
    return reference


def build_exchange_bundle(
    patient_id: str,
    documents: list[Any],
    records: list[Any],
    epic_bundle: dict[str, Any] | None = None,
    profile: PatientProfile | None = None,
    ecw_compat: bool = False,
) -> dict[str, Any]:
    """Build a collection containing only transport-safe clinical resources."""
    entries = [{"resource": exchange_patient(patient_id, profile, epic_bundle, ecw_compat)}]
    _append_persisted_entries(entries, documents, patient_id, "document-extracted")
    for document in documents:
        entries.append({"resource": uploaded_document_reference(document, patient_id)})
    _append_persisted_entries(entries, records, patient_id, "patient-entered")
    if epic_bundle:
        _append_epic_entries(entries, epic_bundle, patient_id)
    if ecw_compat:
        for entry in entries:
            if isinstance(entry.get("resource"), dict):
                apply_identity_assurance(entry["resource"])
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def _append_persisted_entries(entries: list[dict], items: list[Any], patient_id: str, source: str) -> None:
    for item in items:
        bundles = combine_document_bundles([item]) if source == "document-extracted" else combine_document_bundles([], [item])
        for entry in bundles["entry"]:
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict):
                prepared = prepare_exchange_resource(resource, patient_id, source)
                if prepared is not None:
                    entries.append({"resource": prepared})


def _append_epic_entries(entries: list[dict], bundle: dict[str, Any], patient_id: str) -> None:
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or resource.get("resourceType") == "Patient":
            continue
        prepared = prepare_exchange_resource(resource, patient_id, "epic-mychart")
        if prepared is not None:
            entries.append({"resource": prepared})
