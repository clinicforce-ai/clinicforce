"""Local-only clinical narrative generation from FHIR R4 Bundles."""

from __future__ import annotations

import re
import asyncio
import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
BEDROCK_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
SYSTEM_INSTRUCTION = "You are a clinical data extraction assistant. You MUST NOT invent, infer, or hallucinate data. Extract ONLY explicit numbers, dates, medications, and conditions found in the text. If a value is missing, return null. You must respond in valid JSON."

_NARRATIVE_PROMPT = """Write a short clinical-history narrative for a doctor using only the supplied non-numeric clinical context. Connect conditions, allergies, and medication names into clear sentences when present. Do not state, repeat, infer, spell out, or format numbers, measurements, dates, frequencies, dosages, lab results, or vital-sign results. Do not give medical advice. Return JSON only in the shape {"narrative": string}.

CLINICAL CONTEXT:
"""
_UNSAFE_NARRATIVE = re.compile(
    r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"daily|weekly|monthly|hourly|every|once|twice|dose|dosage|mg|mcg|g|ml|"
    r"mmhg|bpm|percent|tablet|capsule)\b",
    re.IGNORECASE,
)


def _resources(fhir_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Return resources from a FHIR R4 Bundle without assuming every entry is valid."""
    return [
        entry["resource"]
        for entry in fhir_bundle.get("entry", [])
        if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
    ]


def _coded_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("text"), str) and value["text"].strip():
        return value["text"].strip()
    for coding in value.get("coding", []):
        if isinstance(coding, dict):
            for key in ("display", "code"):
                if isinstance(coding.get(key), str) and coding[key].strip():
                    return coding[key].strip()
    return None


def _quantity(value: Any) -> tuple[Any, str | None] | None:
    if not isinstance(value, dict) or value.get("value") is None:
        return None
    unit = value.get("unit") or value.get("code")
    return value["value"], unit if isinstance(unit, str) else None


def _observation_date(resource: dict[str, Any]) -> str | None:
    for key in ("effectiveDateTime", "issued"):
        if isinstance(resource.get(key), str):
            return resource[key]
    period = resource.get("effectivePeriod")
    return period.get("start") if isinstance(period, dict) and isinstance(period.get("start"), str) else None


def _note_text(resource: dict[str, Any]) -> str | None:
    notes = resource.get("note")
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, dict) and isinstance(note.get("text"), str) and note["text"].strip():
                return note["text"].strip()
    return None


def _citation(resource: dict[str, Any]) -> dict[str, Any] | None:
    """Read the source citation stored in the FHIR extension for report linking."""
    for extension in resource.get("extension", []):
        if not isinstance(extension, dict) or extension.get("url") != "https://clinicforce.example/fhir/StructureDefinition/source-citation":
            continue
        citation: dict[str, Any] = {}
        for child in extension.get("extension", []):
            if not isinstance(child, dict):
                continue
            if child.get("url") == "source_filename" and isinstance(child.get("valueString"), str):
                citation["source_filename"] = child["valueString"]
            elif child.get("url") == "page_number" and isinstance(child.get("valuePositiveInt"), int):
                citation["page_number"] = child["valuePositiveInt"]
            elif child.get("url") == "document_id" and isinstance(child.get("valuePositiveInt"), int):
                citation["document_id"] = child["valuePositiveInt"]
        if "source_filename" in citation and "page_number" in citation:
            return citation
    return None


def _medication_dosage(resource: dict[str, Any]) -> str:
    """Preserve a FHIR dosage verbatim, or its raw dose quantity when available."""
    dosage = resource.get("dosage", [])
    if not isinstance(dosage, list) or not dosage or not isinstance(dosage[0], dict):
        return "Not specified"
    first = dosage[0]
    if isinstance(first.get("text"), str) and first["text"].strip():
        return first["text"].strip()
    dose_and_rate = first.get("doseAndRate", [])
    if isinstance(dose_and_rate, list) and dose_and_rate and isinstance(dose_and_rate[0], dict):
        quantity = _quantity(dose_and_rate[0].get("doseQuantity"))
        if quantity:
            return f"{quantity[0]} {quantity[1] or ''}".strip()
    return "Not specified"


def extract_structured_data(fhir_bundle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Extract display-ready raw values directly from FHIR, never from the LLM."""
    result: dict[str, list[dict[str, Any]]] = {
        "vitals": [], "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": []
    }
    vital_codes = {"85354-9", "8480-6", "8462-4", "8867-4"}

    for resource in _resources(fhir_bundle):
        resource_type = resource.get("resourceType")
        if resource_type == "Observation":
            code = _coded_text(resource.get("code")) or "Unspecified observation"
            codings = resource.get("code", {}).get("coding", []) if isinstance(resource.get("code"), dict) else []
            codes = {item.get("code") for item in codings if isinstance(item, dict)}
            date = _observation_date(resource)
            components = resource.get("component")
            if "85354-9" in codes and isinstance(components, list):
                for component in components:
                    if not isinstance(component, dict):
                        continue
                    quantity = _quantity(component.get("valueQuantity"))
                    if quantity:
                        name = _coded_text(component.get("code")) or "Blood pressure component"
                        result["vitals"].append({"name": name, "value": quantity[0], "unit": quantity[1], "date": date, "citation": _citation(resource)})
                continue
            quantity = _quantity(resource.get("valueQuantity"))
            string_value = resource.get("valueString")
            if not quantity and not isinstance(string_value, str):
                continue
            item = {
                "name": code, "value": quantity[0] if quantity else string_value,
                "unit": quantity[1] if quantity else None, "date": date,
                "details": _note_text(resource), "citation": _citation(resource),
            }
            categories = resource.get("category", [])
            is_vital = codes & vital_codes or any(
                isinstance(category, dict) and _coded_text(category) == "Vital Signs" for category in categories
            )
            (result["vitals"] if is_vital else result["labs"]).append(item)
        elif resource_type == "MedicationStatement":
            name = _coded_text(resource.get("medicationCodeableConcept"))
            if name:
                result["medications"].append({"name": name, "dosage": _medication_dosage(resource), "details": _note_text(resource), "citation": _citation(resource)})
        elif resource_type == "Condition":
            name = _coded_text(resource.get("code"))
            if name:
                result["conditions"].append({"name": name, "details": _note_text(resource), "citation": _citation(resource)})
        elif resource_type == "AllergyIntolerance":
            name = _coded_text(resource.get("code"))
            if name:
                result["allergies"].append({"name": name, "details": _note_text(resource), "citation": _citation(resource)})
        elif resource_type == "Encounter":
            provider = resource.get("serviceProvider")
            facility = provider.get("display") if isinstance(provider, dict) else None
            period = resource.get("period")
            date = period.get("start") if isinstance(period, dict) and isinstance(period.get("start"), str) else None
            reason = resource.get("reasonCode")
            details = _coded_text(reason[0]) if isinstance(reason, list) and reason else None
            if isinstance(facility, str) and facility.strip():
                result["care_events"].append({"facility": facility.strip(), "date": date, "details": details, "citation": _citation(resource)})
    return result


# A descriptive alias for callers that use the earlier project terminology.
extract_clinical_data = extract_structured_data


def _narrative_context(extracted: dict[str, list[dict[str, Any]]]) -> str:
    """Supply Bedrock only facts it may safely place in narrative prose."""
    sections = []
    for label in ("conditions", "allergies", "medications"):
        names = [item["name"] for item in extracted[label] if item.get("name")]
        if names:
            sections.append(f"{label.title()}: {', '.join(names)}")
    return "\n".join(sections) or "No non-numeric clinical history was available."


def _safe_narrative(narrative: str) -> str:
    """Fail closed if local generation includes facts reserved for source-data tables."""
    sentences = re.split(r"(?<=[.!?])\s+", narrative.strip())
    safe = [sentence.strip() for sentence in sentences if sentence.strip() and not _UNSAFE_NARRATIVE.search(sentence)]
    return " ".join(safe) or "Clinical history has been reviewed; consult the structured source data below."


async def summarize_fhir_bundle(fhir_bundle: dict) -> dict:
    """Generate a constrained Bedrock narrative while retaining raw FHIR values."""
    extracted = extract_structured_data(fhir_bundle)
    def invoke() -> str:
        client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        response = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({"anthropic_version": "bedrock-2023-05-31", "max_tokens": 400, "system": SYSTEM_INSTRUCTION, "messages": [{"role": "user", "content": _NARRATIVE_PROMPT + _narrative_context(extracted)}]}),
            contentType="application/json", accept="application/json",
        )
        body = json.loads(response["body"].read())
        return "".join(item.get("text", "") for item in body.get("content", []) if isinstance(item, dict) and item.get("type") == "text")
    try:
        response_text = await asyncio.to_thread(invoke)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in {"ValidationException", "ThrottlingException", "Throttling"}:
            raise RuntimeError(f"AWS Bedrock rejected or throttled the request ({code}). Please retry or verify the model and region.") from exc
        raise RuntimeError(f"AWS Bedrock request failed ({code}).") from exc
    except BotoCoreError as exc:
        raise RuntimeError("Could not connect to AWS Bedrock. Verify AWS credentials, region, and network access.") from exc
    try:
        decoded = json.loads(response_text)
        narrative = decoded.get("narrative") if isinstance(decoded, dict) else None
    except json.JSONDecodeError as exc:
        raise ValueError("Bedrock response was not valid narrative JSON") from exc
    if not isinstance(narrative, str):
        raise ValueError("Bedrock response did not contain a narrative")
    return {"narrative": _safe_narrative(narrative), **extracted}
