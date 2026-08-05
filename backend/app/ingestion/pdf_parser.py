"""PDF clinical-form intake using AWS Bedrock Claude extraction."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import boto3
import fitz
from botocore.exceptions import BotoCoreError, ClientError


BEDROCK_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
BEDROCK_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
SYSTEM_INSTRUCTION = "You are a clinical data extraction assistant. You MUST NOT invent, infer, or hallucinate data. Extract ONLY explicit numbers, dates, medications, and conditions found in the text. If a value is missing, return null. You must respond in valid JSON."

_PROMPT = """Extract only clinical information explicitly stated in this patient form. Return JSON only, with exactly this shape:
{"conditions":[{"name":str,"clinical_status":"active"|"resolved"|null,"citation":{"source_filename":str,"page_number":int}}],"allergies":[{"substance":str,"criticality":"high"|"medium"|"low"|null,"citation":{"source_filename":str,"page_number":int}}],"medications":[{"name":str,"dosage":str|null,"citation":{"source_filename":str,"page_number":int}}]}
Use empty arrays when a category is absent. Each citation source_filename must exactly equal the supplied filename. page_number must identify the page containing that fact, using the [PAGE n] markers. Do not include a fact unless it is explicitly present in the form.

SOURCE FILENAME: {source_filename}

FORM TEXT:
"""


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        return "\n".join(
            f"[PAGE {page_number}]\n{page.get_text('text')}"
            for page_number, page in enumerate(document, start=1)
        )


async def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """Extract text without blocking the FastAPI event loop."""
    if not pdf_bytes:
        raise ValueError("Uploaded PDF is empty")
    return await asyncio.to_thread(_extract_pdf_text, pdf_bytes)


def _json_from_model_response(response_text: str, source_filename: str) -> dict[str, Any]:
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Bedrock did not return a JSON object")
    for key in ("conditions", "allergies", "medications"):
        if not isinstance(parsed.get(key, []), list):
            raise ValueError(f"Bedrock response field '{key}' must be a list")
        parsed.setdefault(key, [])
        for item in parsed[key]:
            citation = item.get("citation") if isinstance(item, dict) else None
            if (
                not isinstance(citation, dict)
                or citation.get("source_filename") != source_filename
                or not isinstance(citation.get("page_number"), int)
                or isinstance(citation.get("page_number"), bool)
                or citation["page_number"] < 1
            ):
                raise ValueError(f"Bedrock response item in '{key}' has an invalid citation")
    return parsed


def _invoke_bedrock(prompt: str) -> str:
    """Run the synchronous AWS SDK call outside FastAPI's event loop."""
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    response = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "system": SYSTEM_INSTRUCTION,
            "messages": [{"role": "user", "content": prompt}],
        }),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    content = body.get("content") if isinstance(body, dict) else None
    text = "".join(item.get("text", "") for item in content or [] if isinstance(item, dict) and item.get("type") == "text")
    if not text:
        raise ValueError("Bedrock response did not contain text")
    return text


async def parse_pdf_text_with_llm(extracted_text: str, source_filename: str = "uploaded_report.pdf") -> dict:
    """Extract citation-backed facts through AWS Bedrock without blocking FastAPI."""
    if not source_filename.strip():
        raise ValueError("A source filename is required for citation-backed extraction")
    try:
        response_text = await asyncio.to_thread(
            _invoke_bedrock, _PROMPT.replace("{source_filename}", source_filename) + extracted_text
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code in {"ValidationException", "ThrottlingException", "Throttling"}:
            raise RuntimeError(f"AWS Bedrock rejected or throttled the request ({code}). Please retry or verify the model and region.") from exc
        raise RuntimeError(f"AWS Bedrock request failed ({code}).") from exc
    except BotoCoreError as exc:
        raise RuntimeError("Could not connect to AWS Bedrock. Verify AWS credentials, region, and network access.") from exc
    return _json_from_model_response(response_text, source_filename)


def pdf_extraction_to_fhir_bundle(extraction: dict, patient_id: str) -> dict:
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
