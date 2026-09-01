"""Clinician insurance-beta request contracts."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field

from app.api.schemas.base import RequestModel


class InsuranceIntakeRequest(RequestModel):
    member_name: str = Field(min_length=3, max_length=80)
    member_id: str = Field(min_length=5, max_length=40)
    group_number: str = Field(min_length=3, max_length=40)


class InsuranceServiceLinesRequest(RequestModel):
    codes: list[Annotated[str, Field(min_length=5, max_length=8, pattern=r"^[A-Z0-9]+$")]] = Field(
        min_length=1, max_length=20,
    )


class InsuranceEvidenceRequest(RequestModel):
    clinic: Literal["NMCT"]
    payer: Literal["BCBSTX"]
    plan_name: str = Field(min_length=3, max_length=120)
    eligibility_observed_at: date
    coverage_status: Literal["active", "inactive"]
    network_status: Literal["in_network", "out_of_network"]
    coinsurance_percent: int = Field(ge=0, le=100)
    copay_cents: int = Field(ge=0, le=10_000_000)
    individual_deductible_remaining_cents: int = Field(ge=0, le=100_000_000)
    individual_oop_remaining_cents: int = Field(ge=0, le=100_000_000)
    referral_status: Literal["not_required", "required_obtained", "unknown"]
    authorization_status: Literal["not_required", "required_obtained", "unknown"]
