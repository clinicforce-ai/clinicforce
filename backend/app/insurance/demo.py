"""Document-backed, local-only insurance workbench.

The patient case is read from an ignored JSON file outside source control so
the guided prototype can use supplied local evidence without committing PHI.
"""

from __future__ import annotations

import json
import os
from hashlib import sha256
from hmac import compare_digest
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal


ReferralStatus = Literal["not_required", "verified", "unknown", "missing"]
AuthorizationStatus = Literal["not_required", "approved", "pending", "denied"]
DemoStage = Literal["eligibility", "network", "coding", "authorization", "fee_schedule", "calculation"]


class LocalCaseError(ValueError):
    """The local document-backed case has not been configured safely."""


class InsuranceCardContentError(ValueError):
    """The uploaded card is not the configured local evidence document."""


def _load_case() -> dict[str, Any]:
    configured = os.getenv("INSURANCE_LOCAL_CASE_PATH")
    if not configured:
        raise LocalCaseError("Set INSURANCE_LOCAL_CASE_PATH to the untracked local insurance-case JSON file")
    try:
        case = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalCaseError("The local insurance-case JSON file cannot be read") from exc
    if not isinstance(case, dict) or not isinstance(case.get("verification"), dict) or not isinstance(case.get("pricing"), dict):
        raise LocalCaseError("The local insurance-case JSON file has an invalid structure")
    return case


def verification() -> dict[str, Any]:
    """Return the supplied document evidence; it is not a live payer result."""
    return deepcopy(_load_case()["verification"])


def validate_local_card_upload(content: bytes) -> None:
    """Accept only the supplied card evidence in this no-OCR local mode."""
    expected = _load_case().get("accepted_card_sha256")
    if not isinstance(expected, str) or not compare_digest(sha256(content).hexdigest(), expected):
        raise InsuranceCardContentError(
            "This local review accepts only the supplied insurance-card PDF. "
            "Image/card OCR is not configured yet, so unrelated files are rejected."
        )


def _anticipated_service_gates(anticipated_service: str) -> list[str]:
    return [] if anticipated_service.strip() else ["Confirm the anticipated service from the appointment, referral/order, or scheduled procedure before estimating a price."]


def _authorization_gates(referral_status: ReferralStatus, authorization_status: AuthorizationStatus) -> list[str]:
    gates: list[str] = []
    if referral_status in {"unknown", "missing"}:
        gates.append("Verify the referral requirement and referral status with the payer or referring practice.")
    if authorization_status in {"pending", "denied"}:
        gates.append("Resolve prior-authorization status before using an insurance estimate.")
    return gates


def service_estimate(
    *, reason: str, anticipated_service: str, referral_status: ReferralStatus, authorization_status: AuthorizationStatus,
) -> dict[str, Any]:
    """Return the safe stopping point; never invent a rate, final code, or quote."""
    case = _load_case()
    gates = _anticipated_service_gates(anticipated_service)
    gates.extend(_authorization_gates(referral_status, authorization_status))
    allowed_amount = case["pricing"].get("allowed_amount_cents")
    if allowed_amount is None:
        gates.append(case["pricing"]["missing_input"])
    return {
        "demo": True,
        "assistant": {
            "mode": "local document-backed case — Bedrock was not called",
            "reason": reason.strip(), "anticipated_service": anticipated_service.strip() or None,
            "output": "Pre-visit service facts only; no CPT/HCPCS code was generated.",
            "review_required": "A clinician/coder confirms the final diagnosis and billed service after the encounter.",
        },
        "service": {
            "demo_service_key": "DOCUMENTED-SERVICE-REVIEW-REQUIRED",
            "description": case["service_evidence"]["description"],
            "place_of_service": "Office (from portal snapshot)", "units": None, "charge": allowed_amount,
        },
        "referral": {"status": referral_status, "source": "Live source: eCW ServiceRequest plus payer/clearinghouse referral response."},
        "authorization": {"status": authorization_status, "source": case["service_evidence"]["authorization_snapshot"]},
        "gates": gates,
        "estimate": None,
        "disclaimer": "This local result uses supplied document evidence only. It is not a quote, coverage determination, authorization decision, or guarantee of payment.",
    }


def workbench_stage(
    stage: DemoStage, *, reason: str, anticipated_service: str, referral_status: ReferralStatus,
    authorization_status: AuthorizationStatus,
) -> dict[str, Any]:
    """Return one flowchart stage so non-technical reviewers can validate it."""
    case = _load_case()
    verified = verification()
    base: dict[str, Any] = {
        "demo": True, "stage": stage,
        "backend": {"mode": "local document-backed case — no eCW, Availity, clearinghouse, OCR, Bedrock, or payer call was made", "endpoint": "POST /api/clinician/insurance/demo-stage"},
        "gates": [], "terminal_route": None, "next_step": None, "next_label": None,
    }

    if stage == "eligibility":
        base.update({
            "title": "Eligibility and active coverage", "status": "review",
            "source": "Supplied Availity Essentials export. Production source: clearinghouse/Availity 270/271 eligibility response.",
            "inputs": [{"label": "Card and patient identity", "value": "Supplied member card plus patient record", "source": "Patient upload"}, {"label": "Portal lookup", "value": verified["eligibility_transaction"]["response"]["eligibility_code_display"], "source": "Supplied portal export"}],
            "outputs": [{"label": "Coverage status", "value": verified["coverage_fhir"]["status"], "source": "Portal snapshot"}, {"label": "Data discrepancy", "value": verified["source_conflicts"][0], "source": "Card compared with portal export"}],
            "decision": "The portal evidence shows active coverage, but preserve and resolve the card/portal group discrepancy before any live estimate.",
            "next_step": "network", "next_label": "Continue to network evidence",
        })
        return base

    if stage == "network":
        base.update({
            "title": "Network status and out-of-network benefits", "status": "review",
            "source": "Supplied provider agreement plus dated portal network result. Production source: clinic roster plus payer plan benefits.",
            "inputs": [{"label": "Provider evidence", "value": verified["contract_match"]["source"], "source": "Provider agreement / portal export"}, {"label": "Service location", "value": verified["contract_match"]["service_location"], "source": "Portal export"}],
            "outputs": [{"label": "Network result", "value": "In network in the dated portal snapshot", "source": "Portal export"}, {"label": "Future requirement", "value": "Recheck NPI, TIN, location, plan, and date of service live", "source": "Network rule"}],
            "decision": "The supplied evidence supports proceeding to clinical service review, but does not replace a live network check.",
            "next_step": "coding", "next_label": "Continue to service review",
        })
        return base

    if stage == "coding":
        gates = _anticipated_service_gates(anticipated_service)
        base.update({
            "title": "Confirm anticipated service", "status": "passed" if not gates else "blocked",
            "source": "Production source: appointment, referral/order, scheduled procedure, and clinician confirmation. Final coding happens after the encounter.",
            "inputs": [{"label": "Reason for visit", "value": reason.strip(), "source": "Appointment or clinician"}, {"label": "Anticipated service", "value": anticipated_service.strip() or "Not confirmed", "source": "Appointment, referral/order, or clinician"}],
            "outputs": [{"label": "Pre-visit coding status", "value": "No final CPT/HCPCS code required at this stage", "source": "Pre-service workflow"}, {"label": "Document evidence", "value": case["service_evidence"]["billing_code_selection"], "source": "Supplied documents"}],
            "decision": "The anticipated service is ready for referral and authorization checks." if not gates else "Confirm the anticipated service before referral, authorization, and rate checks.",
            "gates": gates, "next_step": "authorization" if not gates else None, "next_label": "Continue to referral and authorization" if not gates else None,
        })
        return base

    if stage == "authorization":
        gates = _authorization_gates(referral_status, authorization_status)
        base.update({
            "title": "Referral and prior authorization", "status": "passed" if not gates else "blocked",
            "source": "Supplied portal procedure checks are dated evidence. Production source: eCW ServiceRequest plus payer prior-auth API/X12 278.",
            "inputs": [{"label": "Referral", "value": referral_status.replace("_", " "), "source": "Clinician selection / future eCW+payer check"}, {"label": "Prior authorization", "value": authorization_status.replace("_", " "), "source": "Clinician selection / future payer check"}],
            "outputs": [{"label": "Document evidence", "value": case["service_evidence"]["authorization_snapshot"], "source": "Portal export"}],
            "decision": "Administrative selections are clear; the price workflow is now blocked only by the missing current rate." if not gates else "Resolve the listed payer requirements before estimating patient responsibility.",
            "gates": gates, "next_step": "fee_schedule" if not gates else None, "next_label": "Continue to contracted rate" if not gates else None,
        })
        return base

    if stage == "fee_schedule":
        base.update({
            "title": "Contracted rate / fee schedule", "status": "blocked",
            "source": "The supplied provider agreement references a current fee schedule. It does not contain the numeric allowed amount for this service.",
            "inputs": [{"label": "Provider contract", "value": "Provider-network and compensation agreement supplied", "source": "Provider agreement"}, {"label": "Selected service", "value": "Still requires clinician/coder approval", "source": "Coding review"}],
            "outputs": [{"label": "Rate result", "value": "No numeric contracted rate in the supplied documents", "source": "Contract review"}],
            "decision": case["pricing"]["missing_input"], "gates": [case["pricing"]["missing_input"]],
        })
        return base

    base.update({
        "title": "Patient responsibility calculation", "status": "terminal",
        "source": "Calculation cannot begin without a current allowed amount or payer estimator result.",
        "inputs": [], "outputs": [], "decision": case["pricing"]["missing_input"],
        "gates": [case["pricing"]["missing_input"]],
        "terminal_route": {"code": "estimate_unavailable", "label": "Estimate unavailable", "detail": "Show verified benefit evidence and route to financial counseling; do not invent a patient price."},
    })
    return base
