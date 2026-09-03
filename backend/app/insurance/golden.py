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


COOKIE_TTL = timedelta(hours=3)
MAX_PDF_PAGES = 2
MAX_PIXELS = 20_000_000
MAX_OCR_DIMENSION = 1600
MIN_OCR_IMAGE_COVERAGE = 0.2
OCR_TIMEOUT_SECONDS = 8
STAGES = ("eligibility", "network", "service", "authorization", "benefits", "estimate")
CARD_IDENTITY_PATTERNS = {
    "member_name": r"(?im)^[ \t]*(?:member|subscriber|patient)?[ \t]*name[ \t]*[:#-]?[ \t]*(?:[^\w\r\n]{0,5}[ \t]*(?:\r?\n[ \t]*)+)?([A-Z][A-Z .,'-]{3,100})[ \t]*$",
    "member_id": r"(?i)(?:member|subscriber|identification)\s*(?:id|number|no\.?|#)\s*[:#-]?\s*([A-Z0-9-]{5,30})",
    "group_number": r"(?i)group\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{3,30})",
}
CARD_DETAIL_PATTERNS = {
    "plan_type": r"(?im)(?:^\s*(?:plan(?:\s*/\s*product|\s*type)?|product)\s*[:#-]\s*(.+?)\s*$|\b((?:blue\s*)?choice\s+ppo\s+plan(?:\s+(?:under|with)\b[^\n]*)?))",
    "coverage_date": r"(?i)(?:coverage|effective)\s*(?:date)?\s*[:#-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    "coinsurance": r"(?i)(?:coinsurance\s*[:#-]?\s*)?(\d{1,3}%\s*after\s*(?:the\s*)?(?:ded(?:uctible)?|ded))",
    "in_network_deductible": r"(?im)(?:in[- ]?network\s+(?:annual\s+)?deductible|(?:annual\s+)?deductible\s*[:#-]?\s*in[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "out_of_network_deductible": r"(?im)(?:out[- ]?of[- ]?network\s+(?:annual\s+)?deductible|(?:annual\s+)?deductible\s*[:#-]?\s*out[- ]?of[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "in_network_oop_maximum": r"(?im)(?:in[- ]?network\s+(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?|(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?\s*[:#-]?\s*in[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "out_of_network_oop_maximum": r"(?im)(?:out[- ]?of[- ]?network\s+(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?|(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?\s*[:#-]?\s*out[- ]?of[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "rx_bin": r"(?i)rx\s*bin\s*[:#-]?\s*([A-Z0-9-]{3,20})",
    "rx_pcn": r"(?i)rx\s*pcn\s*[:#-]?\s*([A-Z0-9-]{2,30})",
    "rx_group": r"(?i)rx\s*(?:grp|group)\s*[:#-]?\s*([A-Z0-9-]{2,30})",
    "preauth_medical_phone": r"(?i)pre-?auth(?:orization)?[\s-]*(?:medical)?[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "mdlive_phone": r"(?i)md\s*live[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "nurseline_phone": r"(?i)(?:24\s*/?\s*7\s*)?nurse\s*line[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "customer_service_phone": r"(?i)customer\s*(?:service|care)[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "provider_service_phone": r"(?i)provider\s*(?:service|care)[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "benefit_advocate_phone": r"(?i)benefit\s*advocate[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "eap_phone": r"(?im)^\s*eap\*?[^\d\n]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
    "pharmacy_phone": r"(?i)pharmacy\*?[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
}
CARD_CONTACT_FIELDS = frozenset(name for name in CARD_DETAIL_PATTERNS if name.endswith("_phone"))


class GoldenCaseError(ValueError):
    pass


class OCRServiceError(RuntimeError):
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
    prepared.save(path, "PNG")


def _card_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, pattern in {**CARD_IDENTITY_PATTERNS, **CARD_DETAIL_PATTERNS}.items():
        if match := re.search(pattern, text):
            value = next(value.strip() for value in match.groups() if value)
            if name == "member_name":
                value = re.sub(r"\s+(?:blue\s*)?choice\s+ppo\s+plan.*$", "", value, flags=re.I).strip()
            fields[name] = value
    benefit = pending_key = ""
    for line in text.splitlines():
        compact = " ".join(line.split())
        normalized = compact.casefold()
        if "deductible" in normalized or re.search(r"\bded\b", normalized):
            benefit = "deductible"
        elif "out-of-pocket" in normalized or "out of pocket" in normalized or re.search(r"\boop\b", normalized):
            benefit = "oop_maximum"
        network = "in_network" if re.search(r"\bin[- ]?network\b", normalized) else "out_of_network" if re.search(r"\bout[- ]?of[- ]?network\b", normalized) else ""
        if benefit and network:
            key = f"{network}_{benefit}"
            value = re.sub(r"^.*?(?:in[- ]?network|out[- ]?of[- ]?network)\s*[:#-]?\s*", "", compact, flags=re.I)
            amounts = re.findall(r"\$\s*\d[\d,]*(?:\.\d{2})?", value)
            if re.search(r"\bind\s*/\s*fam\b", normalized) and len(amounts) >= 2:
                value = f"{amounts[0]} individual / {amounts[1]} family"
            pending_key = key if not value else ""
            if value:
                fields.setdefault(key, value)
        elif pending_key and re.search(r"\$\s*\d", compact):
            fields.setdefault(pending_key, compact)
            pending_key = ""
    pending_contacts: list[str] = []
    contact_labels = {
        "customer service": "customer_service_phone", "provider service": "provider_service_phone",
        "preauth medical": "preauth_medical_phone", "24 7 nurseline": "nurseline_phone",
        "mdlive": "mdlive_phone", "eap": "eap_phone", "benefit advocate": "benefit_advocate_phone",
        "pharmacy": "pharmacy_phone",
    }
    for line in text.splitlines():
        compact = " ".join(line.split())
        normalized = re.sub(r"[^a-z0-9]+", " ", compact.casefold()).strip()
        phone_numbers = re.findall(r"(?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", compact)
        contact = next((key for label, key in contact_labels.items() if normalized.startswith(label)), "")
        if contact and phone_numbers:
            fields.setdefault(contact, phone_numbers[0])
        elif contact:
            pending_contacts.append(contact)
        elif pending_contacts and phone_numbers:
            for phone in phone_numbers:
                if pending_contacts:
                    fields.setdefault(pending_contacts.pop(0), phone)
        elif compact:
            pending_contacts.clear()
    return fields


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
        _write_ocr_image(image, path)
        return _ocr_images([path])


def _pdf_text(content: bytes) -> str:
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.needs_pass or not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise GoldenCaseError("Insurance card PDFs must contain one or two unlocked pages")
        parts = [page.get_text(sort=True) for page in document]
        # Searchable wrapper text does not make an embedded card image
        # searchable. OCR each substantial card image, including the back.
        with tempfile.TemporaryDirectory() as directory:
            paths: list[Path] = []
            for number, page in enumerate(document):
                images = [
                    item for item in page.get_image_info(xrefs=True)
                    if fitz.Rect(item["bbox"]).get_area() >= page.rect.get_area() * MIN_OCR_IMAGE_COVERAGE
                ]
                if images:
                    image = max(images, key=lambda item: fitz.Rect(item["bbox"]).get_area())
                    if image.get("xref"):
                        pixmap = fitz.Pixmap(document, image["xref"])
                    else:
                        pixmap = page.get_pixmap(
                            matrix=fitz.Matrix(2, 2), clip=fitz.Rect(image["bbox"]), colorspace=fitz.csRGB, alpha=False,
                        )
                elif not "".join(parts[number].split()):
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False)
                else:
                    continue
                if pixmap.width * pixmap.height > MAX_PIXELS:
                    raise GoldenCaseError("The insurance card PDF pages are too large")
                if pixmap.alpha:
                    pixmap = fitz.Pixmap(pixmap, 0)
                if pixmap.colorspace.n != 3:
                    pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                path = Path(directory) / f"card-{number}.png"
                _write_ocr_image(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples), path)
                paths.append(path)
            return _ocr_images(paths, parts)
    except GoldenCaseError:
        raise
    except OCRServiceError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable PDF") from exc


def _ocr_images(paths: list[Path], initial: list[str] | None = None) -> str:
    parts = list(initial or ())
    parts.extend(_tesseract(path) for path in paths)
    fields = _card_fields("\n".join(parts))
    if CARD_CONTACT_FIELDS.intersection(fields) and not CARD_CONTACT_FIELDS.issubset(fields):
        # Sparse contact columns need a second layout, usually on the card back.
        # Start there and stop as soon as the known contact set is complete.
        for path in reversed(paths):
            try:
                parts.append(_tesseract(path, "3"))
            except OCRServiceError:
                continue
            if CARD_CONTACT_FIELDS.issubset(_card_fields("\n".join(parts))):
                break
    return "\n".join(parts)


def _tesseract(path: Path, mode: str = "6") -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise OCRServiceError("Tesseract OCR is not installed")
    try:
        result = subprocess.run(
            (executable, str(path), "stdout", "--psm", mode), capture_output=True,
            text=True, timeout=OCR_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise OCRServiceError("Insurance card OCR timed out")
    if result.returncode == 0:
        return result.stdout
    raise OCRServiceError("Insurance card OCR failed")


def read_card(content: bytes, member_name: str = "") -> dict[str, str]:
    if content.startswith(b"%PDF"):
        text = _pdf_text(content)
    elif content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        text = _image_text(content)
    else:
        raise GoldenCaseError("Upload a PDF, JPG, or PNG insurance card")
    fields = _card_fields(text)
    if member_name and not fields.get("member_name"):
        fields["member_name"] = member_name.strip()
    if not all(name in fields for name in CARD_IDENTITY_PATTERNS):
        raise GoldenCaseError("ClinicForce could not read all card fields. Upload a clear image of the front of the card")
    return fields


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
