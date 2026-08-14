"""Envelope encryption for patient-owned Google Drive artifacts.

Drive stores opaque ciphertext only.  A fresh AES-256 data key is created for
each artifact and wrapped by the existing ClinicForce KMS key.  The wrapped key
and authenticated-encryption metadata live in RDS; plaintext is never persisted
outside the request that needs it.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from uuid import uuid4

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KMS_KEY_ID = os.getenv("GOOGLE_TOKEN_KMS_KEY_ID", "").strip()
_ALGORITHM = "AES-256-GCM-v1"


@dataclass(frozen=True)
class EncryptedDriveArtifact:
    ciphertext: bytes
    artifact_id: str
    purpose: str
    encrypted_data_key: str
    nonce: str
    plaintext_sha256: str
    algorithm: str = _ALGORITHM


def _context(patient_id: str, artifact_id: str, purpose: str) -> dict[str, str]:
    return {
        "clinicforce:patient_id": patient_id,
        "clinicforce:artifact_id": artifact_id,
        "clinicforce:purpose": purpose,
    }


def _aad(patient_id: str, artifact_id: str, purpose: str) -> bytes:
    return f"ClinicForce|{patient_id}|{artifact_id}|{purpose}|{_ALGORITHM}".encode("utf-8")


def encrypt_pdf(patient_id: str, purpose: str, plaintext: bytes) -> EncryptedDriveArtifact:
    """Encrypt a PDF with a new KMS-wrapped data key and authenticated metadata."""
    if not KMS_KEY_ID:
        raise RuntimeError("GOOGLE_TOKEN_KMS_KEY_ID must be configured for Drive encryption")
    if not plaintext:
        raise ValueError("Cannot encrypt an empty document")
    artifact_id = str(uuid4())
    context = _context(patient_id, artifact_id, purpose)
    generated = boto3.client("kms").generate_data_key(
        KeyId=KMS_KEY_ID,
        KeySpec="AES_256",
        EncryptionContext=context,
    )
    plaintext_key = bytearray(generated["Plaintext"])
    nonce = os.urandom(12)
    try:
        ciphertext = AESGCM(bytes(plaintext_key)).encrypt(nonce, plaintext, _aad(patient_id, artifact_id, purpose))
    finally:
        for index in range(len(plaintext_key)):
            plaintext_key[index] = 0
    return EncryptedDriveArtifact(
        ciphertext=ciphertext,
        artifact_id=artifact_id,
        purpose=purpose,
        encrypted_data_key=base64.b64encode(generated["CiphertextBlob"]).decode("ascii"),
        nonce=base64.b64encode(nonce).decode("ascii"),
        plaintext_sha256=hashlib.sha256(plaintext).hexdigest(),
    )


def decrypt_pdf(patient_id: str, artifact: object, ciphertext: bytes) -> bytes:
    """Decrypt and authenticate an RDS-recorded Drive artifact in memory."""
    required = ("artifact_id", "purpose", "encrypted_data_key", "nonce", "plaintext_sha256", "algorithm")
    if any(not isinstance(getattr(artifact, field, None), str) or not getattr(artifact, field) for field in required):
        raise RuntimeError("Drive encryption metadata is incomplete")
    if artifact.algorithm != _ALGORITHM:
        raise RuntimeError("Unsupported Drive encryption algorithm")
    context = _context(patient_id, artifact.artifact_id, artifact.purpose)
    key_response = boto3.client("kms").decrypt(
        CiphertextBlob=base64.b64decode(artifact.encrypted_data_key),
        EncryptionContext=context,
    )
    plaintext_key = bytearray(key_response["Plaintext"])
    try:
        plaintext = AESGCM(bytes(plaintext_key)).decrypt(
            base64.b64decode(artifact.nonce), ciphertext, _aad(patient_id, artifact.artifact_id, artifact.purpose)
        )
    finally:
        for index in range(len(plaintext_key)):
            plaintext_key[index] = 0
    if hashlib.sha256(plaintext).hexdigest() != artifact.plaintext_sha256:
        raise RuntimeError("Drive document integrity verification failed")
    return plaintext
