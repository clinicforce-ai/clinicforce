"""Adapters for converting wearable payloads to FHIR R4 Observations."""

from __future__ import annotations

from typing import Any


def _as_number(value: Any, field_name: str) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain a numeric value") from exc
    return int(number) if number.is_integer() else number


def _first_record(payload: dict[str, Any], collection_keys: tuple[str, ...]) -> dict[str, Any]:
    """Accept both a single sample and the usual vendor response collection."""
    for key in collection_keys:
        records = payload.get(key)
        if isinstance(records, list) and records:
            if not isinstance(records[0], dict):
                raise ValueError(f"{key} must contain object records")
            return records[0]
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return payload


async def parse_apple_health_heartrate(payload: dict, patient_id: str) -> dict:
    """Convert one Apple Health heart-rate sample to a final FHIR Observation."""
    sample = _first_record(payload, ("heart_rate", "heartRates", "records"))
    value = sample.get("value", sample.get("heart_rate", sample.get("quantity")))
    timestamp = sample.get("timestamp", sample.get("startDate", sample.get("date")))
    if value is None or not timestamp:
        raise ValueError("Apple Health heart-rate payload requires value and timestamp/startDate")

    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}],
            "text": "Heart rate",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": timestamp,
        "valueQuantity": {
            "value": _as_number(value, "heart rate"),
            "unit": "beats/min",
            "system": "http://unitsofmeasure.org",
            "code": "/min",
        },
    }


async def parse_fitbit_steps(payload: dict, patient_id: str) -> dict:
    """Convert one Fitbit daily step record to a final FHIR Observation."""
    sample = _first_record(payload, ("activities-steps", "steps", "data"))
    value = sample.get("value", sample.get("steps"))
    date = sample.get("dateTime", sample.get("date", payload.get("dateTime")))
    if value is None or not date:
        raise ValueError("Fitbit steps payload requires value and dateTime")

    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "55423-8",
                    "display": "Number of steps in 24 hour Measured",
                }
            ],
            "text": "Number of steps in 24 hour Measured",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": date,
        "valueQuantity": {
            "value": _as_number(value, "steps"),
            "unit": "steps",
        },
    }


def generate_mock_wearable_payload() -> dict:
    """Return representative Apple Health and Fitbit API payload fragments."""
    return {
        "apple_health": {
            "heart_rate": [{"value": 72, "startDate": "2026-08-02T09:30:00-04:00"}]
        },
        "fitbit": {"activities-steps": [{"dateTime": "2026-08-02", "value": "8432"}]},
    }
