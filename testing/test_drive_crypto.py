"""Envelope-encryption tests for patient-owned Drive artifacts."""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import drive_crypto  # noqa: E402


@dataclass
class StoredArtifact:
    artifact_id: str
    purpose: str
    encrypted_data_key: str
    nonce: str
    plaintext_sha256: str
    algorithm: str


def test_drive_artifact_round_trip_uses_kms_envelope_context() -> None:
    kms = Mock()
    kms.generate_data_key.return_value = {"Plaintext": b"a" * 32, "CiphertextBlob": b"wrapped-key"}
    kms.decrypt.return_value = {"Plaintext": b"a" * 32}
    with patch.object(drive_crypto, "KMS_KEY_ID", "kms-key"), patch("app.core.drive_crypto.boto3.client", return_value=kms):
        encrypted = drive_crypto.encrypt_pdf("patient-1", "source", b"%PDF-1.4 private clinical report")
        restored = drive_crypto.decrypt_pdf("patient-1", StoredArtifact(
            artifact_id=encrypted.artifact_id,
            purpose=encrypted.purpose,
            encrypted_data_key=encrypted.encrypted_data_key,
            nonce=encrypted.nonce,
            plaintext_sha256=encrypted.plaintext_sha256,
            algorithm=encrypted.algorithm,
        ), encrypted.ciphertext)

    assert restored == b"%PDF-1.4 private clinical report"
    assert encrypted.ciphertext != restored
    assert base64.b64decode(encrypted.encrypted_data_key) == b"wrapped-key"
    assert kms.decrypt.call_args.kwargs["EncryptionContext"]["clinicforce:patient_id"] == "patient-1"
