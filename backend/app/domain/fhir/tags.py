"""Trusted provenance tags for exchanged FHIR resources."""

from app.api.settings import FHIR_SOURCE_TAG_SYSTEM, FHIR_VERIFICATION_TAG_SYSTEM, SOURCE_TAGS


def append_meta_tag(resource: dict, system: str, code: str, display: str) -> None:
    """Add a stable FHIR tag without discarding upstream metadata."""
    meta = resource.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = resource["meta"] = {}
    tags = meta.setdefault("tag", [])
    if not isinstance(tags, list):
        tags = meta["tag"] = []
    if not any(isinstance(tag, dict) and tag.get("system") == system and tag.get("code") == code for tag in tags):
        tags.append({"system": system, "code": code, "display": display})


def tag_resource_source(resource: dict, source: str, display: str | None = None) -> None:
    """Apply ClinicForce's source and verification convention."""
    if source not in SOURCE_TAGS:
        raise ValueError(f"Unsupported FHIR source tag: {source}")
    append_meta_tag(resource, FHIR_SOURCE_TAG_SYSTEM, source, display or SOURCE_TAGS[source])
    if source in {"document-extracted", "external-shl"}:
        verification = (
            "External document extraction; clinician reconciliation required"
            if source == "document-extracted"
            else "External information; clinician reconciliation required"
        )
        append_meta_tag(resource, FHIR_VERIFICATION_TAG_SYSTEM, "unverified", verification)
