"""Validation and decryption of compact SMART Health Link payloads."""

import base64
import json

from jwcrypto import jwe, jwk


def decrypt_jwe_payload(jwe_payload: str, key: str) -> dict:
    """Decrypt a compact A256GCM JWE using an SHL base64url key."""
    key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    if len(key_bytes) != 32:
        raise ValueError("A256GCM requires a 32-byte symmetric key")
    token = jwe.JWE()
    token.deserialize(jwe_payload)
    token.decrypt(jwk.JWK(kty="oct", k=key))
    bundle = json.loads(token.payload.decode("utf-8"))
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("Decrypted JWE payload is not a FHIR Bundle")
    return bundle
