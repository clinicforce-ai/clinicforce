from __future__ import annotations

from app.scripts.sync_epic_endpoints import _parse


def test_parse_resolves_epic_urn_uuid_endpoint_references() -> None:
    endpoint_id = "9bbe8119-65bf-4343-a0c2-22374f183c68"
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": {
                "resourceType": "Organization",
                "id": "houston-methodist",
                "name": "Houston Methodist",
                "endpoint": [{"reference": f"urn:uuid:{endpoint_id}"}],
            }},
            {"resource": {
                "resourceType": "Endpoint",
                "id": endpoint_id,
                "status": "active",
                "address": "https://fhir.houstonmethodist.org/interconnect-fhir-oauth/api/FHIR/R4",
            }},
        ],
    }

    assert _parse(bundle) == [{
        "id": f"epic:houston-methodist:{endpoint_id}",
        "name": "Houston Methodist",
        "aliases": [],
        "addresses": [],
        "fhir_base": "https://fhir.houstonmethodist.org/interconnect-fhir-oauth/api/FHIR/R4",
    }]
