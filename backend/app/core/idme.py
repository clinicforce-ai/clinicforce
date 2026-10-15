"""Minimal, fail-closed ID.me OpenID Connect client."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from jwcrypto import jwk, jwt

from app.core.patient_models import IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS
from app.core.idme_tokens import IdmeError, _SigningKeyError, VerifiedIdentity, _json_segment, _validate_granted_policy_scope, _verify_id_token


_DISCOVERY_ISSUERS = {
    "https://api.idmelabs.com/oidc/.well-known/openid-configuration": "https://api.idmelabs.com/oidc",
    "https://api.id.me/oidc/.well-known/openid-configuration": "https://api.id.me/oidc",
}
_ENVIRONMENT_DISCOVERY = {
    "sandbox": "https://api.idmelabs.com/oidc/.well-known/openid-configuration",
    "production": "https://api.id.me/oidc/.well-known/openid-configuration",
}
_SCOPE_PATTERN = re.compile(r"[A-Za-z0-9._:/+-]{1,256}")
_DISCOVERY_CACHE_SECONDS = 5 * 60
_discovery_cache: dict[str, tuple[float, dict[str, str]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_discovery_lock = asyncio.Lock()
_jwks_lock = asyncio.Lock()


@dataclass(frozen=True)
class IdmeConfig:
    client_id: str
    client_secret: str | None
    discovery_url: str
    redirect_uri: str
    scope: str
    issuer: str
    token_endpoint_auth_method: str = "client_secret_post"


@dataclass(frozen=True)
class StartedAuthorization:
    authorization_url: str
    state: str
    browser_token: str
    nonce: str
    pkce_verifier: str


def _configured_value() -> IdmeConfig:
    environment = os.getenv("IDME_ENVIRONMENT", "").strip().lower()
    client_id = os.getenv("IDME_CLIENT_ID", "").strip()
    client_secret = os.getenv("IDME_CLIENT_SECRET", "").strip()
    token_endpoint_auth_method = os.getenv(
        "IDME_TOKEN_ENDPOINT_AUTH_METHOD", "client_secret_post"
    ).strip()
    discovery_url = os.getenv("IDME_OIDC_DISCOVERY_URL", "").strip()
    redirect_uri = os.getenv("IDME_REDIRECT_URI", "").strip()
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    scope = " ".join(os.getenv("IDME_SCOPE", "").split())
    issuer = _DISCOVERY_ISSUERS.get(discovery_url)
    redirect = urlsplit(redirect_uri)
    public_base = urlsplit(public_base_url)
    expected_redirect_uri = f"{public_base_url}/api/patient/idme/callback"
    scopes = scope.split()
    if (
        not client_id
        or token_endpoint_auth_method not in {"none", "client_secret_post", "client_secret_basic"}
        or (token_endpoint_auth_method != "none" and not client_secret)
        or issuer is None
        or _ENVIRONMENT_DISCOVERY.get(environment) != discovery_url
        or public_base.scheme != "https"
        or not public_base.netloc
        or public_base.username is not None
        or public_base.password is not None
        or public_base.path not in {"", "/"}
        or public_base.query
        or public_base.fragment
        or redirect_uri != expected_redirect_uri
    ):
        raise IdmeError("ID.me is not configured")
    if (
        redirect.scheme != "https"
        or not redirect.netloc
        or redirect.username is not None
        or redirect.password is not None
        or redirect.fragment
    ):
        raise IdmeError("ID.me redirect URI is invalid")
    if (
        len(scopes) < 2
        or scopes[0] != "openid"
        or not any(item != "openid" for item in scopes)
        or len(scopes) != len(set(scopes))
        or len(scope) > 1_000
        or any(not _SCOPE_PATTERN.fullmatch(item) for item in scopes)
    ):
        raise IdmeError("ID.me scope is invalid")
    return IdmeConfig(
        client_id,
        client_secret or None,
        discovery_url,
        redirect_uri,
        scope,
        issuer,
        token_endpoint_auth_method,
    )


def configured() -> bool:
    try:
        _configured_value()
    except IdmeError:
        return False
    return True


def enabled() -> bool:
    return os.getenv("IDME_ENABLED", "false").strip().lower() == "true" and configured()


def _config() -> IdmeConfig:
    if os.getenv("IDME_ENABLED", "false").strip().lower() != "true":
        raise IdmeError("ID.me is disabled")
    return _configured_value()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _trusted_endpoint(value: Any, expected_host: str) -> str:
    if not isinstance(value, str):
        raise IdmeError("ID.me discovery metadata is incomplete")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise IdmeError("ID.me discovery endpoint is not trusted")
    return value


async def _get_json(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=10.0) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        if response.status_code != 200:
            raise IdmeError("ID.me returned an unsuccessful response")
        payload = response.json()
    except IdmeError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise IdmeError("ID.me could not be reached") from exc
    if not isinstance(payload, dict):
        raise IdmeError("ID.me returned an invalid response")
    return payload


async def _post_form(
    url: str,
    data: dict[str, str],
    basic_auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
            response = await client.post(
                url,
                data=data,
                auth=basic_auth,
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            raise IdmeError("ID.me rejected the authorization response")
        payload = response.json()
    except IdmeError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise IdmeError("ID.me could not be reached") from exc
    if not isinstance(payload, dict):
        raise IdmeError("ID.me returned an invalid token response")
    return payload


async def _discovery(config: IdmeConfig) -> dict[str, Any]:
    cached = _discovery_cache.get(config.discovery_url)
    if cached is not None and time.monotonic() - cached[0] < _DISCOVERY_CACHE_SECONDS:
        return dict(cached[1])
    async with _discovery_lock:
        cached = _discovery_cache.get(config.discovery_url)
        if cached is not None and time.monotonic() - cached[0] < _DISCOVERY_CACHE_SECONDS:
            return dict(cached[1])
        payload = await _get_json(config.discovery_url)
        expected_host = urlsplit(config.discovery_url).hostname or ""
        if payload.get("issuer") != config.issuer:
            raise IdmeError("ID.me issuer does not match its configured environment")
        metadata = {
            "authorization_endpoint": _trusted_endpoint(payload.get("authorization_endpoint"), expected_host),
            "token_endpoint": _trusted_endpoint(payload.get("token_endpoint"), expected_host),
            "jwks_uri": _trusted_endpoint(payload.get("jwks_uri"), expected_host),
            "userinfo_endpoint": _trusted_endpoint(payload.get("userinfo_endpoint"), expected_host),
        }
        response_types = payload.get("response_types_supported")
        if not isinstance(response_types, list) or "code" not in response_types:
            raise IdmeError("ID.me does not advertise the authorization code flow")
        signing_algorithms = payload.get("id_token_signing_alg_values_supported")
        if not isinstance(signing_algorithms, list) or "RS256" not in signing_algorithms:
            raise IdmeError("ID.me does not advertise the required signing algorithm")
        challenge_methods = payload.get("code_challenge_methods_supported")
        # ID.me supports S256 but its current discovery document omits this optional
        # metadata field. If the field is added, fail closed on an incompatible value.
        if challenge_methods is not None and (
            not isinstance(challenge_methods, list) or "S256" not in challenge_methods
        ):
            raise IdmeError("ID.me does not advertise S256 PKCE")
        token_auth_methods = payload.get("token_endpoint_auth_methods_supported")
        if token_auth_methods is not None and (
            not isinstance(token_auth_methods, list)
            or config.token_endpoint_auth_method not in token_auth_methods
        ):
            raise IdmeError("ID.me does not advertise the configured token authentication method")
        advertised_scopes = payload.get("scopes_supported")
        # ID.me provisions policy handles per client and currently advertises
        # only the protocol scope. Validate openid while allowing the assigned
        # `tefca` policy from the signed provisioning document.
        if not isinstance(advertised_scopes, list) or "openid" not in advertised_scopes:
            raise IdmeError("ID.me does not advertise the OpenID Connect scope")
        _discovery_cache[config.discovery_url] = (time.monotonic(), metadata)
        return dict(metadata)


async def begin_authorization() -> StartedAuthorization:
    config = _config()
    metadata = await _discovery(config)
    state = secrets.token_urlsafe(32)
    browser_token = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    pkce_verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(pkce_verifier.encode("ascii")).digest())
    query = urlencode({
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": config.scope,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return StartedAuthorization(
        authorization_url=f"{metadata['authorization_endpoint']}?{query}",
        state=state,
        browser_token=browser_token,
        nonce=nonce,
        pkce_verifier=pkce_verifier,
    )


async def _jwks(url: str, *, force_refresh: bool = False) -> dict[str, Any]:
    cached = _jwks_cache.get(url)
    if not force_refresh and cached is not None and time.monotonic() - cached[0] < _DISCOVERY_CACHE_SECONDS:
        return dict(cached[1])
    async with _jwks_lock:
        cached = _jwks_cache.get(url)
        if not force_refresh and cached is not None and time.monotonic() - cached[0] < _DISCOVERY_CACHE_SECONDS:
            return dict(cached[1])
        keys = await _get_json(url)
        if not isinstance(keys.get("keys"), list):
            raise IdmeError("ID.me returned an invalid signing-key set")
        _jwks_cache[url] = (time.monotonic(), keys)
        return dict(keys)


async def complete_authorization(
    code: str,
    pkce_verifier: str,
    nonce: str,
    intent: str = "login",
) -> VerifiedIdentity:
    if not code or len(code) > 8_192 or not pkce_verifier or not nonce:
        raise IdmeError("ID.me authorization response is incomplete")
    config = _config()
    metadata = await _discovery(config)
    token_request = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "code_verifier": pkce_verifier,
    }
    basic_auth = None
    if config.token_endpoint_auth_method == "client_secret_post":
        token_request["client_secret"] = config.client_secret or ""
    elif config.token_endpoint_auth_method == "client_secret_basic":
        token_request.pop("client_id")
        basic_auth = (config.client_id, config.client_secret or "")
    tokens = await _post_form(metadata["token_endpoint"], token_request, basic_auth)
    _validate_granted_policy_scope(tokens, config)
    raw_token = tokens.get("id_token")
    if not isinstance(raw_token, str):
        raise IdmeError("ID.me did not return an ID token")
    keys = await _jwks(metadata["jwks_uri"])
    try:
        identity = _verify_id_token(raw_token, keys, config, nonce)
    except _SigningKeyError:
        # A single forced refresh supports normal ID.me signing-key rotation.
        identity = _verify_id_token(
            raw_token,
            await _jwks(metadata["jwks_uri"], force_refresh=True),
            config,
            nonce,
        )
    if intent == "tefca_ias" and (
        identity.assurance_level is None or not identity.jti_present
    ):
        raise IdmeError("ID.me TEFCA identity claims are incomplete")
    return identity
