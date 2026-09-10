"""Clinician insurance-beta request contracts."""

from typing import Annotated, Literal

from pydantic import Field

from app.api.schemas.base import RequestModel


class InsuranceIntakeRequest(RequestModel):
    member_name: str = Field(default="", max_length=100)
    member_id: str = Field(default="", max_length=40)
    group_number: str = Field(default="", max_length=40)


class InsuranceServiceLinesRequest(RequestModel):
    quantities: dict[str, Annotated[int, Field(strict=True, ge=1, le=10000)]] = Field(default_factory=dict, max_length=20)
    codes: list[Annotated[str, Field(min_length=5, max_length=8, pattern=r"^[A-Z0-9]+$")]] = Field(
        min_length=1, max_length=20,
    )


class InsuranceClinicalContextRequest(RequestModel):
    clinical_context: str = Field(default="", max_length=8_000)
    visit_type: Literal["new", "established"] | None = None
