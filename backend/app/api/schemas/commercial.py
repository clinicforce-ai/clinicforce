"""Request models for the commercial estimator routes."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=160)
    stedi_payer_id: str = Field(min_length=1, max_length=80)
    category: str = "commercial_medical"
    status: Literal["draft", "enrollment", "fixture_validated", "shadow", "staff_guidance_enabled", "suspended"] = "draft"
    aliases: list[str] = Field(default_factory=list, max_length=100)
    rules: dict[str, Any] = Field(default_factory=dict)


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    billing_npi: str = Field(pattern=r"^\d{10}$")
    rendering_npi: str = Field(pattern=r"^\d{10}$")
    organization_name: str = Field(min_length=1, max_length=160)
    tin: str = Field(min_length=4, max_length=20)
    location_id: str = Field(min_length=1, max_length=100)
    place_of_service: str = Field(min_length=1, max_length=20)
    network_name: str = Field(min_length=1, max_length=120)
    plan_matcher: str = Field(default="", max_length=200)
    effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["draft", "verified", "suspended"] = "draft"
    evidence_reference: str = Field(min_length=3, max_length=500)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    service_category: Literal["office", "emg_ncs"]
    plan_matcher: str = Field(default="", max_length=200)
    status: Literal["review_only", "staff_guidance_enabled", "suspended"] = "review_only"
    rules: dict[str, Any]


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    profile_id: str
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class EcwStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,200}$")


class PrevisitInput(EcwStartInput):
    refresh_benefits: bool = False


class ServiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^(?:\d{5}|[A-Za-z]\d{4}|\d{4}[A-Za-z])$")
    units: int = Field(ge=1, le=10000, strict=True)


class ServicesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[ServiceLine] = Field(min_length=1, max_length=20)


class VisitVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line_id: str = Field(min_length=1, max_length=200)
    kind: Literal["referral", "authorization", "benefits"]
    status: Literal["valid", "not_required", "pending", "denied"] = "valid"
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)
    valid_from: date
    valid_to: date
    units: int = Field(ge=1, le=10000, strict=True)
    selected_ids: list[str] = Field(default_factory=list, max_length=2)
    cost_share: Literal["copay", "coinsurance", "cumulative"] = "copay"
    deductible_applies: bool = True


class IdentityConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)


class ProductMapping(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)
    valid_from: date
    valid_to: date


class VisitReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    selected_ids: list[str] | None = Field(default=None, max_length=20)
    payment_mode: Literal["insurance", "cash"] | None = None
    emg_required: bool | None = None
    insurance_check_from: date | None = None
    cash_selection: str | None = Field(default=None, max_length=40, description="ID of a named package in the active cash schedule, or an empty string to clear the selection. Package selection is a clinician choice; example CPTs in the source are not automatically selected.")
    confirmed_units: dict[str, StrictInt] | None = Field(default=None, max_length=20)
    verification: VisitVerification | None = None
    payments_received_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    payment_reference: str | None = Field(default=None, min_length=3, max_length=500)
    identity_confirmation: IdentityConfirmation | None = None
    product_mapping: ProductMapping | None = None


class CoverageCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{20}$")


class PayerRouteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    route_id: str = Field(min_length=1, max_length=100, description='A supported Stedi route returned for this saved policy; never an arbitrary payer ID.')


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["staff_export", "ecw_document", "fixture", "stedi"]
    response_reference: str = Field(min_length=3, max_length=500)
    requested_dos: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    payer_checked_at: str
    expires_at: str | None = None
    benefits: dict[str, Any]
