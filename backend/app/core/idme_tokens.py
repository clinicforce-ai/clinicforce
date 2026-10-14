"""Verify ID.me ID tokens and assurance claims."""
from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jwcrypto import jwk, jwt

from app.core.patient_models import IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS

if TYPE_CHECKING:
    from app.core.idme import IdmeConfig


class IdmeError(Exception):
    """A safe-to-handle ID.me protocol or configuration failure."""



class _SigningKeyError(IdmeError):
    """An ID-token key miss/signature failure that may be fixed by one JWKS refresh."""



@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    assurance_level: str | None = None
    policy_scope: str | None = None
    jti_present: bool = False



def _json_segment(value: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
    except (binascii.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise IdmeError("ID.me returned a malformed ID token") from exc
    if not isinstance(payload, dict):
        raise IdmeError("ID.me returned a malformed ID token")
    return payload



def _validate_granted_policy_scope(tokens: dict[str, Any], config: IdmeConfig) -> None:
    # ID.me's documented token response returns the assigned policy handle in
    # `scope` but omits the protocol-level `openid` value. Require every
    # configured policy handle so an edited authorization URL cannot downgrade
    # the flow to bare OpenID Connect.
    required_policies = {item for item in config.scope.split() if item != "openid"}
    granted_scope = tokens.get("scope")
    if (
        not required_policies
        or not isinstance(granted_scope, str)
        or not required_policies.issubset(set(granted_scope.split()))
    ):
        raise IdmeError("ID.me did not grant the configured identity policy")



def _verify_id_token(raw_token: str, keys: dict[str, Any], config: IdmeConfig, nonce: str) -> VerifiedIdentity:
    if len(raw_token) > 64_000:
        raise IdmeError("ID.me returned an invalid ID token")
    parts = raw_token.split(".")
    if len(parts) != 3:
        raise IdmeError("ID.me returned a malformed ID token")
    header = _json_segment(parts[0])
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
        raise IdmeError("ID.me returned an ID token with an unsupported header")
    candidates = [
        value for value in keys.get("keys", [])
        if isinstance(value, dict)
        and value.get("kid") == kid
        and value.get("kty") == "RSA"
        and value.get("use", "sig") == "sig"
        and value.get("alg", "RS256") == "RS256"
    ] if isinstance(keys.get("keys"), list) else []
    if len(candidates) != 1:
        raise _SigningKeyError("ID.me signing key was not found")
    try:
        verified = jwt.JWT(
            jwt=raw_token,
            key=jwk.JWK.from_json(json.dumps(candidates[0])),
            algs=["RS256"],
            expected_type="JWS",
        )
        claims = json.loads(verified.claims)
    except Exception as exc:  # jwcrypto exposes multiple signature/format exception types.
        raise _SigningKeyError("ID.me ID token signature validation failed") from exc
    if not isinstance(claims, dict) or claims.get("iss") != config.issuer:
        raise IdmeError("ID.me ID token issuer is invalid")
    audience = claims.get("aud")
    audiences = [audience] if isinstance(audience, str) else audience
    if not isinstance(audiences, list) or not audiences or any(not isinstance(item, str) for item in audiences):
        raise IdmeError("ID.me ID token audience is invalid")
    authorized_party = claims.get("azp")
    if (
        config.client_id not in audiences
        or (authorized_party is not None and authorized_party != config.client_id)
        or (len(audiences) > 1 and authorized_party != config.client_id)
    ):
        raise IdmeError("ID.me ID token audience is invalid")
    now = int(time.time())
    issued_at, expires_at = claims.get("iat"), claims.get("exp")
    not_before = claims.get("nbf")
    if (
        not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or issued_at > now + 60
        or issued_at < now - IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS
        or expires_at <= now
        or expires_at <= issued_at
        or (not_before is not None and (not isinstance(not_before, int) or isinstance(not_before, bool) or not_before > now + 60))
    ):
        raise IdmeError("ID.me ID token timing claims are invalid")
    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not secrets.compare_digest(
        token_nonce.encode("utf-8"), nonce.encode("utf-8")
    ):
        raise IdmeError("ID.me ID token nonce is invalid")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise IdmeError("ID.me ID token subject is invalid")
    assurance = claims.get("identity_assurance_level")
    if assurance is not None and (
        not isinstance(assurance, str) or not assurance or len(assurance) > 128
    ):
        raise IdmeError("ID.me identity assurance claim is invalid")
    jti = claims.get("jti")
    if jti is not None and (not isinstance(jti, str) or not jti or len(jti) > 255):
        raise IdmeError("ID.me token identifier claim is invalid")
    policy = next((item for item in config.scope.split() if item != "openid"), None)
    return VerifiedIdentity(config.issuer, subject, assurance, policy, isinstance(jti, str))
