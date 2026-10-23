"""Read one scheduled patient's insurance inputs from an activated eCW backend app."""

from __future__ import annotations

import asyncio
import json
import base64
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo

import httpx

from app.ingestion import ecw
from app.insurance.stedi_discovery import patient_address, previous_addresses
from app.insurance import provider_specialty
from app.insurance.identity import one_name_edit as _one_name_edit
from app.logs.collection_audit import collection_event


# ChargeItem is intentionally omitted until it is granted in the eCW app.
# Procedure and PractitionerRole are already granted by the registered app.
SCOPES = (
    "system/Patient.read", "system/Coverage.read", "system/Encounter.read",
    "system/ServiceRequest.read", "system/Procedure.read", "system/Condition.read",
    "system/RelatedPerson.read", "system/Organization.read", "system/Practitioner.read",
    "system/PractitionerRole.read", "system/Location.read", "system/ChargeItem.read",
)
# These are intentionally separate from normal appointment reads. A tenant must
# approve them explicitly; a missing optional entitlement must not break intake.
EVIDENCE_SCOPES = (
    "system/CoverageEligibilityResponse.read", "system/DocumentReference.read", "system/Binary.read",
)
REFERENCE_TIMEOUT_SECONDS = 4
SEARCH_TIMEOUT_SECONDS = 6
HISTORY_TIMEOUT_SECONDS = 3
HISTORY_DAYS = 365
HISTORY_VISIT_LIMIT = 12
LOOKUP_TIMEOUT_SECONDS = 24  # CloudFront stops waiting for the origin after 30 seconds.
TOKEN_STAGE_TIMEOUT_SECONDS = 10


class EcwBackendError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str | None = None, stage: str | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.stage = stage


class EcwPatientSelectionRequired(EcwBackendError):
    """Unconfirmed suggestions; callers must not fetch visits or eligibility."""

    def __init__(self, candidates: list[dict]):
        super().__init__("Choose and confirm an eClinicalWorks patient.",
                         reason_code="patient_selection_required", stage="patient_identity")
        self.candidates = candidates


class EcwResourceUnavailable(EcwBackendError):
    """A resource is not available to this activated eCW app."""

    def __init__(self, resource: str, *, reason_code: str | None = None, http_status: int | None = None):
        super().__init__(resource, reason_code=reason_code)
        self.http_status = http_status


def _failure_reason(exc: BaseException) -> str:
    # Exception strings and URLs can contain patient data or credentials.
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, httpx.TimeoutException):
        return {httpx.ConnectTimeout: "connect_timeout", httpx.ReadTimeout: "read_timeout",
                httpx.WriteTimeout: "write_timeout", httpx.PoolTimeout: "pool_timeout"}.get(type(exc), "transport_timeout")
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "stage_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return "upstream_http_error"
    if isinstance(exc, httpx.HTTPError):
        return "transport_error"
    if isinstance(exc, ValueError):
        return "invalid_response"
    if isinstance(exc, EcwBackendError) and exc.reason_code:
        return exc.reason_code
    if exc.__cause__ is not None:
        return _failure_reason(exc.__cause__)
    return "lookup_error" if isinstance(exc, EcwBackendError) else "internal_error"


@contextmanager
def _stage(stage: str, **fields):
    """Log fixed stage/query labels and numeric facts, never request or response values."""
    started = time.monotonic()
    collection_event("ecw_stage_started", stage=stage, **fields)
    facts = {}
    try:
        yield facts
    except BaseException as exc:
        reason = _failure_reason(exc)
        if isinstance(exc, EcwBackendError):
            exc.reason_code = reason
            exc.stage = exc.stage or stage
        collection_event("ecw_stage_failed", stage=stage, outcome="error", reason_code=reason,
                         duration_ms=int((time.monotonic() - started) * 1000), **fields, **facts)
        raise
    else:
        outcome = "unavailable" if facts.get("http_status") in {403, 404} else "success"
        collection_event("ecw_stage_completed", stage=stage, outcome=outcome,
                         duration_ms=int((time.monotonic() - started) * 1000), **fields, **facts)


def _query_kind(params) -> str:
    document_type = dict(params or {}).get('type')
    if document_type in {'64290-0', '103140-0'}:
        return 'insurance_card' if document_type == '64290-0' else 'patient_documents'
    keys = frozenset(dict(params or {}))
    return {frozenset({"given", "family", "birthdate"}): "given_family_birthdate",
            frozenset({"family", "birthdate"}): "family_birthdate",
            frozenset({"given", "birthdate"}): "given_birthdate",
            frozenset({"name", "birthdate"}): "name_birthdate",
            frozenset({"_id"}): "id",
            frozenset({"patient", "date"}): "patient_date",
            frozenset({"patient"}): "patient"}.get(keys, "filtered")


def settings() -> tuple[str, str]:
    client_id = os.getenv("ECW_BACKEND_CLIENT_ID", "").strip()
    issuer = os.getenv("ECW_BACKEND_ISSUER", "").strip().rstrip("/")
    parsed = urlparse(issuer)
    if not client_id or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EcwBackendError("The eClinicalWorks practice connection is not configured yet.")
    if not (ecw.ECW_KMS_KEY_ID or ecw.ECW_CLIENT_JWK):
        raise EcwBackendError("The eClinicalWorks signing key is not configured yet.")
    return client_id, issuer


def configured() -> bool:
    try:
        settings()
        return True
    except EcwBackendError:
        return False


def binding() -> str:
    try:
        return "|".join(settings())
    except EcwBackendError:
        return ""


def _https(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EcwBackendError(f"eClinicalWorks did not publish a valid {label}.")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EcwBackendError(f"eClinicalWorks did not publish a valid {label}.")
    return value.rstrip("/")


def _token_endpoint(metadata: dict[str, Any]) -> str:
    for rest in metadata.get("rest", []):
        security = rest.get("security", {}) if isinstance(rest, dict) else {}
        for outer in security.get("extension", []):
            if not isinstance(outer, dict) or "oauth-uris" not in str(outer.get("url", "")):
                continue
            for inner in outer.get("extension", []):
                if isinstance(inner, dict) and inner.get("url") == "token":
                    return _https(inner.get("valueUri") or inner.get("valueUrl"), "token endpoint")
    raise EcwBackendError("The eClinicalWorks metadata does not include its backend token endpoint.")


async def _response_json(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EcwBackendError(message) from exc
    if not isinstance(payload, dict):
        raise EcwBackendError(message)
    return payload


async def _access_token(client: httpx.AsyncClient, client_id: str, issuer: str,
                        scopes: tuple[str, ...] = SCOPES, *, required_resource: str | None = None) -> str:
    try:
        with _stage("metadata") as facts:
            metadata_response = await asyncio.wait_for(
                client.get(f"{issuer}/metadata", params={"_format": "json"}, headers={"Accept": "application/fhir+json"}),
                TOKEN_STAGE_TIMEOUT_SECONDS)
            facts["http_status"] = metadata_response.status_code
            metadata = await _response_json(metadata_response, "The activated eClinicalWorks practice metadata is unavailable.")
    except asyncio.TimeoutError as exc:
        raise EcwBackendError("eClinicalWorks metadata timed out. Retry the connection.",
                              reason_code="stage_timeout", stage="metadata") from exc
    if required_resource and not any(
            row.get('type') == required_resource for rest in metadata.get('rest', []) if isinstance(rest, dict)
            for row in rest.get('resource', []) if isinstance(row, dict)):
        raise EcwBackendError('eClinicalWorks does not advertise the requested saved resource on this FHIR endpoint.',
                              reason_code='ecw_resource_not_advertised')
    endpoint = _token_endpoint(metadata)
    try:
        with _stage("assertion_signing"):
            assertion = await asyncio.wait_for(
                asyncio.to_thread(ecw._client_assertion, endpoint, client_id), TOKEN_STAGE_TIMEOUT_SECONDS)
        with _stage("token_exchange") as facts:
            token_response = await asyncio.wait_for(client.post(endpoint, data={
                "grant_type": "client_credentials",
                "scope": " ".join(scopes),
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }, headers={"Accept": "application/json"}), TOKEN_STAGE_TIMEOUT_SECONDS)
            facts["http_status"] = token_response.status_code
            if token_response.is_error:
                try:
                    oauth_error = token_response.json().get('error')
                except (ValueError, AttributeError):
                    oauth_error = None
                if isinstance(oauth_error, str) and oauth_error in {'invalid_scope', 'unauthorized_client', 'invalid_client',
                                   'invalid_grant', 'unsupported_grant_type', 'invalid_request'}:
                    raise EcwBackendError('eClinicalWorks rejected the requested backend scope or app credentials.',
                                          reason_code='oauth_' + oauth_error)
            token = await _response_json(
                token_response,
                "eClinicalWorks rejected the backend app. Confirm that this practice activated the app and that its client ID, issuer, scopes and JWKS match.",
            )
            value = token.get("access_token")
            if not isinstance(value, str) or not value:
                raise EcwBackendError("eClinicalWorks did not return a backend access token.", reason_code="invalid_token")
            if isinstance(token.get('scope'), str):
                granted = set(token['scope'].split())
                for resource in ('Coverage', 'CoverageEligibilityResponse', 'DocumentReference', 'Binary'):
                    scope = f'system/{resource}.read'
                    if scope in scopes:
                        collection_event('ecw_scope_result', stage='token_exchange', resource=resource.lower(),
                                         outcome='granted' if scope in granted else 'not_granted')
    except asyncio.TimeoutError as exc:
        raise EcwBackendError("eClinicalWorks backend authorization timed out. Retry the connection.",
                              reason_code="stage_timeout", stage="backend_authorization") from exc
    return value


def _entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [resource for entry in bundle.get("entry", []) if isinstance(entry, dict)
            and isinstance((resource := entry.get("resource")), dict)
            and resource.get("resourceType") != "OperationOutcome"]


async def _search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                  params: dict[str, str] | list[tuple[str, str]], *, first_page_only: bool = False) -> list[dict[str, Any]]:
    url, rows, seen = f"{issuer}/{resource}", [], set()
    origin = urlparse(issuer)
    for page in range(1, 11):
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc) or not parsed.path.startswith(origin.path.rstrip('/') + '/') or url in seen:
            raise EcwBackendError(f"eClinicalWorks returned an invalid pagination link for {resource}.")
        seen.add(url)
        with _stage("resource_search", resource=resource.lower(),
                    query_kind=_query_kind(params) if page == 1 else "pagination", page=page) as facts:
            response = await client.get(url, params=params,
                                        headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
            facts["http_status"] = response.status_code
            if response.status_code in {403, 404}:
                raise EcwResourceUnavailable(resource, reason_code="resource_unavailable",
                                             http_status=response.status_code)
            bundle = await _response_json(response, f"eClinicalWorks could not return {resource} data for this patient.")
            entries = _entries(bundle)
            facts["count"] = len(entries)
            rows.extend(entries)
        if first_page_only:
            return rows[:100]
        next_links = [link.get('url') for link in bundle.get('link', []) if link.get('relation') == 'next']
        if not next_links:
            return rows
        if len(next_links) != 1 or not isinstance(next_links[0], str):
            raise EcwBackendError(f"eClinicalWorks returned ambiguous pagination for {resource}.")
        url, params = urljoin(str(response.url), next_links[0]), None
    raise EcwBackendError(f"eClinicalWorks {resource} results exceeded the supported page limit; the result is incomplete.")


async def _bounded_search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                          params: dict[str, str] | list[tuple[str, str]], *, attempts: int = 1,
                          timeout_seconds: int = SEARCH_TIMEOUT_SECONDS) -> tuple[list[dict[str, Any]], str | None]:
    for _ in range(attempts):
        try:
            return await asyncio.wait_for(
                _search(client, issuer, token, resource, params), timeout=timeout_seconds,
            ), None
        except (asyncio.TimeoutError, httpx.TimeoutException):
            pass
    return [], resource


async def _optional_search(client: httpx.AsyncClient, issuer: str, token: str, resource: str,
                           params: dict[str, str] | list[tuple[str, str]], *, timeout_seconds: int = SEARCH_TIMEOUT_SECONDS
                           ) -> tuple[list[dict[str, Any]], str | None, str | None]:
    try:
        rows, timed_out = await _bounded_search(client, issuer, token, resource, params, timeout_seconds=timeout_seconds)
        return rows, timed_out, None
    except EcwResourceUnavailable:
        return [], None, resource
    except EcwBackendError:
        # A transient FHIR response problem is not evidence that this app lacks
        # the resource permission. Keep the appointment usable and avoid
        # reporting a misleading access failure to clinical staff.
        return [], None, resource + " (read failed)"


async def _read(client: httpx.AsyncClient, issuer: str, token: str, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, str) or not re.fullmatch(r"(?:Patient|RelatedPerson|Organization|Practitioner|PractitionerRole|Location|Condition|Encounter|Appointment)/[A-Za-z0-9.\-]{1,200}", reference):
        return {}
    with _stage("resource_read", resource=reference.split("/", 1)[0].lower()) as facts:
        response = await client.get(f"{issuer}/{reference}", headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"})
        facts["http_status"] = response.status_code
        if response.status_code in {403, 404}:
            return {}
        return await _response_json(response, "eClinicalWorks could not resolve an appointment-linked resource.")


async def _bounded_read(client: httpx.AsyncClient, issuer: str, token: str,
                        reference: str) -> tuple[dict[str, Any], str | None]:
    resource, resource_id = reference.split("/", 1)
    try:
        return await asyncio.wait_for(_read(client, issuer, token, reference), timeout=REFERENCE_TIMEOUT_SECONDS), None
    except (asyncio.TimeoutError, httpx.TimeoutException):
        if resource == 'RelatedPerson':
            # eCW documents only reads by ID for RelatedPerson, not a search endpoint.
            return {}, resource
        # eCW direct reads intermittently stall while the equivalent supported
        # `_id` search succeeds. Try that once without extending the API timeout.
        try:
            rows = await asyncio.wait_for(
                _search(client, issuer, token, resource, {"_id": resource_id}),
                timeout=REFERENCE_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return {}, resource
        except EcwResourceUnavailable:
            return {}, None
        return (rows[0] if len(rows) == 1 else {}), None


async def _cached_patient(client: httpx.AsyncClient, issuer: str, token: str,
                          patient_id: str, profile: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    patient, timed_out = await _bounded_read(client, issuer, token, f"Patient/{patient_id}")
    if timed_out:
        raise EcwBackendError("eClinicalWorks timed out while verifying the saved patient. Retry the lookup.")
    if not patient:
        raise EcwBackendError("The saved eClinicalWorks patient link no longer exists.")
    return patient, None


from app.insurance.ecw.ecw_fields import (
    _norm,
    _name,
    _given_words,
    _phone,
    _reference,
    _period_contains,
    _coding,
    _npi,
    _display,
    _concept_labels,
    _number,
    _source_references,
)
from app.insurance.ecw.ecw_identity_fields import _patient_matches, _candidate_given_name
from app.insurance.ecw.ecw_appointment_fields import (
    _appointment_date,
    _is_upcoming_appointment,
    _visit_context,
    appointment_fields,
    _visit_type,
    _history_summary,
)
from app.insurance.ecw.ecw_coverage_fields import (
    _payer_id,
    _relationship,
    _group,
    _plan_number,
    _plan_details,
    _plan,
)


from app.insurance.ecw.ecw_patients import _patient_for_profile as _resolve_patient_for_profile, _patient_suggestions, patient_account_number


async def _patient_for_profile(client: httpx.AsyncClient, issuer: str, token: str, profile: dict[str, Any],
                               *, allow_suggestions: bool = False) -> tuple[dict, str, str | None]:
    # Resolve transport attributes at call time so existing test and operator patch points keep working.
    return await _resolve_patient_for_profile(
        sys.modules[__name__], client, issuer, token, profile,
        allow_suggestions=allow_suggestions,
    )
from app.insurance.ecw.ecw_appointments import (
    patient_appointment_history as _read_patient_appointment_history,
    lookup_patient_and_appointments as _lookup_patient_and_appointments,
    list_appointments as _list_appointments,
    list_completed_appointments as _list_completed_appointments,
)


from app.insurance.ecw.ecw_fetch import fetch_inputs as _fetch_inputs


from app.insurance.ecw.ecw_coverage import coverage_subscriber, coverage_member_id, coverage_record_status, saved_eligibility_summary, appointment_insurance_candidates, appointment_insurance_fallback


from app.insurance.ecw.ecw_insurance_evidence import (
    fetch_claim_identifiers as _fetch_claim_identifiers,
    fetch_saved_eligibility as _fetch_saved_eligibility,
    probe_saved_eligibility as _probe_saved_eligibility,
    insurance_cards as _insurance_cards,
)


async def patient_appointment_history(profile: dict[str, Any], practice: dict[str, Any], *,
                                      transport: httpx.AsyncBaseTransport | None = None) -> dict:
    return await _read_patient_appointment_history(sys.modules[__name__], profile, practice, transport=transport)


async def lookup_patient_and_appointments(profile: dict[str, Any], practice: dict[str, Any], *,
                                          allow_suggestions: bool = False,
                                          transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    return await _lookup_patient_and_appointments(
        sys.modules[__name__], profile, practice, allow_suggestions=allow_suggestions, transport=transport,
    )


async def list_appointments(profile: dict[str, Any], practice: dict[str, Any], *,
                            transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, str]]:
    return await _list_appointments(sys.modules[__name__], profile, practice, transport=transport)


async def list_completed_appointments(profile: dict[str, Any], practice: dict[str, Any], *,
                                      transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    return await _list_completed_appointments(sys.modules[__name__], profile, practice, transport=transport)


async def fetch_inputs(profile: dict[str, Any], practice: dict[str, Any], appointment_id: str | None = None,
                       *, transport: httpx.AsyncBaseTransport | None = None,
                       review_service_date: str | None = None) -> tuple[dict, list[str], dict]:
    return await _fetch_inputs(sys.modules[__name__], profile, practice, appointment_id,
                               transport=transport, review_service_date=review_service_date)


async def fetch_claim_identifiers(source: dict, *, transport: httpx.AsyncBaseTransport | None = None) -> list[str]:
    return await _fetch_claim_identifiers(sys.modules[__name__], source, transport=transport)


async def fetch_saved_eligibility(source: dict, service_date: str, *,
                                  transport: httpx.AsyncBaseTransport | None = None) -> dict:
    return await _fetch_saved_eligibility(sys.modules[__name__], source, service_date, transport=transport)


async def probe_saved_eligibility(profile: dict[str, Any], *,
                                  transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    return await _probe_saved_eligibility(sys.modules[__name__], profile, transport=transport)


async def insurance_cards(patient_id: str, *, document_id: str | None = None,
                          transport: httpx.AsyncBaseTransport | None = None):
    return await _insurance_cards(sys.modules[__name__], patient_id, document_id=document_id, transport=transport)
