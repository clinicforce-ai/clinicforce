"""Canonical patient resource construction for external exchange."""

from copy import deepcopy
from typing import Any

from app.api.settings import FHIR_SOURCE_TAG_SYSTEM
from app.core.patient_store import PatientProfile

from .tags import tag_resource_source


def first_patient_resource(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bundle:
        return None
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict) and resource.get("resourceType") == "Patient":
            return resource
    return None


def patient_reference(patient_id: str) -> dict[str, str]:
    return {"reference": f"Patient/{patient_id}"}


def exchange_patient(
    patient_id: str,
    profile: PatientProfile | None,
    epic_bundle: dict[str, Any] | None,
    ecw_compat: bool = False,
) -> dict[str, Any]:
    """Build one canonical Patient without replacing saved demographics."""
    patient: dict[str, Any] = {"resourceType": "Patient", "id": patient_id}
    if profile:
        _apply_profile(patient, profile)
    candidate = first_patient_resource(epic_bundle)
    if candidate:
        _apply_epic_demographics(patient, candidate)
    if "meta" not in patient:
        tag_resource_source(patient, "patient-entered")
    if ecw_compat:
        apply_identity_assurance(patient)
    return patient


def _apply_profile(patient: dict[str, Any], profile: PatientProfile) -> None:
    if profile.given_name or profile.family_name:
        name: dict[str, Any] = {"use": "official"}
        if profile.given_name:
            name["given"] = [profile.given_name]
        if profile.family_name:
            name["family"] = profile.family_name
        patient["name"] = [name]
    for attribute, field in (("birth_date", "birthDate"), ("administrative_gender", "gender")):
        value = getattr(profile, attribute)
        if value:
            patient[field] = value
    if any((profile.given_name, profile.family_name, profile.birth_date, profile.administrative_gender)):
        tag_resource_source(patient, "patient-entered")


def _apply_epic_demographics(patient: dict[str, Any], candidate: dict[str, Any]) -> None:
    for field in ("identifier", "telecom", "address"):
        if candidate.get(field) is not None:
            patient[field] = deepcopy(candidate[field])
    for field in ("name", "gender", "birthDate"):
        if field not in patient and candidate.get(field) is not None:
            patient[field] = deepcopy(candidate[field])
    display = None
    tags = candidate.get("meta", {}).get("tag", []) if isinstance(candidate.get("meta"), dict) else []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("system") == FHIR_SOURCE_TAG_SYSTEM and tag.get("code") == "epic-mychart":
            display = tag.get("display")
            break
    tag_resource_source(patient, "epic-mychart", display)


def apply_identity_assurance(patient: dict[str, Any]) -> None:
    meta = patient.setdefault("meta", {})
    security = meta.setdefault("security", [])
    for system, code, display in (
        ("https://smarthealth.cards/ial", "IAL2", "Identity Assurance Level 2"),
        ("http://hl7.org/fhir/identity-assuranceLevel", "level3", "High confidence"),
    ):
        if not any(isinstance(item, dict) and item.get("system") == system and item.get("code") == code for item in security):
            security.append({"system": system, "code": code, "display": display})
    profile = "http://hl7.org/fhir/us/identity-matching/StructureDefinition/IDI-Patient-L2"
    profiles = meta.setdefault("profile", [])
    if profile not in profiles:
        profiles.append(profile)
