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
BOTULINUM_TERMS = ("botox", "botulinum", "dystonia", "spasticity", "chronic migraine")
UNPRICED_CANDIDATES = {
    "64612": "Chemodenervation of facial/trigeminal-innervated muscles",
    "64615": "Chemodenervation for chronic migraine",
    "64642": "Chemodenervation of one extremity muscle group",
    "64643": "Chemodenervation of each additional extremity muscle group",
}


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
    lines: list[dict[str, Any]] = [
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
    if any(term in condition_text for term in BOTULINUM_TERMS):
        lines.extend({
            "code": code, "description": description, "allowed_cents": None, "selected": False,
            "confirmed": False, "available": False, "suggested_by": "Clinician-entered context",
            "evidence_note": "Procedure, units, drug code, and a reviewed NMCT rate are required before it can be estimated.",
        } for code, description in UNPRICED_CANDIDATES.items())
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
        page_text = [(number, page.get_text()) for number, page in enumerate(document, start=1)]
        text = "\n".join(value for _, value in page_text)
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
        participation_pages = _pages_with(page_text, "blue choice ppo")
        compensation_pages = _pages_with(page_text, "compensation")
        participation = f"Blue Choice PPO participation p. {_page_list(participation_pages)}" if participation_pages else "provider participation terms; Blue Choice PPO not explicitly located"
        compensation = f"compensation terms p. {_page_list(compensation_pages)}" if compensation_pages else "compensation terms require clinician review"
        reference = f"Provider agreement · {participation}; {compensation}"
        findings = {
            "provider_network": "participating_blue_choice_ppo" if participation_pages else "participation_requires_review",
            "participation_pages": participation_pages, "compensation_pages": compensation_pages,
        }
    elif kind == "eligibility":
        valid = (
            ("bcbstx" in compact or "blue cross" in compact)
            and "active coverage" in compact
            and _normalized(member_id) in _normalized(compact)
        )
        if not valid:
            raise GoldenCaseError("Upload the patient's dated BCBS eligibility and benefits response")
        in_network_pages = _pages_with(page_text, "in network") + _pages_with(page_text, "in-network")
        out_network_pages = _pages_with(page_text, "out of network") + _pages_with(page_text, "out-of-network")
        network_text = "in-network" if in_network_pages else "out-of-network" if out_network_pages else "network status requires clinician review"
        reference = f"Eligibility response · active coverage; {network_text} p. {_page_list(in_network_pages or out_network_pages) if (in_network_pages or out_network_pages) else 'not stated'}"
        findings = {"network_pages": sorted(set(in_network_pages + out_network_pages))}
    else:
        raise GoldenCaseError("Unsupported insurance evidence type")
    return {
        "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
        "observed_at": date.today().isoformat(), "reference": f"{reference} · {pages} page{'s' if pages != 1 else ''}", "findings": findings,
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
    if values["coverage_status"] != "active":
        raise GoldenCaseError("An estimate requires active coverage in the dated eligibility response")
    if values["eligibility_observed_at"] > date.today():
        raise GoldenCaseError("The eligibility review date cannot be in the future")
    if "unknown" in {values["referral_status"], values["authorization_status"]}:
        raise GoldenCaseError("Confirm both referral and prior-authorization requirements from the uploaded response")
    if values["network_status"] == "out_of_network":
        if values["out_of_network_benefit_status"] == "unknown":
            raise GoldenCaseError("Confirm whether the eligibility response covers out-of-network benefits")
        if values["out_of_network_benefit_status"] == "covered" and not values.get("out_of_network_allowed_cents"):
            raise GoldenCaseError("Enter the out-of-network allowed amount from the eligibility response or approved quote")
    coverage_source, contract_source = documents["eligibility"], documents["contract"]
    coverage_source["observed_at"] = values["eligibility_observed_at"].isoformat()
    coverage = {
        "status": values["coverage_status"], "payer": values["payer"], "product": values["plan_name"],
        "coinsurance_percent": values["coinsurance_percent"], "copay_cents": values["copay_cents"],
        "individual_deductible_remaining_cents": values["individual_deductible_remaining_cents"],
        "individual_oop_remaining_cents": values["individual_oop_remaining_cents"],
        "network_status": values["network_status"], "referral_status": values["referral_status"],
        "authorization_status": values["authorization_status"], "source": coverage_source,
            "out_of_network_benefit_status": values.get("out_of_network_benefit_status", "unknown"),
        "out_of_network_allowed_cents": values.get("out_of_network_allowed_cents"),
    }
    case["coverage"] = coverage
    case["facts"].extend((
        _fact("eligibility", "Coverage", f"Active · {values['payer']} · {values['plan_name']}", coverage_source),
        _fact("eligibility", "Individual deductible remaining", _money(values["individual_deductible_remaining_cents"]), coverage_source),
        _fact("eligibility", "Individual out-of-pocket remaining", _money(values["individual_oop_remaining_cents"]), coverage_source),
        _fact("network", "Center", "Neurology and Movement Center of Texas", contract_source),
        _fact("network", "Contract match", "NMCT participates in Blue Choice PPO", contract_source),
        _fact("network", "Patient network response", values["network_status"].replace("_", " ").title() + " · clinician-confirmed", coverage_source),
        _fact("authorization", "Referral", values["referral_status"].replace("_", " ").title(), coverage_source),
        _fact("authorization", "Prior authorization", values["authorization_status"].replace("_", " ").title(), coverage_source),
    ))
    case["stage_decisions"].append({
        "stage": "eligibility", "status": "passed",
        "decision": "Clinician confirmed the patient's dated BCBS eligibility, individual benefits, and network response",
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
        "network": f"Clinician confirmed NMCT's Blue Choice PPO contract and patient {coverage['network_status'].replace('_', '-')} response",
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
        if estimate["estimate_available"]:
            label = "Contracted allowed amount" if coverage["network_status"] == "in_network" else "Out-of-network allowed amount"
            source = rate_source if coverage["network_status"] == "in_network" else coverage["source"]
            case["facts"].extend((
                _fact("benefits", label, _money(estimate["allowed_cents"]), source),
                _fact("benefits", "Deductible applied", _money(estimate["deductible_applied_cents"]), coverage["source"]),
                _fact("benefits", "Coinsurance", _money(estimate["nominal_coinsurance_cents"]), coverage["source"]),
                _fact("benefits", "Copay", _money(estimate["copay_cents"]), coverage["source"]),
                _fact("benefits", "Out-of-pocket cap adjustment", _money(estimate["oop_cap_adjustment_cents"]), coverage["source"]),
            ))
        else:
            case["facts"].append(_fact("benefits", "Estimate status", estimate["unavailable_reason"], coverage["source"]))
    if stage == "estimate":
        case["status"] = "complete"
        estimate = case["estimate"]
        if estimate["estimate_available"]:
            case["facts"].extend((
                _fact("estimate", "Estimated patient responsibility", _money(estimate["patient_cents"]), coverage["source"]),
                _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), rate_source),
            ))
        else:
            case["facts"].append(_fact("estimate", "Collection amount", "Not estimated", coverage["source"]))
    return case


def calculate(case: dict[str, Any]) -> dict[str, Any]:
    selected = [line for line in case["service_lines"] if line["selected"] and line["confirmed"]]
    coverage = case["coverage"]
    if coverage["network_status"] == "out_of_network" and coverage["out_of_network_benefit_status"] != "covered":
        return {
            "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected],
            "estimate_available": False, "unavailable_reason": "The eligibility evidence does not document a covered out-of-network benefit for this service.",
            "allowed_cents": None, "deductible_applied_cents": None, "nominal_coinsurance_cents": None,
            "copay_cents": None, "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
            "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]), "oop_cap_adjustment_cents": None,
            "patient_cents": None, "plan_cents": None, "source_mode": "documents", "calculated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "No collection estimate is available without documented covered out-of-network benefits; do not substitute the in-network NMCT fee schedule.",
        }
    allowed = int(coverage["out_of_network_allowed_cents"]) if coverage["network_status"] == "out_of_network" else sum(int(line["allowed_cents"]) for line in selected)
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
        "estimate_available": True,
        "deductible_applied_cents": deductible, "nominal_coinsurance_cents": coinsurance,
        "copay_cents": copay,
        "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
        "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]),
        "oop_cap_adjustment_cents": patient - nominal, "patient_cents": patient,
        "plan_cents": allowed - patient, "source_mode": "documents",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Document-backed estimate for anticipated services; not a guarantee of payment or final coding.",
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


def _pages_with(page_text: list[tuple[int, str]], phrase: str) -> list[int]:
    return [number for number, text in page_text if phrase in text.casefold()]


def _page_list(pages: list[int]) -> str:
    return ", ".join(str(page) for page in sorted(set(pages))[:6])
