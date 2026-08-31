"""FHIR Bundle assembly helpers."""

from copy import deepcopy
from typing import Any


def document_payload(document: Any) -> dict[str, Any]:
    return {
        "id": document.id,
        "filename": document.filename,
        "uploaded_at": document.uploaded_at,
        "resource_count": len(document.fhir_bundle.get("entry", [])),
        "extraction_version": getattr(document, "extraction_version", None),
        "storage_provider": getattr(document, "storage_provider", "s3"),
    }


def combine_document_bundles(documents: list[Any], records: list[Any] | None = None) -> dict[str, Any]:
    """Combine persisted document and manual-record entries for workspace use."""
    entries: list[dict[str, Any]] = []
    for document in documents:
        bundle = deepcopy(document.fhir_bundle)
        for entry in bundle.get("entry", []):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                continue
            for extension in resource.get("extension", []):
                if not isinstance(extension, dict) or extension.get("url") != "https://clinicforce.example/fhir/StructureDefinition/source-citation":
                    continue
                children = extension.setdefault("extension", [])
                if isinstance(children, list) and not any(
                    isinstance(child, dict) and child.get("url") == "document_id" for child in children
                ):
                    children.append({"url": "document_id", "valuePositiveInt": document.id})
        entries.extend(entry for entry in bundle.get("entry", []) if isinstance(entry, dict))
    for record in records or []:
        entries.extend(entry for entry in record.fhir_bundle.get("entry", []) if isinstance(entry, dict))
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}
