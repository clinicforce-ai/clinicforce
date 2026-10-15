"""Parse source documents for the document-only V1 workflow.

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
from typing import Any, Callable

from app.insurance.golden import GoldenCaseError
from app.insurance.cpt_suggester import suggest_cpt_codes, clinical_context_from_pages
from app.insurance import cpt_rules, v1_requirements
from app.insurance.v1_sources import MONEY, read_pages, individual_balance_cells, document_dates


MAX_EVIDENCE_PAGES = 100
DOCUMENT_STAGES = {"coverage": "network", "fee_schedule": "network", "contract": "network", "self_pay": "network", "clinical": "service"}


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
    return [row for row in v1_requirements.code_requirements(pages) if row["kind"] == "authorization"]


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


def inspect_pdf(content: bytes, filename: str, kind: str, member_id: str = "", *,
                clinical_reader: Callable[[list[tuple[int, str]]], dict[str, Any]] | None = None) -> dict[str, Any]:
    if kind not in {"coverage", "clinical", "contract", "authorization", "self_pay"}:
        raise GoldenCaseError("Unsupported insurance evidence type")
    pages = _pdf_pages(content)
    text = "\n".join(value for _, value in pages)
    if kind == "clinical":
        try:
            findings = (clinical_reader or clinical_context_from_pages)(pages)
        except Exception as exc:
            raise GoldenCaseError("Clinical text extraction is unavailable. Retry the upload or enter the visit details yourself.") from exc
        return {"id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
                "observed_at": _now(), "document_dates": document_dates(pages), "reference": f"Uploaded clinical document · {len(pages)} pages", "findings": findings}
    if kind != "coverage":
        return {
            "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(), "observed_at": _now(),
            "document_dates": document_dates(pages),
            "reference": f"Uploaded {kind} document · {len(pages)} page{'s' if len(pages) != 1 else ''}",
            "findings": {"pages": [{"page": number, "text": value} for number, value in pages],
                         "requirements": v1_requirements.code_requirements(pages),
                         "policy_terms": v1_requirements.contract_terms(pages) if kind == "contract" else []},
        }
    member_token = re.sub(r"[^A-Z0-9]", "", member_id.upper())
    document_tokens = {re.sub(r"[^A-Z0-9]", "", token.upper()) for token in re.findall(r"[A-Za-z0-9-]+", text)}
    if member_id and member_token not in document_tokens:
        raise GoldenCaseError("The uploaded coverage PDF does not contain the card Member ID")
    fields, evidence = _coverage_fields(pages, individual_balance_cells(content))
    return {
        "id": kind, "filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(), "observed_at": _now(),
        "document_dates": document_dates(pages),
        "reference": f"Uploaded coverage PDF · {len(pages)} page{'s' if len(pages) != 1 else ''}",
        "findings": {"suggested_coverage": fields, "field_evidence": evidence, "pages": [{"page": number, "text": value} for number, value in pages], "requirements": v1_requirements.code_requirements(pages), "authorizations": _authorization_rows(pages), "procedure_lines": _procedure_rows(pages)},
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
            "reference": f"Uploaded rate CSV · {len(rates)} procedure rates", "document_dates": document_dates([(1, content.decode("utf-8-sig"))]), "findings": {"rate_lines": rates, "rate_count": len(rates)}}
