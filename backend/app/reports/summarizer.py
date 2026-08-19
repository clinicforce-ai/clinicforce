"""Local-only clinical narrative generation from FHIR R4 Bundles."""

from __future__ import annotations

import re
from typing import Any
_UNSAFE_NARRATIVE = re.compile(
    r"\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"daily|weekly|monthly|hourly|every|once|twice|dose|dosage|mg|mcg|g|ml|"
    r"mmhg|bpm|percent|tablet|capsule)\b",
    re.IGNORECASE,
)
EPIC_SOURCE_SYSTEM = "https://clinicforce.example/fhir/source"


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


def _observation_value(resource: dict[str, Any]) -> tuple[Any, str | None] | None:
    """Return every common FHIR Observation value without coercing clinical facts.

    A report with only one or two results is especially sensitive to omissions.
    Preserve qualitative, calculated, and component values exactly as represented
    in FHIR; the PDF renderer displays them as documented rather than deriving a
    clinical interpretation.
    """
    quantity = _quantity(resource.get("valueQuantity"))
    if quantity:
        return quantity
    string_value = resource.get("valueString")
    if isinstance(string_value, str) and string_value.strip():
        return string_value.strip(), None
    coded_value = _coded_text(resource.get("valueCodeableConcept"))
    if coded_value:
        return coded_value, None
    for key in ("valueInteger", "valueDecimal", "valueBoolean", "valueDateTime", "valueTime"):
        value = resource.get(key)
        if isinstance(value, str) and value.strip():
            return value, None
        if isinstance(value, (int, float, bool)):
            return value, None
    value_range = resource.get("valueRange")
    if isinstance(value_range, dict):
        low, high = _quantity(value_range.get("low")), _quantity(value_range.get("high"))
        if low or high:
            unit = (low or high)[1]
            lower = str(low[0]) if low else "Below"
            upper = str(high[0]) if high else "Above"
            return f"{lower}–{upper}", unit
    ratio = resource.get("valueRatio")
    if isinstance(ratio, dict):
        numerator, denominator = _quantity(ratio.get("numerator")), _quantity(ratio.get("denominator"))
        if numerator and denominator:
            numerator_text = f"{numerator[0]} {numerator[1] or ''}".strip()
            denominator_text = f"{denominator[0]} {denominator[1] or ''}".strip()
            return f"{numerator_text} / {denominator_text}", None
    return None


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
    # Epic resources are part of the same encrypted QR snapshot, but do not
    # have a patient-uploaded PDF page to link to. A standard FHIR meta.tag is
    # enough to keep their provenance visible without exposing an Epic URL or
    # opaque resource ID in the clinician UI.
    meta = resource.get("meta")
    tags = meta.get("tag") if isinstance(meta, dict) else []
    for tag in tags if isinstance(tags, list) else []:
        if not isinstance(tag, dict) or tag.get("system") != EPIC_SOURCE_SYSTEM or tag.get("code") != "epic-mychart":
            continue
        organization = tag.get("display")
        return {
            "source_type": "epic",
            "organization_name": organization.strip() if isinstance(organization, str) and organization.strip() else "Epic / MyChart",
        }
    return None


def _first_coded_text(values: Any) -> str | None:
    if isinstance(values, (str, bytes, dict)):
        return None
    try:
        iterator = iter(values)
    except TypeError:
        return None
    for value in iterator:
        text = _coded_text(value)
        if text:
            return text
    return None


def _join_details(*values: str | None) -> str | None:
    unique = list(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))
    return " · ".join(unique) if unique else None


def _allergy_name(resource: dict[str, Any]) -> str:
    name = _coded_text(resource.get("code")) or _first_coded_text(
        reaction.get("manifestation") for reaction in resource.get("reaction", []) if isinstance(reaction, dict)
    )
    if name:
        return name
    allergy_type = resource.get("type")
    return "Allergy or intolerance" if allergy_type not in {"allergy", "intolerance"} else f"{allergy_type.title()} recorded"


def _document_reference_details(resource: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Turn a DocumentReference into a readable summary row, never a raw ID."""
    content = resource.get("content")
    attachment = next((item.get("attachment") for item in content if isinstance(item, dict) and isinstance(item.get("attachment"), dict)), None) if isinstance(content, list) else None
    title = attachment.get("title") if isinstance(attachment, dict) and isinstance(attachment.get("title"), str) else None
    description = resource.get("description") if isinstance(resource.get("description"), str) else None
    name = _coded_text(resource.get("type")) or description or title or "Clinical document"
    date = resource.get("date") if isinstance(resource.get("date"), str) else None
    details = _join_details(
        description if description != name else None,
        _coded_text(resource.get("category")) if isinstance(resource.get("category"), dict) else _first_coded_text(resource.get("category")),
        resource.get("status") if isinstance(resource.get("status"), str) else None,
    )
    return name, date, details


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
                    observation_value = _observation_value(component)
                    if observation_value:
                        name = _coded_text(component.get("code")) or "Blood pressure component"
                        result["vitals"].append({"name": name, "value": observation_value[0], "unit": observation_value[1], "date": date, "citation": _citation(resource)})
                continue
            observation_value = _observation_value(resource)
            if not observation_value:
                continue
            item = {
                "name": code, "value": observation_value[0],
                "unit": observation_value[1], "date": date,
                "details": _note_text(resource), "citation": _citation(resource),
            }
            categories = resource.get("category", [])
            is_vital = codes & vital_codes or any(
                isinstance(category, dict) and _coded_text(category) == "Vital Signs" for category in categories
            )
            (result["vitals"] if is_vital else result["labs"]).append(item)
        elif resource_type in {"MedicationStatement", "MedicationRequest", "MedicationDispense"}:
            name = _coded_text(resource.get("medicationCodeableConcept"))
            if name is None:
                name = _coded_text(resource.get("medicationReference"))
            if name:
                result["medications"].append({"name": name, "dosage": _medication_dosage(resource), "details": _note_text(resource), "citation": _citation(resource)})
        elif resource_type == "Condition":
            name = _coded_text(resource.get("code"))
            if name:
                result["conditions"].append({"name": name, "details": _note_text(resource), "citation": _citation(resource)})
        elif resource_type == "AllergyIntolerance":
            reaction_details = _first_coded_text(
                reaction.get("manifestation") for reaction in resource.get("reaction", []) if isinstance(reaction, dict)
            )
            result["allergies"].append({
                "name": _allergy_name(resource),
                "details": _join_details(
                    _note_text(resource), reaction_details, _coded_text(resource.get("clinicalStatus")),
                    _coded_text(resource.get("verificationStatus")),
                ),
                "citation": _citation(resource),
            })
        elif resource_type == "Encounter":
            provider = resource.get("serviceProvider")
            facility = provider.get("display") if isinstance(provider, dict) else None
            period = resource.get("period")
            date = period.get("start") if isinstance(period, dict) and isinstance(period.get("start"), str) else None
            reason = resource.get("reasonCode")
            details = _coded_text(reason[0]) if isinstance(reason, list) and reason else None
            if isinstance(facility, str) and facility.strip():
                result["care_events"].append({"facility": facility.strip(), "date": date, "details": details, "citation": _citation(resource)})
        elif resource_type == "DocumentReference":
            name, date, details = _document_reference_details(resource)
            result["care_events"].append({
                "facility": "Clinical document",
                "date": date,
                "details": _join_details(name, details),
                "citation": _citation(resource),
            })
    return result


# A descriptive alias for callers that use the earlier project terminology.
extract_clinical_data = extract_structured_data


def _safe_narrative(narrative: str) -> str:
    """Fail closed if local generation includes facts reserved for source-data tables."""
    sentences = re.split(r"(?<=[.!?])\s+", narrative.strip())
    safe = [sentence.strip() for sentence in sentences if sentence.strip() and not _UNSAFE_NARRATIVE.search(sentence)]
    return " ".join(safe) or "Clinical history has been reviewed; consult the structured source data below."


async def summarize_fhir_bundle(fhir_bundle: dict) -> dict:
    """Build a deterministic, non-numeric narrative from FHIR source data."""
    extracted = extract_structured_data(fhir_bundle)
    sentences: list[str] = []
    for label, prefix in (
        ("conditions", "Reported conditions"),
        ("allergies", "Reported allergies"),
        ("medications", "Reported medications"),
    ):
        names = [item["name"] for item in extracted[label] if isinstance(item.get("name"), str)]
        safe_names = [name for name in names if not _UNSAFE_NARRATIVE.search(name)]
        if safe_names:
            sentences.append(f"{prefix}: {', '.join(safe_names)}.")
    narrative = " ".join(sentences) or "Clinical history has been reviewed; consult the structured source data below."
    return {"narrative": _safe_narrative(narrative), **extracted}
