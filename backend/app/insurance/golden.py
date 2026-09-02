"""Deterministic, evidence-backed BCBS/NMCT golden-case workflow."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

import fitz
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, UnidentifiedImageError

from app.core.object_store import get_object_bytes


COOKIE_TTL = timedelta(minutes=15)
MAX_PDF_PAGES = 2
MAX_PIXELS = 20_000_000
MAX_OCR_DIMENSION = 2400
STAGES = ("eligibility", "network", "service", "authorization", "benefits", "estimate")
CARD_FIELD_PATTERNS = {
    "member_name": r"(?im)^[ \t]*(?:member|subscriber|patient)[ \t]+name[ \t]*[:#-]?[ \t]*(?:\r?\n[ \t]*)?([A-Z][A-Z .,'-]{3,70})[ \t]*$",
    "member_id": r"(?i)(?:member|subscriber|identification)\s*(?:id|number|no\.?|#)\s*[:#-]?\s*([A-Z0-9-]{5,30})",
    "group_number": r"(?i)group\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{3,30})",
}


class GoldenCaseError(ValueError):
    pass


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_key() -> bytes:
    secret = os.getenv("INSURANCE_SESSION_SECRET", "").strip() or os.getenv("SOURCE_LINK_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("INSURANCE_SESSION_SECRET or SOURCE_LINK_SECRET must contain at least 32 characters")
    return hashlib.sha256(("clinicforce:insurance-share:v1:" + secret).encode()).digest()


def evidence_binding() -> str:
    key = os.getenv("INSURANCE_GOLDEN_BUNDLE_KEY", "").strip()
    path = os.getenv("INSURANCE_GOLDEN_MANIFEST_PATH", "").strip()
    return hashlib.sha256((key or path).encode()).hexdigest()


def create_share_token(patient_id: str, share_manifest_id: str, beta_enabled: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = json.dumps({
        "v": 1, "patient_id": patient_id, "share_manifest_id": share_manifest_id,
        "evidence_binding": evidence_binding(), "beta_enabled": beta_enabled,
        "exp": int((now + COOKIE_TTL).timestamp()),
    }, separators=(",", ":"), sort_keys=True).encode()
    nonce = os.urandom(12)
    return _b64(nonce + AESGCM(_session_key()).encrypt(nonce, payload, b"clinicforce-insurance-share"))


def open_share_token(token: str, require_beta: bool = False) -> dict[str, Any]:
    try:
        raw = _unb64(token)
        payload = json.loads(AESGCM(_session_key()).decrypt(raw[:12], raw[12:], b"clinicforce-insurance-share"))
        if payload.get("v") != 1 or payload.get("evidence_binding") != evidence_binding():
            raise GoldenCaseError("The clinician share session is not valid for this evidence bundle")
        if int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            raise GoldenCaseError("The clinician share session has expired")
        if require_beta and payload.get("beta_enabled") is not True:
            raise GoldenCaseError("Enable the insurance beta from the patient dashboard first")
        return payload
    except GoldenCaseError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The clinician share session is invalid") from exc


def load_manifest() -> dict[str, Any]:
    path = os.getenv("INSURANCE_GOLDEN_MANIFEST_PATH", "").strip()
    key = os.getenv("INSURANCE_GOLDEN_BUNDLE_KEY", "").strip()
    if path:
        content = Path(path).read_bytes()
    elif key:
        content = get_object_bytes(key)
    else:
        raise GoldenCaseError("The golden evidence bundle is not configured")
    try:
        if content.startswith(b"PK"):
            with ZipFile(BytesIO(content)) as bundle:
                manifest = json.loads(bundle.read("manifest.json"))
                if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
                    raise GoldenCaseError("The golden evidence manifest is invalid")
                for source in manifest.get("sources", []):
                    if not isinstance(source, dict):
                        raise GoldenCaseError("The golden evidence manifest is invalid")
                    filename = source.get("filename", "")
                    if Path(filename).name != filename:
                        raise GoldenCaseError("The golden evidence manifest contains an unsafe filename")
                    actual = hashlib.sha256(bundle.read(f"sources/{filename}")).hexdigest()
                    if actual != source.get("sha256"):
                        raise GoldenCaseError("The golden evidence bundle failed its SHA-256 review check")
                rates_source = next((source for source in manifest["sources"] if source.get("id") == "rates"), None)
                if rates_source:
                    rows = csv.DictReader(bundle.read(f"sources/{rates_source['filename']}").decode("utf-8-sig").splitlines())
                    manifest["rate_lines"] = [
                        {
                            "code": row["Procedure Code"].strip(),
                            "description": row["Description"].strip(),
                            "allowed_cents": int(row["Amount"].replace("$", "").replace(".", "")),
                        }
                        for row in rows
                        if row.get("Procedure Code", "").strip().isdigit() and row.get("Amount", "").startswith("$")
                    ]
        else:
            manifest = json.loads(content)
    except GoldenCaseError:
        raise
    except (BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoldenCaseError("The golden evidence manifest is invalid") from exc
    required = ("manifest_id", "identity", "appointment", "card_source", "coverage", "network", "authorization", "rates", "sources")
    if not isinstance(manifest, dict) or any(name not in manifest for name in required):
        raise GoldenCaseError("The golden evidence manifest is incomplete")
    return manifest


def _hash(value: str) -> str:
    return hashlib.sha256(re.sub(r"[^a-z0-9]", "", value.casefold()).encode()).hexdigest()


def patient_matches(profile: Any, manifest: dict[str, Any]) -> bool:
    identity = manifest["identity"]
    return bool(profile and profile.given_name and profile.family_name and profile.birth_date) and all((
        _hash(profile.given_name) == identity.get("given_name_sha256"),
        _hash(profile.family_name) == identity.get("family_name_sha256"),
        _hash(profile.birth_date) == identity.get("birth_date_sha256"),
    ))


def _write_ocr_image(image: Image.Image, path: Path) -> None:
    prepared = image.convert("RGB")
    prepared.thumbnail((MAX_OCR_DIMENSION, MAX_OCR_DIMENSION), Image.Resampling.LANCZOS)
    prepared.save(path, "JPEG", quality=85)


def _card_fields(text: str) -> dict[str, str]:
    return {name: match.group(1).strip() for name, pattern in CARD_FIELD_PATTERNS.items() if (match := re.search(pattern, text))}


def _retry_name_ocr(image: Image.Image) -> str:
    """Crop the left-middle region of a card and re-run OCR to recover the subscriber name."""
    crop = image.crop((
        int(image.width * 0.05), int(image.height * 0.15),
        int(image.width * 0.58), int(image.height * 0.65),
    ))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "name-region.jpg"
        _write_ocr_image(crop, path)
        return _tesseract(path)


def _image_text(content: bytes) -> str:
    try:
        image = Image.open(BytesIO(content))
        if image.width * image.height > MAX_PIXELS:
            raise GoldenCaseError("The insurance card image is too large")
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable image") from exc
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "card.jpg"
        _write_ocr_image(image, path)
        text = _tesseract(path)
        if "member_name" not in _card_fields(text):
            text += "\n" + _retry_name_ocr(image)
        return text


def _pdf_text(content: bytes) -> str:
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.needs_pass or not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise GoldenCaseError("Insurance card PDFs must contain one or two unlocked pages")
        parts = [page.get_text() for page in document]
        if len(_card_fields("\n".join(parts))) == len(CARD_FIELD_PATTERNS):
            return "\n".join(parts)
        with tempfile.TemporaryDirectory() as directory:
            for index, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                if pixmap.width * pixmap.height > MAX_PIXELS:
                    raise GoldenCaseError("The insurance card PDF pages are too large")
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                path = Path(directory) / f"page-{index}.jpg"
                _write_ocr_image(image, path)
                parts.append(_tesseract(path))
                if len(_card_fields("\n".join(parts))) == len(CARD_FIELD_PATTERNS):
                    break
                # If we found some fields but the name is missing, crop and retry
                found = _card_fields("\n".join(parts))
                if found and "member_name" not in found:
                    parts.append(_retry_name_ocr(image))
                    if len(_card_fields("\n".join(parts))) == len(CARD_FIELD_PATTERNS):
                        break
        return "\n".join(parts)
    except GoldenCaseError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable PDF") from exc


def _tesseract(path: Path) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    result = subprocess.run(
        (executable, str(path), "stdout", "--psm", "3"), capture_output=True,
        text=True, check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def read_card(content: bytes) -> dict[str, str]:
    if content.startswith(b"%PDF"):
        text = _pdf_text(content)
    elif content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        text = _image_text(content)
    else:
        raise GoldenCaseError("Upload a PDF, JPG, or PNG insurance card")
    fields = _card_fields(text)
    return {name: fields.get(name, "") for name in CARD_FIELD_PATTERNS}


def card_matches(fields: dict[str, str], manifest: dict[str, Any]) -> bool:
    identity = manifest["identity"]
    name_tokens = [part for part in re.split(r"\s+", fields.get("member_name", "").strip()) if part]
    name_hashes = {_hash(part) for part in name_tokens}
    return len(name_tokens) >= 2 and all((
        identity.get("given_name_sha256") in name_hashes,
        identity.get("family_name_sha256") in name_hashes,
        _hash(fields.get("member_id", "")) == identity.get("member_id_sha256"),
        _hash(fields.get("group_number", "")) == identity.get("group_number_sha256"),
    ))


def pending_case(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "source_mode": "golden", "status": "review", "next_stage": "intake",
        "ocr": {**fields, "confirmed": False}, "appointment": None, "coverage": None,
        "service_lines": [], "clinical_context": "", "stage_decisions": [], "facts": [], "sources": [], "estimate": None,
    }


def unlock_case(case: dict[str, Any], manifest: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    if not card_matches(fields, manifest):
        raise GoldenCaseError("This beta supports only the reviewed BCBS/NMCT golden case")
    case.update({
        # The reviewed card identifies the demo patient only. Benefits and the
        # provider agreement must still be uploaded and confirmed for this visit.
        "source_mode": "documents", "status": "in_progress", "next_stage": "eligibility", "ocr": {**fields, "confirmed": True},
        "appointment": manifest["appointment"], "coverage": None, "documents": {}, "service_lines": [],
        "facts": [
            _fact("intake", "Appointment", "New patient neurology office visit · neuropathy", manifest["appointment"]["source"]),
            _fact("intake", "Member card match", "Member name, member ID, and group number confirmed", manifest["card_source"]),
        ], "sources": [],
    })
    return case


def _base_facts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    coverage, network, authorization = manifest["coverage"], manifest["network"], manifest["authorization"]
    return [
        _fact("intake", "Appointment", "New patient neurology office visit · neuropathy", manifest["appointment"]["source"]),
        _fact("intake", "Member card match", "Member name, member ID, and group number confirmed", manifest["card_source"]),
        _fact("eligibility", "Medical coverage", "Active · BCBSTX Preferred Provider Option Medical", coverage["source"]),
        _fact("eligibility", "Individual deductible remaining", "$0.00", coverage["source"]),
        _fact("eligibility", "Individual out-of-pocket remaining", "$0.00", coverage["source"]),
        _fact("eligibility", "Eligibility transaction", coverage["transaction_reference"], coverage["source"]),
        _fact("network", "Network match", network["display"], network["source"]),
        _fact("network", "Participation terms", "Blue Choice PPO; lesser of billed charge or current fee schedule, less cost share", network["agreement_source"]),
        _fact("service", "EMG/NCS", "Not scheduled in the reviewed appointment; select only if the clinician plans it for this visit", manifest["appointment"]["source"]),
        _fact("authorization", "Referral", "Not required for this in-network PPO visit", authorization["referral_source"]),
        _fact("authorization", "Prior authorization", "Not required for selected CPT 99204", authorization["prior_auth_source"]),
    ]


def _fact(stage: str, label: str, value: str, citation: dict[str, Any]) -> dict[str, Any]:
    return {"stage": stage, "label": label, "value": value, "citation": citation, "source_mode": "golden"}


def select_services(case: dict[str, Any], codes: list[str], manifest: dict[str, Any]) -> dict[str, Any]:
    if case.get("next_stage") != "service":
        raise GoldenCaseError("Complete the preceding stages before confirming the anticipated service")
    selected_codes = set(codes)
    selected = [line for line in case["service_lines"] if line["code"] in selected_codes]
    if len(selected) != len(selected_codes):
        raise GoldenCaseError("Every selected CPT must be a suggested candidate")
    office_codes = [line["code"] for line in selected if line["code"].startswith("992")]
    if len(office_codes) != 1:
        raise GoldenCaseError("Choose exactly one anticipated office-visit code")
    for line in case["service_lines"]:
        line.update(selected=line["code"] in selected_codes, confirmed=line["code"] in selected_codes)
    case["next_stage"] = "authorization"
    display = ", ".join(line["code"] for line in selected)
    case["stage_decisions"].append({"stage": "service", "status": "passed", "decision": f"CPT {display} confirmed as anticipated pre-visit services", "source_mode": "golden"})
    case["facts"].append(_fact("service", "Anticipated services", f"{display} · clinician confirmed; final coding remains post-encounter", manifest["rates_source"]))
    return case


def select_service(case: dict[str, Any], code: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the original one-office-visit golden case."""
    return select_services(case, [code], manifest)


def advance(case: dict[str, Any], stage: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if stage not in STAGES or case.get("next_stage") != stage or stage == "service":
        raise GoldenCaseError("Complete the insurance stages in order")
    decisions = {
        "eligibility": "Active medical coverage and individual accumulators confirmed",
        "network": "NMCT provider and office location confirmed in Blue Choice PPO",
        "authorization": "Clinician confirmed referral and prior-authorization evidence for the selected services",
        "benefits": "Contracted rate and benefit arithmetic confirmed",
        "estimate": "Pre-visit estimate completed",
    }
    next_stage = {"eligibility": "network", "network": "service", "authorization": "benefits", "benefits": "estimate", "estimate": None}[stage]
    case["stage_decisions"].append({"stage": stage, "status": "passed", "decision": decisions[stage], "source_mode": "golden"})
    case["next_stage"] = next_stage
    if stage == "benefits":
        case["estimate"] = calculate(case, manifest)
        estimate = case["estimate"]
        if estimate.get("estimate_available", True):
            case["facts"].extend((
                _fact("benefits", "Contracted allowed amount", _money(estimate["allowed_cents"]), manifest["rates_source"]),
                _fact("benefits", "Nominal 20% coinsurance", _money(estimate["nominal_coinsurance_cents"]), manifest["coverage"]["source"]),
                _fact("benefits", "Out-of-pocket cap adjustment", "−" + _money(abs(estimate["oop_cap_adjustment_cents"])), manifest["coverage"]["source"]),
            ))
        else:
            case["facts"].append(_fact("benefits", "Estimate status", estimate["unavailable_reason"], manifest["rates_source"]))
    if stage == "estimate":
        case["status"] = "complete"
        estimate = case["estimate"]
        if estimate.get("estimate_available", True):
            case["facts"].extend((
                _fact("estimate", "Allowed amount", _money(estimate["allowed_cents"]), manifest["rates_source"]),
                _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), manifest["rates_source"]),
                _fact("estimate", "Cost-share result", "Nominal 20% coinsurance reduced to $0.00 because individual OOP remaining is $0.00", manifest["coverage"]["source"]),
                _fact("estimate", "Rejected collection instruction", "$241.51 · included an unselected EMG service and is not reproducible", manifest["rejected_source"]),
            ))
        else:
            case["facts"].append(_fact("estimate", "Collection amount", "Not estimated", manifest["rates_source"]))
    return case


def calculate(case: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    selected = [line for line in case["service_lines"] if line["selected"] and line["confirmed"]]
    if any(line.get("allowed_cents") is None for line in selected):
        return {
            "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected],
            "estimate_available": False, "unavailable_reason": "At least one selected CPT has no reviewed NMCT rate. Add the rate before collecting an estimate.",
            "allowed_cents": None, "nominal_coinsurance_cents": None, "individual_deductible_remaining_cents": 0,
            "individual_oop_remaining_cents": int(manifest["coverage"]["individual_oop_remaining_cents"]), "oop_cap_adjustment_cents": None,
            "patient_cents": None, "plan_cents": None, "source_mode": "golden", "calculated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": "A price requires a reviewed NMCT rate for every selected anticipated CPT.",
        }
    allowed = sum(int(line["allowed_cents"]) for line in selected)
    percent = Decimal(str(manifest["coverage"]["coinsurance_percent"])) / Decimal(100)
    nominal = int((Decimal(allowed) * percent).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    oop_remaining = int(manifest["coverage"]["individual_oop_remaining_cents"])
    patient = min(nominal, oop_remaining)
    return {
        "service_code": ", ".join(line["code"] for line in selected), "service_codes": [line["code"] for line in selected], "allowed_cents": allowed,
        "estimate_available": True, "nominal_coinsurance_cents": nominal, "individual_deductible_remaining_cents": 0,
        "individual_oop_remaining_cents": oop_remaining, "oop_cap_adjustment_cents": patient - nominal,
        "patient_cents": patient, "plan_cents": allowed - patient, "source_mode": "golden",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Estimate for covered, in-network care; not a guarantee of payment or final coding.",
    }


def _money(cents: int) -> str:
    return f"${Decimal(cents) / Decimal(100):,.2f}"
