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
    """Read one clinic CSV used only for billed-charge and cash-price lookup."""
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
    if "code" not in headers.values() or not {"cash", "charge"}.intersection(v for v in headers.values() if v):
        raise golden.GoldenCaseError("The fee CSV needs CPT/HCPCS and Cash Price and/or Billed Charge columns.")
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
        if cash is None and charge is None:
            raise golden.GoldenCaseError(f"CPT/HCPCS {code} has no cash price or billed charge.")
        raw_status = normalize(values.get("status", ""))
        status = ("scheduled" if raw_status in {"scheduled", "visit", "officevisit"} else
                  "possible" if raw_status in {"possible", "procedure", "future", "ifelected", "optional"} else None)
        if raw_status and status is None:
            raise golden.GoldenCaseError(f"CPT/HCPCS {code} has an invalid Service Status; use Scheduled or Possible.")
        lines.append({"code": code, "description": values.get("description", code)[:200],
                      "cash_cents": int(Decimal(cash) * 100) if cash is not None else None,
                      "charge": charge, "service_status": status})
        seen.add(code)
    if not lines:
        raise golden.GoldenCaseError("The fee CSV does not contain a CPT/HCPCS price row.")
    return {"filename": Path(filename).name, "sha256": hashlib.sha256(content).hexdigest(),
            "observed_at": datetime.now(timezone.utc).isoformat(), "lines": lines,
            "rate_count": len(lines), "notes": ["Clinic prices only; insurance benefits and allowed amounts come from Availity."]}


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


def assemble(sources: dict, profile: dict, practice: dict) -> tuple[dict, list[str]]:
    """Return a read-only preview and exact missing-document instructions."""
    card = sources.get("card", {}).get("fields", {})
    order = sources.get("order", {}).get("fields", {})
    clinic = sources.get("provider", {}).get("fields", {})
    missing = []
    if not card:
        missing.append("Upload the patient's insurance card (example: Availity Essentials.pdf).")
    if not order:
        missing.append("Upload the patient-specific coded order / encounter PDF or CSV, including patient name and DOB.")
    for key in ("patient_first_name", "patient_last_name", "patient_birth_date"):
        if not profile.get(key) or normalize(str(order.get(key, ""))) != normalize(str(profile.get(key, ""))):
            missing.append(f"The coded encounter must include {key.replace('_', ' ')} matching the opened QR patient. Correct the source export or patient profile.")
    member_id = card.get("member_id") or order.get("member_id")
    if not member_id:
        missing.append("Upload a clearer card or registration export containing Member ID.")
    if card.get("member_id") and order.get("member_id") and normalize(card["member_id"]) != normalize(order["member_id"]):
        missing.append("The card and registration export have different Member IDs. Upload matching documents.")
    relationship = order.get("relationship")
    if relationship not in {"18", "01", "19", "G8"}:
        missing.append("The registration/encounter export must state Subscriber Relationship Code (18 self, 01 spouse, 19 child, G8 other).")
    subscriber = {"firstName": order.get("subscriber_first_name"), "lastName": order.get("subscriber_last_name"),
                  "birthDate": order.get("subscriber_birth_date"), "memberId": member_id}
    if relationship == "18":
        subscriber.update(firstName=profile.get("patient_first_name"), lastName=profile.get("patient_last_name"), birthDate=profile.get("patient_birth_date"))
    if not all(subscriber.values()):
        missing.append("Upload the registration export with subscriber name, birth date and Member ID.")
    expected_name = [normalize(part) for key in ("firstName", "lastName") for part in str(subscriber.get(key) or "").split()]
    card_name = {normalize(part) for part in card.get("member_name", "").split()}
    if card_name and (len(expected_name) < 2 or not all(x in card_name for x in expected_name)):
        missing.append("The insurance card's subscriber name does not match this encounter. Upload the correct card.")
    payer_id = order.get("payer_id") or card.get("payer_id")
    if not payer_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", payer_id):
        missing.append("The registration export needs Availity Payer ID, or the administrator must configure an exact card-payer mapping.")
    if card.get("payer_id") and order.get("payer_id") and card["payer_id"] != order["payer_id"]:
        missing.append("The card and encounter identify different payers. Upload matching documents.")
    npi = clinic.get("provider_npi") or order.get("provider_npi")
    providers = practice.get("providers", {})
    provider = providers.get(npi, {}) if isinstance(providers, dict) else {}
    if not provider:
        missing.append("Upload the provider agreement / billing-provider export. Its NPI must be approved in the administrator's Availity practice configuration.")
    elif clinic.get("provider_tax_id") and clinic["provider_tax_id"] != provider.get("ein"):
        missing.append("The provider document's Tax ID differs from the configured billing provider. Ask the administrator to correct the setup.")
    if npi and order.get("provider_npi") and npi != order["provider_npi"]:
        missing.append("The order and provider document have different billing-provider NPIs. Upload matching documents.")
    for key in ("lastName", "ein", "addressLine1", "city", "stateCode", "zipCode"):
        if not provider.get(key):
            missing.append(f"Administrator: configure the clinic's verified billing-provider {key}; a contract's notice address is not assumed to be its billing address.")
    if not practice.get("submitter_id"):
        missing.append("Administrator: add the Availity-issued submitter ID to the server practice configuration.")
    service_date = order.get("service_date", "")
    try:
        date.fromisoformat(service_date)
    except ValueError:
        missing.append("Upload the dated encounter/order with Service Date in YYYY-MM-DD format.")
    if not re.fullmatch(r"\d{2}", order.get("place_of_service", "")):
        missing.append("The encounter export must include Place of Service Code.")
    coverage_missing = list(missing)
    review_missing = list(missing)
    diagnoses = [d.replace(".", "").upper() for d in order.get("diagnoses", [])]
    if not diagnoses or len(diagnoses) > 12 or any(not re.fullmatch(r"[A-Z][0-9][A-Z0-9]{1,5}", d) for d in diagnoses):
        missing.append("Upload a clinician-coded order with explicit ICD-10 diagnosis codes; symptoms cannot supply these codes.")
        review_missing.append(missing[-1])
    if not re.fullmatch(r"[0-9]{10}", order.get("patient_phone", "")):
        missing.append("The registration/encounter export must include the patient's 10-digit phone number for PCE.")
    if order.get("rendering_provider_npi") and not providers.get(order["rendering_provider_npi"]):
        missing.append("The order's rendering-provider NPI is not approved in the clinic configuration. Ask the administrator to add the verified provider.")
    lines = [dict(row) for row in sources.get("order", {}).get("lines", [])]
    charges = sources.get("charges", {}).get("lines", [])
    for row in lines:
        if not row.get("diagnosis") and len(diagnoses) == 1:
            row["diagnosis"] = diagnoses[0]
        matches = [c for c in charges if c["code"] == row["code"] and c["modifiers"] == row["modifiers"]]
        if row.get("charge") is None and len(matches) == 1:
            # Charge exports contain the total billed line charge, not a unit rate.
            row["charge"] = matches[0]["charge"]
    if not lines or len(lines) > 20:
        missing.append("Upload a coded order containing 1–20 intended service lines. The example email does not establish a billable service list.")
        review_missing.append(missing[-1])
    if any(not row.get("units") or row.get("charge") is None for row in lines):
        missing.append("Upload the order with units and total billed charge per line, or a matching clinic line-charge CSV. The PPO fee schedule cannot replace billed charges.")
    if any(not row.get("diagnosis") or row["diagnosis"] not in diagnoses for row in lines):
        missing.append("The coded order must associate each service line with its ICD-10 diagnosis. Upload a corrected encounter export.")
    if any(not row.get("units") for row in lines):
        review_missing.append("Upload the coded order with the intended quantity for every service.")
    if not providers.get(order.get("rendering_provider_npi"), {}).get("lastName"):
        review_missing.append("Upload an order identifying the Rendering Provider NPI; the clinic administrator must approve that provider.")
    if not re.fullmatch(r"[A-Z0-9]{1,3}", order.get("service_type", "")):
        review_missing.append("The order export must include the Service Type Code needed for authorization/referral.")
    return {"patient": profile, "subscriber": subscriber, "relationship": relationship, "member_id": member_id,
            "payer_id": payer_id, "group_number": order.get("group_number") or card.get("group_number"),
            "provider": {**provider, "npi": npi}, "submitter_id": practice.get("submitter_id"),
            "rendering_provider": {**providers.get(order.get("rendering_provider_npi"), {}), "npi": order.get("rendering_provider_npi")},
            "service_type": order.get("service_type"),
            "service_date": service_date, "place_of_service": order.get("place_of_service"),
            "coverage_missing": coverage_missing,
            "review_missing": list(dict.fromkeys(review_missing)),
            "diagnoses": diagnoses, "patient_phone": order.get("patient_phone"), "lines": lines,
            "reason": order.get("reason") or sources.get("appointment", {}).get("fields", {}).get("reason", "")}, list(dict.fromkeys(missing))
