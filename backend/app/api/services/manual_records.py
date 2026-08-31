"""FHIR conversion for explicitly patient-entered clinical facts."""

from typing import Any

from fastapi import HTTPException

from app.api.schemas import ManualRecordRequest


def manual_record_bundle(request: ManualRecordRequest, patient_id: str) -> tuple[str, dict[str, Any]]:
    name, hospital = (request.name or "").strip(), (request.hospital or "").strip()
    details, date = (request.details or "").strip(), (request.date or "").strip()
    patient = {"reference": f"Patient/{patient_id}"}
    if request.category == "hospital":
        if not hospital:
            raise HTTPException(status_code=422, detail="Enter the hospital or clinic name")
        resource = {"resourceType": "Encounter", "status": "finished", "subject": patient, "serviceProvider": {"display": hospital}}
        if date:
            resource["period"] = {"start": date}
        if details:
            resource["reasonCode"] = [{"text": details}]
        return hospital, _bundle(resource)
    if request.category in {"lab", "vital"}:
        if not name or not (request.value or "").strip():
            raise HTTPException(status_code=422, detail="Enter a test or measurement name and its result")
        resource = _observation(request, patient, name, date, details)
        return name, _bundle(resource)
    if request.category == "condition":
        if not name:
            raise HTTPException(status_code=422, detail="Enter the condition name")
        return name, _bundle(_noted_resource("Condition", "subject", patient, name, details))
    if request.category == "allergy":
        if not name:
            raise HTTPException(status_code=422, detail="Enter the allergy or intolerance")
        return name, _bundle(_noted_resource("AllergyIntolerance", "patient", patient, name, details))
    if not name:
        raise HTTPException(status_code=422, detail="Enter the medication name")
    resource = {"resourceType": "MedicationStatement", "status": "active", "subject": patient, "medicationCodeableConcept": {"text": name}}
    if (request.dosage or "").strip():
        resource["dosage"] = [{"text": request.dosage.strip()}]
    if details:
        resource["note"] = [{"text": details}]
    return name, _bundle(resource)


def _observation(request: ManualRecordRequest, patient: dict, name: str, date: str, details: str) -> dict:
    raw_value = request.value.strip()
    try:
        numeric = float(raw_value)
        if not numeric == numeric or numeric in (float("inf"), float("-inf")):
            raise ValueError
        value: dict = {"valueQuantity": {"value": numeric}}
        if (request.unit or "").strip():
            value["valueQuantity"]["unit"] = request.unit.strip()
    except ValueError:
        value = {"valueString": raw_value}
    resource = {"resourceType": "Observation", "status": "final", "subject": patient, "category": [{"text": "Vital Signs" if request.category == "vital" else "Laboratory"}], "code": {"text": name}, **value}
    if date:
        resource["effectiveDateTime"] = date
    if details:
        resource["note"] = [{"text": details}]
    return resource


def _noted_resource(resource_type: str, patient_field: str, patient: dict, name: str, details: str) -> dict:
    resource = {"resourceType": resource_type, patient_field: patient, "code": {"text": name}}
    if details:
        resource["note"] = [{"text": details}]
    return resource


def _bundle(resource: dict) -> dict:
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": resource}]}
