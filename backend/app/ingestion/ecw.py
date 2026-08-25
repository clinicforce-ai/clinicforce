"""Patient-authorized eClinicalWorks / healow SMART-on-FHIR connection.

The eClinicalWorks production resource-server address is derived only from a
six-character OEM (practice) code.  OAuth endpoints are then discovered from
that resource server's SMART metadata; users never provide a hostname or OAuth
endpoint.  This prevents the connector from becoming an OAuth credential relay.
"""

from __future__ import annotations

import asyncio
import base64
import boto3
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from jwcrypto import jwe, jwk, jwt


ECW_CLIENT_ID = os.getenv("ECW_CLIENT_ID", "").strip()
# Local-development fallback. Production uses AWS KMS below, so ClinicForce
# never stores an exportable private signing key.
ECW_CLIENT_JWK = os.getenv("ECW_CLIENT_JWK", "").strip()
ECW_KMS_KEY_ID = os.getenv("ECW_KMS_KEY_ID", "").strip()
# Reuse the task-only root secret if a distinct state secret has not yet been
# injected.  HMAC domain separation below produces independent state-key
# material and does not expose either original secret.
ECW_OAUTH_STATE_SECRET = (
    os.getenv("ECW_OAUTH_STATE_SECRET", "").strip()
    or os.getenv("EPIC_OAUTH_STATE_SECRET", "").strip()
)
ECW_FHIR_BASE_TEMPLATE = os.getenv(
    "ECW_FHIR_BASE_TEMPLATE",
    # healow publishes active practice endpoints at
    # https://connect4.healow.com/apps/jsp/dev/r4/fhirEndpoints.jsp. The
    # former ecwcms Azure hostname was retired, so SMART discovery there
    # fails before the browser can be sent to the healow sign-in page.
    "https://fhir4.healow.com/fhir/r4/{practice_code}",
).strip()

OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60
LIVE_SESSION_MAX_AGE_SECONDS = 55 * 60
MAX_PAGES_PER_RESOURCE = 50
MAX_RESOURCES_PER_TYPE = 5_000
MAX_FHIR_RESPONSE_BYTES = 10 * 1024 * 1024
_PRACTICE_CODE = re.compile(r"^[A-Z0-9]{6}$")

# Request only the patient-scoped, read-only set selected for ClinicForce's
# healow registration.  Duplicate catalog rows for Condition/Observation
# categories map to one SMART resource scope each.
PATIENT_READ_SCOPES = (
    "patient/Patient.read", "patient/AllergyIntolerance.read", "patient/CarePlan.read",
    "patient/CareTeam.read", "patient/DiagnosticReport.read", "patient/DocumentReference.read", "patient/Encounter.read",
    "patient/Goal.read", "patient/Immunization.read", "patient/Medication.read",
    "patient/MedicationDispense.read", "patient/MedicationRequest.read", "patient/Procedure.read",
    "patient/Provenance.read", "patient/Coverage.read", "patient/ServiceRequest.read",
    "patient/Condition.read", "patient/Observation.read",
)

# These are the patient-scoped resource families ClinicForce requests from
# healow. A practice may grant only some of them; those denials are surfaced in
# the review screen rather than being mistaken for an empty chart.
USCDI_RESOURCE_TYPES = (
    "Patient", "AllergyIntolerance", "CarePlan", "CareTeam", "Condition", "Coverage",
    "DiagnosticReport", "DocumentReference", "Encounter", "Goal", "Immunization",
    "MedicationDispense", "MedicationRequest", "Observation", "Procedure",
    "Provenance", "ServiceRequest",
)


class EcwError(RuntimeError):
    """A fixed, patient-safe eClinicalWorks connection error."""


@dataclass(frozen=True)
class EcwOAuthStart:
    authorization_url: str
    state_cookie: str


@dataclass
class LiveEcwSession:
    patient_id: str
    practice_code: str
    fhir_base: str
    remote_patient_id: str
    access_token: str
    expires_at: float
    review_bundle: dict[str, Any] | None = None
    review_availability: dict[str, str] | None = None
    retrieved_at: str | None = None


_live_sessions: dict[str, LiveEcwSession] = {}


def normalize_practice_code(value: str) -> str:
    """Normalize the six-character eCW OEM code without accepting a host."""
    normalized = value.strip().upper()
    if not _PRACTICE_CODE.fullmatch(normalized):
        raise EcwError("Enter the six-character eClinicalWorks practice code")
    return normalized


def fhir_base_for_practice(practice_code: str) -> str:
    """Return the eCW production FHIR resource-server URL for an OEM code."""
    normalized = normalize_practice_code(practice_code)
    try:
        value = ECW_FHIR_BASE_TEMPLATE.format(practice_code=normalized)
    except (KeyError, ValueError) as exc:  # defensive deployment misconfiguration guard
        raise EcwError("eClinicalWorks connection is not configured correctly") from exc
    return _valid_https_url(value, name="FHIR endpoint")


def enabled() -> bool:
    return bool(ECW_CLIENT_ID and ECW_OAUTH_STATE_SECRET and (ECW_KMS_KEY_ID or ECW_CLIENT_JWK))


def _client_key() -> jwk.JWK:
    """Return the local-development signing key, never a production KMS key."""
    if not enabled():
        raise EcwError("eClinicalWorks connection is not configured yet")
    try:
        key = jwk.JWK.from_json(ECW_CLIENT_JWK)
    except Exception as exc:
        raise EcwError("eClinicalWorks connection key is not configured correctly") from exc
    if not key.has_private:
        raise EcwError("eClinicalWorks connection key is not configured correctly")
    # A stable kid is needed to match the public JWKS registered in healow.
    if not key.get("kid"):
        raise EcwError("eClinicalWorks connection key is missing its key ID")
    return key


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unsigned_b64url_integer(value: int) -> str:
    width = max(1, (value.bit_length() + 7) // 8)
    return _b64url(value.to_bytes(width, "big"))


def _kms_public_jwk() -> dict[str, str]:
    """Translate KMS's DER public key to a standard RSA JWKS entry."""
    try:
        response = boto3.client("kms").get_public_key(KeyId=ECW_KMS_KEY_ID)
        public_key = load_der_public_key(response["PublicKey"])
    except Exception as exc:
        raise EcwError("eClinicalWorks signing key is unavailable") from exc
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise EcwError("eClinicalWorks signing key is not an RSA key")
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA", "kid": ECW_KMS_KEY_ID,
        "n": _unsigned_b64url_integer(numbers.n), "e": _unsigned_b64url_integer(numbers.e),
        "use": "sig", "alg": "RS384",
    }


def public_jwks() -> dict[str, list[dict[str, Any]]]:
    """Return only the registered public key; never expose private material."""
    if ECW_KMS_KEY_ID:
        return {"keys": [_kms_public_jwk()]}
    key = _client_key()
    return {"keys": [json.loads(key.export_public())]}


def _state_key() -> jwk.JWK:
    if not ECW_OAUTH_STATE_SECRET:
        raise EcwError("eClinicalWorks connection is not configured yet")
    digest = hmac.new(
        ECW_OAUTH_STATE_SECRET.encode("utf-8"), b"clinicforce:ecw-oauth-state:v1", hashlib.sha256,
    ).digest()
    return jwk.JWK(kty="oct", k=base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"))


def _valid_https_url(value: str, *, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EcwError(f"eClinicalWorks returned an invalid {name}")
    return value.rstrip("/")


async def _smart_configuration(fhir_base: str) -> dict[str, str]:
    url = f"{_valid_https_url(fhir_base, name='FHIR endpoint')}/.well-known/smart-configuration"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EcwError("This practice's secure connection details are unavailable") from exc
    if not isinstance(payload, dict):
        raise EcwError("This practice returned invalid secure connection details")
    issuer = payload.get("issuer")
    authorization_endpoint, token_endpoint = payload.get("authorization_endpoint"), payload.get("token_endpoint")
    if not isinstance(issuer, str) or not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
        raise EcwError("This practice does not provide the required SMART OAuth endpoints")
    return {
        "issuer": _valid_https_url(issuer, name="issuer"),
        "authorization_endpoint": _valid_https_url(authorization_endpoint, name="authorization endpoint"),
        "token_endpoint": _valid_https_url(token_endpoint, name="token endpoint"),
    }


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _seal_state(payload: dict[str, Any]) -> str:
    token = jwe.JWE(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        protected={"alg": "dir", "enc": "A256GCM"},
    )
    token.add_recipient(_state_key())
    return token.serialize(compact=True)


def _open_state(value: str) -> dict[str, Any]:
    try:
        token = jwe.JWE()
        token.deserialize(value, key=_state_key())
        payload = json.loads(token.payload.decode("utf-8"))
    except Exception as exc:
        raise EcwError("Your eClinicalWorks connection session expired. Start again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("expires_at"), (int, float)) or payload["expires_at"] < time.time():
        raise EcwError("Your eClinicalWorks connection session expired. Start again.")
    return payload


def _client_assertion(token_endpoint: str) -> str:
    now = int(time.time())
    claims = {
        "iss": ECW_CLIENT_ID,
        "sub": ECW_CLIENT_ID,
        "aud": token_endpoint,
        "iat": now,
        "exp": now + 300,
        "jti": secrets.token_urlsafe(32),
    }
    if ECW_KMS_KEY_ID:
        header = {"alg": "RS384", "kid": ECW_KMS_KEY_ID, "typ": "JWT"}
        signing_input = ".".join((_b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")), _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))))
        try:
            signature = boto3.client("kms").sign(
                KeyId=ECW_KMS_KEY_ID, Message=signing_input.encode("ascii"), MessageType="RAW",
                SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_384",
            )["Signature"]
        except Exception as exc:
            raise EcwError("eClinicalWorks signing key is unavailable") from exc
        return f"{signing_input}.{_b64url(signature)}"
    key = _client_key()
    signed = jwt.JWT(header={"alg": "RS384", "kid": key.get("kid"), "typ": "JWT"}, claims=json.dumps(claims, separators=(",", ":")))
    signed.make_signed_token(key)
    return signed.serialize()


async def begin_authorization(practice_code: str, patient_id: str, redirect_uri: str, *, popup: bool = False) -> EcwOAuthStart:
    if not enabled():
        raise EcwError("eClinicalWorks connection is not configured yet")
    practice_code = normalize_practice_code(practice_code)
    fhir_base = fhir_base_for_practice(practice_code)
    configuration = await _smart_configuration(fhir_base)
    fhir_base = configuration["issuer"]
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    payload = {
        "state": state,
        "verifier": verifier,
        "patient_id": patient_id,
        "practice_code": practice_code,
        "fhir_base": fhir_base,
        "token_endpoint": configuration["token_endpoint"],
        "popup": popup,
        "expires_at": time.time() + OAUTH_STATE_MAX_AGE_SECONDS,
    }
    query = urlencode({
        "response_type": "code",
        "client_id": ECW_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": " ".join(("launch/patient", "openid", "profile", "fhirUser", *PATIENT_READ_SCOPES)),
        "state": state,
        "aud": fhir_base,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    })
    return EcwOAuthStart(f"{configuration['authorization_endpoint']}?{query}", _seal_state(payload))


def popup_requested(state_cookie: str) -> bool:
    try:
        return bool(_open_state(state_cookie).get("popup"))
    except EcwError:
        return False


async def complete_authorization(state_cookie: str, state: str, code: str, redirect_uri: str, patient_id: str) -> tuple[str, bool]:
    payload = _open_state(state_cookie)
    if not code or not secrets.compare_digest(state, str(payload.get("state", ""))):
        raise EcwError("Your eClinicalWorks connection could not be verified. Start again.")
    if not secrets.compare_digest(patient_id, str(payload.get("patient_id", ""))):
        raise EcwError("This eClinicalWorks connection belongs to a different ClinicForce session.")
    token_endpoint = str(payload.get("token_endpoint", ""))
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.post(
                token_endpoint,
                data={
                    # healow identifies a confidential asymmetric client from
                    # the private_key_jwt assertion's iss/sub claims. Its
                    # client_id form parameter is for public-client exchanges.
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri, "code_verifier": str(payload["verifier"]),
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": _client_assertion(token_endpoint),
                },
                headers={"Accept": "application/json"},
            )
        response.raise_for_status()
        token = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EcwError("eClinicalWorks could not complete this connection. Check the practice code and app authorization.") from exc
    access_token, remote_patient_id = token.get("access_token"), token.get("patient")
    if not isinstance(access_token, str) or not access_token or not isinstance(remote_patient_id, str) or not remote_patient_id:
        raise EcwError("eClinicalWorks did not return a patient record for this connection")
    try:
        lifetime = min(max(int(token.get("expires_in", 3600)), 60), LIVE_SESSION_MAX_AGE_SECONDS)
    except (TypeError, ValueError):
        lifetime = LIVE_SESSION_MAX_AGE_SECONDS
    session_id = secrets.token_urlsafe(32)
    _live_sessions[session_id] = LiveEcwSession(
        patient_id=patient_id, practice_code=str(payload["practice_code"]), fhir_base=str(payload["fhir_base"]),
        remote_patient_id=remote_patient_id, access_token=access_token, expires_at=time.time() + lifetime,
    )
    return session_id, bool(payload.get("popup"))


def clear_live_session(session_id: str) -> None:
    _live_sessions.pop(session_id, None)


def get_live_session(session_id: str, patient_id: str) -> LiveEcwSession:
    """Return the current patient-owned connection without exposing its token."""
    session = _live_sessions.get(session_id)
    if session is None or session.patient_id != patient_id or session.expires_at <= time.time():
        if session_id:
            clear_live_session(session_id)
        raise EcwError("Your eClinicalWorks session has ended. Reconnect healow to continue.")
    return session


def clear_live_session_by_value(value: LiveEcwSession) -> None:
    for key, session in list(_live_sessions.items()):
        if session is value:
            _live_sessions.pop(key, None)


def _same_origin(left: str, right: str) -> bool:
    first, second = urlparse(left), urlparse(right)
    return (first.scheme, first.netloc) == (second.scheme, second.netloc)


def _safe_next_url(fhir_base: str, value: str) -> str:
    target = urljoin(f"{fhir_base}/", value)
    if not _same_origin(fhir_base, target) or not target.startswith(f"{fhir_base}/"):
        raise EcwError("eClinicalWorks returned an unsafe continuation link")
    return target


async def _get_json(
    session: LiveEcwSession, url: str, params: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Read one bounded FHIR response, following neither redirects nor hosts."""
    headers = {"Authorization": f"Bearer {session.access_token}", "Accept": "application/fhir+json"}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise EcwError("eClinicalWorks is temporarily unavailable. Try again shortly.") from exc
            await asyncio.sleep(2 ** attempt)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                raise EcwError("eClinicalWorks is temporarily unavailable. Try again shortly.")
            try:
                delay = min(max(int(response.headers.get("Retry-After", "")), 1), 15)
            except ValueError:
                delay = 2 ** attempt
            await asyncio.sleep(delay)
            continue
        if response.status_code in {401, 403, 404}:
            return response.status_code, {}, dict(response.headers)
        if len(response.content) > MAX_FHIR_RESPONSE_BYTES:
            raise EcwError("eClinicalWorks returned too much data for one resource family. Try again shortly.")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EcwError("eClinicalWorks returned an unreadable clinical-data response") from exc
        if not isinstance(payload, dict):
            raise EcwError("eClinicalWorks returned an invalid clinical-data response")
        return response.status_code, payload, dict(response.headers)
    raise EcwError("eClinicalWorks is temporarily unavailable. Try again shortly.")


def _collection(resources: list[dict[str, Any]], status: str = "ok") -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "resourceType": "Bundle", "type": "collection",
        "entry": [{"resource": resource} for resource in resources],
    }
    if status != "ok":
        bundle["extension"] = [{"url": "urn:clinicforce:ecw-status", "valueString": status}]
    return bundle


def _sanitize_resource_for_review(resource: dict[str, Any]) -> dict[str, Any]:
    """Do not send document bytes or provider download URLs to the browser."""
    sanitized = json.loads(json.dumps(resource))
    if sanitized.get("resourceType") == "DocumentReference":
        for content in sanitized.get("content", []):
            attachment = content.get("attachment") if isinstance(content, dict) else None
            if isinstance(attachment, dict):
                attachment.pop("data", None)
                attachment.pop("url", None)
    return sanitized


async def _search_resources(
    session: LiveEcwSession, resource_type: str, params: dict[str, str],
) -> tuple[list[dict[str, Any]], str]:
    status, bundle, _ = await _get_json(session, f"{session.fhir_base}/{resource_type}", params)
    if status == 401:
        clear_live_session_by_value(session)
        raise EcwError("Your eClinicalWorks session has ended. Reconnect healow to continue.")
    if status in {403, 404}:
        return [], "forbidden" if status == 403 else "unavailable"
    resources: list[dict[str, Any]] = []
    pages = 0
    visited_pages: set[str] = set()
    while True:
        for entry in bundle.get("entry", []):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if isinstance(resource, dict) and resource.get("resourceType") != "OperationOutcome":
                resources.append(_sanitize_resource_for_review(resource))
                if len(resources) >= MAX_RESOURCES_PER_TYPE:
                    return resources, "truncated"
        next_url = next((
            link.get("url") for link in bundle.get("link", [])
            if isinstance(link, dict) and link.get("relation") == "next" and isinstance(link.get("url"), str)
        ), None)
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
        if status == 401:
            clear_live_session_by_value(session)
            raise EcwError("Your eClinicalWorks session has ended. Reconnect healow to continue.")
        if status != 200:
            return resources, "partial"
    return resources, "ok"


async def fetch_resource_type(session: LiveEcwSession, resource_type: str) -> dict[str, Any]:
    if resource_type not in USCDI_RESOURCE_TYPES:
        raise EcwError("That eClinicalWorks resource type is not available in ClinicForce")
    if resource_type == "Patient":
        status, patient, _ = await _get_json(session, f"{session.fhir_base}/Patient/{session.remote_patient_id}")
        if status == 401:
            clear_live_session_by_value(session)
            raise EcwError("Your eClinicalWorks session has ended. Reconnect healow to continue.")
        if status in {403, 404}:
            return _collection([], "forbidden" if status == 403 else "unavailable")
        return _collection([_sanitize_resource_for_review(patient)])
    resources, resource_status = await _search_resources(session, resource_type, {"patient": session.remote_patient_id})
    return _collection(resources, resource_status)


async def fetch_record(session: LiveEcwSession) -> tuple[dict[str, Any], dict[str, str]]:
    """Retrieve approved resources only; persistence waits for explicit review."""
    concurrency = asyncio.Semaphore(5)

    async def bounded_fetch(resource_type: str) -> dict[str, Any]:
        async with concurrency:
            return await fetch_resource_type(session, resource_type)

    bundles = await asyncio.gather(
        *(bounded_fetch(resource_type) for resource_type in USCDI_RESOURCE_TYPES), return_exceptions=True,
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
        result_status = next((
            extension.get("valueString") for extension in bundle.get("extension", [])
            if isinstance(extension, dict) and extension.get("url") == "urn:clinicforce:ecw-status"
        ), "ok")
        availability[resource_type] = "empty" if result_status == "ok" and not bundle.get("entry") else str(result_status)
    record = {"resourceType": "Bundle", "type": "collection", "entry": entries}
    session.review_bundle = record
    session.review_availability = availability
    session.retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return record, availability


def source_metadata(session: LiveEcwSession) -> dict[str, str]:
    return {
        "organization_name": f"eClinicalWorks / healow (practice {session.practice_code})",
        "practice_code": session.practice_code,
        "retrieved_at": session.retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
