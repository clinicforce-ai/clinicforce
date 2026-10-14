"""Server-side Availity transport. Never follow arbitrary payer-supplied URLs."""

from __future__ import annotations

import os
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


TOKEN_URL = "https://api.availity.com/v1/token"
COVERAGES_URL = "https://api.availity.com/availity/v1/coverages"
DEMO_SCOPE = "healthcare-hipaa-transactions-demo"
LIVE_SCOPE = "healthcare-hipaa-transactions"
PATHS = {"coverage": "/v1/coverages", "estimate": "/v2/patient-cost-estimates/prof",
         "authorization": "/v2/service-reviews", "referral": "/v2/service-reviews"}
DEMO_IDS = {"estimate": "efe8b6ab-e47d-4975-aa73-adda35311850", "authorization": "12345678", "referral": "12345678"}
DEMO_SCENARIOS = {"coverage": ("Coverages-Complete-i", "Coverages-Complete-i"),
                  "estimate": ("PCEP-Accepted", "PCEP-Success"),
                  "authorization": ("SR-CreateRequestAccepted-i", "SR-GetComplete-i"),
                  "referral": ("SR-CreateRequestAccepted-i", "SR-GetComplete-i")}


@dataclass(frozen=True)
class Settings:
    environment: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    scope: str
    base_url: str
    token_url: str

    @classmethod
    def read(cls) -> Settings:
        environment = os.getenv("AVAILITY_ENVIRONMENT", "demo").strip()
        if environment not in {"demo", "production"}:
            raise ValueError("AVAILITY_ENVIRONMENT must be demo or production")
        scope = os.getenv("AVAILITY_SCOPE", DEMO_SCOPE if environment == "demo" else LIVE_SCOPE).strip()
        expected = DEMO_SCOPE if environment == "demo" else LIVE_SCOPE
        if expected not in scope.split() or (LIVE_SCOPE if environment == "demo" else DEMO_SCOPE) in scope.split():
            raise ValueError("Availity environment and OAuth scope do not match")
        base = os.getenv("AVAILITY_BASE_URL", "https://api.availity.com/availity").rstrip("/")
        token = os.getenv("AVAILITY_TOKEN_URL", TOKEN_URL)
        for address, paths in ((base, {"", "/availity"}), (token, {"/v1/token"})):
            url = urlsplit(address)
            if (url.scheme != "https" or url.netloc not in {"api.availity.com", "tst.api.availity.com", "qua.api.availity.com"}
                    or url.path not in paths or url.query or url.fragment):
                raise ValueError("Use an approved Availity HTTPS gateway and token URL")
        if urlsplit(base).netloc != urlsplit(token).netloc:
            raise ValueError("Availity gateway and token hosts must match")
        return cls(environment, os.getenv("AVAILITY_CLIENT_ID") or os.getenv("AVAILITY_API_KEY", ""),
                   os.getenv("AVAILITY_CLIENT_SECRET") or os.getenv("AVAILITY_SECRET_KEY", ""), scope, base, token)

    @property
    def binding(self) -> str:
        # Credential rotation preserves cases; changing the account or environment does not.
        return hashlib.sha256(json.dumps([self.environment, self.client_id, self.scope, self.base_url]).encode()).hexdigest()


@dataclass(frozen=True)
class CoverageInquiry:
    payer_id: str
    provider_npi: str
    submitter_id: str
    member_id: str
    patient_first_name: str
    patient_last_name: str
    patient_birth_date: str
    service_type: str = "30"

    def as_form(self) -> dict[str, str]:
        return {
            "payerId": self.payer_id,
            "providerNpi": self.provider_npi,
            "submitterId": self.submitter_id,
            "memberId": self.member_id,
            "patientFirstName": self.patient_first_name,
            "patientLastName": self.patient_last_name,
            "patientBirthDate": self.patient_birth_date,
            "serviceType": self.service_type,
        }

    def missing_fields(self) -> list[str]:
        return [key for key, value in self.as_form().items() if not value.strip()]


class AvailityClient:
    """Submit one Coverage inquiry and reduce it to ClinicForce outcomes."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        scope: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id or os.getenv("AVAILITY_CLIENT_ID") or os.getenv("AVAILITY_API_KEY")
        self._client_secret = client_secret or os.getenv("AVAILITY_CLIENT_SECRET") or os.getenv("AVAILITY_SECRET_KEY")
        self._scope = scope or os.getenv("AVAILITY_SCOPE", DEMO_SCOPE)
        self._transport = transport
        self._token = ""
        self._token_until = 0.0
        self._token_lock = asyncio.Lock()
        self._token_url = TOKEN_URL

    async def check_coverage(
        self,
        inquiry: CoverageInquiry,
        *,
        mock_scenario: str | None = None,
    ) -> dict[str, Any]:
        missing = inquiry.missing_fields()
        if missing:
            return {"outcome": "input_error", "message": f"Missing: {', '.join(missing)}."}
        if not self._client_id or not self._client_secret:
            return {
                "outcome": "input_error",
                "message": "Availity credentials are not configured on the server.",
            }

        try:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
                token = await self._access_token(client)
                headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                if mock_scenario:
                    headers["X-Api-Mock-Scenario-ID"] = mock_scenario
                    headers["X-Api-Mock-Response"] = "true"
                response = await client.post(COVERAGES_URL, data=inquiry.as_form(), headers=headers)
        except httpx.HTTPError:
            return {"outcome": "payer_error", "message": "Unable to reach Availity."}

        payload = _json_body(response)
        if response.status_code == 400:
            return {"outcome": "input_error", "message": _message(payload, "Availity rejected the input.")}
        if response.is_error:
            return {"outcome": "payer_error", "message": _message(payload, "Availity request failed.")}
        return _normalize_coverage(
            payload,
            response.headers.get("x-api-id"),
            response.headers.get("x-api-mock-response", "").lower() == "true",
        )

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_until:
                return self._token
            return await self._new_token(client)

    async def _new_token(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": self._scope,
            },
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(8, connect=5),
        )
        response.raise_for_status()
        token = _json_body(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise httpx.HTTPError("Availity token response did not contain an access token")
        self._token = token
        try:
            lifetime = max(0, min(3600, int(_json_body(response).get("expires_in", 0))) - 30)
        except (TypeError, ValueError):
            lifetime = 0
        self._token_until = time.monotonic() + lifetime
        return token


class Gateway(AvailityClient):
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings.client_id, settings.client_secret, scope=settings.scope, transport=transport)
        self.settings = settings
        self._token_url = settings.token_url

    def resource_id(self, location: str, resource: str) -> str | None:
        url = urlsplit(urljoin(self.settings.base_url + PATHS[resource] + "/", location))
        base = urlsplit(self.settings.base_url)
        prefix = base.path + PATHS[resource] + "/"
        if (url.scheme != base.scheme or url.netloc != base.netloc or url.query or url.fragment
                or not url.path.startswith(prefix)):
            return None
        candidate = url.path[len(prefix):]
        return candidate if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", candidate) else None

    async def request(self, method: str, path: str, *, data: dict | None = None,
                      body: dict | None = None, params: dict | None = None,
                      scenario: str | None = None, resource: str | None = None) -> dict:
        if not self.settings.client_id or not self.settings.client_secret:
            return {"outcome": "unavailable", "message": "Availity credentials are not configured on the backend."}
        if self.settings.environment == "production" and scenario:
            raise ValueError("Mock scenarios cannot be sent in production")
        if not re.fullmatch(r"/v[12]/[a-z0-9/-]+(?:[A-Za-z0-9_-]*)", path):
            raise ValueError("Invalid Availity resource path")
        submitted = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), transport=self._transport, follow_redirects=False) as client:
                for attempt in range(2):
                    token = await self._access_token(client)
                    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
                    if scenario:
                        headers.update({"X-Api-Mock-Response": "true", "X-Api-Mock-Scenario-ID": scenario})
                    submitted = True
                    response = await client.request(method, self.settings.base_url + path, headers=headers, data=data, json=body, params=params)
                    if response.status_code != 401 or attempt:
                        break
                    self._token = ""
        except httpx.HTTPError:
            return {"outcome": "indeterminate" if submitted and method == "POST" else "unavailable",
                    "message": "Availity could not be reached. A submitted transaction may still exist; do not submit it again." if submitted and method == "POST" else "Availity authentication or connection failed. Check the server configuration and subscription."}
        mock = response.headers.get("x-api-mock-response", "").lower() == "true"
        if (self.settings.environment == "production" and mock) or (scenario and response.is_success and not mock):
            return {"outcome": "unavailable", "message": "Availity response environment did not match this workflow."}
        try:
            payload = response.json() if len(response.content) < 5_000_000 else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, (dict, list)):
            payload = {}
        result = {"http_status": response.status_code, "mock_response": mock,
                  "transaction_id": response.headers.get("x-api-id"), "payload": payload}
        retry = response.headers.get("retry-after", "3")
        result["retry_after"] = min(300, max(3, int(retry))) if retry.isdigit() else 5
        if response.is_error or response.is_redirect:
            outcome = "input_error" if response.status_code in {400, 422} else "unavailable"
            if method == "POST" and response.status_code >= 500:
                outcome = "indeterminate"
            return {**result, "outcome": outcome, "message": _message(payload if isinstance(payload, dict) else {},
                    f"Availity returned HTTP {response.status_code}. Check product access, payer enrollment, and request requirements.")}
        if resource:
            obj = unwrap(payload, resource)
            location = response.headers.get("location", "")
            resource_id = self.resource_id(location, resource) if location else None
            body_id = str(obj.get("id", ""))
            if not resource_id and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", body_id):
                resource_id = body_id
            if location and not self.resource_id(location, resource):
                return {**result, "outcome": "indeterminate", "message": "Availity returned an unexpected polling URL. Contact support with the transaction ID."}
            if scenario and resource in DEMO_IDS:
                resource_id = DEMO_IDS[resource]
            result["resource_id"] = resource_id
            status_code = str(obj.get("statusCode", ""))
            pending = response.status_code == 202 or status_code in {"0", "R1", "A4", "A6"}
            outcome = "pending" if pending else "complete"
            if resource == "coverage" and not pending and status_code != "4":
                outcome = "payer_error"
            if resource in {"authorization", "referral"} and not pending and status_code not in {"A1", "A2", "A3", "C", "CT", "NA", "51", "71"}:
                outcome = "payer_error"
                result["message"] = "Availity did not return a recognized service-review determination. Contact the payer with the transaction ID."
            if obj.get("errors") or (obj.get("validationMessages") and outcome != "complete"):
                outcome = "payer_error"
                result["message"] = _message(obj, "Availity returned validation errors.")
            if pending and not resource_id:
                outcome = "indeterminate"
                result["message"] = "The transaction was accepted without a usable result ID. Contact Availity before resubmitting."
            return {**result, "outcome": outcome}
        return {**result, "outcome": "complete"}


@lru_cache(maxsize=2)
def gateway(settings: Settings) -> Gateway:
    return Gateway(settings)


from app.insurance.availity_response import unwrap, _json_body, _normalize_coverage, _payer_id, _message
