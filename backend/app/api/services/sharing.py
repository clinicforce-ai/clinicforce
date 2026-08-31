"""Creation of encrypted SMART Health Link payloads."""

import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any


def create_upload_shl(
    patient_id: str | None,
    fhir_bundle: dict[str, Any],
    passcode: str | None,
    source: str,
    require_passcode: bool,
    public_base_url: str,
    ttl_seconds: int,
    generate_key: Callable[[], str],
    encrypt_bundle: Callable[[dict[str, Any], bytes], str],
    hash_pin: Callable[[str], str],
    create_manifest: Callable[..., str],
    create_uri: Callable[..., str],
    generate_qr: Callable[[str], str],
) -> tuple[str, str, str]:
    """Persist an encrypted share and return its URI, QR image, and identifier."""
    if require_passcode and not passcode:
        raise ValueError("A passcode is required for a protected SHL")
    if not require_passcode and passcode is not None:
        raise ValueError("An unprotected SHL cannot include a passcode")
    key = generate_key()
    key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    encrypted_jwe = encrypt_bundle(fhir_bundle, key_bytes)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    manifest_id = create_manifest(
        patient_id, encrypted_jwe,
        hash_pin(passcode) if require_passcode and passcode is not None else None,
        source, expires_at.isoformat(),
    )
    uri = create_uri(
        f"{public_base_url}/manifest/{manifest_id}", key, "ClinicForce records",
        int(expires_at.timestamp()), viewer_url=f"{public_base_url}/" if require_passcode else None,
        require_passcode=require_passcode,
    )
    return uri, generate_qr(uri), str(manifest_id)
