"""Fast identity extraction from one or two patient-provided insurance card pages."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import boto3
import fitz
from botocore.config import Config

from app.ingestion.bedrock_extractor import BEDROCK_MODEL_ID
from app.insurance import golden, v1_sources


def _pages(contents: list[bytes]) -> list[tuple[int, str]]:
    pending: list[tuple[int, str, bytes | None]] = []
    for content in contents:
        if content.startswith(b"%PDF"):
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or not 1 <= document.page_count <= 2:
                    raise ValueError("Use an unlocked card PDF with one or two pages")
                for page in document:
                    text = page.get_text(sort=True).strip()
                    images = page.get_image_info(xrefs=True)
                    card_images = [item for item in images if fitz.Rect(item["bbox"]).get_area() >= page.rect.get_area() * .05]
                    if card_images:
                        # A PDF can place a searchable DOB outside the card image.
                        # Preserve that text while Textract reads the image itself.
                        image = card_images[0] if len(card_images) == 1 else None
                        if image and image.get("xref"):
                            picture = document.extract_image(image["xref"])["image"]
                        else:
                            scale = min(2, golden.MAX_OCR_DIMENSION / max(page.rect.width, page.rect.height))
                            picture = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False).tobytes("png")
                    else:
                        picture = None if text else page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False).tobytes("png")
                    pending.append((len(pending) + 1, text, picture))
        else:
            pending.append((len(pending) + 1, "", content))
    if len(pending) > 2:
        raise ValueError("Upload at most two card pages")
    def read_image(item: tuple[int, str, bytes | None]) -> str:
        if not item[2]:
            return ""
        try:
            return golden._image_text(item[2])
        except (golden.GoldenCaseError, golden.OCRServiceError):
            return ""  # A second page or selectable PDF text may still be usable.

    with ThreadPoolExecutor(max_workers=2) as pool:
        readings = list(pool.map(read_image, pending))
    pages = [(number, "\n".join(part for part in (text, reading) if part))
             for (number, text, _), reading in zip(pending, readings)]
    if not any(text.strip() for _, text in pages):
        raise ValueError("No readable card text was found")
    return pages


def _model_fields(pages: list[tuple[int, str]]) -> dict:
    if not (os.getenv("ECS_CONTAINER_METADATA_URI_V4") or os.getenv("INSURANCE_CARD_OCR") == "textract"):
        return {}
    prompt = "\n\n".join(f"Page {number}:\n{text[:6000]}" for number, text in pages)
    response = boto3.client("bedrock-runtime", config=Config(connect_timeout=3, read_timeout=25,
        retries={"max_attempts": 1})).converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": "Read only the supplied insurance-card text. Return one JSON object with given_name, family_name, birth_date (YYYY-MM-DD), and member_id. Use empty strings for absent or uncertain values. Choose the patient/member name, not the insurer, provider, or another family member. Do not invent details."}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0},
    )
    output = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
    match = re.search(r"\{.*\}", output, re.S)
    return json.loads(match[0]) if match else {}


def _birth(value: str) -> str:
    for format_string in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            parsed = datetime.strptime(value.strip(), format_string).date()
            return parsed.isoformat() if date(1850, 1, 1) <= parsed <= date.today() else ""
        except ValueError:
            pass
    return ""


def read_identity(contents: list[bytes], needed: set[str] | None = None) -> dict:
    """Use Textract for images and Bedrock when labeled OCR leaves required fields unclear."""
    pages = _pages(contents)
    parsed, _ = v1_sources.card_fields(pages)
    printed_name = parsed.get("member_name") or ""
    if "," in printed_name:
        family, given = printed_name.split(",", 1)
        given_name, family_name = given.strip(), family.strip()
    else:
        name = printed_name.split()
        given_name, family_name = " ".join(name[:-1]), name[-1] if len(name) > 1 else ""
    fields = {"given_name": given_name, "family_name": family_name,
              "birth_date": parsed.get("birth_date", ""), "member_id": parsed.get("member_id", "")}
    required = needed or set(fields)
    if any(not fields[key] for key in required):
        try:
            model = _model_fields(pages)
            source = "\n".join(text for _, text in pages).casefold()
            for key in fields:
                value = model.get(key)
                if fields[key] or not isinstance(value, str):
                    continue
                value = value.strip()
                if key == "birth_date":
                    value = _birth(value)
                    if value:
                        parsed = date.fromisoformat(value)
                        variants = (value, parsed.strftime("%m/%d/%Y"), parsed.strftime("%m-%d-%Y"),
                                    f"{parsed.month}/{parsed.day}/{parsed.year}")
                        if not any(variant in source for variant in variants):
                            continue
                elif key == "member_id":
                    if not re.fullmatch(r"[A-Za-z0-9-]{3,80}", value) or value.casefold() not in source:
                        continue
                elif not value or not all(part.casefold() in source for part in value.split()):
                    continue
                fields[key] = value
        except Exception:
            pass  # Printed fields and patient edits remain available if Bedrock is unavailable.
    return {key: value for key, value in fields.items() if value}
