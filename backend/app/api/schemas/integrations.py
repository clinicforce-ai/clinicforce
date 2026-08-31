"""EHR connection and diagnostic request contracts."""

from pydantic import Field, field_validator

from app.api.schemas.base import RequestModel
from app.api.schemas.patients import PatientCredentials
from app.ingestion import ecw


class IntegrationTestRequest(RequestModel):
    patient_id: str = Field(min_length=1, max_length=128)
    passcode: str = Field(min_length=1, max_length=128)


class EpicAuthorizeRequest(PatientCredentials):
    endpoint_id: str = Field(min_length=1, max_length=256)
    popup: bool = False


class EcwAuthorizeRequest(PatientCredentials):
    """Start a standalone patient SMART launch for one eCW practice."""

    practice_code: str = Field(min_length=1, max_length=32)
    popup: bool = False

    @field_validator("practice_code")
    @classmethod
    def normalize_ecw_practice_code(cls, value: str) -> str:
        return ecw.normalize_practice_code(value)
