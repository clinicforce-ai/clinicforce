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
    status: Literal["draft", "enrollment", "fixture_validated", "shadow", "suspended"] = "draft"
    aliases: list[str] = Field(default_factory=list, max_length=100)
    rules: dict[str, Any] = Field(default_factory=dict)


class ConnectionActivationInput(BaseModel):
    """Administrator attestation for an existing payer route and Stedi service type."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reference: str = Field(min_length=10, max_length=500)
    service_type_code: str = Field(pattern=r"^[A-Za-z0-9]{1,4}$")
    aliases: list[str] = Field(default_factory=list, max_length=100)


class ConnectionAliasInput(BaseModel):
    """Administrator-approved eCW payer names for an enabled route."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reference: str = Field(min_length=10, max_length=500)
    aliases: list[str] = Field(min_length=1, max_length=100)


class CollectionPolicyInput(BaseModel):
    """Clinic administrator's payer-specific check-in collection decision."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["defer_to_claim", "disabled"]
    reference: str = Field(min_length=10, max_length=500)
    allow_supported_oon_advance: bool = False
    allow_cms_based_advance: bool = False


class AgreementInventoryScopeInput(BaseModel):
    """Provider and billing scope covered by one UHC inventory attestation."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rendering_npi: str = Field(pattern=r"^\d{10}$")
    billing_npi: str = Field(pattern=r"^\d{10}$")
    tin: str = Field(pattern=r"^\d{9}$")
    location_id: str = Field(min_length=1, max_length=100)
    place_of_service: str = Field(min_length=1, max_length=20)
    effective_from: date
    effective_to: date | None = None
    cms_locality: str | None = Field(default=None, pattern=r"^\d{2}$")
    cms_qp_status: Literal['qp', 'non_qp'] | None = None
    cms_payment_reference: str | None = Field(default=None, min_length=10, max_length=500)


class AgreementInventoryInput(BaseModel):
    """One-time administrator certification of the complete known UHC agreement inventory."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["certify", "revoke"]
    reference: str = Field(min_length=10, max_length=500)
    scopes: list[AgreementInventoryScopeInput] = Field(default_factory=list, max_length=100)


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
    service_type_code: str | None = Field(default=None, pattern=r'^[A-Z0-9]{1,3}$', description='Payer-confirmed service category for the selected CPT or HCPCS. Selected payer rows must have this category and describe this service family, or name the exact procedure. Unrelated broad categories such as chiropractic for a botulinum drug are rejected.')
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
    contract_product: str | None = Field(default=None, min_length=3, max_length=200, description='Exact product named in the linked signed clinic agreement; required when the payer label is an alias.')
    rate_plan_matcher: str | None = Field(default=None, min_length=1, max_length=100, description='Exact contracted rate plan; currently must match the verified network plan matcher.')
    network_identifiers: list[str] = Field(default_factory=list, max_length=10, description='Payer network identifiers confirmed by the same scoped agreement and administrator evidence.')


class PayerAllowanceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    line_id: str = Field(min_length=1, max_length=200)
    unit_allowed_cents: StrictInt = Field(gt=0, le=100_000_000)
    channel: Literal['payer_portal', 'payer_phone', 'payer_document']
    reference: str = Field(min_length=8, max_length=500)
    valid_through: date


class BillingProtectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Literal['qmb', 'not_qmb']
    reference: str = Field(min_length=8, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)


class BotoxReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    evidence_reference: str | None = Field(default=None, max_length=500)
    modifier_reference: str | None = Field(default=None, max_length=500)
    indication: str | None = Field(default=None, max_length=500)
    anatomical_region: str | None = Field(default=None, max_length=500)
    encounter_status: Literal['planned', 'performed'] | None = None
    brand: Literal['Botox', 'other', 'unknown'] | None = None
    supply_source: Literal['clinic', 'specialty_pharmacy', 'unknown'] | None = None
    planned_units: StrictInt | None = Field(default=None, ge=1, le=10000, description='Proposed HCPCS units for a future visit; not an administered dose or received stock.')
    administered_units: StrictInt | None = Field(default=None, ge=1, le=10000)
    discarded_units: StrictInt | None = Field(default=None, ge=0, le=10000)
    separate_em_documented: bool = False


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
    confirmed_modifiers: dict[str, list[str]] | None = Field(default=None, max_length=20, description='Clinician-reviewed CPT/HCPCS modifiers by selected service line ID. Empty list confirms none; a code is not inferred from discarded units.')
    botox: BotoxReviewInput | None = Field(default=None, description='Clinician-reviewed Botox indication, anatomy, drug source, administered/discarded units and any separately documented E/M. Appointment wording and received stock do not establish billable care.')
    verification: VisitVerification | None = None
    payments_received_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    payment_reference: str | None = Field(default=None, min_length=3, max_length=500)
    identity_confirmation: IdentityConfirmation | None = None
    product_mapping: ProductMapping | None = Field(default=None, description='Administrator-sourced mapping of the payer-returned product to an existing exact-product clinic agreement. Local or regional Medicare Advantage PPO products cannot be mapped to a general UHC PPO agreement.')
    payer_allowance: PayerAllowanceInput | None = Field(default=None, description='Administrator-documented payer out-of-network allowance per unit for one selected CPT, with a payer reference and expiration date. The server binds it to the current patient, payer product, provider, location and visit.')
    billing_protection: BillingProtectionInput | None = Field(default=None, description='Administrator-verified QMB status for an identified Medicare Advantage member, bound to this payer response and service date. Unknown status does not authorize an advance.')
    clear_payer_allowance_line_id: str | None = Field(default=None, min_length=1, max_length=200)


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
