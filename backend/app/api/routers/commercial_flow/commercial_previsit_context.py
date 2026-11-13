"""Small eCW context projections shared by commercial visit flows."""
from __future__ import annotations

from typing import Any
from app.insurance import provider_specialty


def ecw_case_data(inputs: dict, source: dict, conflicts: list[str]) -> dict[str, Any]:
    return {"appointment_id": source.get("appointment_id"), "appointment_date": source.get("appointment_date"),
            "retrieved_at": source.get("retrieved_at"), "appointment_status": source.get("appointment_status"),
            "suggested_lines": [{"code": row.get("code"), "units": row.get("units"), "description": row.get("description")}
                                for row in inputs.get("lines", []) if row.get("code")],
            "reason": inputs.get("reason"), "conflicts": conflicts}


def past_visit_with_saved_estimate(visit: dict[str, Any], case: dict | None) -> dict[str, Any]:
    """Attach only an estimate already saved for this exact eCW encounter."""
    ecw = ((case or {}).get("commercial") or {}).get("ecw") or {}
    estimate = (case or {}).get("estimate")
    if (case or {}).get("source_mode") != "commercial" or ecw.get("appointment_id") != visit.get("id") or not isinstance(estimate, dict):
        return {**visit, "saved_estimate": None}
    return {**visit, "saved_estimate": {
        "created_at": case.get("created_at"),
        "allowed_cents": estimate.get("allowed_cents"),
        "patient_cents": estimate.get("patient_cents"),
        "plan_cents": estimate.get("plan_cents"),
        "disclaimer": estimate.get("disclaimer"),
    }}


def benefit_context(inputs: dict, source: dict) -> dict:
    """Use a validated NPI; conflicting visit and eCW providers cannot be priced."""
    rendering = source.get('rendering_provider') or {}
    source_npi = str(rendering.get('npi') or '')
    visit_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    conflict = bool(source_npi and visit_npi and source_npi != visit_npi)
    npi = None if conflict else (visit_npi or source_npi)
    npi = npi if npi and len(npi) == 10 and npi.isdigit() else None
    return {'place_of_service': inputs.get('place_of_service'),
            'visit_context': {key: inputs.get(key) for key in ('reason', 'diagnoses')},
            'provider_office_role': provider_specialty.office_role(rendering.get('roles') or []),
            'provider_specialties': rendering.get('roles') or [],
            'provider_npi': npi, 'provider_npi_conflict': conflict}
