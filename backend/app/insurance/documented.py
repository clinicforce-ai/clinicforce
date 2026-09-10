"""Document-only V1 insurance workflow.

Every patient-specific value returned by this module is read from an uploaded
card, coverage PDF, or rate CSV. The workflow has no payer, clinic, procedure,
or benefit defaults.
"""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from pathlib import Path
from typing import Any

from app.insurance.golden import GoldenCaseError
from app.insurance.cpt_suggester import suggest_cpt_codes
from app.insurance.v1_sources import MONEY, read_pages, individual_balance_cells


MAX_EVIDENCE_PAGES = 100
DOCUMENT_STAGES = {"coverage": "network", "fee_schedule": "network", "contract": "network", "clinical": "service", "authorization": "authorization"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _citation(source: dict[str, Any], reference: str | None = None) -> dict[str, str]:
    return {
        "source_filename": source["filename"], "sha256": source["sha256"],
        "reference": reference or source["reference"], "observed_at": source["observed_at"],
    }


def _fact(stage: str, label: str, value: str, source: dict[str, Any], reference: str | None = None) -> dict[str, Any]:
    return {"stage": stage, "label": label, "value": value, "citation": _citation(source, reference), "source_mode": "documents"}


def _money(cents: int | None) -> str:
    return "Not stated" if cents is None else f"${Decimal(cents) / Decimal(100):,.2f}"


def pending_case(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "source_mode": "documents", "payment_path": "insurance", "status": "review", "next_stage": "intake",
        "ocr": {**fields, "confirmed": False}, "documents": {}, "coverage": None, "clinical_context": "", "service_lines": [],
        "stage_decisions": [], "facts": [], "estimate": None,
    }


def confirm_intake(case: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    source = case.get("card_source")
    if not source:
        raise GoldenCaseError("The uploaded insurance-card source is missing; start a new review")
    case.update(
        status="in_progress", next_stage="eligibility", ocr={**fields, "confirmed": True},
        facts=[_fact("intake", "Insurance card", "Fields shown above were read from the uploaded card", source)],
    )
    return case


def _pdf_pages(content: bytes) -> list[tuple[int, str]]:
    return read_pages(content, MAX_EVIDENCE_PAGES)


def _page_with(pages: list[tuple[int, str]], pattern: str) -> int | None:
    return next((number for number, text in pages if re.search(pattern, text, re.I)), None)


def _coverage_fields(pages: list[tuple[int, str]], table_cells: list | None = None) -> tuple[dict, dict]:
    """Never join unrelated pages/sections or silently choose one of several values."""
    candidates: dict[str, list[tuple[Any, dict]]] = {}

    def add(key: str, value: Any, page: int, quote: str) -> None:
        candidates.setdefault(key, []).append((value, {"page": page, "quote": quote}))

    for page, text in pages:
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            plan = re.fullmatch(r"(?:plan\s*/\s*product|plan|product)\s*:\s*(.+)", line, re.I)
            if plan:
                add("plan_name", plan[1], page, line)
            network = re.fullmatch(r"(?:provider\s+)?network\s+status\s*:\s*(in[- ]network|out[- ]of[- ]network)", line, re.I)
            if network:
                add("network_status", "in_network" if network[1].lower().startswith("in") else "out_of_network", page, line)
            # Individual balances only. Annual totals, family columns, plan-paid
            # percentages and unlabeled amounts cannot become patient inputs.
            if re.search(r"\bindividual\b", line, re.I) and not re.search(r"\bfamily\b", line, re.I):
                amounts = re.findall(MONEY, line)
                if len(amounts) == line.count("$"):
                    balance = re.search(rf"({MONEY})\s+remaining\b|remaining\s*[:=]?\s*({MONEY})", line, re.I)
                    if balance:
                        cents = int(Decimal(re.sub(r"[$,\s]", "", balance[1] or balance[2])) * 100)
                        if re.search(r"deductible", line, re.I) and not re.search(r"out[- ]of[- ]pocket|\boop\b", line, re.I):
                            add("deductible_remaining_cents", cents, page, line)
                        elif re.search(r"out[- ]of[- ]pocket|\boop\b", line, re.I) and not re.search(r"deductible", line, re.I):
                            add("oop_remaining_cents", cents, page, line)
            coinsurance = re.fullmatch(r"(?:patient\s+)?coinsurance\s*:\s*(\d{1,3})\s*%(?:\s+after\s+(?:the\s+)?deductible)?|(?:patient\s+)?(\d{1,3})\s*%\s+coinsurance(?:\s+after\s+(?:the\s+)?deductible)?", line, re.I)
            if coinsurance and 0 <= int(coinsurance[1] or coinsurance[2]) <= 100:
                add("coinsurance_percent", int(coinsurance[1] or coinsurance[2]), page, line)
    for key, value, evidence in table_cells or []:
        add(key, value, evidence["page"], evidence["quote"])
    fields, evidence = {}, {}
    for key, entries in candidates.items():
        if len({value for value, _ in entries}) == 1:
            fields[key], evidence[key] = entries[0]
    return fields, evidence


def _authorization_rows(pages: list[tuple[int, str]]) -> list[dict[str, str]]:
    rows = []
    for page, text in pages:
        for match in re.finditer(r"(?im)^\s*(\d{5}|[A-Z]\d{4})\s*[-:]\s*([^\n]+)", text):
            block = match.group(0)
            status = "not_required" if re.search(r"no\s+(?:prior\s+)?auth(?:orization)?\s+required", block, re.I) else "required" if re.search(r"auth(?:orization)?\s+required", block, re.I) else "not_stated"
            rows.append({"code": match.group(1), "status": status, "quote": block.strip(), "reference": f"page {page} · uploaded authorization entry"})
    return rows


def _procedure_rows(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """Return only procedure codes explicitly labelled in the coverage PDF."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page, text in pages:
        for match in re.finditer(r"(?i)\b(?:CPT(?:\s+code)?|HCPCS)\s*[:#-]?\s*(\d{5}|[A-Z]\d{4}|\d{4}[A-Z])\b", text):
            code = match.group(1).upper()
            if code not in seen:
                rows.append({"code": code, "description": code, "allowed_cents": None, "reference": f"page {page} · uploaded coverage PDF"})
                seen.add(code)
    for row in _authorization_rows(pages):
        if row["code"] not in seen:
            rows.append({"code": row["code"], "description": row["code"], "allowed_cents": None, "reference": row["reference"]})
            seen.add(row["code"])
    return rows


def inspect_pdf(content: bytes, filename: str, kind: str, member_id: str = "") -> dict[str, Any]:
    if kind not in {"coverage", "clinical", "contract", "authorization"}:
        raise GoldenCaseError("Unsupported insurance evidence type")
    pages = _pdf_pages(content)
    text = "\n".join(value for _, value in pages)
    if kind != "coverage":
        return {
            "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(), "observed_at": _now(),
            "reference": f"Uploaded {kind} document · {len(pages)} page{'s' if len(pages) != 1 else ''}",
            "findings": {"pages": [{"page": number, "text": value} for number, value in pages], **({"clinical_context": text, "procedure_lines": _procedure_rows(pages)} if kind == "clinical" else {"authorizations": _authorization_rows(pages)} if kind == "authorization" else {})},
        }
    member_token = re.sub(r"[^A-Z0-9]", "", member_id.upper())
    document_tokens = {re.sub(r"[^A-Z0-9]", "", token.upper()) for token in re.findall(r"[A-Za-z0-9-]+", text)}
    if member_id and member_token not in document_tokens:
        raise GoldenCaseError("The uploaded coverage PDF does not contain the card Member ID")
    fields, evidence = _coverage_fields(pages, individual_balance_cells(content))
    return {
        "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(), "observed_at": _now(),
        "reference": f"Uploaded coverage PDF · {len(pages)} page{'s' if len(pages) != 1 else ''}",
        "findings": {"suggested_coverage": fields, "field_evidence": evidence, "pages": [{"page": number, "text": value} for number, value in pages], "authorizations": _authorization_rows(pages), "procedure_lines": _procedure_rows(pages)},
    }


def inspect_csv(content: bytes, filename: str) -> dict[str, Any]:
    try:
        rows = list(csv.DictReader(StringIO(content.decode("utf-8-sig")), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise GoldenCaseError("The rate CSV is not readable") from exc
    if not rows or not rows[0]:
        raise GoldenCaseError("Upload a CSV with a procedure-code and amount column")
    aliases = {"procedure code": "code", "cpt": "code", "hcpcs": "code", "code": "code", "amount": "amount", "allowed amount": "amount", "rate": "amount", "description": "description"}
    rates = []
    for number, row in enumerate(rows, 2):
        values = {aliases.get(str(key).strip().casefold(), ""): str(value or "").strip() for key, value in row.items()}
        code, amount = values.get("code", "").upper(), values.get("amount", "")
        if not code or not amount:
            continue
        if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
            raise GoldenCaseError("A procedure code in the CSV is invalid")
        try:
            rate = Decimal(amount.removeprefix("$").replace(",", ""))
            if not rate.is_finite() or rate < 0:
                raise ValueError("Rate must be finite and non-negative")
            cents = int((rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except Exception as exc:
            raise GoldenCaseError(f"The amount for CPT/HCPCS {code} is invalid") from exc
        if cents < 0 or any(rate["code"] == code for rate in rates):
            raise GoldenCaseError(f"CPT/HCPCS {code} must have one non-negative rate; check duplicate rows and network columns.")
        rates.append({"code": code, "description": values.get("description") or code, "allowed_cents": cents, "reference": f"row {number}", "quote": ", ".join(str(value or "") for value in row.values())})
    if not rates:
        raise GoldenCaseError("The rate CSV does not contain a procedure code and amount")
    return {"id": "fee_schedule", "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(), "observed_at": _now(),
            "reference": f"Uploaded rate CSV · {len(rates)} procedure rates", "findings": {"rate_lines": rates, "rate_count": len(rates)}}


def add_document(case: dict[str, Any], kind: str, source: dict[str, Any]) -> dict[str, Any]:
    case.setdefault("documents", {})[kind] = source
    if kind == "coverage":
        _apply_coverage(case)
    if kind == "clinical":
        case["service_lines"] = []
    if kind == "authorization":
        _apply_authorizations(case)
    return case


def _apply_coverage(case: dict[str, Any]) -> None:
    source = case.get("documents", {}).get("coverage")
    if not source:
        return
    values = source["findings"]["suggested_coverage"]
    case["coverage"] = {**values, "source": source}
    facts = []
    labels = (("plan_name", "Plan"), ("network_status", "Network"), ("deductible_remaining_cents", "Individual deductible remaining"), ("oop_remaining_cents", "Individual out-of-pocket remaining"), ("coinsurance_percent", "Coinsurance"))
    for key, label in labels:
        if key not in values:
            continue
        value = values[key]
        display = _money(value) if key.endswith("_cents") else f"{value}%" if key == "coinsurance_percent" else str(value).replace("_", " ")
        evidence = source["findings"]["field_evidence"][key]
        fact = _fact("network", label, display, source, f"page {evidence['page']}")
        fact["citation"]["quote"] = evidence["quote"]
        facts.append(fact)
    case["facts"] = [fact for fact in case.get("facts", []) if fact["stage"] != "network"] + facts


def case_rate_lines(case: dict[str, Any]) -> list[dict[str, Any]]:
    return case.get("documents", {}).get("fee_schedule", {}).get("findings", {}).get("rate_lines", [])


def _document_procedure_lines(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge explicitly extracted PDF and CSV codes without inventing a code."""
    lines: dict[str, dict[str, Any]] = {}
    coverage = case.get("documents", {}).get("coverage", {})
    for line in coverage.get("findings", {}).get("procedure_lines", []):
        lines[line["code"]] = {**line, "source_document": "Uploaded coverage PDF"}
    for line in case_rate_lines(case):
        existing = lines.get(line["code"], {})
        lines[line["code"]] = {**existing, **line, "source_document": "Uploaded rate CSV" if not existing else "Uploaded coverage PDF and rate CSV"}
    return list(lines.values())


def set_clinical_context(case: dict[str, Any], value: str, visit_type: str | None = None) -> dict[str, Any]:
    """Suggest only rate-CSV codes using patient-provided text or clinical PDF text."""
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete coverage and rate-document review before requesting CPT suggestions")
    manual_context = " ".join(value.split()).strip()
    clinical_pdf = case.get("documents", {}).get("clinical", {}).get("findings", {}).get("clinical_context", "")
    context = "\n".join(part for part in (manual_context, clinical_pdf) if part).strip()
    if len(context) < 3:
        raise GoldenCaseError("Enter the patient's condition or upload a searchable clinical PDF")
    try:
        suggestions = suggest_cpt_codes(context + (f"\nPatient visit type: {visit_type}" if visit_type else ""), case_rate_lines(case))
    except Exception as exc:
        raise GoldenCaseError("CPT suggestions are temporarily unavailable; try again later") from exc
    rate_by_code = {line["code"]: line for line in case_rate_lines(case)}
    lines = {}
    for code, reason in suggestions.items():
        rate = rate_by_code[code]
        lines[code] = {
            **lines.get(code, {}), **rate, "source_document": "ClinicForce suggestion using patient-provided context and uploaded rate CSV",
            "suggested_by": "ClinicForce", "recommendation_reason": reason,
        }
    case["clinical_context"] = manual_context
    case["visit_type"] = visit_type
    case["service_lines"] = [
        {**line, "selected": False, "confirmed": False, "available": True, "plain_description": line["description"]}
        for line in lines.values()
    ]
    return case


def select_services(case: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete the coverage and rate-document review before selecting procedures")
    selected = set(codes)
    lines = case.get("service_lines", [])
    if not selected or selected.difference({line["code"] for line in lines}):
        raise GoldenCaseError("Select one or more CPT/HCPCS codes read from the uploaded documents")
    for line in lines:
        line.update(selected=line["code"] in selected, confirmed=line["code"] in selected)
    position = {code: index for index, code in enumerate(codes)}
    lines.sort(key=lambda line: position.get(line["code"], len(position)))
    case["next_stage"] = "authorization"
    _apply_authorizations(case)
    case["stage_decisions"].append({"stage": "service", "status": "passed", "decision": "Uploaded CPT/HCPCS codes selected", "source_mode": "documents"})
    return case


def _apply_authorizations(case: dict[str, Any]) -> None:
    entries = {}
    for kind in ("coverage", "authorization"):
        source = case.get("documents", {}).get(kind, {})
        for row in source.get("findings", {}).get("authorizations", []):
            entries.setdefault(row["code"], []).append((row, source.get("filename", "")))
    for line in case.get("service_lines", []):
        rows = entries.get(line["code"], [])
        line.update(authorization_status="not_stated", authorization_reference="")
        if rows and len({row["status"] for row, _ in rows}) == 1:
            row, filename = rows[0]
            line.update(authorization_status=row["status"], authorization_reference=f"{filename} · {row['reference']} · {row['quote']}")


def advance(case: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage not in {"eligibility", "network", "authorization", "benefits"} or case.get("next_stage") != stage:
        raise GoldenCaseError("Complete the document-review steps in order")
    if stage == "network":
        if not case.get("documents", {}).get("coverage") or not case_rate_lines(case):
            raise GoldenCaseError("Upload both a coverage PDF and a rate CSV before continuing")
        _apply_coverage(case)
        case["service_lines"] = []
    if stage == "authorization":
        _apply_authorizations(case)
    if stage == "benefits":
        case["estimate"] = calculate(case)
        _add_estimate_facts(case)
    case["stage_decisions"].append({"stage": stage, "status": "passed", "decision": "Uploaded-document review completed", "source_mode": "documents"})
    case["next_stage"] = {"eligibility": "network", "network": "service", "authorization": "benefits", "benefits": None}[stage]
    if stage == "benefits":
        case["status"] = "complete"
    return case


def calculate(case: dict[str, Any]) -> dict[str, Any]:
    selected = [line for line in case.get("service_lines", []) if line.get("selected") and line.get("confirmed")]
    coverage = case.get("coverage") or {}
    common = {"service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected], "line_items": [{"code": line["code"], "description": line["description"], "allowed_cents": line["allowed_cents"]} for line in selected], "source_mode": "documents", "calculated_at": _now()}
    required = ("deductible_remaining_cents", "oop_remaining_cents")
    if not selected or any(key not in coverage for key in required):
        return {**common, "estimate_available": False, "unavailable_reason": "The uploaded coverage PDF must explicitly state individual deductible and out-of-pocket remaining amounts.", "allowed_cents": None, "deductible_applied_cents": None, "nominal_coinsurance_cents": None, "copay_cents": None, "individual_deductible_remaining_cents": coverage.get("deductible_remaining_cents"), "individual_oop_remaining_cents": coverage.get("oop_remaining_cents"), "oop_cap_adjustment_cents": None, "patient_cents": None, "plan_cents": None, "disclaimer": "No estimate is shown when an uploaded source does not state a required value."}
    if any(line["allowed_cents"] is None for line in selected):
        return {**common, "estimate_available": False, "unavailable_reason": "The uploaded rate CSV does not state an amount for every selected CPT/HCPCS code.", "allowed_cents": None, "deductible_applied_cents": None, "nominal_coinsurance_cents": None, "copay_cents": None, "individual_deductible_remaining_cents": coverage["deductible_remaining_cents"], "individual_oop_remaining_cents": coverage["oop_remaining_cents"], "oop_cap_adjustment_cents": None, "patient_cents": None, "plan_cents": None, "disclaimer": "No estimate is shown when an uploaded source does not state a required value."}
    allowed = sum(int(line["allowed_cents"]) for line in selected)
    percent = coverage.get("coinsurance_percent")
    if percent is None and int(coverage["oop_remaining_cents"]) > 0:
        deductible = min(allowed, int(coverage["deductible_remaining_cents"]))
        return {**common, "estimate_available": False, "unavailable_reason": "The uploaded coverage PDF does not state coinsurance.", "allowed_cents": allowed, "deductible_applied_cents": deductible, "nominal_coinsurance_cents": None, "copay_cents": None, "individual_deductible_remaining_cents": coverage["deductible_remaining_cents"], "individual_oop_remaining_cents": coverage["oop_remaining_cents"], "oop_cap_adjustment_cents": None, "patient_cents": None, "plan_cents": None, "disclaimer": "No estimate is shown when an uploaded source does not state coinsurance."}
    deductible_remaining, oop_remaining = int(coverage["deductible_remaining_cents"]), int(coverage["oop_remaining_cents"])
    line_items = []
    for line in selected:
        line_allowed = int(line["allowed_cents"])
        line_deductible = min(line_allowed, deductible_remaining)
        line_coinsurance = int((Decimal(line_allowed - line_deductible) * Decimal(percent or 0) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        line_nominal = min(line_allowed, line_deductible + line_coinsurance)
        line_patient = min(line_nominal, oop_remaining)
        line_items.append({"code": line["code"], "description": line["description"], "allowed_cents": line_allowed, "deductible_applied_cents": line_deductible, "coinsurance_cents": line_coinsurance, "patient_cents": line_patient, "plan_cents": line_allowed - line_patient})
        deductible_remaining -= line_deductible
        oop_remaining -= line_patient
    deductible = sum(line["deductible_applied_cents"] for line in line_items)
    coinsurance = sum(line["coinsurance_cents"] for line in line_items)
    nominal = deductible + coinsurance
    patient = sum(line["patient_cents"] for line in line_items)
    return {**common, "line_items": line_items, "estimate_available": True, "allowed_cents": allowed, "deductible_applied_cents": deductible, "nominal_coinsurance_cents": coinsurance, "copay_cents": None, "individual_deductible_remaining_cents": coverage["deductible_remaining_cents"], "individual_oop_remaining_cents": coverage["oop_remaining_cents"], "oop_cap_adjustment_cents": patient - nominal, "patient_cents": patient, "plan_cents": allowed - patient, "collect_today_cents": patient, "confidence": "source-backed", "confidence_reason": "Patient and plan shares are calculated from the benefit values and rates extracted from this review's uploads.", "plain_explanation": "Estimated from the uploaded benefit document and selected uploaded rate-CSV entries.", "disclaimer": "Pre-visit estimate only; final responsibility depends on claim adjudication."}


def _add_estimate_facts(case: dict[str, Any]) -> None:
    estimate, coverage = case["estimate"], case.get("coverage") or {}
    source = coverage.get("source")
    case["facts"] = [fact for fact in case.get("facts", []) if fact["stage"] not in {"benefits", "estimate"}]
    if not estimate["estimate_available"] or not source:
        if source:
            case["facts"].append(_fact("benefits", "Estimate", estimate["unavailable_reason"], source))
        return
    rate_source = case["documents"]["fee_schedule"]
    for line in estimate["line_items"]:
        case["facts"].append(_fact("benefits", f"CPT/HCPCS {line['code']} rate", _money(line["allowed_cents"]), rate_source))
    case["facts"].extend((_fact("benefits", "Deductible applied", _money(estimate["deductible_applied_cents"]), source), _fact("benefits", "Coinsurance", _money(estimate["nominal_coinsurance_cents"]), source), _fact("estimate", "Estimated patient responsibility", _money(estimate["patient_cents"]), source), _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), rate_source)))
