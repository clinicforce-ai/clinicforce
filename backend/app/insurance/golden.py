"""Shared insurance-session and local document-reading helpers.

Despite the historical module name, this module contains no fixture or payer
data. It only returns text that is present in an uploaded document.
"""

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
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import boto3
from botocore.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, UnidentifiedImageError


COOKIE_TTL = timedelta(hours=3)
MAX_PDF_PAGES = 2
MAX_PIXELS = 20_000_000
MAX_OCR_DIMENSION = 1600
MIN_OCR_IMAGE_COVERAGE = 0.2
OCR_TIMEOUT_SECONDS = 20
CARD_IDENTITY_PATTERNS = {
    "member_name": r"(?im)^[ \t]*(?:member|subscriber|patient)?[ \t]*name[ \t]*[:#-]?[ \t]*(?:[^\w\r\n]{0,5}[ \t]*(?:\r?\n[ \t]*)+)?([A-Z][A-Z .,'-]{3,100})[ \t]*$",
    "member_id": r"(?i)(?:member|subscriber|identification)\s*(?:id|number|no\.?|#)\s*[:#-]?\s*([A-Z0-9-]{5,30})",
    "group_number": r"(?i)group\s*(?:number|no\.?|#)?\s*[:#-]?\s*([A-Z0-9-]{3,30})",
}
CARD_DETAIL_PATTERNS = {
    "plan_type": r"(?im)^\s*(?:plan(?:\s*/\s*product|\s*type)?|product)\s*[:#-]\s*(.+?)\s*$",
    "coverage_date": r"(?i)(?:coverage|effective)\s*(?:date)?\s*[:#-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    "coinsurance": r"(?i)(?:coinsurance\s*[:#-]?\s*)?(\d{1,3}%\s*after\s*(?:the\s*)?(?:ded(?:uctible)?|ded))",
    "in_network_deductible": r"(?im)(?:in[- ]?network\s+(?:annual\s+)?deductible|(?:annual\s+)?deductible\s*[:#-]?\s*in[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "out_of_network_deductible": r"(?im)(?:out[- ]?of[- ]?network\s+(?:annual\s+)?deductible|(?:annual\s+)?deductible\s*[:#-]?\s*out[- ]?of[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "in_network_oop_maximum": r"(?im)(?:in[- ]?network\s+(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?|(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?\s*[:#-]?\s*in[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "out_of_network_oop_maximum": r"(?im)(?:out[- ]?of[- ]?network\s+(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?|(?:out[- ]?of[- ]?pocket|oop)(?:\s+(?:max(?:imum)?|limit))?\s*[:#-]?\s*out[- ]?of[- ]?network)\s*[:#-]?\s*([^\n]{1,100})",
    "rx_bin": r"(?i)rx\s*bin\s*[:#-]?\s*([A-Z0-9-]{3,20})", "rx_pcn": r"(?i)rx\s*pcn\s*[:#-]?\s*([A-Z0-9-]{2,30})", "rx_group": r"(?i)rx\s*(?:grp|group)\s*[:#-]?\s*([A-Z0-9-]{2,30})",
    "preauth_medical_phone": r"(?i)pre-?auth(?:orization)?[\s-]*(?:medical)?[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "mdlive_phone": r"(?i)md\s*live[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "nurseline_phone": r"(?i)(?:24\s*/?\s*7\s*)?nurse\s*line[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "customer_service_phone": r"(?i)customer\s*(?:service|care)[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "provider_service_phone": r"(?i)provider\s*(?:service|care)[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "benefit_advocate_phone": r"(?i)benefit\s*advocate[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "eap_phone": r"(?im)^\s*eap\*?[^\d\n]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})", "pharmacy_phone": r"(?i)pharmacy\*?[^\d]{0,40}((?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})",
}


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
    return hashlib.sha256(b"clinicforce:insurance:document-only:v1").hexdigest()


def create_share_token(patient_id: str, share_manifest_id: str, beta_enabled: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = json.dumps({"v": 1, "patient_id": patient_id, "share_manifest_id": share_manifest_id, "evidence_binding": evidence_binding(), "beta_enabled": beta_enabled, "exp": int((now + COOKIE_TTL).timestamp())}, separators=(",", ":"), sort_keys=True).encode()
    nonce = os.urandom(12)
    return _b64(nonce + AESGCM(_session_key()).encrypt(nonce, payload, b"clinicforce-insurance-share"))


def open_share_token(token: str, require_beta: bool = False) -> dict[str, Any]:
    try:
        raw = _unb64(token)
        payload = json.loads(AESGCM(_session_key()).decrypt(raw[:12], raw[12:], b"clinicforce-insurance-share"))
        if payload.get("v") != 1 or payload.get("evidence_binding") != evidence_binding() or int(payload.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            raise GoldenCaseError("The clinician share session is invalid or expired")
        if require_beta and payload.get("beta_enabled") is not True:
            raise GoldenCaseError("Enable the insurance beta from the patient dashboard first")
        return payload
    except GoldenCaseError:
        raise
    except Exception as exc:
        raise GoldenCaseError("The clinician share session is invalid") from exc


def _write_ocr_image(image: Image.Image, path: Path) -> None:
    prepared = image.convert("RGB")
    prepared.thumbnail((MAX_OCR_DIMENSION, MAX_OCR_DIMENSION), Image.Resampling.LANCZOS)
    prepared.save(path, "PNG")


def _card_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, pattern in {**CARD_IDENTITY_PATTERNS, **CARD_DETAIL_PATTERNS}.items():
        if match := re.search(pattern, text):
            fields[name] = next(value.strip() for value in match.groups() if value)
    benefit = pending_key = ""
    for line in text.splitlines():
        compact, normalized = " ".join(line.split()), line.casefold()
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
    contact_labels = {"customer service": "customer_service_phone", "provider service": "provider_service_phone", "preauth medical": "preauth_medical_phone", "24 7 nurseline": "nurseline_phone", "mdlive": "mdlive_phone", "eap": "eap_phone", "benefit advocate": "benefit_advocate_phone", "pharmacy": "pharmacy_phone"}
    for line in text.splitlines():
        compact = " ".join(line.split())
        normalized = re.sub(r"[^a-z0-9]+", " ", compact.casefold()).strip()
        phones = re.findall(r"(?:1[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", compact)
        contact = next((key for label, key in contact_labels.items() if normalized.startswith(label)), "")
        if contact and phones:
            fields.setdefault(contact, phones[0])
        elif contact:
            pending_contacts.append(contact)
        elif pending_contacts and phones:
            fields.setdefault(pending_contacts.pop(0), phones[0])
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
        try:
            if not os.getenv("ECS_CONTAINER_METADATA_URI_V4") and os.getenv("INSURANCE_CARD_OCR") != "textract":
                return _ocr_images([path])
            client = boto3.client("textract", region_name=os.getenv("AWS_REGION", "us-east-1"),
                                  config=Config(connect_timeout=3, read_timeout=12, retries={"max_attempts": 0}))
            result = client.detect_document_text(Document={"Bytes": path.read_bytes()})
            text = "\n".join(block["Text"] for block in result.get("Blocks", []) if block.get("BlockType") == "LINE")
            if text.strip():
                return text
        except Exception:
            pass  # Local OCR keeps uploads usable if Textract is unavailable.
        return _ocr_images([path])


def _pdf_text(content: bytes) -> str:
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.needs_pass or not 1 <= document.page_count <= MAX_PDF_PAGES:
            raise GoldenCaseError("Insurance-card PDFs must contain one or two unlocked pages")
        parts = [page.get_text(sort=True) for page in document]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for number, page in enumerate(document):
                images = [item for item in page.get_image_info(xrefs=True) if fitz.Rect(item["bbox"]).get_area() >= page.rect.get_area() * MIN_OCR_IMAGE_COVERAGE]
                if not images:
                    continue
                pixmap = fitz.Pixmap(document, max(images, key=lambda item: fitz.Rect(item["bbox"]).get_area())["xref"])
                if pixmap.width * pixmap.height > MAX_PIXELS:
                    raise GoldenCaseError("The insurance-card PDF pages are too large")
                if pixmap.alpha:
                    pixmap = fitz.Pixmap(pixmap, 0)
                path = Path(directory) / f"card-{number}.png"
                _write_ocr_image(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples), path)
                paths.append(path)
            return _ocr_images(paths, parts)
    except (GoldenCaseError, OCRServiceError):
        raise
    except Exception as exc:
        raise GoldenCaseError("The uploaded insurance card is not a readable PDF") from exc


def _ocr_images(paths: list[Path], initial: list[str] | None = None) -> str:
    return "\n".join([*(initial or []), *(_tesseract(path, "6" if index == 0 else "3") for index, path in enumerate(paths))])


def _tesseract(path: Path, mode: str = "6") -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise OCRServiceError("Tesseract OCR is not installed")
    try:
        result = subprocess.run((executable, str(path), "stdout", "--psm", mode), capture_output=True, text=True, timeout=OCR_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OCRServiceError("Insurance-card OCR timed out") from exc
    if result.returncode:
        raise OCRServiceError("Insurance-card OCR failed")
    return result.stdout


def read_card(content: bytes) -> dict[str, str]:
    if not content.startswith(b"%PDF"):
        raise GoldenCaseError("Upload the insurance card as a PDF")
    fields = _card_fields(_pdf_text(content))
    if not all(name in fields for name in CARD_IDENTITY_PATTERNS):
        raise GoldenCaseError("ClinicForce could not read member name, Member ID, and group number from the uploaded card PDF")
    return fields
