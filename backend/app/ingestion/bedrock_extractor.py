"""Citation-preserving clinical extraction through Amazon Bedrock.

Only the ECS task role can call Bedrock.  The browser never receives source PDF
bytes, AWS credentials, or a model identifier.  The model is asked for a small,
strict fact schema; this module, not the model, creates the FHIR resources.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Iterable
from typing import Any, Literal

import boto3
import fitz
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
EXTRACTION_VERSION = "bedrock-haiku-4.5-v1"
PAGES_PER_REQUEST = 6
MAX_TEXT_CHARS_PER_PAGE = 8_000
MAX_OUTPUT_TOKENS = 2_048

_FACT_TYPES = Literal["condition", "allergy", "medication", "vital", "lab", "encounter"]

_SYSTEM_PROMPT = """You extract clinical facts from a patient-provided PDF for a clinician.
Return JSON only, with exactly one top-level key: facts. Each fact must contain type and page.
Allowed types: condition, allergy, medication, vital, lab, encounter.
Use only facts explicitly visible in the supplied source pages. Never infer, diagnose, calculate,
or fabricate missing values, dates, dosages, units, or clinical status. Keep exact source wording.
For condition, allergy, medication, vital, and lab provide name. For vital and lab also provide
value when visible and unit when visible. For medication include dosage only when visible.
For encounter provide facility. Include date and details only if explicitly present.
Every fact's page must be the page number where it appears. Omit uncertain facts.
Example: {"facts":[{"type":"vital","page":2,"name":"Blood pressure","value":"120/80","unit":"mmHg"}]}"""


class ExtractedFact(BaseModel):
    """A deliberately small schema before translating facts to FHIR."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: _FACT_TYPES
    page: int = Field(ge=1, le=10_000)
    name: str | None = Field(default=None, max_length=512)
    value: str | int | float | None = Field(default=None, max_length=512)
    unit: str | None = Field(default=None, max_length=128)
    date: str | None = Field(default=None, max_length=64)
    details: str | None = Field(default=None, max_length=2_000)
    dosage: str | None = Field(default=None, max_length=512)
    facility: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def has_required_display_value(self) -> "ExtractedFact":
        if self.type == "encounter":
            if not self.facility:
                raise ValueError("Encounter facts require a facility")
        elif not self.name:
            raise ValueError(f"{self.type} facts require a name")
        # FHIR accepts ISO dates/datetimes.  Do not pass model-formatted prose to
        # HAPI; the visible fact remains useful without a normalized date.
        if self.date and not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?(?:T[^\s]+)?", self.date):
            self.date = None
        return self


class BedrockExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[ExtractedFact] = Field(default_factory=list, max_length=500)


def _citation(filename: str, page: int) -> list[dict[str, Any]]:
    return [{
        "url": "https://clinicforce.example/fhir/StructureDefinition/source-citation",
        "extension": [
            {"url": "source_filename", "valueString": filename},
            {"url": "page_number", "valuePositiveInt": page},
        ],
    }]


def _number_or_text(value: str | int | float) -> tuple[str, dict[str, Any]]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "valueQuantity", {"value": value}
    text = str(value).strip()
    try:
        numeric = float(text)
    except ValueError:
        return "valueString", text
    if not numeric == numeric or numeric in {float("inf"), float("-inf")}:
        return "valueString", text
    return "valueQuantity", {"value": numeric}


def facts_to_fhir_bundle(facts: Iterable[ExtractedFact], patient_id: str, filename: str) -> dict[str, Any]:
    """Translate validated model output to standard FHIR R4 resources."""

    entries: list[dict[str, Any]] = []
    patient = {"reference": f"Patient/{patient_id}"}
    for fact in facts:
        extension = _citation(filename, fact.page)
        if fact.type == "condition":
            resource: dict[str, Any] = {"resourceType": "Condition", "subject": patient, "code": {"text": fact.name}, "extension": extension}
            if fact.details:
                resource["note"] = [{"text": fact.details}]
        elif fact.type == "allergy":
            resource = {"resourceType": "AllergyIntolerance", "patient": patient, "code": {"text": fact.name}, "extension": extension}
            if fact.details:
                resource["note"] = [{"text": fact.details}]
        elif fact.type == "medication":
            resource = {"resourceType": "MedicationStatement", "status": "unknown", "subject": patient, "medicationCodeableConcept": {"text": fact.name}, "extension": extension}
            if fact.dosage:
                resource["dosage"] = [{"text": fact.dosage}]
            if fact.details:
                resource["note"] = [{"text": fact.details}]
        elif fact.type in {"vital", "lab"}:
            resource = {
                "resourceType": "Observation", "status": "unknown", "subject": patient,
                "category": [{"text": "Vital Signs" if fact.type == "vital" else "Laboratory"}],
                "code": {"text": fact.name}, "extension": extension,
            }
            if fact.value is not None:
                value_key, value = _number_or_text(fact.value)
                if value_key == "valueQuantity" and fact.unit:
                    value["unit"] = fact.unit
                resource[value_key] = value
            if fact.date:
                resource["effectiveDateTime"] = fact.date
            if fact.details:
                resource["note"] = [{"text": fact.details}]
        else:
            resource = {"resourceType": "Encounter", "status": "unknown", "subject": patient, "serviceProvider": {"display": fact.facility}, "extension": extension}
            if fact.date:
                resource["period"] = {"start": fact.date}
            if fact.details:
                resource["reasonCode"] = [{"text": fact.details}]
        entries.append({"resource": resource})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


def _page_parts(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Return source pages as text, with a compact image fallback for scans."""

    pages: list[dict[str, Any]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append({"number": index, "text": text[:MAX_TEXT_CHARS_PER_PAGE]})
            else:
                # Claude Haiku 4.5 supports images; only render scans when a page has
                # no extractable text, which keeps cost and request size bounded.
                image = page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
                pages.append({"number": index, "image": image.tobytes("png")})
    if not pages:
        raise ValueError("The PDF has no readable pages")
    return pages


def _json_from_response(text: str) -> BedrockExtraction:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
    try:
        return BedrockExtraction.model_validate_json(candidate)
    except ValidationError as exc:
        raise ValueError("Bedrock returned an invalid clinical extraction") from exc


def _invoke_chunk(pages: list[dict[str, Any]]) -> BedrockExtraction:
    content: list[dict[str, Any]] = [{"text": "Extract facts from these source pages. Return JSON only."}]
    for page in pages:
        content.append({"text": f"SOURCE PAGE {page['number']}"})
        if "text" in page:
            content.append({"text": page["text"]})
        else:
            content.append({"image": {"format": "png", "source": {"bytes": page["image"]}}})
    response = boto3.client("bedrock-runtime").converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
    )
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
    if not text:
        raise ValueError("Bedrock returned no clinical extraction")
    return _json_from_response(text)


def _deduplicate(facts: Iterable[ExtractedFact]) -> list[ExtractedFact]:
    unique: dict[tuple[str, int, str, str, str], ExtractedFact] = {}
    for fact in facts:
        marker = (fact.type, fact.page, fact.name or fact.facility or "", str(fact.value or ""), fact.date or "")
        unique.setdefault(marker, fact)
    return list(unique.values())


async def extract_pdf_to_fhir_bundle(pdf_bytes: bytes, patient_id: str, filename: str) -> dict[str, Any]:
    """Run a bounded Bedrock extraction for every page, then create FHIR locally."""

    pages = await asyncio.to_thread(_page_parts, pdf_bytes)
    chunks = [pages[index:index + PAGES_PER_REQUEST] for index in range(0, len(pages), PAGES_PER_REQUEST)]
    results = await asyncio.gather(*(asyncio.to_thread(_invoke_chunk, chunk) for chunk in chunks))
    facts = _deduplicate(fact for result in results for fact in result.facts)
    return facts_to_fhir_bundle(facts, patient_id, filename)
