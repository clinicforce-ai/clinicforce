"""Refresh the non-patient Epic User-access Brands endpoint catalog."""

from __future__ import annotations

import asyncio

import httpx

from app.core.patient_store import replace_epic_endpoints
from app.logs.logger import log_info

BRANDS_URL = "https://open.epic.com/Endpoints/Brands"


def _reference_id(value: object) -> str | None:
    if not isinstance(value, dict) or not isinstance(value.get("reference"), str):
        return None
    return value["reference"].rsplit("/", 1)[-1]


def _parse(bundle: dict) -> list[dict[str, object]]:
    resources = [entry.get("resource") for entry in bundle.get("entry", []) if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)]
    endpoints = {str(resource.get("id")): resource for resource in resources if resource.get("resourceType") == "Endpoint" and isinstance(resource.get("id"), str)}
    result: list[dict[str, object]] = []
    for organization in resources:
        if organization.get("resourceType") != "Organization" or not isinstance(organization.get("name"), str):
            continue
        aliases = [value for value in organization.get("alias", []) if isinstance(value, str)]
        addresses = [address for address in organization.get("address", []) if isinstance(address, dict)]
        for reference in organization.get("endpoint", []):
            endpoint = endpoints.get(_reference_id(reference) or "")
            if not isinstance(endpoint, dict) or endpoint.get("status") != "active" or not isinstance(endpoint.get("address"), str):
                continue
            result.append({
                "id": f"epic:{organization.get('id', organization['name'])}:{endpoint.get('id')}",
                "name": organization["name"], "aliases": aliases, "addresses": addresses,
                "fhir_base": endpoint["address"],
            })
    return result


async def main() -> None:
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=False) as client:
        response = await client.get(BRANDS_URL, headers={"Accept": "application/fhir+json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("resourceType") != "Bundle":
        raise RuntimeError("Epic endpoint catalog did not return a FHIR Bundle")
    endpoints = _parse(payload)
    if not endpoints:
        raise RuntimeError("Epic endpoint catalog contained no active endpoints")
    await asyncio.to_thread(replace_epic_endpoints, endpoints)
    log_info("Epic endpoint catalog", "Updated %d Epic endpoint records", len(endpoints))


if __name__ == "__main__":
    asyncio.run(main())
