"""Deterministic, evidence-backed BCBS/NMCT golden-case workflow."""

from __future__ import annotations

import base64
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
STAGES = ("eligibility", "network", "service", "authorization", "benefits", "estimate")


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


def _image_text(content: bytes) -> str:
    try:
        image = Image.open(BytesIO(content))
        if image.width * image.height > MAX_PIXELS:
            raise GoldenCaseError("The insurance card image is too large")
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable image") from exc
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "card.png"
        image.convert("RGB").save(path)
        return _tesseract(path)


def _pdf_text(content: bytes) -> str:
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.needs_pass or not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise GoldenCaseError("Insurance card PDFs must contain one or two unlocked pages")
        parts: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            for index, page in enumerate(document):
                parts.append(page.get_text())
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                if pixmap.width * pixmap.height > MAX_PIXELS:
                    raise GoldenCaseError("The insurance card PDF pages are too large")
                path = Path(directory) / f"page-{index}.png"
                pixmap.save(path)
                parts.append(_tesseract(path))
        return "\n".join(parts)
    except GoldenCaseError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable PDF") from exc


def _tesseract(path: Path) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("Tesseract OCR is not installed")
    try:
        result = subprocess.run(
            (executable, str(path), "stdout", "--psm", "6"), capture_output=True,
            text=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GoldenCaseError("Insurance card OCR timed out") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise GoldenCaseError("ClinicForce could not read the insurance card")
    return result.stdout


def read_card(content: bytes) -> dict[str, str]:
    if content.startswith(b"%PDF"):
        text = _pdf_text(content)
    elif content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        text = _image_text(content)
    else:
        raise GoldenCaseError("Upload a PDF, JPG, or PNG insurance card")
    patterns = {
        "member_name": r"(?im)^\s*(?:member|subscriber|patient)(?:\s+name)?\s*[:#-]?\s*([A-Z][A-Z .,'-]{3,70})\s*$",
        "member_id": r"(?i)(?:member|subscriber|identification)\s*(?:id|number|no\.?|#)\s*[:#-]?\s*([A-Z0-9-]{5,30})",
        "group_number": r"(?i)group\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{3,30})",
    }
    fields = {name: match.group(1).strip() for name, pattern in patterns.items() if (match := re.search(pattern, text))}
    if len(fields) != len(patterns):
        raise GoldenCaseError("Confirm the member name, member ID, and group number from a clearer card image")
    return fields


def card_matches(fields: dict[str, str], manifest: dict[str, Any]) -> bool:
    identity = manifest["identity"]
    name_tokens = [part for part in re.split(r"\s+", fields.get("member_name", "").strip()) if part]
    return len(name_tokens) >= 2 and all((
        _hash(name_tokens[0]) == identity.get("given_name_sha256"),
        _hash(name_tokens[-1]) == identity.get("family_name_sha256"),
        _hash(fields.get("member_id", "")) == identity.get("member_id_sha256"),
        _hash(fields.get("group_number", "")) == identity.get("group_number_sha256"),
    ))


def pending_case(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "source_mode": "golden", "status": "review", "next_stage": "intake",
        "ocr": {**fields, "confirmed": False}, "appointment": None, "coverage": None,
        "service_lines": [], "stage_decisions": [], "facts": [], "sources": [], "estimate": None,
    }


def unlock_case(case: dict[str, Any], manifest: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    if not card_matches(fields, manifest):
        raise GoldenCaseError("This beta supports only the reviewed BCBS/NMCT golden case")
    case.update({
        "status": "in_progress", "next_stage": "eligibility", "ocr": {**fields, "confirmed": True},
        "appointment": manifest["appointment"], "coverage": manifest["coverage"],
        "service_lines": [
            {"code": "99204", "description": "New patient office visit, moderate MDM", "allowed_cents": int(manifest["rates"]["99204"]), "selected": True, "confirmed": False},
            {"code": "99205", "description": "New patient office visit, high MDM", "allowed_cents": int(manifest["rates"]["99205"]), "selected": False, "confirmed": False},
        ],
        "facts": _base_facts(manifest), "sources": list(manifest["sources"]),
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
        _fact("service", "EMG/NCS", "Excluded — not scheduled for this visit", manifest["appointment"]["source"]),
        _fact("authorization", "Referral", "Not required for this in-network PPO visit", authorization["referral_source"]),
        _fact("authorization", "Prior authorization", "Not required for selected CPT 99204", authorization["prior_auth_source"]),
    ]


def _fact(stage: str, label: str, value: str, citation: dict[str, Any]) -> dict[str, Any]:
    return {"stage": stage, "label": label, "value": value, "citation": citation, "source_mode": "golden"}


def select_service(case: dict[str, Any], code: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if case.get("next_stage") != "service" or code not in {"99204", "99205"}:
        raise GoldenCaseError("Complete the preceding stages before confirming the anticipated service")
    for line in case["service_lines"]:
        line.update(selected=line["code"] == code, confirmed=line["code"] == code)
    case["next_stage"] = "authorization"
    case["stage_decisions"].append({"stage": "service", "status": "passed", "decision": f"CPT {code} confirmed as an anticipated pre-visit service", "source_mode": "golden"})
    case["facts"].append(_fact("service", "Anticipated service", f"{code} · clinician confirmed; final coding remains post-encounter", manifest["rates_source"]))
    return case


def advance(case: dict[str, Any], stage: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if stage not in STAGES or case.get("next_stage") != stage or stage == "service":
        raise GoldenCaseError("Complete the insurance stages in order")
    decisions = {
        "eligibility": "Active medical coverage and individual accumulators confirmed",
        "network": "NMCT provider and office location confirmed in Blue Choice PPO",
        "authorization": "Referral and prior authorization are not required for the selected visit",
        "benefits": "Contracted rate and benefit arithmetic confirmed",
        "estimate": "Pre-visit estimate completed",
    }
    next_stage = {"eligibility": "network", "network": "service", "authorization": "benefits", "benefits": "estimate", "estimate": None}[stage]
    case["stage_decisions"].append({"stage": stage, "status": "passed", "decision": decisions[stage], "source_mode": "golden"})
    case["next_stage"] = next_stage
    if stage == "benefits":
        case["estimate"] = calculate(case, manifest)
        estimate = case["estimate"]
        case["facts"].extend((
            _fact("benefits", "Contracted allowed amount", _money(estimate["allowed_cents"]), manifest["rates_source"]),
            _fact("benefits", "Nominal 20% coinsurance", _money(estimate["nominal_coinsurance_cents"]), manifest["coverage"]["source"]),
            _fact("benefits", "Out-of-pocket cap adjustment", "−" + _money(abs(estimate["oop_cap_adjustment_cents"])), manifest["coverage"]["source"]),
        ))
    if stage == "estimate":
        case["status"] = "complete"
        estimate = case["estimate"]
        case["facts"].extend((
            _fact("estimate", "Allowed amount", _money(estimate["allowed_cents"]), manifest["rates_source"]),
            _fact("estimate", "Estimated plan responsibility", _money(estimate["plan_cents"]), manifest["rates_source"]),
            _fact("estimate", "Cost-share result", "Nominal 20% coinsurance reduced to $0.00 because individual OOP remaining is $0.00", manifest["coverage"]["source"]),
            _fact("estimate", "Rejected collection instruction", "$241.51 · included an unselected EMG service and is not reproducible", manifest["rejected_source"]),
        ))
    return case


def calculate(case: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    selected = next(line for line in case["service_lines"] if line["selected"] and line["confirmed"])
    allowed = int(selected["allowed_cents"])
    percent = Decimal(str(manifest["coverage"]["coinsurance_percent"])) / Decimal(100)
    nominal = int((Decimal(allowed) * percent).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    oop_remaining = int(manifest["coverage"]["individual_oop_remaining_cents"])
    patient = min(nominal, oop_remaining)
    return {
        "service_code": selected["code"], "allowed_cents": allowed,
        "nominal_coinsurance_cents": nominal, "individual_deductible_remaining_cents": 0,
        "individual_oop_remaining_cents": oop_remaining, "oop_cap_adjustment_cents": patient - nominal,
        "patient_cents": patient, "plan_cents": allowed - patient, "source_mode": "golden",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Estimate for covered, in-network care; not a guarantee of payment or final coding.",
    }


def _money(cents: int) -> str:
    return f"${Decimal(cents) / Decimal(100):,.2f}"
