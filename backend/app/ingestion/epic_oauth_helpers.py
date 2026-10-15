"""Validate Epic SMART endpoints and protect OAuth state."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from jwcrypto import jwe, jwk

EPIC_OAUTH_STATE_SECRET = os.getenv("EPIC_OAUTH_STATE_SECRET", "").strip()


class EpicError(RuntimeError):
    """A safe, patient-facing Epic integration failure."""



def _state_key() -> jwk.JWK:
    if not EPIC_OAUTH_STATE_SECRET:
        raise EpicError("Epic connection is not configured")
    # Production reuses the existing task-only root secret rather than storing
    # a second secret. HMAC domain separation gives the Epic state-encryption
    # key independent key material from source-link signing.
    digest = hmac.new(
        EPIC_OAUTH_STATE_SECRET.encode("utf-8"), b"clinicforce:epic-oauth-state:v1", hashlib.sha256,
    ).digest()
    return jwk.JWK(kty="oct", k=base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"))



def _valid_https_url(value: str, *, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise EpicError(f"The selected hospital returned an invalid {name}")
    return value.rstrip("/")



def _same_origin(left: str, right: str) -> bool:
    first, second = urlparse(left), urlparse(right)
    return (first.scheme, first.netloc) == (second.scheme, second.netloc)



async def _smart_configuration(fhir_base: str) -> dict[str, Any]:
    fhir_base = _valid_https_url(fhir_base, name="FHIR endpoint")
    url = f"{fhir_base}/.well-known/smart-configuration"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EpicError("This hospital's secure connection details are unavailable") from exc
    if not isinstance(payload, dict):
        raise EpicError("This hospital returned invalid secure connection details")
    authorization_endpoint = payload.get("authorization_endpoint")
    token_endpoint = payload.get("token_endpoint")
    if not isinstance(authorization_endpoint, str) or not isinstance(token_endpoint, str):
        raise EpicError("This hospital does not provide the required SMART OAuth endpoints")
    authorization_endpoint = _valid_https_url(authorization_endpoint, name="authorization endpoint")
    token_endpoint = _valid_https_url(token_endpoint, name="token endpoint")
    # Epic production endpoints normally use the same origin. Rejecting a
    # cross-origin response prevents endpoint catalog poisoning from turning the
    # app into an OAuth credential relay.
    if not _same_origin(fhir_base, authorization_endpoint) or not _same_origin(fhir_base, token_endpoint):
        raise EpicError("This hospital's OAuth endpoints are not trusted for this connection")
    return {"authorization_endpoint": authorization_endpoint, "token_endpoint": token_endpoint}



def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")



def _seal_state(payload: dict[str, Any]) -> str:
    token = jwe.JWE(json.dumps(payload, separators=(",", ":")).encode("utf-8"), protected={"alg": "dir", "enc": "A256GCM"})
    token.add_recipient(_state_key())
    return token.serialize(compact=True)



def _open_state(value: str) -> dict[str, Any]:
    try:
        token = jwe.JWE()
        token.deserialize(value, key=_state_key())
        payload = json.loads(token.payload.decode("utf-8"))
    except Exception as exc:
        raise EpicError("Your Epic connection session expired. Start again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("expires_at"), (int, float)):
        raise EpicError("Your Epic connection session expired. Start again.")
    if payload["expires_at"] < time.time():
        raise EpicError("Your Epic connection session expired. Start again.")
    return payload



def _safe_next_url(fhir_base: str, value: str) -> str:
    target = urljoin(f"{fhir_base}/", value)
    if not _same_origin(fhir_base, target) or not target.startswith(f"{fhir_base}/"):
        raise EpicError("Epic returned an unsafe continuation link")
    return target
