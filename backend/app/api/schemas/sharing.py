"""Share-link and SHL exchange request contracts."""

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from app.api.schemas.base import RequestModel
from app.api.schemas.patients import PatientCredentials


class ManifestRequest(RequestModel):
    """SHL manifests allow forward-compatible fields from conforming clients."""

    model_config = ConfigDict(extra="ignore")
    recipient: str = Field(min_length=1, max_length=512)
    passcode: str | None = Field(default=None, min_length=1, max_length=128)
    embeddedLengthMax: int | None = Field(default=None, ge=0)

    @field_validator("passcode")
    @classmethod
    def normalize_passcode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Passcode is required")
        return value


class DoctorSummaryRequest(RequestModel):
    manifest_id: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=256)
    passcode: str = Field(min_length=4, max_length=4)


class GeneratePatientLinkRequest(PatientCredentials):
    """Create either the default protected share or a bare SHL test share."""

    share_mode: Literal["standard", "ecw_compat"] = "standard"
    passcode: str | None = Field(default=None, min_length=4, max_length=4, pattern=r"^\d{4}$")

    @field_validator("passcode")
    @classmethod
    def normalize_link_passcode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.isdigit() or len(value) != 4:
            raise ValueError("Choose a four-digit sharing passcode")
        return value


class ExternalShlImportRequest(RequestModel):
    """Browser-decrypted external SHL payload ready for local import."""

    source_host: str = Field(min_length=1, max_length=253)
    bundle: dict[str, Any]

    @field_validator("source_host")
    @classmethod
    def validate_external_source_host(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or "/" in value or ":" in value or any(char.isspace() for char in value):
            raise ValueError("External source host is invalid")
        return value
