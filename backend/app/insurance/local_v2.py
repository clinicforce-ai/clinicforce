"""Local-only, document-driven insurance-estimate cases.

This is intentionally isolated from payer/EHR integrations. It stores only
staff-reviewed extracted text locally so NMCT can validate each workflow stage
with supplied documents before credentials or external APIs are introduced.
"""

from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from app.insurance import plan_matrix


DOCUMENT_KINDS = {"insurance_card", "eligibility_printout", "provider_contract", "out_of_network_benefits", "fee_schedule", "authorization_requirements", "referral", "authorization"}
STAGES = ("eligibility", "network", "service", "authorization", "fee_schedule", "calculation")
_lock = Lock()
_field_patterns = {
    "member_id": r"(?im)^\s*(?:member|subscriber)\s*(?:id|number)\s*[:#]\s*([A-Za-z0-9-]{4,})",
    "member_name": r"(?:member|subscriber|patient)\s*name\s*[:#]?\s*([A-Za-z][A-Za-z ,.'-]{2,80})",
    "group_number": r"group\s*(?:number|no\.?|#)?\s*[:#]?\s*([A-Za-z0-9-]{3,})",
    "plan_name": r"(?:plan|product)\s*(?:name)?\s*[:#]?\s*([A-Za-z][A-Za-z0-9 .-]{2,70})",
    "effective_date": r"(?:effective|coverage)\s*(?:date|from)?\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    "deductible": r"deductible\s*(?:remaining)?\s*[:#]?\s*(\$?[\d,]+(?:\.\d{2})?)",
    "out_of_pocket": r"(?:out[- ]of[- ]pocket|oop)\s*(?:remaining)?\s*[:#]?\s*(\$?[\d,]+(?:\.\d{2})?)",
    "copay": r"co[- ]?pay\s*[:#]?\s*(\$?[\d,]+(?:\.\d{2})?)",
    "coinsurance": r"co[- ]?insurance\s*[:#]?\s*(\d+(?:\.\d+)?\s*%)",
    "payer": r"payer\s*:\s*([^\n]+)",
    "plan_product": r"plan\s*/\s*product\s*:\s*([^\n]+)",
    "provider_npi": r"\bNPI\s*:\s*(\d{10})",
    "place_of_service": r"place\s+of\s+service\s*:\s*([^\n]+)",
    "date_of_service": r"date\s+of\s+service\s*[:#]?\s*([^\n]+)",
    "contracted_provider": r"and\s+([A-Z][A-Z &.,'-]{4,})\s+\(individual professional provider",
}
_document_requirements = {
    "insurance_card": (
        "Insurance details needs a member ID card PDF, not an eligibility response or provider agreement.",
        lambda text: "member id card" in text or "insurance card" in text,
    ),
    "eligibility_printout": (
        "Eligibility needs a dated eligibility/benefits response showing coverage, not the member ID card.",
        lambda text: ("eligibility" in text or "active coverage" in text or "member status" in text)
        and "member id" in text and ("group number" in text or "payer" in text),
    ),
    "provider_contract": (
        "Network status needs the clinic–payer provider participation agreement, not a patient benefits printout.",
        lambda text: "provider agreement" in text and ("participating provider" in text or "provider networks" in text),
    ),
    "out_of_network_benefits": (
        "Out-of-network benefits needs a payer benefits response that specifically addresses out-of-network coverage.",
        lambda text: "out of network" in text and ("benefit" in text or "coverage" in text),
    ),
    "fee_schedule": (
        "Fee schedule needs a current numeric rate sheet with CPT/HCPCS service lines; a provider agreement alone is not a rate sheet.",
        lambda text: ("cpt" in text or "hcpcs" in text) and bool(re.search(r"\$\s*\d|\b\d{5}\b", text)),
    ),
    "authorization_requirements": (
        "Referral and prior authorization needs a payer requirements response for the planned service, not a clinic note or email.",
        lambda text: ("referral" in text or "authorization" in text) and "required" in text,
    ),
    "referral": (
        "Referral evidence needs an ordering/referral document, not a scheduling note or email.",
        lambda text: "referral" in text and ("provider" in text or "order" in text),
    ),
    "authorization": (
        "Prior-authorization evidence needs a payer authorization determination, not a clinic email.",
        lambda text: ("prior authorization" in text or "authorization" in text) and ("approved" in text or "approval" in text),
    ),
}


def _path() -> Path:
    configured = os.getenv("INSURANCE_V2_LOCAL_STORE_PATH", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "insurance-v2.local.json"


def _golden_case_path() -> Path:
    configured = os.getenv("INSURANCE_GOLDEN_CASE_PATH", "").strip()
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "nmct-golden-patient.local.json"


def _document_from_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    document = deepcopy(payload)
    document.update({"id": str(uuid4()), "uploaded_at": _now(), "reviewed": False})
    return document


def _matching_golden_case(card: dict[str, Any]) -> dict[str, Any] | None:
    """Return locally supplied evidence only for its matching card; never query a payer."""
    path = _golden_case_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    member_id = str(card.get("fields", {}).get("member_id", ""))
    member_hash = sha256(member_id.encode("utf-8")).hexdigest() if member_id else ""
    return payload if (
        payload.get("card_member_sha256") == member_hash
        or payload.get("card_content_sha256") == card.get("content_sha256")
        or payload.get("card_text_sha256") == card.get("text_sha256")
    ) else None


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"cases": {}, "contracts": {}}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("The local insurance V2 store is invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("cases"), dict) or not isinstance(result.get("contracts"), dict):
        raise ValueError("The local insurance V2 store has an invalid shape")
    return result


def _save(store: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_pages(text: str, kind: str) -> list[int]:
    terms = {
        "eligibility_printout": ("provider information", "plan / product", "in network", "out of network"),
        "provider_contract": ("provider networks", "participating provider", "attachment a"),
        "out_of_network_benefits": ("out of network", "benefit", "coverage"),
    }.get(kind, ())
    pages: list[int] = []
    for match in re.finditer(r"\[PAGE\s+(\d+)\]\n(.*?)(?=\[PAGE\s+\d+\]\n|\Z)", text, re.IGNORECASE | re.DOTALL):
        page = match.group(2).casefold()
        if kind == "provider_contract" and "provider provides its express authority" in page:
            pages.append(int(match.group(1)))
        elif kind != "provider_contract" and any(term in page for term in terms):
            pages.append(int(match.group(1)))
    return pages


def _network_indicator(text: str) -> str:
    lower = text.casefold()
    in_network = lower.count("in network")
    out_of_network = lower.count("out of network")
    if in_network and not out_of_network:
        return "in_network"
    if out_of_network and not in_network:
        return "out_of_network"
    return "unknown"


def _requirement_indicator(text: str, phrase: str) -> str:
    compact = re.sub(r"\s+", " ", text.casefold())
    if re.search(rf"(?:no|not)\s+{phrase}\s+(?:is\s+)?required|{phrase}\s+(?:is\s+)?not\s+required", compact):
        return "not_required"
    if re.search(rf"{phrase}\s+(?:is\s+)?required", compact):
        return "required"
    return "unknown"


def extract_document(kind: str, filename: str, text: str) -> dict[str, Any]:
    if kind not in DOCUMENT_KINDS:
        raise ValueError("Unsupported insurance document type")
    lower = text.casefold()
    message, matches = _document_requirements[kind]
    if not matches(lower):
        raise ValueError(message)
    fields: dict[str, str] = {}
    for name, pattern in _field_patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields[name] = match.group(1).strip()
    if kind == "eligibility_printout":
        fields["network_indicator"] = _network_indicator(text)
    if kind == "authorization_requirements":
        fields["referral_requirement"] = _requirement_indicator(text, "referral")
        fields["authorization_requirement"] = _requirement_indicator(text, "(?:prior )?authorization")
    if kind == "out_of_network_benefits":
        fields["out_of_network_benefit"] = "not available" if re.search(r"no\s+out[- ]of[- ]network\s+(?:benefits?|coverage)", lower) else "documented"
    signals = [label for label in ("active", "in network", "out of network", "prior authorization", "referral", "fee schedule", "allowable", "contract") if label in lower]
    return {"id": str(uuid4()), "kind": kind, "filename": Path(filename).name, "uploaded_at": _now(), "text_characters": len(text), "text_sha256": sha256(text.encode("utf-8")).hexdigest(), "evidence_pages": _evidence_pages(text, kind), "fields": fields, "signals": signals, "reviewed": False}


def create_case(patient_label: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    if not documents:
        raise ValueError("Upload at least one insurance document")
    card_name = documents[0].get("fields", {}).get("member_name", "")
    evidence = _matching_golden_case(documents[0])
    evidence_documents = []
    if evidence:
        evidence_documents = [_document_from_evidence(payload) for payload in evidence.get("documents", {}).values() if isinstance(payload, dict)]
    case = {
        "id": str(uuid4()),
        "patient_label": patient_label.strip() or card_name or "Patient identity pending confirmation",
        "created_at": _now(),
        "documents": documents + evidence_documents,
        "service": evidence.get("appointment", {}).get("service", "") if evidence else "",
        "appointment": evidence.get("appointment", {}) if evidence else {},
        "network_status": "unknown",
        "plan_rule": {"status": "pending"},
        "referral_status": "unknown",
        "authorization_status": "unknown",
        "recorded_collection": evidence.get("recorded_collection") if evidence else None,
        "evidence_conflicts": evidence.get("evidence_conflicts", []) if evidence else [],
        "stages": {},
    }
    with _lock:
        store = _load()
        store["cases"][case["id"]] = case
        _save(store)
    return deepcopy(case)


def get_case(case_id: str) -> dict[str, Any]:
    with _lock:
        case = _load()["cases"].get(case_id)
    if not isinstance(case, dict):
        raise KeyError("Insurance estimate case was not found")
    return deepcopy(case)


def add_document(case_id: str, document: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        store = _load()
        case = store["cases"].get(case_id)
        if not isinstance(case, dict):
            raise KeyError("Insurance estimate case was not found")
        case["documents"].append(document)
        _save(store)
    return deepcopy(case)


def review_document(case_id: str, document_id: str, fields: dict[str, str]) -> dict[str, Any]:
    with _lock:
        store = _load()
        case = store["cases"].get(case_id)
        if not isinstance(case, dict):
            raise KeyError("Insurance estimate case was not found")
        document = next((item for item in case["documents"] if item["id"] == document_id), None)
        if not isinstance(document, dict):
            raise KeyError("Insurance document was not found")
        document["fields"] = {key: value.strip() for key, value in fields.items() if value.strip()}
        document["reviewed"] = True
        document["reviewed_at"] = _now()
        _save(store)
    return deepcopy(case)


def _documents(case: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in case["documents"] if item["kind"] == kind]


def _reviewed_latest(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    reviewed = [item for item in documents if item["reviewed"]]
    return reviewed[-1] if reviewed else None


def _referral_required(plan_rule: dict[str, Any]) -> str:
    rule = plan_rule.get("rule") if isinstance(plan_rule, dict) else None
    if not isinstance(rule, dict):
        return "unknown"
    value = str(rule.get("referral_required", "")).casefold()
    if value.startswith("yes"):
        return "required"
    if value.startswith("no"):
        return "not_required"
    return "unknown"


def run_stage(case_id: str, stage: str, service: str = "") -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("Unsupported insurance workflow stage")
    with _lock:
        store = _load()
        case = store["cases"].get(case_id)
        if not isinstance(case, dict):
            raise KeyError("Insurance estimate case was not found")
        if service.strip(): case["service"] = service.strip()
        card = _documents(case, "insurance_card")
        eligibility = _documents(case, "eligibility_printout")
        contract = _documents(case, "provider_contract")
        oon_benefits = _documents(case, "out_of_network_benefits")
        rates = _documents(case, "fee_schedule")
        requirements = _documents(case, "authorization_requirements")
        referrals = _documents(case, "referral")
        authorizations = _documents(case, "authorization")
        result: dict[str, Any]
        if stage == "eligibility":
            ready = bool(card and eligibility and all(item["reviewed"] for item in card + eligibility))
            latest_eligibility = _reviewed_latest(eligibility)
            if ready and latest_eligibility:
                case["plan_rule"] = plan_matrix.match_rule(
                    latest_eligibility["fields"].get("payer", ""), latest_eligibility["fields"].get("plan_product", ""),
                )
            plan_rule = case["plan_rule"]
            matrix_ready = plan_rule.get("status") == "matched"
            if ready and matrix_ready:
                result = {"stage": stage, "status": "passed", "decision": "Coverage evidence and the matching clinic payer-plan rule are reviewed.", "next_step": "network"}
            elif ready:
                result = {"stage": stage, "status": "blocked", "decision": f"Coverage evidence is reviewed, but {plan_rule.get('message', 'the clinic payer-plan rule could not be resolved')}.", "next_step": None}
            else:
                result = {"stage": stage, "status": "blocked", "decision": "Upload and review both an insurance card and eligibility printout.", "next_step": None}
        elif stage == "network":
            contract_ready = bool(contract and all(item["reviewed"] for item in contract))
            eligibility_ready = bool(eligibility and all(item["reviewed"] for item in eligibility))
            benefits_ready = bool(oon_benefits and all(item["reviewed"] for item in oon_benefits))
            indicators = {
                item["fields"].get("network_indicator", "unknown")
                for item in eligibility
                if item["reviewed"] and all(item["fields"].get(field) for field in ("provider_npi", "plan_product", "date_of_service", "place_of_service"))
            }
            network_status = indicators.pop() if len(indicators) == 1 else "unknown"
            case["network_status"] = network_status
            if not contract_ready or not eligibility_ready:
                result = {"stage": stage, "status": "blocked", "decision": "Review both the payer eligibility response and the clinic–payer participation agreement before determining network status.", "next_step": None}
            elif network_status == "in_network":
                result = {"stage": stage, "status": "passed", "decision": "The payer response shows in-network benefits for the queried provider and service location; the provider participation agreement is reviewed.", "next_step": "service"}
            elif network_status == "out_of_network" and benefits_ready and oon_benefits[-1]["fields"].get("out_of_network_benefit") == "not available":
                result = {"stage": stage, "status": "terminal", "decision": "The payer response shows out-of-network status and the reviewed benefits evidence shows no applicable out-of-network benefit. Route to self-pay review.", "next_step": None}
            elif network_status == "out_of_network" and benefits_ready:
                result = {"stage": stage, "status": "passed", "decision": "The payer response shows out-of-network status and reviewed out-of-network benefits evidence is available.", "next_step": "service"}
            elif network_status == "out_of_network":
                result = {"stage": stage, "status": "blocked", "decision": "The payer response shows out-of-network status. Upload the matching out-of-network benefits response to determine the next path.", "next_step": None}
            else:
                result = {"stage": stage, "status": "blocked", "decision": "The reviewed evidence does not state one network result for this provider, location, and date of service. Obtain a provider-specific payer eligibility response.", "next_step": None}
        elif stage == "service":
            ready = bool(case["service"])
            result = {"stage": stage, "status": "passed" if ready else "blocked", "decision": "Anticipated service is recorded." if ready else "Record the anticipated service from the appointment, referral/order, or scheduled procedure.", "next_step": "authorization" if ready else None}
        elif stage == "authorization":
            plan_rule = case.get("plan_rule", {})
            matrix_referral_requirement = _referral_required(plan_rule)
            requirement_document = _reviewed_latest(requirements)
            payer_referral_requirement = requirement_document["fields"].get("referral_requirement", "unknown") if requirement_document else "unknown"
            payer_authorization_requirement = requirement_document["fields"].get("authorization_requirement", "unknown") if requirement_document else "unknown"
            referral_requirement = matrix_referral_requirement if matrix_referral_requirement != "unknown" else payer_referral_requirement
            referral_ready = referral_requirement == "not_required" or (referral_requirement == "required" and bool(referrals and all(item["reviewed"] for item in referrals)))
            authorization_ready = payer_authorization_requirement == "not_required" or (payer_authorization_requirement == "required" and bool(authorizations and all(item["reviewed"] for item in authorizations)))
            case["referral_status"] = "verified" if referral_requirement == "required" and referral_ready else referral_requirement
            case["authorization_status"] = "approved" if payer_authorization_requirement == "required" and authorization_ready else payer_authorization_requirement
            if plan_rule.get("status") != "matched":
                result = {"stage": stage, "status": "blocked", "decision": "A matching clinic payer-plan rule is required before referral evidence can be assessed.", "next_step": None}
            elif referral_requirement == "unknown" and not requirement_document:
                result = {"stage": stage, "status": "blocked", "decision": "This clinic payer-plan rule varies by member or employer group. Upload and review the payer's current requirements response before determining whether a referral is needed.", "next_step": None}
            elif referral_requirement == "unknown":
                result = {"stage": stage, "status": "blocked", "decision": "The reviewed payer response does not clearly say whether a referral is required. Obtain a specific payer determination.", "next_step": None}
            elif not referral_ready:
                result = {"stage": stage, "status": "blocked", "decision": "The clinic payer-plan matrix requires a referral for this plan. Upload and review the referral order before continuing.", "next_step": None}
            elif not requirement_document:
                result = {"stage": stage, "status": "blocked", "decision": "Upload and review the payer's current prior-authorization requirements response for the planned service. The matrix identifies possible triggers but cannot prove the member-specific result.", "next_step": None}
            elif payer_authorization_requirement == "unknown":
                result = {"stage": stage, "status": "blocked", "decision": "The reviewed payer response does not clearly say whether prior authorization is required for this planned service. Obtain a specific payer determination.", "next_step": None}
            elif not authorization_ready:
                result = {"stage": stage, "status": "blocked", "decision": "The payer requires prior authorization. Upload and review the payer approval for the planned service before continuing.", "next_step": None}
            else:
                result = {"stage": stage, "status": "passed", "decision": "The plan's referral rule and the payer's current authorization requirement are supported by reviewed evidence.", "next_step": "fee_schedule"}
        elif stage == "fee_schedule":
            ready = bool(rates and all(item["reviewed"] for item in rates))
            result = {"stage": stage, "status": "passed" if ready else "blocked", "decision": "A reviewed fee schedule is available." if ready else "Upload a numeric fee schedule or rate sheet. A provider agreement alone is not enough.", "next_step": "calculation" if ready else None}
        else:
            result = {
                "stage": stage,
                "status": "blocked",
                "decision": "A final patient balance cannot be supported from the reviewed evidence. The response provides deductible and out-of-pocket amounts but no service-specific copay or coinsurance; the final selected service lines are also not recorded.",
                "next_step": None,
            }
            if case.get("recorded_collection"):
                result["recorded_collection"] = case["recorded_collection"]
            if case.get("evidence_conflicts"):
                result["evidence_conflicts"] = case["evidence_conflicts"]
        result.update({"ran_at": _now(), "inputs": {"documents": [{"id": item["id"], "kind": item["kind"], "reviewed": item["reviewed"]} for item in case["documents"]], "service": case["service"], "network_status": case["network_status"], "plan_rule": case["plan_rule"], "referral_status": case["referral_status"], "authorization_status": case["authorization_status"]}})
        case["stages"][stage] = result
        _save(store)
    return deepcopy(result)
