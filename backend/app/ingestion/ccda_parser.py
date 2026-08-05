"""Minimal, namespace-aware C-CDA to FHIR R4 mapper."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from lxml import etree

_NS = {"cda": "urn:hl7-org:v3"}


def _text(element: etree._Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join(element.itertext()).strip()
    return value or None


def _extract_ccda(xml_text: str) -> dict[str, Any]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(xml_text.encode("utf-8"), parser=parser)
    patient_role = root.find(".//cda:recordTarget/cda:patientRole", _NS)
    if patient_role is None:
        raise ValueError("C-CDA document has no recordTarget/patientRole")
    identifier = patient_role.find("cda:id", _NS)
    raw_id = (identifier.get("extension") or identifier.get("root")) if identifier is not None else None
    patient_id = re.sub(r"[^A-Za-z0-9\-.]", "-", raw_id or "ccda-patient")
    patient = patient_role.find("cda:patient", _NS)
    name = patient.find("cda:name", _NS) if patient is not None else None
    demographics: dict[str, Any] = {"id": patient_id}
    if name is not None:
        demographics["given"] = [_text(node) for node in name.findall("cda:given", _NS) if _text(node)]
        family = _text(name.find("cda:family", _NS))
        if family:
            demographics["family"] = family
    if patient is not None:
        birth = patient.find("cda:birthTime", _NS)
        gender = patient.find("cda:administrativeGenderCode", _NS)
        if birth is not None and birth.get("value"):
            value = birth.get("value")
            demographics["birthDate"] = f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) >= 8 else value
        if gender is not None and gender.get("code"):
            demographics["gender"] = {"M": "male", "F": "female", "O": "other", "UN": "unknown"}.get(gender.get("code"), "unknown")

    result: dict[str, Any] = {"patient": demographics, "conditions": [], "allergies": []}
    for section in root.xpath(".//cda:section", namespaces=_NS):
        title = (_text(section.find("cda:title", _NS)) or "").lower()
        if "problem" in title or "condition" in title:
            target = result["conditions"]
        elif "allerg" in title:
            target = result["allergies"]
        else:
            continue
        for observation in section.xpath(".//cda:observation", namespaces=_NS):
            value = observation.find("cda:value", _NS)
            code = observation.find("cda:code", _NS)
            display = (value.get("displayName") if value is not None else None) or (code.get("displayName") if code is not None else None)
            if not display:
                continue
            target.append({"text": display, "code": (value.get("code") if value is not None else None) or (code.get("code") if code is not None else None), "code_system": (value.get("codeSystem") if value is not None else None) or (code.get("codeSystem") if code is not None else None)})
    return result


async def extract_ccda_data(xml_text: str) -> dict:
    """Extract demographics, problem-list entries, and allergies from C-CDA XML."""
    return await asyncio.to_thread(_extract_ccda, xml_text)


def ccda_data_to_fhir_bundle(data: dict) -> dict:
    """Map extracted C-CDA values to FHIR R4 Patient, Condition, and AllergyIntolerance."""
    details = data["patient"]
    patient: dict[str, Any] = {"resourceType": "Patient", "id": details["id"]}
    if details.get("given") or details.get("family"):
        patient["name"] = [{key: details[key] for key in ("given", "family") if details.get(key)}]
    for key in ("birthDate", "gender"):
        if details.get(key):
            patient[key] = details[key]
    reference = {"reference": f"Patient/{details['id']}"}
    entries: list[dict[str, Any]] = [{"resource": patient}]
    for condition in data.get("conditions", []):
        code: dict[str, Any] = {"text": condition["text"]}
        if condition.get("code") and condition.get("code_system"):
            code["coding"] = [{"code": condition["code"], "system": f"urn:oid:{condition['code_system']}"}]
        entries.append({"resource": {"resourceType": "Condition", "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]}, "code": code, "subject": reference}})
    for allergy in data.get("allergies", []):
        code = {"text": allergy["text"]}
        if allergy.get("code") and allergy.get("code_system"):
            code["coding"] = [{"code": allergy["code"], "system": f"urn:oid:{allergy['code_system']}"}]
        entries.append({"resource": {"resourceType": "AllergyIntolerance", "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]}, "code": code, "patient": reference}})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


async def parse_ccda_to_fhir_bundle(xml_text: str) -> dict:
    """Convenience entry point for parsing a raw C-CDA document into FHIR R4."""
    return ccda_data_to_fhir_bundle(await extract_ccda_data(xml_text))
