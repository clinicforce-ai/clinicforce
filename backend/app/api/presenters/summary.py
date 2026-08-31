"""Clinician summary response projections."""

from copy import deepcopy

from app.core.source_links import document_download_url


def with_source_download_links(summary: dict, manifest_id: str, public_base_url: str) -> dict:
    """Bind source-document URLs to the authorized manifest only."""
    rendered = deepcopy(summary)
    for section in ("vitals", "labs", "medications", "conditions", "allergies", "care_events"):
        for item in rendered.get(section, []):
            citation = item.get("citation") if isinstance(item, dict) else None
            document_id = citation.get("document_id") if isinstance(citation, dict) else None
            if isinstance(document_id, int) and document_id > 0:
                citation["download_url"] = document_download_url(public_base_url, document_id, manifest_id)
    return rendered
