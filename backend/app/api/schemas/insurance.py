"""Clinician insurance-beta request contracts."""

from typing import Literal

from pydantic import Field

from app.api.schemas.base import RequestModel


class InsuranceIntakeRequest(RequestModel):
    member_name: str = Field(min_length=3, max_length=80)
    member_id: str = Field(min_length=5, max_length=40)
    group_number: str = Field(min_length=3, max_length=40)


class InsuranceServiceLinesRequest(RequestModel):
    code: Literal["99204", "99205"]
