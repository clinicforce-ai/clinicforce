"""Patient profile, record, and download request contracts."""

from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from app.api.schemas.base import RequestModel
from app.api.settings import MAX_BULK_DOWNLOAD_DOCUMENTS


class PatientCredentials(RequestModel):
    patient_id: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)

    @field_validator("patient_id")
    @classmethod
    def normalize_patient_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Patient ID cannot be empty")
        return value


class PatientProfileRequest(PatientCredentials):
    """Minimum US Core demographics stored by ClinicForce."""

    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    administrative_gender: Literal["male", "female", "other", "unknown"]

    @field_validator("given_name", "family_name")
    @classmethod
    def normalize_legal_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Legal name fields cannot be empty")
        return value


class StoragePreferenceRequest(PatientCredentials):
    storage_provider: Literal["s3", "google_drive"]


class IdmeStartRequest(RequestModel):
    intent: Literal["login", "link"] = "login"


class DocumentBatchRequest(PatientCredentials):
    """Authenticated, bounded selection from the patient workspace."""

    document_ids: list[int] = Field(min_length=1, max_length=MAX_BULK_DOWNLOAD_DOCUMENTS)

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, value: list[int]) -> list[int]:
        if any(document_id <= 0 for document_id in value):
            raise ValueError("Document IDs must be positive")
        return list(dict.fromkeys(value))


class ManualRecordRequest(PatientCredentials):
    category: Literal["hospital", "lab", "vital", "condition", "allergy", "medication"]
    hospital: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    value: str | None = Field(default=None, max_length=256)
    unit: str | None = Field(default=None, max_length=64)
    date: str | None = Field(default=None, max_length=32)
    details: str | None = Field(default=None, max_length=4_000)
    dosage: str | None = Field(default=None, max_length=512)
