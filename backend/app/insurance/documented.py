"""Temporary document-backed NMCT/BCBS fallback for patients outside the golden fixture."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import fitz

from app.insurance.golden import GoldenCaseError


MAX_EVIDENCE_PAGES = 100
NEURODIAGNOSTIC_CODES = {
    "95885", "95886", "95887", "95907", "95908", "95909", "95910", "95911", "95912", "95913",
}
NEURODIAGNOSTIC_TERMS = (
    "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain",
)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def card_matches_patient(fields: dict[str, str], profile: Any) -> bool:
    if not profile or not profile.given_name or not profile.family_name:
        return False
    tokens = {_normalized(value) for value in re.split(r"\s+", fields.get("member_name", "")) if value}
    return _normalized(profile.given_name) in tokens and _normalized(profile.family_name) in tokens


def service_lines(clinical_context: str, rate_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    condition_text = clinical_context.casefold()
    codes = {"99202", "99203", "99204", "99205"}
    if any(term in condition_text for term in NEURODIAGNOSTIC_TERMS):
        codes.update(NEURODIAGNOSTIC_CODES)
    lines = [
        {
            **line,
            "selected": line["code"] == "99204",
            "confirmed": False,
            "available": True,
            "suggested_by": "New-patient visit" if line["code"].startswith("992") else "Clinician-entered context",
        }
        for line in rate_lines
        if line["code"] in codes
    ]
    return lines


def pending_case(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "source_mode": "documents", "status": "review", "next_stage": "intake",
        "ocr": {**fields, "confirmed": False}, "clinical_context": "", "documents": {},
        "appointment": None, "coverage": None, "service_lines": [],
        "stage_decisions": [], "facts": [], "sources": [], "estimate": None,
    }


def confirm_intake(case: dict[str, Any], profile: Any, fields: dict[str, str]) -> dict[str, Any]:
    if not card_matches_patient(fields, profile):
        raise GoldenCaseError("The member name on the card must match the patient opened from the QR")
    reason = "Neurology consultation"
    source = _citation("Patient QR", "Shared Patient and Condition resources", hashlib.sha256(case["id"].encode()).hexdigest())
    case.update({
        "status": "in_progress", "next_stage": "eligibility", "ocr": {**fields, "confirmed": True},
        "appointment": {
            "fhir_r4_resource_type": "Appointment", "status": "proposed", "start": None, "end": None,
            "date": date.today().isoformat(), "service_type": "Neurology office visit",
            "appointment_type": "New patient", "reason": reason,
            "participants": ["Patient", "NMCT clinician"],
            "provider": "Neurology and Movement Center of Texas", "location": "NMCT office", "source": source,
        },
        "facts": [
            _fact("intake", "Patient and card", "QR patient name matches the reviewed member-card name", source),
        ],
    })
    return case


def set_clinical_context(case: dict[str, Any], value: str, rate_lines: list[dict[str, Any]]) -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete network review before requesting CPT suggestions")
    context = " ".join(value.split()).strip()
    if len(context) < 3:
        raise GoldenCaseError("Enter the conditions or symptoms for this anticipated visit")
    case["clinical_context"] = context
    case["service_lines"] = service_lines(context, rate_lines)
    return case


def inspect_pdf(content: bytes, filename: str, kind: str, member_id: str = "") -> dict[str, Any]:
    if not content.startswith(b"%PDF"):
        raise GoldenCaseError("Upload a searchable PDF document")
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.needs_pass or not 1 <= document.page_count <= MAX_EVIDENCE_PAGES:
            raise GoldenCaseError("Evidence PDFs must contain 1 to 100 unlocked pages")
        text = "\n".join(page.get_text() for page in document)
        pages = document.page_count
    except GoldenCaseError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The uploaded evidence is not a readable PDF") from exc
    compact = " ".join(text.casefold().split())
    if len(compact) < 80:
        raise GoldenCaseError("Upload a searchable PDF; this document has no usable text layer")
    if kind == "contract":
        valid = (
            "professional provider agreement" in compact
            and "blue cross and blue shield" in compact
            and "neurology and movement center" in compact
            and "authorized signature" in compact
        )
        if not valid:
            raise GoldenCaseError("Upload the NMCT–Blue Cross Blue Shield professional provider agreement")
        reference = "Provider agreement · NMCT and BCBS text confirmed"
    elif kind == "eligibility":
        valid = (
            ("bcbstx" in compact or "blue cross" in compact)
            and "active coverage" in compact
            and ("in network" in compact or "in-network" in compact)
            and _normalized(member_id) in _normalized(compact)
        )
        if not valid:
            raise GoldenCaseError("Upload the patient's dated BCBS eligibility and benefits response")
        reference = "Eligibility response · active coverage and network text confirmed"
    else:
        raise GoldenCaseError("Unsupported insurance evidence type")
    return {
        "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
        "observed_at": date.today().isoformat(), "reference": f"{reference} · {pages} page{'s' if pages != 1 else ''}",
    }


def add_document(case: dict[str, Any], kind: str, source: dict[str, Any]) -> dict[str, Any]:
    case.setdefault("documents", {})[kind] = source
    case["sources"] = [item for item in case.get("sources", []) if item.get("id") != kind] + [{
        key: source[key] for key in ("id", "filename", "sha256", "observed_at")
    }]
    return case


def confirm_evidence(case: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    documents = case.get("documents", {})
    if not all(kind in documents for kind in ("eligibility", "contract")):
        raise GoldenCaseError("Upload both the patient's eligibility PDF and the NMCT–BCBS provider contract")
    if values["coverage_status"] != "active" or values["network_status"] != "in_network":
        raise GoldenCaseError("This temporary fallback calculates only active, in-network NMCT/BCBS coverage")
    if values["eligibility_observed_at"] > date.today():
        raise GoldenCaseError("The eligibility review date cannot be in the future")
    if "unknown" in {values["referral_status"], values["authorization_status"]}:
        raise GoldenCaseError("Confirm both referral and prior-authorization requirements from the uploaded response")
    coverage_source, contract_source = documents["eligibility"], documents["contract"]
    coverage_source["observed_at"] = values["eligibility_observed_at"].isoformat()
    coverage = {
        "status": values["coverage_status"], "payer": values["payer"], "product": values["plan_name"],
        "coinsurance_percent": values["coinsurance_percent"], "copay_cents": values["copay_cents"],
        "individual_deductible_remaining_cents": values["individual_deductible_remaining_cents"],
        "individual_oop_remaining_cents": values["individual_oop_remaining_cents"],
        "network_status": values["network_status"], "referral_status": values["referral_status"],
        "authorization_status": values["authorization_status"], "source": coverage_source,
    }
    case["coverage"] = coverage
    case["facts"].extend((
        _fact("eligibility", "Coverage", f"Active · {values['payer']} · {values['plan_name']}", coverage_source),
        _fact("eligibility", "Individual deductible remaining", _money(values["individual_deductible_remaining_cents"]), coverage_source),
        _fact("eligibility", "Individual out-of-pocket remaining", _money(values["individual_oop_remaining_cents"]), coverage_source),
        _fact("network", "Center", "Neurology and Movement Center of Texas", contract_source),
        _fact("network", "Contract match", "NMCT · Blue Cross Blue Shield · clinician-confirmed", contract_source),
        _fact("network", "Patient network response", "In network · clinician-confirmed", coverage_source),
        _fact("authorization", "Referral", values["referral_status"].replace("_", " ").title(), coverage_source),
        _fact("authorization", "Prior authorization", values["authorization_status"].replace("_", " ").title(), coverage_source),
    ))
    case["stage_decisions"].append({
        "stage": "eligibility", "status": "passed",
        "decision": "Clinician confirmed the patient's dated BCBS eligibility and individual benefits",
        "source_mode": "documents",
    })
    case["next_stage"] = "network"
    return case


def select_services(case: dict[str, Any], codes: list[str], rate_source: dict[str, Any]) -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete the preceding stages before confirming the anticipated service")
    selected_codes = set(codes)
    selected = [line for line in case["service_lines"] if line["code"] in selected_codes]
    if len(selected) != len(selected_codes) or any(
        not line.get("available") or line.get("allowed_cents") is None for line in selected
    ):
        raise GoldenCaseError("Every selected CPT must have a reviewed NMCT contracted rate")
    office_codes = [line["code"] for line in selected if line["code"].startswith("992")]
    if len(office_codes) != 1:
        raise GoldenCaseError("Choose exactly one anticipated new-patient office-visit code")
    for line in case["service_lines"]:
        line.update(selected=line["code"] in selected_codes, confirmed=line["code"] in selected_codes)
    display = ", ".join(line["code"] for line in selected)
    case["facts"].append(_fact(
        "service", "Anticipated services", f"{display} · clinician confirmed; final coding remains post-encounter",
        rate_source,
    ))
    case["stage_decisions"].append({
        "stage": "service", "status": "passed", "decision": f"CPT candidates {display} confirmed as anticipated",
        "source_mode": "documents",
    })
    case["next_stage"] = "authorization"
    return case


def advance(case: dict[str, Any], stage: str, rate_source: dict[str, Any]) -> dict[str, Any]:
    if case.get("next_stage") != stage or stage not in {"network", "authorization", "benefits", "estimate"}:
        raise GoldenCaseError("Complete the insurance stages in order")
    coverage = case["coverage"]
    if stage == "authorization" and "unknown" in {coverage["referral_status"], coverage["authorization_status"]}:
        raise GoldenCaseError("Confirm referral and prior-authorization requirements from the uploaded response")
    decisions = {
        "network": "Clinician confirmed NMCT, BCBS contract, and patient in-network response",
        "authorization": "Clinician confirmed referral and prior-authorization evidence",
        "benefits": "Reviewed contracted rate and patient benefits calculated",
        "estimate": "Document-backed pre-visit estimate completed",
    }
    case["stage_decisions"].append({
        "stage": stage, "status": "passed", "decision": decisions[stage], "source_mode": "documents",
    })
    case["next_stage"] = {"network": "service", "authorization": "benefits", "benefits": "estimate", "estimate": None}[stage]
    if stage == "benefits":
        case["estimate"] = calculate(case)
        estimate = case["estimate"]
        case["facts"].extend((
            _fact("benefits", "Contracted allowed amount", _money(estimate["allowed_cents"]), rate_source),
            _fact("benefits", "Deductible applied", _money(estimate["deductible_applied_cents"]), coverage["source"]),
            _fact("benefits", "Coinsurance", _money(estimate["nominal_coinsurance_cents"]), coverage["source"]),
            _fact("benefits", "Copay", _money(estimate["copay_cents"]), coverage["source"]),
            _fact("benefits", "Out-of-pocket cap adjustment", _money(estimate["oop_cap_adjustment_cents"]), coverage["source"]),
        ))
    if stage == "estimate":
        case["status"] = "complete"
        estimate = case["estimate"]
        case["facts"].extend((
            _fact("estimate", "Estimated patient responsibility", _money(estimate["patient_cents"]), coverage["source"]),
            _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), rate_source),
        ))
    return case


def calculate(case: dict[str, Any]) -> dict[str, Any]:
    selected = [line for line in case["service_lines"] if line["selected"] and line["confirmed"]]
    coverage = case["coverage"]
    allowed = sum(int(line["allowed_cents"]) for line in selected)
    deductible = min(allowed, int(coverage["individual_deductible_remaining_cents"]))
    after_deductible = allowed - deductible
    coinsurance = int((
        Decimal(after_deductible) * Decimal(str(coverage["coinsurance_percent"])) / Decimal(100)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    copay = int(coverage["copay_cents"])
    nominal = min(allowed, deductible + coinsurance + copay)
    patient = min(nominal, int(coverage["individual_oop_remaining_cents"]))
    return {
        "service_code": ", ".join(line["code"] for line in selected),
        "service_codes": [line["code"] for line in selected], "allowed_cents": allowed,
        "deductible_applied_cents": deductible, "nominal_coinsurance_cents": coinsurance,
        "copay_cents": copay,
        "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
        "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]),
        "oop_cap_adjustment_cents": patient - nominal, "patient_cents": patient,
        "plan_cents": allowed - patient, "source_mode": "documents",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Document-backed estimate for one anticipated in-network service; not a guarantee of payment or final coding.",
    }


def _citation(filename: str, reference: str, sha256: str, observed_at: str | None = None) -> dict[str, str]:
    return {
        "source_filename": filename, "sha256": sha256, "reference": reference,
        "observed_at": observed_at or date.today().isoformat(),
    }


def _fact(stage: str, label: str, value: str, source: dict[str, Any]) -> dict[str, Any]:
    citation = source if "source_filename" in source else _citation(
        source["filename"], source["reference"], source["sha256"], source["observed_at"],
    )
    return {"stage": stage, "label": label, "value": value, "citation": citation, "source_mode": "documents"}


def _money(cents: int) -> str:
    sign = "−" if cents < 0 else ""
    return f"{sign}${Decimal(abs(cents)) / Decimal(100):,.2f}"
