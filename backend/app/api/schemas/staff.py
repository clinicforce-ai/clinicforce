"""Validated request and response models for the staff workspace."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core import clinic_email


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MissingRateGap(Model):
    payer: str
    code: str
    kind: Literal['insured_allowed', 'clinic_cash']
    estimate_count: int = Field(ge=1)
    last_observed_at: str


class MissingRatesResponse(Model):
    gaps: list[MissingRateGap]
    group_count: int = Field(ge=0)
    scanned_case_count: int = Field(ge=0)
    scan_limit: int = Field(ge=1)
    scan_limit_reached: bool


class Login(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._+\-]*$")
    password: str = Field(min_length=1, max_length=256)

    @field_validator("username", mode="before")
    @classmethod
    def normalized_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Member(Login):
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    role: Literal["staff", "admin"] = "staff"

    @field_validator("display_name")
    @classmethod
    def valid_display_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a staff name without control characters")
        return value


class Patient(Model):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    member_id: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=254)
    payer: str = Field(default="", max_length=200)
    group_number: str = Field(default="", max_length=80)
    relationship: Literal["self", "spouse", "child", "other", "unknown", "18", "01", "19", "G8"]
    subscriber_name: str = Field(default="", max_length=200)
    subscriber_birth_date: date | None = None
    patient_id: str | None = Field(default=None, max_length=100)
    identity_confirmed: Literal[True]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return clinic_email._address(value) if value else ""

    @model_validator(mode="after")
    def subscriber_identified(self):
        if self.relationship not in {"self", "18", "unknown"} and not self.subscriber_name:
            raise ValueError("Enter the policyholder name when the patient is not the policyholder")
        return self

    @field_validator("subscriber_birth_date", mode="before")
    @classmethod
    def empty_date(cls, value):
        return None if value == "" else value

    @field_validator("birth_date", "subscriber_birth_date")
    @classmethod
    def plausible_date(cls, value):
        if value and not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid birth date that is not in the future")
        return value


class PrebookingEligibility(Model):
    """Prospective-person eligibility inquiry; it creates no clinic patient."""

    payer_id: str = Field(min_length=1, max_length=100, description="Stedi payer ID selected from the payer directory")
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    birth_date: date
    member_id: str = Field(default="", max_length=80, description="Optional; some payers require it to identify coverage")

    @field_validator("given_name", "family_name", "member_id")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Remove control characters from insurance details")
        return value

    @field_validator("birth_date")
    @classmethod
    def plausible_birth_date(cls, value: date) -> date:
        if not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid birth date that is not in the future")
        return value

class PrebookingPayerOption(Model):
    id: str
    name: str


class PrebookingOptions(Model):
    payers: list[PrebookingPayerOption]


class PrebookingResult(Model):
    status: Literal["active_today", "inactive_today", "needs_review"]
    message: str
    checked_at: datetime
    checked_on: date
    payer: str
    check_id: str | None
    coverage_start: date | None
    coverage_end: date | None


class PrebookingPlanRequest(Model):
    """Reason-only clinical planning before any payer transaction."""

    reason: str = Field(min_length=3, max_length=800)
    visit_type: Literal["new", "established", "unknown"] = "unknown"

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise ValueError("Remove control characters from the visit reason")
        return value


class PrebookingSelectedService(Model):
    code: str = Field(pattern=r"^(?:[0-9]{5}|[A-Z][0-9]{4}|[0-9]{4}[A-Z])$")
    units: int = Field(ge=1, le=4, strict=True)


class PrebookingEstimateRequest(PrebookingEligibility, PrebookingPlanRequest):
    """Prospective planning scenario; no eCW encounter is implied."""

    member_id: str = Field(min_length=1, max_length=80, description="Required to match a member-bound plan before estimating cost")
    selected_services: list[PrebookingSelectedService] = Field(min_length=1, max_length=8)
    network_context_id: str | None = Field(default=None, max_length=100)
    service_date: date | None = Field(default=None, description="Tentative date; defaults to today")
    relationship: Literal["self", "dependent"] = "self"
    subscriber_given_name: str = Field(default="", max_length=100)
    subscriber_family_name: str = Field(default="", max_length=100)
    subscriber_birth_date: date | None = None

    @field_validator("member_id")
    @classmethod
    def member_id_from_card(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter the member ID from the insurance card")
        return value.strip()

    @field_validator("subscriber_given_name", "subscriber_family_name")
    @classmethod
    def valid_subscriber_name(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Remove control characters from policyholder details")
        return value

    @field_validator("subscriber_birth_date")
    @classmethod
    def valid_subscriber_birth_date(cls, value: date | None) -> date | None:
        if value and not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid policyholder birth date")
        return value

    @model_validator(mode="after")
    def validate_scenario(self):
        if self.relationship == "dependent" and not (self.member_id and self.subscriber_given_name
                                                       and self.subscriber_family_name and self.subscriber_birth_date):
            raise ValueError("Enter the policyholder name, birth date and member ID for dependent coverage")
        return self


class PrebookingPlanCandidate(Model):
    code: str
    reason: str
    role: str
    service_type: str | None
    selected_by_default: bool = Field(default=False, description="Planning baseline included automatically; optional ideas remain unselected")


class PrebookingPlanGroup(Model):
    label: str
    selection_mode: Literal["single", "multiple"]
    codes: list[PrebookingPlanCandidate]


class PrebookingPlanResponse(Model):
    groups: list[PrebookingPlanGroup]
    notes: list[str]
    message: str


class PrebookingContextOption(Model):
    id: str
    label: str


class PrebookingContextsResponse(Model):
    contexts: list[PrebookingContextOption]


class PrebookingEstimateTerms(Model):
    copay_cents: int | None
    coinsurance_percent: str | None
    deductible_annual_cents: int | None
    deductible_remaining_cents: int | None
    oop_annual_cents: int | None
    oop_remaining_cents: int | None
    deductible_applicability: str | None
    authorization_signal: str | None
    referral_signal: str | None
    resolved_service_type: str | None


class PrebookingEstimateCoverage(Model):
    status: Literal["active_today", "inactive_today", "needs_review"]
    message: str
    coverage_start: date | None
    coverage_end: date | None


class PrebookingEstimateLine(Model):
    code: str
    units: int
    role: str
    service_type: str | None
    allowed_cents: int | None
    patient_cents: int | None
    rate_source: str | None = Field(description="Matched NMCT contract-rate source for this selected service; null when the visit cannot be priced")
    benefits: PrebookingEstimateTerms
    issues: list[str]
    conditions: list[str]


class PrebookingPatientAddress(Model):
    """Address returned for the identity-bound patient by Stedi, when supplied by the payer."""

    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class PrebookingEstimateResponse(Model):
    """Unknown financial values are null, never assumed to be zero."""

    coverage: PrebookingEstimateCoverage
    payer: str
    checked_at: datetime
    checked_on: date
    check_ids: list[str]
    patient_address: PrebookingPatientAddress | None = Field(
        description="Patient address returned by the matched Stedi response; null if absent or identity is unverified")
    network_status: Literal["in_network", "out_of_network", "unknown"]
    network_evidence: dict[str, Any] | None
    plan_name: str | None
    benefits: PrebookingEstimateTerms | None
    lines: list[PrebookingEstimateLine]
    allowed_total_cents: int | None = Field(description="Total matched contract-allowed cost for selected services; null unless every line is priced")
    plan_total_cents: int | None = Field(description="Estimated insurer share of the allowed total; null unless the complete visit is priced")
    patient_min_cents: int | None
    patient_max_cents: int | None
    estimate_status: Literal["priced", "conditional", "needs_review"]
    issues: list[str]


class IntakeSearch(Model):
    query: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=50)
    offset: int = Field(default=0, ge=0, le=1_000_000_000, strict=True)


class PatientSearch(Model):
    query: str = Field(default="", max_length=200)
    given_name: str = Field(default="", max_length=100)
    family_name: str = Field(default="", max_length=100)
    birth_date: str = Field(default="", max_length=10)


class EcwLookup(Model):
    ecw_account_number: str = Field(default="", max_length=80, description="Exact eCW secondary ACCT identifier. With birth date, this can find a patient without entering a name; staff must confirm the returned identity.")
    selected_patient_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.\-]{1,200}$", description="eCW suggestion explicitly selected by staff; re-read and verify against the submitted canonical demographics.")
    identity_confirmed: bool = Field(default=False, description="Required when selecting a suggested patient.")

    @field_validator("ecw_account_number")
    @classmethod
    def clean_account_number(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def confirmed_selection(self):
        if self.selected_patient_id and not self.identity_confirmed:
            raise ValueError("Confirm the selected patient before continuing")
        if (self.selected_patient_id or not self.ecw_account_number) and not all((self.given_name, self.family_name)):
            raise ValueError("Enter first and last name, or search by eCW ACCT number and date of birth")
        return self

    given_name: str = Field(default="", max_length=100, description="Patient first name; optional when an eCW ACCT number is supplied.")
    middle_initial: str = Field(default="", max_length=10)
    family_name: str = Field(default="", max_length=100, description="Patient last name; optional when an eCW ACCT number is supplied.")
    birth_date: date = Field(description="Patient DOB; required for both demographic and eCW ACCT lookup.")

    @field_validator("birth_date")
    @classmethod
    def plausible_birth_date(cls, value):
        if not date(1850, 1, 1) <= value <= date.today():
            raise ValueError("Enter a valid date of birth that is not in the future")
        return value
