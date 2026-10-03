"""Server-only Stedi real-time eligibility client."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


ELIGIBILITY_URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"


def fallback_enabled() -> bool:
    """Allow operations to turn off Stedi once Availity is the sole workflow."""
    return os.getenv("STEDI_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    mode: str
    api_key: str = field(repr=False)

    @classmethod
    def read(cls) -> "Settings":
        test_key = os.getenv("STEDI_TEST_API_KEY", "").strip()
        production_key = os.getenv("STEDI_API_KEY", "").strip()
        if test_key:
            return cls("test", test_key)
        if production_key:
            return cls("production", production_key)
        return cls("disabled", "")


def _person(first: object, last: object) -> dict | None:
    if not isinstance(first, str) or not isinstance(last, str) or not first.strip() or not last.strip():
        return None
    return {"person": {"firstName": first.strip(), "lastName": last.strip()}}


def request_body(inputs: dict, practice: dict, *, service_type: str = "98") -> tuple[dict | None, str | None]:
    """Build a 270 JSON request from verified eCW and card-scanner values."""
    payer_id = inputs.get("payer_id")
    stedi_payer_id = (practice.get("stedi_payer_ids") or {}).get(payer_id)
    provider = inputs.get("provider") or {}
    npi = provider.get("npi")
    provider_config = (practice.get("providers") or {}).get(npi, {})
    organization = provider_config.get("stedi_organization_name")
    subscriber = inputs.get("subscriber") or {}
    name = _person(subscriber.get("firstName"), subscriber.get("lastName"))
    if not isinstance(stedi_payer_id, str) or not stedi_payer_id:
        return None, "ClinicForce has not mapped this payer to a verified Stedi payer ID."
    if not isinstance(npi, str) or not npi.isdigit() or len(npi) != 10 or not isinstance(organization, str) or not organization.strip():
        return None, "ClinicForce must configure the billing provider NPI and Stedi organization name for this location."
    if not name or not all(isinstance(subscriber.get(key), str) and subscriber[key].strip() for key in ("memberId", "birthDate")):
        return None, "The subscriber name, date of birth, and member ID are required for Stedi eligibility."
    body: dict[str, Any] = {
        "payerId": stedi_payer_id,
        "provider": {"name": {"organization": organization.strip()}, "npi": npi},
        "subscriber": {"memberId": subscriber["memberId"].strip(), "dateOfBirth": subscriber["birthDate"].strip(), "name": name},
        "encounter": {"services": [{"system": "STC", "value": service_type}]},
    }
    service_date = inputs.get("service_date")
    if isinstance(service_date, str) and len(service_date) == 10:
        body["encounter"]["dates"] = {"service": {"start": service_date}}
    if inputs.get("relationship") != "18":
        patient = inputs.get("patient") or {}
        dependent_name = _person(patient.get("patient_first_name"), patient.get("patient_last_name"))
        birth_date = patient.get("patient_birth_date")
        if not dependent_name or not isinstance(birth_date, str) or not birth_date.strip():
            return None, "The dependent name and date of birth are required for Stedi eligibility."
        body["dependent"] = {"dateOfBirth": birth_date.strip(), "name": dependent_name}
    return body, None


class Client:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def check(self, body: dict) -> dict:
        if not self.settings.api_key:
            return {"outcome": "unavailable", "message": "Stedi is not configured on the backend."}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), transport=self.transport) as client:
                response = await client.post(ELIGIBILITY_URL, headers={"Authorization": self.settings.api_key, "Content-Type": "application/json"}, json=body)
        except httpx.HTTPError:
            return {"outcome": "unavailable", "message": "Stedi could not be reached."}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.is_error:
            return {"outcome": "payer_error" if response.status_code < 500 else "unavailable", "http_status": response.status_code,
                    "payload": payload, "message": str(payload.get("message") or "Stedi could not complete this eligibility check.")}
        return {"outcome": "complete", "http_status": response.status_code, "payload": payload,
                "check_id": payload.get("id") if isinstance(payload.get("id"), str) else None,
                "mock_response": self.settings.mode == "test"}
