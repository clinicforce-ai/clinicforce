"""Trusted-boundary normalization for imported FHIR bundles."""

from typing import Any

from app.api.settings import ECW_IMPORT_RESOURCE_TYPES

from .resources import prepare_exchange_resource


def normalize_external_shl_bundle(bundle: dict[str, Any], patient_id: str, source_host: str) -> dict[str, Any]:
    candidates = _bundle_candidates(bundle)
    entries = _prepared_entries(candidates, patient_id, "external-shl", f"External SMART Health Link: {source_host}")
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def normalize_ecw_bundle(bundle: dict[str, Any], patient_id: str, practice_code: str) -> dict[str, Any]:
    candidates = [candidate for candidate in _bundle_candidates(bundle) if candidate.get("resourceType") != "Patient"]
    entries = _prepared_entries(
        candidates, patient_id, "ecw-healow", f"eClinicalWorks / healow practice {practice_code}", ECW_IMPORT_RESOURCE_TYPES,
    )
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def _bundle_candidates(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if bundle.get("resourceType") != "Bundle":
        return [bundle]
    return [entry["resource"] for entry in bundle.get("entry", []) if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)]


def _prepared_entries(
    candidates: list[dict[str, Any]], patient_id: str, source: str, display: str, allowed_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        prepared = prepare_exchange_resource(candidate, patient_id, source, display, allowed_types)
        if prepared is not None:
            entries.append({"resource": prepared})
    return entries
