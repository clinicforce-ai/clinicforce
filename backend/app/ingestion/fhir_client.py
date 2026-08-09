"""Asynchronous client for the configured HAPI FHIR R4 server."""

from __future__ import annotations

from copy import deepcopy
import os
from typing import Any
from uuid import uuid4

import httpx

HAPI_FHIR_URL = os.getenv("HAPI_FHIR_URL", "http://localhost:8080").rstrip("/")
FHIR_BASE_URL = (
    HAPI_FHIR_URL if HAPI_FHIR_URL.endswith("/fhir") else f"{HAPI_FHIR_URL}/fhir"
)


class FHIRRequestError(httpx.HTTPStatusError):
    """An HTTP error from HAPI that retains the response body for diagnostics."""


def _transaction_from_collection(bundle: dict[str, Any]) -> dict[str, Any]:
    """Make a HAPI-writable transaction without changing the shareable source bundle."""
    resources: list[dict[str, Any]] = []
    patient_references: set[str] = set()
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        resource_type = resource.get("resourceType") if isinstance(resource, dict) else None
        if not isinstance(resource_type, str) or not resource_type:
            raise ValueError("Each Bundle entry must contain a resource with resourceType")
        copied_resource = deepcopy(resource)
        resources.append(copied_resource)
        _collect_patient_references(copied_resource, patient_references)

    patient_urns = {
        reference: f"urn:uuid:{uuid4()}" for reference in sorted(patient_references)
    }
    transaction_entries: list[dict[str, Any]] = []
    for reference, patient_urn in patient_urns.items():
        # The user-provided ID is kept as an identifier, not forced as HAPI's server ID.
        # `fullUrl` lets every resource in this transaction refer to the new Patient.
        transaction_entries.append(
            {
                "fullUrl": patient_urn,
                "resource": {
                    "resourceType": "Patient",
                    "identifier": [
                        {"system": "urn:clinicforce:patient-id", "value": reference.removeprefix("Patient/")}
                    ],
                },
                "request": {
                    "method": "POST",
                    "url": "Patient",
                    "ifNoneExist": f"identifier=urn:clinicforce:patient-id|{reference.removeprefix('Patient/')}",
                },
            }
        )

    for resource in resources:
        _replace_patient_references(resource, patient_urns)
        resource_type = resource["resourceType"]
        transaction_entries.append(
            {
                "resource": resource,
                "request": {"method": "POST", "url": resource_type},
            }
        )
    return {"resourceType": "Bundle", "type": "transaction", "entry": transaction_entries}


def _collect_patient_references(value: Any, references: set[str]) -> None:
    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and reference.startswith("Patient/"):
            references.add(reference)
        for child in value.values():
            _collect_patient_references(child, references)
    elif isinstance(value, list):
        for child in value:
            _collect_patient_references(child, references)


def _replace_patient_references(value: Any, replacements: dict[str, str]) -> None:
    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and reference in replacements:
            value["reference"] = replacements[reference]
        for child in value.values():
            _replace_patient_references(child, replacements)
    elif isinstance(value, list):
        for child in value:
            _replace_patient_references(child, replacements)


async def post_to_fhir(resource: dict) -> dict[str, Any]:
    """Create a resource on HAPI, converting collection Bundles to transactions."""
    resource_type = resource.get("resourceType")
    if not resource_type:
        raise ValueError("FHIR resource must include resourceType")
    if resource_type == "Bundle" and resource.get("type") == "collection":
        if not resource.get("entry"):
            # A valid extraction can contain no supported clinical facts to persist.
            return {"resourceType": "Bundle", "type": "transaction-response", "entry": []}
        resource = _transaction_from_collection(resource)
    url = FHIR_BASE_URL if resource_type == "Bundle" else f"{FHIR_BASE_URL}/{resource_type}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=resource,
            headers={
                "Accept": "application/fhir+json",
                "Prefer": "return=representation",
            },
        )
        if response.is_error:
            raise FHIRRequestError(
                f"HAPI rejected POST {url} with {response.status_code}: {response.text}",
                request=response.request,
                response=response,
            )
    return response.json() if response.content else {"status_code": response.status_code}


async def get_patient_records(patient_id: str) -> dict[str, Any]:
    """Retrieve a patient's complete record bundle from HAPI."""
    url = f"{FHIR_BASE_URL}/Patient/{patient_id}/$everything"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers={"Accept": "application/fhir+json"})
        if response.is_error:
            raise FHIRRequestError(
                f"HAPI rejected GET {url} with {response.status_code}: {response.text}",
                request=response.request,
                response=response,
            )
    payload = response.json()
    if payload.get("resourceType") != "Bundle":
        raise ValueError("HAPI patient record response was not a FHIR Bundle")
    return payload
