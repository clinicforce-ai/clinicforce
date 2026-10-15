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


from app.insurance.availity_source_parser import (
    KINDS,
    ALIASES,
    FEE_ALIASES,
    normalize,
    decimal_text,
    pdf_text,
    labelled,
    read_source,
    read_fee_schedule,
    line,
)


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
