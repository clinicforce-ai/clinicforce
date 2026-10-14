"""Live, patient-authorized Epic SMART-on-FHIR access.

This module deliberately keeps Epic access tokens and retrieved resources out of
RDS, S3, HAPI, browser storage, and logs.  A live connection survives only for
the current backend process and its short-lived browser session cookie.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from jwcrypto import jwe, jwk
from app.ingestion.epic_oauth_helpers import EPIC_OAUTH_STATE_SECRET, EpicError, _state_key, _valid_https_url, _same_origin, _smart_configuration, _pkce_challenge, _seal_state, _open_state, _safe_next_url


EPIC_CLIENT_ID = os.getenv("EPIC_CLIENT_ID", "").strip()
OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60
LIVE_SESSION_MAX_AGE_SECONDS = 55 * 60
MAX_PAGES_PER_RESOURCE = 50
MAX_RESOURCES_PER_TYPE = 5_000
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
CONDITION_SEARCH_CATEGORIES = (
    "problem-list-item",
    "health-concern",
    "encounter-diagnosis",
)
OBSERVATION_SEARCH_CATEGORIES = (
    "laboratory",
    "vital-signs",
    "social-history",
    "core-characteristics",
    "survey",
)
CAREPLAN_SEARCH_CATEGORIES = (
    "assess-plan",
    "longitudinal",
)

# Only standard patient-facing USCDI v3 R4 resources. This list deliberately
# excludes Outside Record, research, and proprietary APIs so ClinicForce's
# OAuth request stays compatible with Epic Automatic Client ID Distribution.
# A hospital can still filter a permitted family; 403/404 and malformed/partial
# results are surfaced as availability rather than being mistaken for an empty
# chart.
USCDI_RESOURCE_TYPES = (
    "Patient", "AllergyIntolerance", "CarePlan", "CareTeam", "Condition",
    "Coverage", "Device", "DiagnosticReport", "DocumentReference", "Encounter",
    "Goal", "Immunization", "MedicationRequest", "Observation",
)




@dataclass(frozen=True)
class EpicOAuthStart:
    authorization_url: str
    state_cookie: str


@dataclass
class LiveEpicSession:
    patient_id: str
    fhir_base: str
    remote_patient_id: str
    access_token: str
    expires_at: float
    organization_name: str


_live_sessions: dict[str, LiveEpicSession] = {}


def enabled() -> bool:
    return bool(EPIC_CLIENT_ID and EPIC_OAUTH_STATE_SECRET)


async def begin_authorization(
    fhir_base: str, organization_name: str, patient_id: str, redirect_uri: str, *, popup: bool = False,
) -> EpicOAuthStart:
    if not enabled():
        raise EpicError("Epic connection is not configured yet")
    fhir_base = _valid_https_url(fhir_base, name="FHIR endpoint")
    configuration = await _smart_configuration(fhir_base)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    payload = {
        "state": state,
        "verifier": verifier,
        "patient_id": patient_id,
        "fhir_base": fhir_base,
        "organization_name": organization_name[:256],
        "token_endpoint": configuration["token_endpoint"],
        "popup": popup,
        "expires_at": time.time() + OAUTH_STATE_MAX_AGE_SECONDS,
    }
    scopes = ["launch/patient", "openid", "fhirUser"] + [f"patient/{resource}.read" for resource in USCDI_RESOURCE_TYPES]
    query = urlencode({
        "response_type": "code",
        "client_id": EPIC_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "aud": fhir_base,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    })
    return EpicOAuthStart(f"{configuration['authorization_endpoint']}?{query}", _seal_state(payload))


async def complete_authorization(
    state_cookie: str, state: str, code: str, redirect_uri: str, patient_id: str,
) -> tuple[str, bool]:
    payload = _open_state(state_cookie)
    if not secrets.compare_digest(state, str(payload.get("state", ""))) or not code:
        raise EpicError("Your Epic connection could not be verified. Start again.")
    # The callback must still belong to the ClinicForce account that initiated
    # it.  State is encrypted, but tying it to the current local session also
    # prevents a callback URL from being replayed in another signed-in browser.
    if not secrets.compare_digest(patient_id, str(payload.get("patient_id", ""))):
        raise EpicError("This Epic connection belongs to a different ClinicForce session.")
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.post(
                str(payload["token_endpoint"]),
                data={
                    "grant_type": "authorization_code", "code": code,
                    "client_id": EPIC_CLIENT_ID, "redirect_uri": redirect_uri,
                    "code_verifier": str(payload["verifier"]),
                },
                headers={"Accept": "application/json"},
            )
        response.raise_for_status()
        token = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EpicError("Epic could not complete this connection. Check the app authorization and try again.") from exc
    access_token, remote_patient_id = token.get("access_token"), token.get("patient")
    if not isinstance(access_token, str) or not access_token or not isinstance(remote_patient_id, str) or not remote_patient_id:
        raise EpicError("Epic did not return a patient record for this connection")
    expires_in = token.get("expires_in", 3600)
    try:
        lifetime = min(max(int(expires_in), 60), LIVE_SESSION_MAX_AGE_SECONDS)
    except (TypeError, ValueError):
        lifetime = LIVE_SESSION_MAX_AGE_SECONDS
    session_id = secrets.token_urlsafe(32)
    _live_sessions[session_id] = LiveEpicSession(
        patient_id=str(payload["patient_id"]), fhir_base=str(payload["fhir_base"]),
        remote_patient_id=remote_patient_id, access_token=access_token,
        expires_at=time.time() + lifetime, organization_name=str(payload["organization_name"]),
    )
    return session_id, bool(payload.get("popup"))


def popup_requested(state_cookie: str) -> bool:
    """Return the requested presentation mode without trusting a query value."""
    try:
        return bool(_open_state(state_cookie).get("popup"))
    except EpicError:
        return False


def clear_live_session(session_id: str) -> None:
    _live_sessions.pop(session_id, None)


def get_live_session(session_id: str, patient_id: str) -> LiveEpicSession:
    session = _live_sessions.get(session_id)
    if session is None or session.patient_id != patient_id or session.expires_at <= time.time():
        if session_id:
            clear_live_session(session_id)
        raise EpicError("Your live Epic session has ended. Connect MyChart again.")
    return session




async def _get_json(session: LiveEpicSession, url: str, params: dict[str, str] | None = None) -> tuple[int, dict[str, Any], dict[str, str]]:
    headers = {"Authorization": f"Bearer {session.access_token}", "Accept": "application/fhir+json"}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise EpicError("Epic is temporarily unavailable. Try again shortly.") from exc
            await asyncio.sleep(2 ** attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                raise EpicError("Epic is temporarily unavailable. Try again shortly.")
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(max(int(retry_after), 1), 15)
            except ValueError:
                delay = 2 ** attempt
            await asyncio.sleep(delay)
            continue
        if response.status_code in {401, 403, 404}:
            return response.status_code, {}, dict(response.headers)
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EpicError("Epic returned an unreadable clinical-data response") from exc
        if not isinstance(payload, dict):
            raise EpicError("Epic returned an invalid clinical-data response")
        return response.status_code, payload, dict(response.headers)
    raise EpicError("Epic is temporarily unavailable. Try again shortly.")


async def _search_resources(
    session: LiveEpicSession, resource_type: str, params: dict[str, str],
) -> tuple[list[dict[str, Any]], str]:
    """Read one paginated FHIR search without treating an empty result as an error."""
    status, bundle, _ = await _get_json(session, f"{session.fhir_base}/{resource_type}", params)
    if status == 401:
        clear_live_session_by_value(session)
        raise EpicError("Your live Epic session has ended. Connect MyChart again.")
    if status in {403, 404}:
        return [], "forbidden" if status == 403 else "unavailable"
    resources: list[dict[str, Any]] = []
    pages = 0
    visited_pages: set[str] = set()
    while True:
        for entry in bundle.get("entry", []):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict) and resource.get("resourceType") != "OperationOutcome":
                resources.append(resource)
                if len(resources) >= MAX_RESOURCES_PER_TYPE:
                    return resources, "truncated"
        next_url = next((link.get("url") for link in bundle.get("link", []) if isinstance(link, dict) and link.get("relation") == "next" and isinstance(link.get("url"), str)), None)
        if not next_url:
            break
        target = _safe_next_url(session.fhir_base, next_url)
        if target in visited_pages:
            return resources, "partial"
        visited_pages.add(target)
        pages += 1
        if pages >= MAX_PAGES_PER_RESOURCE:
            return resources, "truncated"
        status, bundle, _ = await _get_json(session, target)
        if status != 200:
            return resources, "partial"
    return resources, "ok"


def _collection(resources: list[dict[str, Any]], status: str = "ok") -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": resource} for resource in resources],
    }
    if status != "ok":
        bundle["extension"] = [{
            "url": "urn:clinicforce:epic-status",
            "valueString": status,
        }]
    return bundle


async def fetch_resource_type(session: LiveEpicSession, resource_type: str) -> dict[str, Any]:
    if resource_type not in USCDI_RESOURCE_TYPES:
        raise EpicError("That Epic resource type is not available in ClinicForce")
    if resource_type == "Patient":
        status, patient, _ = await _get_json(session, f"{session.fhir_base}/Patient/{session.remote_patient_id}")
        if status == 401:
            clear_live_session_by_value(session)
            raise EpicError("Your live Epic session has ended. Connect MyChart again.")
        if status in {403, 404}:
            return _collection([], "unavailable")
        return _collection([patient])

    # Epic exposes MyChart condition data in distinct search categories. Asking
    # for each category prevents a generic empty Condition search from hiding a
    # problem list or health concern that the patient can see in MyChart.
    search_params = [{"patient": session.remote_patient_id}]
    if resource_type == "Condition":
        search_params.extend({"patient": session.remote_patient_id, "category": category} for category in CONDITION_SEARCH_CATEGORIES)
    elif resource_type == "Observation":
        search_params.extend({"patient": session.remote_patient_id, "category": category} for category in OBSERVATION_SEARCH_CATEGORIES)
    elif resource_type == "CarePlan":
        search_params.extend({"patient": session.remote_patient_id, "category": category} for category in CAREPLAN_SEARCH_CATEGORIES)

    resources: list[dict[str, Any]] = []
    statuses: list[str] = []
    for params in search_params:
        try:
            returned, search_status = await _search_resources(session, resource_type, params)
        except EpicError:
            # A legacy Epic tenant can decline an optional category while still
            # serving its regular search. Preserve its usable data.
            statuses.append("partial")
            continue
        resources.extend(returned)
        statuses.append(search_status)

    unique_resources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for resource in resources:
        resource_id = resource.get("id")
        key = (str(resource.get("resourceType", resource_type)), str(resource_id))
        if resource_id is not None and key in seen:
            continue
        if resource_id is not None:
            seen.add(key)
        unique_resources.append(resource)
        if len(unique_resources) >= MAX_RESOURCES_PER_TYPE:
            return _collection(unique_resources, "truncated")

    if any(status == "ok" for status in statuses):
        overall_status = "partial" if any(status in {"partial", "truncated"} for status in statuses) else "ok"
    elif "forbidden" in statuses:
        overall_status = "forbidden"
    elif "unavailable" in statuses:
        overall_status = "unavailable"
    else:
        overall_status = "partial"
    return _collection(unique_resources, overall_status)


def clear_live_session_by_value(value: LiveEpicSession) -> None:
    for key, session in list(_live_sessions.items()):
        if session is value:
            _live_sessions.pop(key, None)


async def fetch_record(session: LiveEpicSession) -> tuple[dict[str, Any], dict[str, str]]:
    # Some patient records span many families. Keep the first live retrieval
    # responsive without issuing an unbounded burst against a hospital's FHIR
    # service.
    concurrency = asyncio.Semaphore(6)

    async def bounded_fetch(resource_type: str) -> dict[str, Any]:
        async with concurrency:
            return await fetch_resource_type(session, resource_type)

    bundles = await asyncio.gather(
        *(bounded_fetch(resource_type) for resource_type in USCDI_RESOURCE_TYPES),
        return_exceptions=True,
    )
    entries: list[dict[str, Any]] = []
    availability: dict[str, str] = {}
    for resource_type, bundle in zip(USCDI_RESOURCE_TYPES, bundles, strict=True):
        if isinstance(bundle, Exception):
            if "session has ended" in str(bundle):
                raise bundle
            availability[resource_type] = "partial"
            continue
        entries.extend(entry for entry in bundle.get("entry", []) if isinstance(entry, dict))
        status = next((extension.get("valueString") for extension in bundle.get("extension", []) if isinstance(extension, dict) and extension.get("url") == "urn:clinicforce:epic-status"), "ok")
        availability[resource_type] = "empty" if status == "ok" and not bundle.get("entry") else str(status)
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}, availability


async def stream_document(session: LiveEpicSession, document_id: str) -> tuple[bytes, str]:
    if not document_id or "/" in document_id:
        raise EpicError("Invalid Epic document")
    status, document, _ = await _get_json(session, f"{session.fhir_base}/DocumentReference/{document_id}")
    if status == 401:
        clear_live_session_by_value(session)
        raise EpicError("Your live Epic session has ended. Connect MyChart again.")
    if status != 200:
        raise EpicError("This Epic document is no longer available")
    contents = document.get("content", [])
    attachment = next((item.get("attachment") for item in contents if isinstance(item, dict) and isinstance(item.get("attachment"), dict) and str(item["attachment"].get("contentType", "")).lower() == "application/pdf"), None)
    if not isinstance(attachment, dict) or not isinstance(attachment.get("url"), str):
        raise EpicError("Epic did not provide a PDF for this document")
    target = _safe_next_url(session.fhir_base, attachment["url"])
    headers = {"Authorization": f"Bearer {session.access_token}", "Accept": "application/pdf"}
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=False) as client:
            response = await client.get(target, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise EpicError("Epic could not open this PDF") from exc
    if len(response.content) > MAX_DOCUMENT_BYTES or not response.content.startswith(b"%PDF-"):
        raise EpicError("Epic returned an unsupported PDF")
    title = attachment.get("title") if isinstance(attachment.get("title"), str) else "epic-document.pdf"
    return response.content, title


def source_metadata(session: LiveEpicSession) -> dict[str, str]:
    return {"organization_name": session.organization_name, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
