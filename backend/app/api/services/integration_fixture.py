"""Deterministic local data used by the integration-test endpoint."""

import asyncio
from html import escape
from typing import Any

import fitz

from app.ingestion.ccda_parser import parse_ccda_to_fhir_bundle
from app.ingestion.pdf_parser import extract_text_from_pdf_bytes, parse_pdf_text_with_llm, pdf_extraction_to_fhir_bundle
from app.ingestion.wearables import generate_mock_wearable_payload, parse_apple_health_heartrate, parse_fitbit_steps
from app.logs.logger import log_info


async def build_ingestion_resources(patient_id: str) -> list[dict[str, Any]]:
    """Run local fixture adapters and return patient-first FHIR resources."""
    wearable = generate_mock_wearable_payload()
    heart_rate, steps = await asyncio.gather(
        parse_apple_health_heartrate(wearable["apple_health"], patient_id),
        parse_fitbit_steps(wearable["fitbit"], patient_id),
    )
    pdf_text = await extract_text_from_pdf_bytes(
        await asyncio.to_thread(make_demo_pdf, "Patient reports no additional symptoms in this demo form.")
    )
    pdf_bundle = pdf_extraction_to_fhir_bundle(await parse_pdf_text_with_llm(pdf_text, "demo-intake.pdf"), patient_id)
    ccda_bundle = await parse_ccda_to_fhir_bundle(demo_ccda(patient_id))
    resources = bundle_resources(ccda_bundle) + [heart_rate, steps] + bundle_resources(pdf_bundle)
    resources.sort(key=lambda resource: resource.get("resourceType") != "Patient")
    counts: dict[str, int] = {}
    for resource in resources:
        resource_type = resource.get("resourceType", "Unknown")
        counts[resource_type] = counts.get(resource_type, 0) + 1
    log_info("Ingestion", "FHIR R4 resources produced: %s", counts)
    return resources


def make_demo_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    result = document.tobytes()
    document.close()
    return result


def demo_ccda(patient_id: str) -> str:
    safe_id = escape(patient_id, quote=True)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <recordTarget><patientRole><id extension="{safe_id}"/><patient><name><given>ClinicForce</given><family>Demo</family></name><administrativeGenderCode code="UN"/></patient></patientRole></recordTarget>
  <component><structuredBody><component><section><title>Problems</title><entry><observation><code code="38341003" codeSystem="2.16.840.1.113883.6.96" displayName="Hypertension"/></observation></entry></section></component>
  <component><section><title>Allergies</title><entry><observation><code code="91936005" codeSystem="2.16.840.1.113883.6.96" displayName="Allergy to penicillin"/></observation></entry></section></component></structuredBody></component>
</ClinicalDocument>'''


def bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry["resource"] for entry in bundle.get("entry", []) if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)]
