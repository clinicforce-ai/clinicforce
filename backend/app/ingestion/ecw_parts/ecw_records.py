"""Patient-authorized eCW resource assembly and safe review fields."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import ModuleType
from typing import Any

from app.ingestion.ecw_parts.resource_types import OBSERVATION_CATEGORIES, USCDI_RESOURCE_TYPES


def _sanitize_resource_for_review(resource: dict[str, Any]) -> dict[str, Any]:
    """Do not send document bytes or provider download URLs to the browser."""
    sanitized = json.loads(json.dumps(resource))
    if isinstance(sanitized.get("contained"), list):
        sanitized["contained"] = [
            _sanitize_resource_for_review(item) for item in sanitized["contained"]
            if isinstance(item, dict) and item.get("resourceType") != "Binary"
        ]
    if sanitized.get("resourceType") == "DocumentReference":
        for content in sanitized.get("content", []):
            attachment = content.get("attachment") if isinstance(content, dict) else None
            if isinstance(attachment, dict):
                attachment.pop("data", None)
                attachment.pop("url", None)
    return sanitized

def _unique_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for resource in resources:
        if resource.get("resourceType") not in USCDI_RESOURCE_TYPES:
            continue
        key = (resource["resourceType"], str(resource.get("id") or json.dumps(resource, sort_keys=True)))
        unique.setdefault(key, _sanitize_resource_for_review(resource))
    return list(unique.values())

async def fetch_resource_type(entry: ModuleType, session: Any, resource_type: str) -> dict[str, Any]:
    if resource_type not in USCDI_RESOURCE_TYPES:
        raise entry.EcwError("That eClinicalWorks resource type is not available in ClinicForce")
    if resource_type == "Patient":
        status, patient, _ = await entry._get_json(session, f"{session.fhir_base}/Patient/{session.remote_patient_id}")
        if status == 401:
            entry.clear_live_session_by_value(session)
            raise entry.EcwError("Your eClinicalWorks session has ended. Reconnect healow to continue.")
        if status in {403, 404}:
            return entry._collection([], "forbidden" if status == 403 else "unavailable")
        if patient.get("resourceType") != "Patient" or patient.get("id") != session.remote_patient_id:
            raise entry.EcwError("eClinicalWorks returned an unexpected patient identity. Reconnect before importing.")
        return entry._collection([_sanitize_resource_for_review(patient)])
    params = {"patient": session.remote_patient_id, "_revinclude": "Provenance:target"}
    if resource_type == "CarePlan":
        params["category"] = "assess-plan"
    elif resource_type == "CareTeam":
        params["status"] = "active"
    elif resource_type == "Provenance":
        # Provenance has no patient search. Request it alongside its targets.
        resources, result = await entry._search_resources(session, "Patient", {
            "_id": session.remote_patient_id, "_revinclude": "Provenance:target",
        })
        return entry._collection([r for r in resources if r.get("resourceType") == "Provenance"], result)
    if resource_type != "Observation":
        resources, result = await entry._search_resources(session, resource_type, params)
        return entry._collection(_unique_resources(resources), result)

    resources: list[dict[str, Any]] = []
    statuses: list[str] = []
    for category in OBSERVATION_CATEGORIES:
        try:
            found, result = await entry._search_resources(session, "Observation", {**params, "category": category})
        except entry.EcwError as exc:
            if "session has ended" in str(exc):
                raise
            found, result = [], "error"
        resources.extend(found)
        statuses.append(result)
    result = "ok" if all(s == "ok" for s in statuses) else statuses[0] if len(set(statuses)) == 1 else "partial"
    return entry._collection(_unique_resources(resources), result)

async def fetch_record(entry: ModuleType, session: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Retrieve approved resources only; persistence waits for explicit review."""
    concurrency = asyncio.Semaphore(5)

    async def bounded_fetch(resource_type: str) -> dict[str, Any]:
        async with concurrency:
            return await entry.fetch_resource_type(session, resource_type)

    bundles = await asyncio.gather(
        *(bounded_fetch(resource_type) for resource_type in USCDI_RESOURCE_TYPES), return_exceptions=True,
    )
    entries: list[dict[str, Any]] = []
    availability: dict[str, str] = {}
    for resource_type, bundle in zip(USCDI_RESOURCE_TYPES, bundles, strict=True):
        if isinstance(bundle, Exception):
            if resource_type == "Patient" or "session has ended" in str(bundle):
                raise bundle
            availability[resource_type] = "error"
            continue
        entries.extend(entry for entry in bundle.get("entry", []) if isinstance(entry, dict))
        result_status = next((
            extension.get("valueString") for extension in bundle.get("extension", [])
            if isinstance(extension, dict) and extension.get("url") == "urn:clinicforce:ecw-status"
        ), "ok")
        matches = any(entry.get("resource", {}).get("resourceType") == resource_type for entry in bundle.get("entry", []) if isinstance(entry, dict))
        availability[resource_type] = "empty" if result_status == "ok" and not matches else str(result_status)
    resources = _unique_resources([entry["resource"] for entry in entries if isinstance(entry.get("resource"), dict)])
    entries = [{"resource": resource} for resource in resources]
    if any(r.get("resourceType") == "Provenance" for r in resources) and availability.get("Provenance") == "empty":
        availability["Provenance"] = "ok"
    record = {"resourceType": "Bundle", "type": "collection", "entry": entries}
    session.review_bundle = record
    session.review_availability = availability
    session.retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return record, availability

def source_metadata(session: Any) -> dict[str, str]:
    return {
        "organization_name": f"eClinicalWorks / healow (practice {session.practice_code})",
        "practice_code": session.practice_code,
        "retrieved_at": session.retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
