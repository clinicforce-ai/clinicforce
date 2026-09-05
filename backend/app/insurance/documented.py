"""Temporary document-backed NMCT/BCBS fallback for patients outside the golden fixture."""

from __future__ import annotations

import hashlib
import csv
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from pathlib import Path
from typing import Any

import fitz

from app.insurance.golden import GoldenCaseError


MAX_EVIDENCE_PAGES = 100
# ponytail: a compact, clinician-reviewed demo catalog; replace with a licensed,
# versioned CPT/HCPCS terminology service before claiming universal coverage.
LOCAL_CPT_CANDIDATES = (
    ("95885", "Needle EMG, limited", ("emg", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95886", "Needle EMG, complete", ("emg", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95887", "Needle EMG, non-extremity", ("emg", "neuropathy", "radiculopathy", "nerve pain")),
    ("95907", "Nerve conduction study, 1–2 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95908", "Nerve conduction study, 3–4 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95909", "Nerve conduction study, 5–6 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95910", "Nerve conduction study, 7–8 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95911", "Nerve conduction study, 9–10 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95912", "Nerve conduction study, 11–12 studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("95913", "Nerve conduction study, 13 or more studies", ("emg", "ncs", "neuropathy", "carpal tunnel", "radiculopathy", "numbness", "tingling", "paresthesia", "nerve pain")),
    ("64612", "Chemodenervation, facial muscles", ("botox", "botulinum", "blepharospasm", "hemifacial spasm", "dystonia")),
    ("64615", "Chemodenervation for chronic migraine", ("botox", "botulinum", "chronic migraine", "migraine")),
    ("64642", "Chemodenervation, one extremity muscle group", ("botox", "botulinum", "spasticity", "dystonia")),
    ("64643", "Chemodenervation, each additional extremity muscle group", ("botox", "botulinum", "spasticity", "dystonia")),
    ("J0585", "OnabotulinumtoxinA, per unit", ("botox", "onabotulinumtoxin", "chronic migraine", "migraine", "spasticity", "dystonia")),
    ("76881", "Extremity ultrasound, complete", ("sonography", "ultrasound", "ultrasonography", "musculoskeletal ultrasound")),
    ("76882", "Extremity ultrasound, limited", ("sonography", "ultrasound", "ultrasonography", "musculoskeletal ultrasound")),
    ("70551", "MRI of brain without contrast", ("brain", "headache", "migraine", "seizure", "stroke", "brain lesion")),
    ("70552", "MRI of brain with contrast", ("brain", "brain tumor", "brain lesion")),
    ("70553", "MRI of brain with and without contrast", ("brain", "brain tumor", "brain lesion")),
    ("95816", "Routine EEG", ("brain", "seizure", "epilepsy", "blackout", "loss of consciousness")),
    ("93000", "Twelve-lead electrocardiogram", ("heart", "cardiac", "chest pain", "palpitation", "arrhythmia", "syncope")),
    ("93306", "Complete transthoracic echocardiogram", ("heart", "cardiac", "murmur", "heart failure", "shortness of breath")),
    ("93224", "Holter monitor, 24 to 48 hours", ("heart", "cardiac", "palpitation", "arrhythmia", "syncope")),
    ("93015", "Cardiovascular stress test", ("heart", "cardiac", "chest pain", "exercise intolerance")),
    ("93970", "Duplex ultrasound of extremity veins, complete", ("leg", "calf", "leg swelling", "edema", "dvt", "deep vein thrombosis")),
    ("93971", "Duplex ultrasound of an extremity vein, limited", ("leg", "calf", "leg swelling", "edema", "dvt", "deep vein thrombosis")),
    ("93922", "Noninvasive arterial study of extremities", ("leg", "claudication", "peripheral artery", "poor circulation")),
)
PLAIN_CPT_EXPLANATIONS = {
    "99202": "New-patient office visit, about 15 minutes, for a straightforward problem.",
    "99203": "New-patient office visit, about 30 minutes, for a low-complexity problem.",
    "99204": "New-patient office visit, about 45 minutes, for a moderately complex problem.",
    "99205": "New-patient office visit, about 60 minutes, for a highly complex problem.",
    "99211": "Brief established-patient office visit that may not require the clinician to see the patient.",
    "99212": "Established-patient office visit, about 10 minutes, for a straightforward problem.",
    "99213": "Established-patient office visit, about 20 minutes, for a low-complexity problem.",
    "99214": "Established-patient office visit, about 30 minutes, for a moderately complex problem.",
    "99215": "Established-patient office visit, about 40 minutes, for a highly complex problem.",
    "95885": "Needle EMG that checks electrical activity in a limited area of a limb or spine.",
    "95886": "Needle EMG that checks electrical activity in a complete limb or spine area.",
    "95887": "Needle EMG that checks electrical activity in non-limb muscles alongside nerve testing.",
    "95907": "Nerve-conduction testing of 1–2 nerves to assess nerve signal problems.",
    "95908": "Nerve-conduction testing of 3–4 nerves to assess nerve signal problems.",
    "95909": "Nerve-conduction testing of 5–6 nerves to assess nerve signal problems.",
    "95910": "Nerve-conduction testing of 7–8 nerves to assess nerve signal problems.",
    "95911": "Nerve-conduction testing of 9–10 nerves to assess nerve signal problems.",
    "95912": "Nerve-conduction testing of 11–12 nerves to assess nerve signal problems.",
    "95913": "Nerve-conduction testing of 13 or more nerves to assess nerve signal problems.",
    "J0585": "The Botox medicine itself; the final number of units is documented after treatment.",
}

NCS_CODES = {f"959{number:02d}" for number in range(7, 14)}
EMG_ADD_ON_CODES = {"95885", "95886", "95887"}
BOTULINUM_ADMIN_CODES = {"64612", "64615", "64642", "64643"}


def _service_item(code: str, description: str = "") -> tuple[str, str, str]:
    if code.startswith("992"):
        return "office-visit", "Office visit", "scheduled"
    if code in NCS_CODES | EMG_ADD_ON_CODES:
        return "emg-ncs", "EMG / nerve-conduction study", "if_elected"
    if code in BOTULINUM_ADMIN_CODES | {"J0585"}:
        return "botox-treatment", "Botox treatment", "if_elected"
    if code in {"76881", "76882"}:
        return "extremity-ultrasound", "Extremity ultrasound", "if_elected"
    if code in {"70551", "70552", "70553"}:
        return "brain-mri", "Brain MRI", "if_elected"
    if code == "95816":
        return "eeg", "EEG", "if_elected"
    if code in {"93000", "93306", "93224", "93015"}:
        return "cardiac-testing", "Cardiac testing", "if_elected"
    if code in {"93970", "93971", "93922"}:
        return "vascular-testing", "Vascular testing", "if_elected"
    return f"procedure-{code.casefold()}", description or f"Procedure {code}", "if_elected"

DOCUMENT_STAGES = {
    "contract": "network",
    "coverage": "network",
    "fee_schedule": "network",
    "appointment_email": "service",
    "pricing_guide": "service",
}

BCBSTX_REFERRAL_URL = "https://www.bcbstx.com/provider/education/education/tools/availity-authorizations"
NEUROPATHY_GUIDANCE = {
    "95907": "Possible for a very limited 1–2 nerve study; the physician decides the nerve count after examination.",
    "95908": "Possible for a 3–4 nerve study; the physician decides the nerve count after examination.",
    "95909": "Possible for a 5–6 nerve study; the physician decides the nerve count after examination.",
    "95910": "The local research lists this for a one-limb or single-nerve pattern.",
    "95911": "The local research lists this as a common both-leg neuropathy study.",
    "95912": "The local research reserves this larger study for broad symptoms.",
    "95913": "The local research reserves this largest study for broad or complex symptoms.",
    "95885": "A limited needle EMG may accompany nerve-conduction testing when clinically indicated.",
    "95886": "A complete-limb needle EMG may accompany nerve-conduction testing when clinically indicated.",
    "95887": "A non-extremity needle EMG applies only when the examined muscles are not in an arm or leg.",
}

CASH_PACKAGE_LABELS = (
    ("Carpal Tunnel Evaluation — One Hand", "carpal tunnel evaluation - one hand", "Numbness, tingling, or hand symptoms on one side"),
    ("Carpal Tunnel Evaluation — Both Hands", "carpal tunnel evaluation - both hands", "Symptoms involving both hands or comparison testing"),
    ("Neck-Related Nerve Problem — One Arm", "neck-related nerve problem - one arm", "Pain, weakness, numbness, or tingling from the neck into one arm"),
    ("Back-Related Nerve Problem — One Leg", "back-related nerve problem - one leg", "Pain, weakness, numbness, or tingling from the back into one leg"),
    ("Neuropathy Evaluation — Both Legs", "neuropathy evaluation - both legs", "Burning, numbness, tingling, or sensory changes in both feet or legs"),
    ("Comprehensive Full Study — Arms and Legs", "comprehensive full study - arms and legs", "Widespread or complex nerve or muscle symptoms"),
    ("Single Nerve Injury Evaluation", "single nerve injury evaluation", "Suspected injury to one specific nerve"),
)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def card_matches_patient(fields: dict[str, str], profile: Any) -> bool:
    if not profile or not profile.given_name or not profile.family_name:
        return False
    tokens = {_normalized(value) for value in re.split(r"\s+", fields.get("member_name", "")) if value}
    return _normalized(profile.given_name) in tokens and _normalized(profile.family_name) in tokens


def _visit_reason(code: str, visit_type: str) -> str:
    if visit_type == "new" and code in {"99204", "99205"}:
        return "The clinic research identifies 99204 or 99205 as common possibilities for a complex new specialist visit; final coding depends on the completed visit."
    return f"This is one possible {visit_type}-patient office-visit level; the clinician chooses the final level after the visit."


def service_lines(
    clinical_context: str,
    rate_lines: list[dict[str, Any]],
    visit_type: str = "new",
    authorizations: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    condition_text = clinical_context.casefold()
    codes = {"99202", "99203", "99204", "99205"} if visit_type == "new" else {"99211", "99212", "99213", "99214", "99215"}
    explicit_codes = set(re.findall(r"\b(?:[A-Z]\d{4}|\d{5})\b", clinical_context.upper()))
    matched_candidates = [candidate for candidate in LOCAL_CPT_CANDIDATES if any(term in condition_text for term in candidate[2])]
    codes.update(explicit_codes | {code for code, _, _ in matched_candidates})
    authorizations = authorizations or {}
    lines: list[dict[str, Any]] = [
        {
            **line,
            "service_item_id": _service_item(line["code"], line["description"])[0],
            "service_item_name": _service_item(line["code"], line["description"])[1],
            "service_status": _service_item(line["code"], line["description"])[2],
            "selected": False,
            "confirmed": False,
            "available": True,
            "suggested_by": f"{visit_type.title()}-patient visit" if line["code"].startswith("992") else "Clinician-entered context",
            "plain_description": PLAIN_CPT_EXPLANATIONS.get(line["code"], line["description"]),
            "recommendation_reason": (
                _visit_reason(line["code"], visit_type)
                if line["code"].startswith("992") else NEUROPATHY_GUIDANCE.get(line["code"])
            ),
            "authorization_status": authorizations.get(line["code"], {}).get("status", "not_found"),
            "authorization_reference": authorizations.get(line["code"], {}).get("reference", "This CPT was not included in the uploaded coverage query."),
        }
        for line in rate_lines
        if line["code"] in codes
    ]
    priced_codes = {line["code"] for line in lines}
    lines.extend({
        "code": code, "description": description, "allowed_cents": None, "selected": False,
        "service_item_id": _service_item(code)[0], "service_item_name": _service_item(code)[1],
        "service_status": _service_item(code)[2],
        "confirmed": False, "available": True, "suggested_by": "Clinician-entered context",
        "evidence_note": "Local CPT catalog candidate. Clinician confirmation and a reviewed NMCT rate are required before pricing.",
        "plain_description": PLAIN_CPT_EXPLANATIONS.get(code, description),
        "recommendation_reason": NEUROPATHY_GUIDANCE.get(code),
        "authorization_status": authorizations.get(code, {}).get("status", "not_found"),
        "authorization_reference": authorizations.get(code, {}).get("reference", "This CPT was not included in the uploaded coverage query."),
    } for code, description, _ in matched_candidates if code not in priced_codes)
    lines.extend({
        "code": code, "description": "Clinician-entered procedure code", "allowed_cents": None,
        "service_item_id": _service_item(code)[0], "service_item_name": _service_item(code)[1],
        "service_status": "if_elected", "selected": False, "confirmed": False, "available": True,
        "suggested_by": "Exact code entered by clinician",
        "evidence_note": "This code is not in the uploaded fee schedule. Add a reviewed rate before estimating it.",
        "plain_description": "Exact CPT/HCPCS code entered for clinician review.",
        "recommendation_reason": "Staff entered this code explicitly; ClinicForce did not infer it from the visit reason.",
        "authorization_status": authorizations.get(code, {}).get("status", "not_found"),
        "authorization_reference": authorizations.get(code, {}).get("reference", "This code was not included in the uploaded coverage query."),
    } for code in sorted(explicit_codes - priced_codes - {candidate[0] for candidate in matched_candidates}))
    return lines


def case_rate_lines(case: dict[str, Any]) -> list[dict[str, Any]]:
    return case.get("documents", {}).get("fee_schedule", {}).get("findings", {}).get("rate_lines", [])


def case_authorizations(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = case.get("documents", {}).get("coverage", {}).get("findings", {}).get("authorizations", [])
    return {row["code"]: row for row in rows if row.get("code")}


def pending_case(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "source_mode": "documents", "payment_path": "insurance", "status": "review", "next_stage": "intake",
        "ocr": {**fields, "confirmed": False}, "clinical_context": "", "documents": {},
        "appointment": None, "coverage": None, "service_lines": [], "visit_type": "new",
        "stage_decisions": [], "facts": [], "sources": [], "estimate": None,
    }


def self_pay_case() -> dict[str, Any]:
    return {
        "source_mode": "documents", "payment_path": "self_pay", "status": "in_progress", "next_stage": "service",
        "ocr": {"member_name": "", "member_id": "", "group_number": "", "confirmed": False},
        "clinical_context": "", "documents": {}, "appointment": None, "coverage": None,
        "service_lines": [], "visit_type": "new", "stage_decisions": [], "facts": [],
        "sources": [], "estimate": None,
    }


def confirm_intake(case: dict[str, Any], profile: Any, fields: dict[str, str]) -> dict[str, Any]:
    if not card_matches_patient(fields, profile):
        raise GoldenCaseError("The member name on the card must match the patient opened from the QR")
    source = case.get("card_source")
    if not source:
        raise GoldenCaseError("The uploaded insurance card source is missing; start a new review")
    case.update({
        "status": "in_progress", "next_stage": "eligibility", "ocr": {**fields, "confirmed": True},
        "appointment": None,
        "facts": [
            _fact("intake", "Patient and card", "The uploaded card name matches the patient opened from the QR", source),
        ],
    })
    return case


def set_clinical_context(case: dict[str, Any], value: str, rate_lines: list[dict[str, Any]], visit_type: str = "new") -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete network review before requesting CPT suggestions")
    context = " ".join(value.split()).strip()
    if len(context) < 3:
        raise GoldenCaseError("Enter the conditions or symptoms for this anticipated visit")
    case["clinical_context"] = context
    case["visit_type"] = visit_type
    case["service_lines"] = service_lines(context, rate_lines, visit_type, case_authorizations(case))
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
        compensation_pages = _pages_with(page_text, "lesser of")
        participation = f"Blue Choice PPO participation p. {_page_list(participation_pages)}" if participation_pages else "provider participation terms; Blue Choice PPO not explicitly located"
        compensation = f"fee-schedule terms p. {_page_list(compensation_pages)}" if compensation_pages else "fee-schedule terms require clinician review"
        reference = f"Provider agreement · {participation}; {compensation}"
        findings = {
            "provider_network": "participating_blue_choice_ppo" if participation_pages else "participation_requires_review",
            "participation_pages": participation_pages, "compensation_pages": compensation_pages, "pages_scanned": pages,
        }
    elif kind in {"coverage", "eligibility"}:
        normalized_member_id = _normalized(member_id)
        valid = (
            ("bcbstx" in compact or "blue cross" in compact)
            and "active coverage" in compact
            and bool(normalized_member_id)
            and normalized_member_id in _normalized(compact)
        )
        if not valid:
            raise GoldenCaseError("Upload the patient's dated BCBS eligibility and benefits response")
        in_network_pages = _pages_with(page_text, "in network") + _pages_with(page_text, "in-network")
        out_network_pages = _pages_with(page_text, "out of network") + _pages_with(page_text, "out-of-network")
        network_text = "in-network" if in_network_pages else "out-of-network" if out_network_pages else "network status requires clinician review"
        reference = f"Eligibility response · active coverage; {network_text} p. {_page_list(in_network_pages or out_network_pages) if (in_network_pages or out_network_pages) else 'not stated'}"
        suggested = _suggested_coverage(text)
        page_phrases = {
            "active_page": "active coverage", "plan_page": "plan / product",
            "network_page": "in network", "benefit_page": "annual deductible",
        }
        for key, phrase in page_phrases.items():
            matching_pages = _pages_with(page_text, phrase)
            if matching_pages:
                suggested[key] = matching_pages[0]
        findings = {
            "network_pages": sorted(set(in_network_pages + out_network_pages)),
            "pages_scanned": pages,
            "suggested_coverage": suggested,
            "authorizations": _authorization_findings(page_text),
        }
    elif kind == "appointment_email":
        valid = "shmso.com mail" in compact and "new patient" in compact and "neuropathy" in compact
        if not valid:
            raise GoldenCaseError("Upload the appointment email thread that states the visit type and reason")
        evidence_pages = sorted(set(_pages_with(page_text, "new patient") + _pages_with(page_text, "neuropathy")))
        reference = f"Appointment email · new patient and neuropathy/EMG discussion p. {_page_list(evidence_pages)}"
        findings = {
            "visit_type": "new", "clinical_context": "Neuropathy with numbness or tingling; EMG/NCS verification requested",
            "evidence_pages": evidence_pages, "pages_scanned": pages,
        }
    elif kind == "pricing_guide":
        valid = "ncs/emg cash-pay pricing guide" in compact and "neuropathy evaluation" in compact
        if not valid:
            raise GoldenCaseError("Upload the NMCT NCS/EMG cash-pay pricing guide")
        reference = "NCS/EMG cash-pay guide · symptom-to-study package examples p. 1; physician decides final study p. 1"
        findings = {
            "pages_scanned": pages,
            "use": "Clinical scope and cash comparison only; insured CPT rates come from the uploaded fee schedule.",
            "cash_packages": _cash_packages(compact),
        }
    else:
        raise GoldenCaseError("Unsupported insurance evidence type")
    return {
        "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
        "observed_at": _observed_now(), "reference": f"{reference} · {pages} page{'s' if pages != 1 else ''}", "findings": findings,
    }


def inspect_csv(content: bytes, filename: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8-sig")
        rows = list(csv.DictReader(StringIO(text)))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise GoldenCaseError("The fee schedule is not a readable CSV file") from exc
    required = {"Procedure Code", "Description", "Network", "Amount"}
    if not rows or not required.issubset(rows[0]):
        raise GoldenCaseError("Upload the Availity fee schedule CSV with procedure code, description, network, and amount columns")
    rate_lines = []
    for row in rows:
        code, amount = (row.get("Procedure Code") or "").strip(), (row.get("Amount") or "").strip()
        if not re.fullmatch(r"[A-Z0-9]{5,8}", code) or not amount.startswith("$"):
            continue
        try:
            cents = int((Decimal(amount.removeprefix("$").replace(",", "")) * 100).quantize(Decimal("1")))
        except Exception as exc:
            raise GoldenCaseError(f"The fee schedule amount for CPT {code} is not valid") from exc
        rate_lines.append({"code": code, "description": (row.get("Description") or code).strip(), "allowed_cents": cents})
    if not rate_lines or not any((row.get("Network") or "").strip() == "Blue Choice PPO" for row in rows):
        raise GoldenCaseError("Upload the NMCT Blue Choice PPO fee schedule CSV")
    timestamp = next(((row.get("Description") or "").strip() for row in rows if (row.get("Procedure Code") or "").strip() == "Time Stamp"), "not stated")
    return {
        "id": "fee_schedule", "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
        "observed_at": _observed_now(),
        "reference": f"Availity fee schedule · Blue Choice PPO · {len(rate_lines)} CPT rates · generated {timestamp}",
        "findings": {"rate_lines": rate_lines, "rate_count": len(rate_lines), "network": "Blue Choice PPO", "generated_at": timestamp},
    }


def add_document(case: dict[str, Any], kind: str, source: dict[str, Any]) -> dict[str, Any]:
    # This fallback also covers future API adapters that produce the same source shape.
    source.setdefault("observed_at", _observed_now())
    case.setdefault("documents", {})[kind] = source
    case["sources"] = [item for item in case.get("sources", []) if item.get("id") != kind] + [{
        key: source[key] for key in ("id", "filename", "sha256", "observed_at")
    }]
    if kind == "appointment_email":
        findings = source["findings"]
        case["clinical_context"], case["visit_type"] = findings["clinical_context"], findings["visit_type"]
        case["appointment"] = {
            "fhir_r4_resource_type": "Appointment", "status": "proposed", "start": None, "end": None,
            "date": "Not stated in uploaded email", "service_type": "New patient visit",
            "appointment_type": "New patient", "reason": findings["clinical_context"],
            "participants": ["Patient", "Clinician"], "provider": "Not stated in uploaded email",
            "location": "Not stated in uploaded email", "source": _citation(
                source["filename"], source["reference"], source["sha256"], source["observed_at"],
            ),
        }
    if kind in {"contract", "coverage"}:
        _apply_network_evidence(case)
    if case.get("payment_path") == "self_pay" and kind == "pricing_guide":
        case.update(status="complete", next_stage=None)
    return case


def _apply_network_evidence(case: dict[str, Any]) -> None:
    documents = case.get("documents", {})
    if not all(kind in documents for kind in ("contract", "coverage")):
        return
    contract, coverage_source = documents["contract"], documents["coverage"]
    suggested = coverage_source.get("findings", {}).get("suggested_coverage", {})
    required = {
        "coverage_status", "network_status", "plan_name", "deductible_remaining_cents", "oop_remaining_cents",
        "active_page", "plan_page", "network_page", "benefit_page",
    }
    if missing := required.difference(suggested):
        labels = ", ".join(sorted(missing))
        raise GoldenCaseError(f"The uploaded Availity response does not clearly state: {labels}")
    plan_name = suggested["plan_name"].casefold()
    if "ppo" not in plan_name and "preferred provider" not in plan_name:
        raise GoldenCaseError("The uploaded Availity response does not show the BCBSTX PPO plan used by this workflow")
    contract_match = contract.get("findings", {}).get("provider_network") == "participating_blue_choice_ppo"
    if not contract_match:
        raise GoldenCaseError("The uploaded contract does not explicitly show NMCT participating in Blue Choice PPO")
    if not contract.get("findings", {}).get("compensation_pages"):
        raise GoldenCaseError("The uploaded contract does not clearly show the fee-schedule compensation terms")
    patient_network = suggested.get("network_status")
    network_status = "in_network" if contract_match and patient_network == "in_network" else "out_of_network"
    card_percent = re.search(r"(\d{1,3})%", case.get("ocr", {}).get("coinsurance", ""))
    case["coverage"] = {
        "status": suggested["coverage_status"], "payer": "BCBSTX",
        "product": suggested["plan_name"],
        "coinsurance_percent": int(card_percent.group(1)) if card_percent else None, "copay_cents": None,
        "individual_deductible_remaining_cents": suggested["deductible_remaining_cents"],
        "individual_oop_remaining_cents": suggested["oop_remaining_cents"],
        "network_status": network_status, "referral_status": "unknown", "authorization_status": "code_specific",
        "out_of_network_benefit_status": suggested.get("out_of_network_benefit_status", "unknown"),
        "out_of_network_allowed_cents": None, "source": coverage_source,
    }
    contract_page = contract["findings"]["participation_pages"][0]
    compensation_page = (contract["findings"].get("compensation_pages") or [contract_page])[0]
    benefit_page = suggested["benefit_page"]
    facts = [
        _fact("network", "Coverage status", "Active medical coverage for this member", _with_reference(coverage_source, f"page {suggested['active_page']} · Member Status: Active Coverage"), "This confirms the member's medical plan was active when the uploaded eligibility response was produced."),
        _fact("network", "Plan used for this review", suggested["plan_name"], _with_reference(coverage_source, f"page {suggested['plan_page']} · uploaded Plan / Product field"), "This identifies the patient's plan so it can be compared with the clinic's participation agreement."),
        _fact("network", "Provider agreement", "NMCT signed participation terms for the Blue Choice PPO provider network", _with_reference(contract, f"page {contract_page} · signed Blue Choice PPO Provider Network participation row"), "This shows the clinic agreed to participate in the same Blue Choice PPO network named in the patient's response."),
        _fact("network", "Contracted-price rule", "For covered services, NMCT accepts the lower of its billed charge or the current BCBSTX fee-schedule amount, minus applicable patient cost share", _with_reference(contract, f"page {compensation_page} · uploaded compensation terms"), "This means the reviewed fee schedule sets the starting price, and the patient's benefits determine how that price is split."),
        _fact("network", "Patient network result", f"{network_status.replace('_', ' ').title()} for this uploaded BCBSTX response", _with_reference(coverage_source, f"page {suggested['network_page']} · uploaded network status and place of service"), f"Because the patient response and clinic agreement match, {network_status.replace('_', '-')} benefit terms apply to this estimate."),
        _fact("network", "Individual deductible remaining", _money(suggested["deductible_remaining_cents"]), _with_reference(coverage_source, f"page {benefit_page} · Annual Deductible, Individual Remaining"), "This is the remaining individual deductible that may be paid before coinsurance for covered services."),
        _fact("network", "Individual out-of-pocket remaining", _money(suggested["oop_remaining_cents"]), _with_reference(coverage_source, f"page {benefit_page} · Out Of Pocket, Individual Remaining"), "This is the most additional covered cost shown for the individual before the plan's out-of-pocket limit is reached."),
    ]
    if suggested.get("accumulators_met_percent") == 100:
        facts.append(_fact(
            "network", "What the 100% means",
            "The individual deductible and out-of-pocket limits shown are 100% met. It does not mean every service is automatically covered at 100%.",
            _with_reference(coverage_source, f"page {benefit_page} · individual accumulator columns show 100% and $0 Remaining"),
            "The 100% describes progress toward the annual limits, not a promise that every medical service is paid in full.",
        ))
    case["facts"] = [fact for fact in case.get("facts", []) if fact.get("stage") != "network"] + facts


def select_services(case: dict[str, Any], codes: list[str], rate_source: dict[str, Any]) -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete the preceding stages before confirming the anticipated service")
    selected_codes = set(codes)
    selected = [line for line in case["service_lines"] if line["code"] in selected_codes]
    if len(selected) != len(selected_codes):
        raise GoldenCaseError("Every selected CPT must be a suggested candidate")
    office_codes = [line["code"] for line in selected if line["code"].startswith("992")]
    if len(office_codes) != 1:
        raise GoldenCaseError("Choose exactly one anticipated office-visit code")
    ncs_codes = selected_codes & NCS_CODES
    if len(ncs_codes) > 1:
        raise GoldenCaseError(
            "Choose one nerve-conduction range (95907–95913) based on the total studies expected; "
            "the ranges are alternatives, not separate procedures."
        )
    if selected_codes & EMG_ADD_ON_CODES and not ncs_codes:
        raise GoldenCaseError("The selected EMG add-on requires one nerve-conduction base code (95907–95913) for the same day.")
    botox_admin = selected_codes & BOTULINUM_ADMIN_CODES
    if len(botox_admin - {"64643"}) > 1:
        raise GoldenCaseError("Choose one primary Botox administration code for the treated condition.")
    if "64643" in selected_codes and "64642" not in selected_codes:
        raise GoldenCaseError("CPT 64643 is an additional-extremity code and requires the primary 64642 service.")
    if bool(botox_admin) != ("J0585" in selected_codes):
        raise GoldenCaseError("A Botox service requires both an administration procedure and HCPCS J0585 for the medicine.")
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
    if case.get("next_stage") != stage or stage not in {"eligibility", "network", "authorization", "benefits", "estimate"}:
        raise GoldenCaseError("Complete the insurance stages in order")
    if stage == "eligibility":
        if not case.get("ocr", {}).get("confirmed"):
            raise GoldenCaseError("Confirm the insurance card details first")
        source = case.get("card_source")
        if not source:
            raise GoldenCaseError("The uploaded insurance card source is missing; start a new review")
        case["facts"] = [fact for fact in case.get("facts", []) if fact.get("stage") != "eligibility"] + [
            _fact("eligibility", "Insurance card check", "Valid card: the member name matches the patient opened from the QR", _with_reference(source, "uploaded card · member name matched to the shared patient")),
            _fact("eligibility", "Saved for the next checks", f"Member ID ending {case['ocr']['member_id'][-4:]}, group {case['ocr']['group_number']}, and card plan details", _with_reference(source, "uploaded card · reviewed member and plan fields")),
        ]
        case["stage_decisions"].append({
            "stage": "eligibility", "status": "passed", "decision": "The insurance card is valid for this patient and its reviewed details are saved for matching",
            "source_mode": "documents",
        })
        case["next_stage"] = "network"
        return case
    if stage == "network":
        required = {"contract", "coverage", "fee_schedule"}
        missing = required.difference(case.get("documents", {}))
        if missing:
            raise GoldenCaseError("Upload the provider contract, Availity coverage details, and fee schedule before continuing")
        _apply_network_evidence(case)
        if case.get("coverage", {}).get("status") != "active":
            raise GoldenCaseError("The uploaded Availity response does not show active medical coverage")
    coverage = case["coverage"]
    if stage == "authorization":
        referral_source = _citation(
            "BCBSTX provider website", BCBSTX_REFERRAL_URL,
            hashlib.sha256(BCBSTX_REFERRAL_URL.encode()).hexdigest(),
        )
        selected = [line for line in case["service_lines"] if line.get("selected")]
        authorization_facts = []
        for line in selected:
            auth_status = line.get("authorization_status")
            auth_value = {
                "not_required": "No authorization required in the uploaded coverage query",
                "required": "Prior authorization required in the uploaded coverage query",
            }.get(auth_status, "Not answered by the uploaded coverage query—verify with BCBSTX before scheduling")
            authorization_facts.extend((
                _fact(
                    "authorization", f"CPT {line['code']} referral",
                    "Not established by the uploaded files—verify the member's plan referral rule before scheduling",
                    referral_source,
                ),
                _fact(
                    "authorization", f"CPT {line['code']} prior authorization",
                    auth_value,
                    _with_reference(coverage["source"], line.get("authorization_reference", "CPT was not included in the uploaded query")),
                ),
            ))
        case["facts"] = [fact for fact in case.get("facts", []) if fact.get("stage") != "authorization"] + authorization_facts
    decisions = {
        "network": f"NMCT's signed Blue Choice PPO participation and the member's {coverage['network_status'].replace('_', '-')} response were matched",
        "authorization": "Referral and CPT-specific prior-authorization evidence reviewed",
        "benefits": "Reviewed contracted rate and patient benefits calculated",
        "estimate": "Document-backed pre-visit estimate completed",
    }
    case["stage_decisions"].append({
        "stage": stage, "status": "passed", "decision": decisions[stage], "source_mode": "documents",
    })
    case["next_stage"] = {"network": "service", "authorization": "benefits", "benefits": None, "estimate": None}[stage]
    if stage == "benefits":
        case["estimate"] = calculate(case)
        estimate = case["estimate"]
        benefit_page = coverage["source"].get("findings", {}).get("suggested_coverage", {}).get("benefit_page")
        benefit_source = _with_reference(
            coverage["source"],
            f"page {benefit_page} · individual deductible, out-of-pocket, and benefit amounts" if benefit_page else coverage["source"]["reference"],
        )
        if estimate["estimate_available"]:
            label = "Contracted allowed amount" if coverage["network_status"] == "in_network" else "Out-of-network allowed amount"
            source = rate_source if coverage["network_status"] == "in_network" else coverage["source"]
            line_facts = [
                _fact(
                    "benefits", f"CPT {line['code']} allowed amount", _money(line["allowed_cents"]), rate_source,
                    f"The uploaded fee schedule lists {_money(line['allowed_cents'])} as NMCT's Blue Choice PPO allowed amount for this anticipated CPT.",
                )
                for line in estimate["line_items"] if line["allowed_cents"] is not None
            ] if coverage["network_status"] == "in_network" else []
            case["facts"].extend(line_facts + [
                _fact("benefits", label, _money(estimate["allowed_cents"]), source),
                _fact("benefits", "Deductible applied", _money(estimate["deductible_applied_cents"]), benefit_source),
                _fact("benefits", "Coinsurance", _money(estimate["nominal_coinsurance_cents"]) if estimate["nominal_coinsurance_cents"] is not None else "Not stated in the uploaded coverage response", benefit_source),
                _fact("benefits", "Copay", _money(estimate["copay_cents"]) if estimate["copay_cents"] is not None else "Not stated in the uploaded coverage response", benefit_source),
                _fact("benefits", "Out-of-pocket result", "Patient cost share is capped at $0.00 because the individual out-of-pocket remaining is $0.00" if estimate["individual_oop_remaining_cents"] == 0 else _money(estimate["oop_cap_adjustment_cents"]), benefit_source),
            ])
        else:
            case["facts"].append(_fact("benefits", "Estimate status", estimate["unavailable_reason"], coverage["source"]))
        _complete_estimate(case, coverage, rate_source)
    if stage == "estimate":  # Backward compatibility for cases saved before estimates became automatic.
        _complete_estimate(case, coverage, rate_source)
    return case


def _complete_estimate(case: dict[str, Any], coverage: dict[str, Any], rate_source: dict[str, Any]) -> None:
    case.update(status="complete", next_stage=None)
    if not any(item.get("stage") == "estimate" for item in case["stage_decisions"]):
        case["stage_decisions"].append({
            "stage": "estimate", "status": "passed", "decision": "Document-backed pre-visit estimate completed",
            "source_mode": "documents",
        })
    case["facts"] = [fact for fact in case.get("facts", []) if fact.get("stage") != "estimate"]
    estimate = case["estimate"]
    benefit_page = coverage["source"].get("findings", {}).get("suggested_coverage", {}).get("benefit_page")
    benefit_source = _with_reference(
        coverage["source"],
        f"page {benefit_page} · individual deductible, out-of-pocket, and benefit amounts" if benefit_page else coverage["source"]["reference"],
    )
    if estimate["estimate_available"]:
        case["facts"].extend((
            _fact("estimate", "Estimated patient responsibility", _money(estimate["patient_cents"]), benefit_source, "This is the patient's estimated share after applying the reviewed rates and individual benefit balances."),
            _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), rate_source, "This is the remaining reviewed amount expected from the plan after the patient's estimated share."),
        ))
    else:
        case["facts"].append(_fact("estimate", "Collection amount", "Not estimated", coverage["source"]))


def calculate(case: dict[str, Any], include_service_items: bool = True) -> dict[str, Any]:
    selected = [line for line in case["service_lines"] if line["selected"] and line["confirmed"]]
    coverage = case["coverage"]
    line_items = [
        {"code": line["code"], "description": line.get("description", line["code"]), "allowed_cents": line.get("allowed_cents")}
        for line in selected
    ] if coverage["network_status"] == "in_network" else []
    if any(line.get("allowed_cents") is None for line in selected):
        return {
            "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected], "line_items": line_items,
            "estimate_available": False, "unavailable_reason": "At least one selected CPT has no reviewed NMCT rate. Add the rate before collecting an estimate.",
            "allowed_cents": None, "deductible_applied_cents": None, "nominal_coinsurance_cents": None,
            "copay_cents": None, "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
            "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]), "oop_cap_adjustment_cents": None,
            "patient_cents": None, "plan_cents": None, "source_mode": "documents", "calculated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "A price requires a reviewed NMCT rate for every selected anticipated CPT.",
        }
    if coverage["network_status"] == "out_of_network" and coverage["out_of_network_benefit_status"] != "covered":
        return {
            "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected], "line_items": line_items,
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
    percent = coverage.get("coinsurance_percent")
    copay_value = coverage.get("copay_cents")
    if percent is None and copay_value is None and int(coverage["individual_oop_remaining_cents"]) > 0:
        return {
            "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected], "line_items": line_items,
            "estimate_available": False, "unavailable_reason": "The uploaded evidence does not state the member's coinsurance, and out-of-pocket cost remains.",
            "allowed_cents": allowed, "deductible_applied_cents": deductible, "nominal_coinsurance_cents": None,
            "copay_cents": None, "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
            "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]), "oop_cap_adjustment_cents": None,
            "patient_cents": None, "plan_cents": None, "source_mode": "documents", "calculated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "A patient estimate needs a current coinsurance or copay when out-of-pocket cost remains.",
        }
    coinsurance = None if percent is None else int((
        Decimal(after_deductible) * Decimal(str(percent)) / Decimal(100)
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    copay = int(copay_value) if copay_value is not None else None
    nominal = min(allowed, deductible + (coinsurance or 0) + (copay or 0))
    patient = min(nominal, int(coverage["individual_oop_remaining_cents"]))
    line_summary = (
        " and ".join(f"CPT {line['code']} at {_money(line['allowed_cents'])}" for line in selected)
        if coverage["network_status"] == "in_network"
        else f"CPT {', '.join(line['code'] for line in selected)} with a documented out-of-network allowance"
    )
    if int(coverage["individual_oop_remaining_cents"]) == 0:
        explanation = (
            f"Collect $0.00 because the uploaded benefits report $0.00 remaining individual out-of-pocket responsibility. "
            f"The normal cost share for {line_summary} would be {_money(nominal)}, but the out-of-pocket limit reduces it by "
            f"{_money(nominal)}. ClinicForce therefore estimates the plan responsibility at {_money(allowed)}."
        )
    else:
        explanation = (
            f"Collect {_money(patient)} based on {line_summary}. The reviewed rates total {_money(allowed)}; the normal patient "
            f"share before the out-of-pocket limit is {_money(nominal)}, and the uploaded individual out-of-pocket balance caps "
            f"the estimate at {_money(patient)}."
        )
    result = {
        "service_code": ", ".join(line["code"] for line in selected),
        "service_codes": [line["code"] for line in selected], "line_items": line_items, "allowed_cents": allowed,
        "estimate_available": True,
        "deductible_applied_cents": deductible, "nominal_coinsurance_cents": coinsurance,
        "copay_cents": copay,
        "individual_deductible_remaining_cents": int(coverage["individual_deductible_remaining_cents"]),
        "individual_oop_remaining_cents": int(coverage["individual_oop_remaining_cents"]),
        "oop_cap_adjustment_cents": None if coinsurance is None and copay is None else patient - nominal, "patient_cents": patient,
        "plan_cents": allowed - patient, "source_mode": "documents",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "plain_explanation": explanation,
        "disclaimer": "Pre-visit estimate based on current eligibility, reviewed rates, and anticipated services. Final patient responsibility may change after coding and claim adjudication.",
    }
    if include_service_items:
        result["service_item_estimates"] = _service_item_estimates(case, selected)
        scheduled = [item for item in result["service_item_estimates"] if item["status"] == "scheduled"]
        result["collect_today_cents"] = sum(int(item["patient_cents"]) for item in scheduled)
        result["collection_action"] = "do_not_collect" if result["collect_today_cents"] == 0 else "collect"
        uncertain = any(item["status"] == "if_elected" for item in result["service_item_estimates"])
        unknown_auth = any(line.get("authorization_status") not in {"not_required", "required"} for line in selected)
        result["confidence"] = "medium" if uncertain or unknown_auth else "high"
        result["confidence_reason"] = (
            "Procedure selection or payer requirements may change after the clinician's examination."
            if uncertain or unknown_auth else
            "The selected services have reviewed rates, benefit balances, and payer-requirement results."
        )
        if result["collect_today_cents"] != result["patient_cents"]:
            result["plain_explanation"] = (
                f"Collect {_money(result['collect_today_cents'])} for the scheduled service. "
                f"If every selected optional service is performed, the total patient estimate is {_money(result['patient_cents'])}; "
                "do not collect an optional amount until the clinician elects that service."
            )
    return result


def _service_item_estimates(case: dict[str, Any], selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in selected:
        item_id = line.get("service_item_id") or _service_item(line["code"], line.get("description", ""))[0]
        grouped.setdefault(item_id, []).append(line)
    estimates = []
    for item_id, lines in grouped.items():
        _, default_name, default_status = _service_item(lines[0]["code"], lines[0].get("description", ""))
        estimate = calculate({**case, "service_lines": lines}, include_service_items=False)
        estimates.append({
            "id": item_id,
            "name": lines[0].get("service_item_name", default_name),
            "status": lines[0].get("service_status", default_status),
            "codes": [line["code"] for line in lines],
            "estimate_available": estimate["estimate_available"],
            "allowed_cents": estimate["allowed_cents"],
            "normal_patient_share_cents": None if estimate["oop_cap_adjustment_cents"] is None else (
                int(estimate["patient_cents"]) - int(estimate["oop_cap_adjustment_cents"])
            ),
            "oop_adjustment_cents": estimate["oop_cap_adjustment_cents"],
            "patient_cents": estimate["patient_cents"],
            "plan_cents": estimate["plan_cents"],
        })
    return estimates


def _citation(filename: str, reference: str, sha256: str, observed_at: str | None = None) -> dict[str, str]:
    return {
        "source_filename": filename, "sha256": sha256, "reference": reference,
        "observed_at": observed_at or _observed_now(),
    }


def _fact(stage: str, label: str, value: str, source: dict[str, Any], explanation: str | None = None) -> dict[str, Any]:
    citation = source if "source_filename" in source else _citation(
        source["filename"], source["reference"], source["sha256"], source["observed_at"],
    )
    fact = {"stage": stage, "label": label, "value": value, "citation": citation, "source_mode": "documents"}
    if explanation:
        fact["explanation"] = explanation
    return fact


def _with_reference(source: dict[str, Any], reference: str) -> dict[str, Any]:
    return {
        "filename": source.get("filename", source.get("source_filename", "Uploaded evidence")),
        "sha256": source["sha256"], "observed_at": source["observed_at"], "reference": reference,
    }


def _money(cents: int) -> str:
    sign = "−" if cents < 0 else ""
    return f"{sign}${Decimal(abs(cents)) / Decimal(100):,.2f}"


def _observed_now() -> str:
    """Timestamp every uploaded source and future API observation in UTC."""
    return datetime.now(timezone.utc).isoformat()


def _cash_packages(compact: str) -> list[dict[str, Any]]:
    packages = []
    for name, document_label, best_for in CASH_PACKAGE_LABELS:
        match = re.search(rf"{re.escape(document_label)}.{{0,180}}?\$([\d,]+)", compact)
        if match:
            packages.append({
                "name": name, "best_for": best_for,
                "price_cents": int(match.group(1).replace(",", "")) * 100,
                "page": 1,
            })
    if not packages:
        raise GoldenCaseError("The uploaded cash-pay guide does not contain readable package prices")
    return packages


def _pages_with(page_text: list[tuple[int, str]], phrase: str) -> list[int]:
    needle = " ".join(phrase.casefold().split())
    return [number for number, text in page_text if needle in " ".join(text.casefold().split())]


def _page_list(pages: list[int]) -> str:
    return ", ".join(str(page) for page in sorted(set(pages))[:6])


def _suggested_coverage(text: str) -> dict[str, Any]:
    """Return only explicit eligibility facts; a clinician still confirms them."""
    compact = " ".join(text.casefold().split())

    def amount(pattern: str) -> int | None:
        match = re.search(pattern, compact)
        return int(Decimal(match.group(1).replace(",", "")) * 100) if match else None

    plans = re.findall(r"plan\s*/\s*product\s*:\s*([^\n]+)", text, re.IGNORECASE)
    plan_name = next((value.strip() for value in plans if "medical" in value.casefold()), plans[0].strip() if plans else None)
    network_status = "in_network" if "in network" in compact else "out_of_network" if "out of network" in compact else None
    out_of_network_benefit = (
        "not_covered" if re.search(r"out[- ]of[- ]network.*?(?:not covered|no benefits?)", compact)
        else "covered" if re.search(r"out[- ]of[- ]network.*?(?:covered|coinsurance|deductible|copay)", compact)
        else None
    )
    suggested: dict[str, Any] = {
        "coverage_status": "active" if "active coverage" in compact else None,
        "network_status": network_status,
        "out_of_network_benefit_status": out_of_network_benefit,
        "plan_name": plan_name,
        "deductible_remaining_cents": amount(r"annual deductible.*?\$[\d,]+\s*/.*?\$([\d,]+)\s+remaining"),
        "oop_remaining_cents": amount(r"out of pocket.*?\$[\d,]+\s*/.*?\$([\d,]+)\s+remaining"),
        "accumulators_met_percent": 100 if re.search(r"(?:annual deductible|out of pocket).*?100%", compact) else None,
    }
    return {key: value for key, value in suggested.items() if value is not None}


def _authorization_findings(page_text: list[tuple[int, str]]) -> list[dict[str, Any]]:
    findings = []
    document_text = "\n".join(text for _, text in page_text).casefold()
    code_count = sum(len(re.findall(r"(?m)^\s*\d{5}\s*-", text)) for _, text in page_text)
    all_not_required = code_count > 0 and document_text.count("no auth required") >= code_count
    for page_number, text in page_text:
        page_not_required = "no auth required" in text.casefold()
        matches = list(re.finditer(r"(?m)^\s*(\d{5})\s*-\s*([^\n]+)", text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = " ".join(text[match.start():end].split())
            local_not_required = "no auth required" in block.casefold()
            status = "not_required" if all_not_required or local_not_required else "required" if re.search(r"(?<!no )auth required", block.casefold()) else "unknown"
            page_reference = f"pages {page_number}–{page_number + 1}" if all_not_required and not page_not_required else f"page {page_number}"
            findings.append({
                "code": match.group(1), "description": match.group(2).strip(), "status": status,
                "page": page_number,
                "reference": f"{page_reference} · CPT {match.group(1)} · {'No Auth Required' if status == 'not_required' else 'authorization result requires review'}",
            })
    return findings
