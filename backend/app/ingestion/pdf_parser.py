"""Local, deterministic PDF clinical-form extraction.

Patient report text must not be sent to an external model. This module extracts
only values explicitly present on labelled lines and retains a page citation.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import fitz


_LABELS = {
    "conditions": re.compile(r"^\s*(?:conditions?|diagnoses?)\s*:\s*(.+)$", re.IGNORECASE),
    "allergies": re.compile(r"^\s*(?:allerg(?:y|ies)|allergy\s+intolerances?)\s*:\s*(.+)$", re.IGNORECASE),
    "medications": re.compile(r"^\s*(?:medications?|meds?)\s*:\s*(.+)$", re.IGNORECASE),
}


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        return "\n".join(
            f"[PAGE {page_number}]\n{page.get_text('text')}"
            for page_number, page in enumerate(document, start=1)
        )


async def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text without blocking FastAPI's event loop."""
    if not pdf_bytes:
        raise ValueError("Uploaded PDF is empty")
    return await asyncio.to_thread(_extract_pdf_text, pdf_bytes)


async def parse_pdf_text_with_llm(extracted_text: str, source_filename: str = "uploaded_report.pdf") -> dict[str, list[dict[str, Any]]]:
    """Compatibility name for the previous extractor; this implementation is local."""
    if not source_filename.strip():
        raise ValueError("A source filename is required for citation-backed extraction")
    result: dict[str, list[dict[str, Any]]] = {"conditions": [], "allergies": [], "medications": []}
    page_number = 1
    for line in extracted_text.splitlines():
        marker = re.fullmatch(r"\[PAGE\s+(\d+)\]", line.strip(), re.IGNORECASE)
        if marker:
            page_number = int(marker.group(1))
            continue
        for category, expression in _LABELS.items():
            match = expression.match(line)
            if not match:
                continue
            citation = {"source_filename": source_filename, "page_number": page_number}
            for value in (value.strip() for value in re.split(r"[;,]", match.group(1))):
                if not value:
                    continue
                if category == "conditions":
                    result[category].append({"name": value, "clinical_status": None, "citation": citation})
                elif category == "allergies":
                    result[category].append({"substance": value, "criticality": None, "citation": citation})
                else:
                    result[category].append({"name": value, "dosage": None, "citation": citation})
            break
    return result


def pdf_extraction_to_fhir_bundle(extraction: dict[str, Any], patient_id: str) -> dict[str, Any]:
    """Map citation-backed form facts into a FHIR R4 collection Bundle."""
    entries: list[dict[str, Any]] = []
    patient = {"reference": f"Patient/{patient_id}"}

    def citation_extension(item: dict[str, Any]) -> list[dict[str, Any]]:
        citation = item.get("citation")
        if not isinstance(citation, dict):
            return []
        return [{"url": "https://clinicforce.example/fhir/StructureDefinition/source-citation", "extension": [
            {"url": "source_filename", "valueString": citation["source_filename"]},
            {"url": "page_number", "valuePositiveInt": citation["page_number"]},
        ]}]

    for item in extraction.get("conditions", []):
        if not item.get("name"):
            continue
        resource: dict[str, Any] = {"resourceType": "Condition", "code": {"text": item["name"]}, "subject": patient, "extension": citation_extension(item)}
        if item.get("clinical_status") in {"active", "resolved"}:
            resource["clinicalStatus"] = {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": item["clinical_status"]}]}
        entries.append({"resource": resource})
    for item in extraction.get("allergies", []):
        if not item.get("substance"):
            continue
        resource = {"resourceType": "AllergyIntolerance", "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]}, "code": {"text": item["substance"]}, "patient": patient, "extension": citation_extension(item)}
        if item.get("criticality") in {"high", "low"}:
            resource["criticality"] = item["criticality"]
        entries.append({"resource": resource})
    for item in extraction.get("medications", []):
        if not item.get("name"):
            continue
        resource = {"resourceType": "MedicationStatement", "status": "active", "medicationCodeableConcept": {"text": item["name"]}, "subject": patient, "extension": citation_extension(item)}
        if item.get("dosage"):
            resource["dosage"] = [{"text": item["dosage"]}]
        entries.append({"resource": resource})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}
