"""Utilities for creating encrypted SMART Health Links (SHLs)."""

import base64
import json
import os
from io import BytesIO

import qrcode
from jwcrypto import jwe, jwk
from passlib.context import CryptContext


# PBKDF2 is provided by passlib without a native blocking backend dependency.
# It is appropriate here because the SHL PIN is deliberately short and we only
# persist its salted passlib hash, never the PIN itself.
_PIN_CONTEXT = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _base64url_encode(value: bytes) -> str:
    """Encode bytes as unpadded base64url, as required by SHL/JWK fields."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def generate_symmetric_key() -> str:
    """Return a cryptographically secure 256-bit key in base64url form."""
    return _base64url_encode(os.urandom(32))


def encrypt_bundle(fhir_bundle: dict, key_bytes: bytes) -> str:
    """Encrypt a FHIR Bundle as a compact direct-encryption A256GCM JWE."""
    if len(key_bytes) != 32:
        raise ValueError("A256GCM requires a 32-byte symmetric key")

    plaintext = json.dumps(
        fhir_bundle, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    encryption_key = jwk.JWK(kty="oct", k=_base64url_encode(key_bytes))
    token = jwe.JWE(plaintext=plaintext, protected={"alg": "dir", "enc": "A256GCM"})
    token.add_recipient(encryption_key)
    return token.serialize(compact=True)


def hash_pin(passcode: str) -> str:
    """Hash a four-digit SHL passcode; never persist the plaintext value."""
    if not passcode.isdigit() or len(passcode) != 4:
        raise ValueError("Passcode must be exactly four digits")
    return _PIN_CONTEXT.hash(passcode)


def verify_pin(passcode: str, hashed_pin: str) -> bool:
    """Safely verify a passcode against its stored passlib hash."""
    if not passcode.isdigit() or len(passcode) != 4:
        return False
    try:
        return _PIN_CONTEXT.verify(passcode, hashed_pin)
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Hash a patient-workspace password without applying the SHL PIN policy."""
    if len(password) < 4:
        raise ValueError("Password must contain at least four characters")
    return _PIN_CONTEXT.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a patient-workspace password without exposing hash errors."""
    if not password:
        return False
    try:
        return _PIN_CONTEXT.verify(password, hashed_password)
    except (ValueError, TypeError):
        return False


def create_shl_uri(manifest_url: str, key: str, label: str) -> str:
    """Create an SHL URI containing the manifest URL and decryption key."""
    payload = {"url": manifest_url, "key": key, "label": label, "flag": "P"}
    encoded_payload = _base64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return f"shlink:/{encoded_payload}"


def generate_qr_code(shl_uri: str) -> str:
    """Render an SHL URI to a QR image and return its base64-encoded PNG."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(shl_uri)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
