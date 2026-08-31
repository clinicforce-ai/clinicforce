"""Transport-safe clinical-resource transformations."""

from copy import deepcopy
from typing import Any

from app.api.settings import EXCHANGE_RESOURCE_TYPES, FHIR_SOURCE_TAG_SYSTEM, FHIR_VERIFICATION_TAG_SYSTEM

from .patient import patient_reference
from .tags import tag_resource_source


def prepare_exchange_resource(
    resource: dict[str, Any],
    patient_id: str,
    source: str,
    display: str | None = None,
    allowed_resource_types: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return an exchange-safe R4 resource tied to the local patient."""
    if resource.get("resourceType") not in (allowed_resource_types or EXCHANGE_RESOURCE_TYPES):
        return None
    prepared = deepcopy(resource)
    reference = patient_reference(patient_id)
    for field in ("subject", "patient", "beneficiary"):
        if field in prepared:
            prepared[field] = reference
    resource_type = prepared["resourceType"]
    if resource_type in {"AllergyIntolerance", "Immunization", "Device"}:
        prepared.setdefault("patient", reference)
    elif resource_type == "Coverage":
        prepared.setdefault("beneficiary", reference)
    else:
        prepared.setdefault("subject", reference)
    _remove_document_attachment_data(prepared)
    _remove_untrusted_provenance(prepared, source)
    tag_resource_source(prepared, source, display)
    return prepared


def _remove_document_attachment_data(resource: dict[str, Any]) -> None:
    if resource.get("resourceType") != "DocumentReference":
        return
    for content in resource.get("content", []):
        attachment = content.get("attachment") if isinstance(content, dict) else None
        if isinstance(attachment, dict):
            attachment.pop("data", None)
            attachment.pop("url", None)


def _remove_untrusted_provenance(resource: dict[str, Any], source: str) -> None:
    if source not in {"external-shl", "ecw-healow"}:
        return
    meta = resource.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("tag"), list):
        return
    meta["tag"] = [
        tag for tag in meta["tag"]
        if not (
            isinstance(tag, dict)
            and (
                tag.get("system") == FHIR_SOURCE_TAG_SYSTEM and tag.get("code") in {"external-shl", "ecw-healow"}
                or tag.get("system") == FHIR_VERIFICATION_TAG_SYSTEM and tag.get("code") == "unverified"
            )
        )
    ]
