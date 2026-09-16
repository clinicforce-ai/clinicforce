"""Immutable records returned by patient persistence operations."""

from dataclasses import dataclass
from typing import Any


# ID.me proofing can take longer than an ordinary OAuth sign-in. The
# authorization code is still short-lived and exchanged immediately after the
# callback; this only controls the one-time, browser-bound RP transaction.
IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS = 30 * 60


@dataclass(frozen=True)
class StoredDocument:
    id: int
    filename: str
    uploaded_at: str
    fhir_bundle: dict[str, Any]
    s3_key: str
    storage_provider: str = "s3"
    extraction_version: str | None = None


@dataclass(frozen=True)
class StoredRecord:
    id: int
    category: str
    label: str
    created_at: str
    fhir_bundle: dict[str, Any]


@dataclass(frozen=True)
class PatientProfile:
    patient_id: str
    given_name: str | None
    family_name: str | None
    birth_date: str | None
    administrative_gender: str | None


@dataclass(frozen=True)
class PatientProfileSuggestions:
    given_name: str | None
    family_name: str | None


@dataclass(frozen=True)
class StoredDoctorReport:
    id: str
    patient_id: str
    filename: str
    s3_key: str


@dataclass(frozen=True)
class StoredPatientDocument(StoredDocument):
    patient_id: str = ""


@dataclass(frozen=True)
class StoredDriveConnection:
    patient_id: str
    google_subject: str
    refresh_token_ciphertext: str
    folder_id: str
    summary_file_id: str | None
    summary_fingerprint: str | None


@dataclass(frozen=True)
class StoredDriveCrypto:
    file_id: str
    patient_id: str
    artifact_id: str
    purpose: str
    encrypted_data_key: str
    nonce: str
    plaintext_sha256: str
    algorithm: str


@dataclass(frozen=True)
class EpicEndpoint:
    id: str
    name: str
    aliases: list[str]
    addresses: list[dict[str, Any]]
    fhir_base: str


@dataclass(frozen=True)
class StoredEpicManifest:
    manifest_id: str
    created_at: str


@dataclass(frozen=True)
class StoredIdmeTransaction:
    intent: str
    patient_id: str | None
    nonce: str
    pkce_verifier: str


class InvalidManifestPin(Exception):
    """A rejected PIN with its remaining lifetime attempts."""

    def __init__(self, remaining_attempts: int) -> None:
        self.remaining_attempts = remaining_attempts
        super().__init__("Invalid manifest passcode")
