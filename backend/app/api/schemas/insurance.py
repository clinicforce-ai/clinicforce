"""Local insurance-demo request contracts."""

from typing import Literal

from pydantic import Field, field_validator

from app.api.schemas.base import RequestModel


class InsuranceEstimateDemoRequest(RequestModel):
    reason: str = Field(min_length=3, max_length=2_000)
    anticipated_service: str = Field(default="", max_length=1_000)
    referral_status: Literal["not_required", "verified", "unknown", "missing"] = "unknown"
    authorization_status: Literal["not_required", "approved", "pending", "denied"] = "not_required"

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reason for visit cannot be empty")
        return value


class InsuranceDemoStageRequest(InsuranceEstimateDemoRequest):
    stage: Literal["eligibility", "network", "coding", "authorization", "fee_schedule", "calculation"]


class InsuranceV2DocumentReviewRequest(RequestModel):
    fields: dict[str, str] = Field(default_factory=dict)


class InsuranceV2StageRequest(RequestModel):
    service: str = Field(default="", max_length=1_000)
