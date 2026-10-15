"""Document inputs for API inquiries, never document-derived benefits or prices.

Only explicitly labelled identifiers and coded services are imported. Narrative
symptoms cannot establish a diagnosis, CPT level, service date, or billed charge.
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

import fitz

from app.insurance import golden


KINDS = {"card", "provider", "appointment", "order", "charges"}
# These are an import contract, not a claim that eCW exports this exact format.
ALIASES = {
    "patient_first_name": ("Patient First Name",), "patient_last_name": ("Patient Last Name",),
    "patient_birth_date": ("Patient DOB", "Patient Birth Date"),
    "member_id": ("Member ID", "Subscriber ID"), "payer_id": ("Availity Payer ID",),
    "relationship": ("Subscriber Relationship Code",),
    "subscriber_first_name": ("Subscriber First Name",), "subscriber_last_name": ("Subscriber Last Name",),
    "subscriber_birth_date": ("Subscriber DOB",), "group_number": ("Group Number",),
    "patient_phone": ("Patient Phone",), "service_date": ("Service Date", "Date of Service"),
    "place_of_service": ("Place of Service Code", "POS"),
    "reason": ("Reason for Visit", "Visit Reason"), "diagnosis": ("ICD-10", "Diagnosis Code"),
    "code": ("CPT", "HCPCS", "Procedure Code"), "units": ("Units", "Quantity"),
    "charge": ("Billed Charge", "Line Charge"), "modifiers": ("Modifiers",),
    "provider_npi": ("Billing Provider NPI", "Provider NPI", "NPI"),
    "provider_name": ("Billing Provider Name", "Provider Name"),
    "rendering_provider_npi": ("Rendering Provider NPI",), "service_type": ("Service Type Code",),
    "provider_tax_id": ("Billing Provider Tax ID", "Tax ID", "TIN"),
    "address": ("Billing Address",), "city": ("Billing City",),
    "state": ("Billing State",), "zip_code": ("Billing ZIP",),
}

FEE_ALIASES = {
    "procedurecode": "code", "cpt": "code", "hcpcs": "code", "code": "code",
    "description": "description", "servicedescription": "description",
    "cashprice": "cash", "selfpayprice": "cash",
    "billedcharge": "charge", "linecharge": "charge", "standardcharge": "charge",
    "contractedallowedamount": "allowed", "allowedamount": "allowed", "contractrate": "allowed",
    "servicestatus": "status", "estimatetype": "status", "servicecategory": "status",
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def decimal_text(value: str, *, money: bool = False) -> str:
    try:
        number = Decimal(value.strip().replace("$", "").replace(",", ""))
        if not number.is_finite() or number < 0 or number > 1_000_000 or (not money and number == 0):
            raise ValueError
        if money and number != number.quantize(Decimal("0.01")):
            raise ValueError
        return format(number, ".2f" if money else "f")
    except (InvalidOperation, ValueError):
        raise golden.GoldenCaseError("The document contains an invalid charge or unit count; upload a corrected clinic export.") from None


def pdf_text(content: bytes) -> str:
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            if doc.needs_pass or not 1 <= len(doc) <= 100:
                raise ValueError
            text = "\n".join(page.get_text() for page in doc)
            if len(text) > 1_000_000:
                raise ValueError
    except Exception:
        raise golden.GoldenCaseError("Upload an unlocked PDF with at most 100 pages.") from None
    if len(text.strip()) < 20:
        raise golden.GoldenCaseError("This document needs a text layer. Export a searchable PDF or CSV from the clinic system.")
    return text


def labelled(text: str) -> dict[str, str]:
    fields = {}
    for key, names in ALIASES.items():
        labels = "|".join(re.escape(name) for name in (key, *names))
        values = {m.strip() for m in re.findall(rf"(?im)^(?:{labels})[ \t]*:[ \t]*([^\r\n|]+)", text) if m.strip()}
        if len(values) == 1:
            fields[key] = values.pop()
    return fields


def read_source(kind: str, content: bytes, filename: str, payer_aliases: dict[str, str]) -> dict:
    if kind not in KINDS:
        raise golden.GoldenCaseError("Unsupported V2 input document")
    suffix = Path(filename).suffix.lower()
    source = {"filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
              "observed_at": datetime.now(timezone.utc).isoformat(), "fields": {}, "lines": [], "notes": []}
    if kind == "card":
        if suffix not in {".pdf", ".png", ".jpg", ".jpeg"}:
            raise golden.GoldenCaseError("Upload the insurance card as PDF, PNG or JPG.")
        # Discard copays, deductibles and every other card benefit field.
        if content.startswith(b"%PDF"):
            text = golden._pdf_text(content)
        elif content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
            text = golden._image_text(content)
        else:
            raise golden.GoldenCaseError("The card is not a readable PDF, PNG or JPG.")
        fields = golden._card_fields(text)
        if not fields.get("member_id") or not fields.get("member_name"):
            raise golden.GoldenCaseError("Upload a clearer card showing the subscriber name and Member ID.")
        source["fields"] = {k: fields[k] for k in ("member_name", "member_id", "group_number") if fields.get(k)}
        explicit = labelled(text).get("payer_id")
        candidates = {v for k, v in payer_aliases.items() if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", text, re.I)}
        if explicit or len(candidates) == 1:
            source["fields"]["payer_id"] = explicit or candidates.pop()
        source["notes"] = ["Identity only. Card benefit amounts are not used."]
        return source
    if suffix == ".csv" and kind in {"order", "charges", "provider"}:
        try:
            rows = list(csv.DictReader(StringIO(content.decode("utf-8-sig")), strict=True))
        except (UnicodeError, csv.Error):
            raise golden.GoldenCaseError("Upload a UTF-8 CSV exported by the clinic.") from None
        if not rows or len(rows) > 1000:
            raise golden.GoldenCaseError("The export must have 1–1,000 rows.")
        aliases = {normalize(name): key for key, names in ALIASES.items() for name in (key, *names)}
        records = [{aliases[normalize(k)]: str(v or "").strip() for k, v in row.items()
                    if k and normalize(k) in aliases and isinstance(v, str)} for row in rows]
        if kind == "charges" and any("Network" in row or "Amount" in row for row in rows) and not any(r.get("charge") for r in records):
            raise golden.GoldenCaseError("This is a negotiated fee schedule. Upload the clinic's billed-charge export with a Billed Charge column; V2 gets allowed amounts from Availity.")
        for key in ALIASES.keys() - {"code", "units", "charge", "modifiers", "diagnosis"}:
            values = {r[key] for r in records if r.get(key)}
            if len(values) > 1:
                raise golden.GoldenCaseError(f"The export contains conflicting {key.replace('_', ' ')} values. Upload one patient encounter.")
            if values:
                source["fields"][key] = values.pop()
        source["lines"] = [line(r, charges_only=kind == "charges") for r in records if r.get("code")]
        diagnoses = list(dict.fromkeys(r["diagnosis"] for r in records if r.get("diagnosis")))
        if diagnoses:
            source["fields"]["diagnoses"] = diagnoses
    elif suffix == ".pdf":
        text = pdf_text(content)
        fields = labelled(text)
        if kind == "provider":
            # Only the signature-page identifiers, not the contract's network/rate terms.
            for key, label, pattern in (("provider_npi", "NPI", r"[1-4]\d{9}"), ("provider_tax_id", "Tax ID", r"\d{9}")):
                candidates = set(re.findall(rf"(?im)^\s*{label}\s*:\s*({pattern})\b", text))
                if len(candidates) == 1:
                    fields[key] = candidates.pop()
            match = re.search(r"(?i)and\s+([^\n]{4,100})\s*\(individual professional provider", text)
            if match:
                fields.setdefault("provider_name", match.group(1).strip())
        if kind == "appointment":
            # Never retain the email body: it may contain passwords and old benefit/price advice.
            if not fields.get("reason"):
                phrases = [x for x in ("neuropathy", "numbness", "tingling", "EMG") if re.search(rf"\b{re.escape(x)}\b", text, re.I)]
                if phrases:
                    fields["reason"] = "Mentioned in correspondence: " + ", ".join(phrases)
            source["notes"].append("Visit context only; an email timestamp is not a service date and symptoms are not confirmed procedure or diagnosis codes.")
        if kind == "order":
            # Each coded order row must explicitly contain its own quantity and charge.
            for row in text.splitlines():
                if re.match(r"(?i)^\s*(?:CPT|HCPCS|Procedure Code)\s*:", row):
                    values = labelled(row.replace(" | ", "\n").replace("|", "\n"))
                    source["lines"].append(line(values))
            if fields.get("diagnosis"):
                fields["diagnoses"] = [fields["diagnosis"]]
        source["fields"] = fields
    else:
        raise golden.GoldenCaseError("Upload a searchable PDF, or a clinic CSV export for coded orders, charges or provider details.")
    allowed = ({"provider_npi", "provider_tax_id", "provider_name", "address", "city", "state", "zip_code"} if kind == "provider"
               else {"reason"} if kind == "appointment" else set(ALIASES) | {"diagnoses"})
    source["fields"] = {k: v for k, v in source["fields"].items() if k in allowed}
    if kind == "charges":
        source["fields"] = {}
    return source


def read_fee_schedule(content: bytes, filename: str) -> dict:
    """Read clinic cash/billed prices and optional clinic-owned allowed rates."""
    if Path(filename).suffix.lower() != ".csv":
        raise golden.GoldenCaseError("Upload the clinic fee schedule as a CSV file.")
    try:
        reader = csv.DictReader(StringIO(content.decode("utf-8-sig")), strict=True)
        rows = list(reader)
    except (UnicodeError, csv.Error):
        raise golden.GoldenCaseError("Upload a readable UTF-8 clinic fee CSV.") from None
    if not rows or len(rows) > 1000:
        raise golden.GoldenCaseError("The clinic fee CSV must contain 1–1,000 rows.")
    headers = {normalize(str(name or "")): FEE_ALIASES.get(normalize(str(name or ""))) for name in (reader.fieldnames or [])}
    if "code" not in headers.values() or not {"cash", "charge", "allowed"}.intersection(v for v in headers.values() if v):
        raise golden.GoldenCaseError("The fee CSV needs CPT/HCPCS and Cash Price and/or Billed Charge columns, or a Contracted Allowed Amount column.")
    lines, seen = [], set()
    for number, row in enumerate(rows, 2):
        values = {FEE_ALIASES[normalize(str(name))]: str(value or "").strip()
                  for name, value in row.items() if name and normalize(str(name)) in FEE_ALIASES}
        code = values.get("code", "").replace(".", "").upper()
        if not code:
            continue
        if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
            raise golden.GoldenCaseError(f"The procedure code on fee CSV row {number} is invalid.")
        if code in seen:
            raise golden.GoldenCaseError(f"CPT/HCPCS {code} appears more than once in the fee CSV.")
        cash = decimal_text(values["cash"], money=True) if values.get("cash") else None
        charge = decimal_text(values["charge"], money=True) if values.get("charge") else None
        allowed = decimal_text(values["allowed"], money=True) if values.get("allowed") else None
        if cash is None and charge is None and allowed is None:
            raise golden.GoldenCaseError(f"CPT/HCPCS {code} has no cash price, billed charge, or contracted allowed amount.")
        raw_status = normalize(values.get("status", ""))
        status = ("scheduled" if raw_status in {"scheduled", "visit", "officevisit"} else
                  "possible" if raw_status in {"possible", "procedure", "future", "ifelected", "optional"} else None)
        if raw_status and status is None:
            raise golden.GoldenCaseError(f"CPT/HCPCS {code} has an invalid Service Status; use Scheduled or Possible.")
        lines.append({"code": code, "description": values.get("description", code)[:200],
                      "cash_cents": int(Decimal(cash) * 100) if cash is not None else None,
                      "charge": charge, "allowed_cents": int(Decimal(allowed) * 100) if allowed is not None else None,
                      "service_status": status})
        seen.add(code)
    if not lines:
        raise golden.GoldenCaseError("The fee CSV does not contain a CPT/HCPCS price row.")
    return {"filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(), "lines": lines,
            "rate_count": len(lines), "notes": ["Clinic prices are used for Availity PCE. Contracted Allowed Amount is clinic-owned and used only by the temporary Stedi local-estimate path."]}


def line(row: dict, *, charges_only: bool = False) -> dict:
    code = row.get("code", "").upper()
    if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
        raise golden.GoldenCaseError("A procedure code in the export is invalid.")
    modifiers = [m.strip().upper() for m in re.split(r"[,; ]+", row.get("modifiers", "")) if m.strip()]
    if len(modifiers) > 4 or any(not re.fullmatch(r"[A-Z0-9]{2}", m) for m in modifiers):
        raise golden.GoldenCaseError("A procedure modifier in the export is invalid.")
    return {"code": code, "modifiers": modifiers, "diagnosis": row.get("diagnosis", "").replace(".", "").upper() or None,
            "units": decimal_text(row["units"]) if row.get("units") and not charges_only else None,
            "charge": decimal_text(row["charge"], money=True) if row.get("charge") else None}
