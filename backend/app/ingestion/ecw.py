"""Patient-authorized eClinicalWorks / healow SMART-on-FHIR connection.

The eClinicalWorks production resource-server address is derived only from a
six-character OEM (practice) code.  OAuth endpoints are then discovered from
that resource server's SMART metadata; users never provide a hostname or OAuth
endpoint.  This prevents the connector from becoming an OAuth credential relay.
"""

from __future__ import annotations

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
from typing import Any
from urllib.parse import urlencode, urlparse

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
    "https://ecwcms-drehlqyh14.azurewebsites.net/FHIRServer/fhir/{practice_code}",
).strip()

OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60
LIVE_SESSION_MAX_AGE_SECONDS = 55 * 60
_PRACTICE_CODE = re.compile(r"^[A-Z0-9]{6}$")

# Request only the patient-scoped, read-only set selected for ClinicForce's
# healow registration.  Duplicate catalog rows for Condition/Observation
# categories map to one SMART resource scope each.
PATIENT_READ_SCOPES = (
    "patient/Patient.read", "patient/AllergyIntolerance.read", "patient/CarePlan.read",
    "patient/DiagnosticReport.read", "patient/DocumentReference.read", "patient/Encounter.read",
    "patient/Goal.read", "patient/Immunization.read", "patient/Medication.read",
    "patient/MedicationDispense.read", "patient/MedicationRequest.read", "patient/Procedure.read",
    "patient/Provenance.read", "patient/Coverage.read", "patient/ServiceRequest.read",
    "patient/Condition.read", "patient/Observation.read",
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
    authorization_endpoint, token_endpoint = payload.get("authorization_endpoint"), payload.get("token_endpoint")
    if not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
        raise EcwError("This practice does not provide the required SMART OAuth endpoints")
    return {
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
        "scope": " ".join(("launch/patient", "openid", "profile", "fhirUser", "online_access", *PATIENT_READ_SCOPES)),
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
                    "grant_type": "authorization_code", "code": code, "client_id": ECW_CLIENT_ID,
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
