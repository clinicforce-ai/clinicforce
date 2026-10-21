"""Extract visit-linked codes and diagnoses from returned eCW resources."""
from __future__ import annotations

import re
from typing import Any

from app.insurance.ecw.ecw_fields import (
    _coding,
    _concept_labels,
    _norm,
    _number,
    _reference,
    _source_references,
)
from app.insurance.ecw.ecw_appointment_fields import _history_summary, _visit_type


def coded_visit(visit: dict, relevant_orders: list[dict],
                relevant_procedures: list[dict], charges: list[dict],
                conditions: list[dict],
                encounter_ref: str, visit_refs: set[str],
                linked_appointments: list[dict] | None = None,
                ) -> tuple[list[dict], list[str], list[dict], list[dict]]:
    """Return charges, diagnoses, coded lines and linked conditions."""
    relevant_charges = [row for row in charges if _reference(row.get("context")) == encounter_ref]
    clinical_refs = set(ref for row in [visit, *relevant_orders, *relevant_procedures, *relevant_charges] for ref in _source_references(row))
    relevant_conditions = [row for row in conditions if _reference(row.get("encounter")) == encounter_ref
                           or f"Condition/{row.get('id')}" in clinical_refs]
    diagnoses = list(dict.fromkeys(code for row in [visit, *relevant_orders, *relevant_procedures,
                                                    *relevant_charges, *relevant_conditions]
                                    for code in _coding(row, "diagnosis")))[:12]
    lines: dict[str, dict[str, Any]] = {}
    classified = ([(row, "scheduled") for row in relevant_charges] +
                  [(row, "scheduled") for row in relevant_procedures] +
                  [(row, "scheduled" if f"ServiceRequest/{row.get('id')}" in visit_refs or
                    _reference(row.get("encounter")) == encounter_ref else "possible") for row in relevant_orders] +
                  [(visit, "scheduled")])
    for row, service_status in classified:
        row_diagnoses = _coding(row, "diagnosis")
        for code in _coding(row, "procedure"):
            line_id = f"{row.get('resourceType', 'service')}:{row.get('id', 'visit')}:{code}"
            item = lines.setdefault(line_id, {"id": line_id, "code": code, "units": None, "charge": None, "modifiers": [],
                                          "diagnosis": None, "service_status": service_status})
            description = next((label for label in _concept_labels(row.get("code"))
                                if _norm(label) != _norm(code)), None)
            if description:
                item["description"] = description
            if service_status == "scheduled":
                item["service_status"] = "scheduled"
            if len(row_diagnoses) == 1:
                item["diagnosis"] = row_diagnoses[0]
            quantity = row.get("quantity") or row.get("quantityQuantity") or {}
            if isinstance(quantity, dict) and quantity.get("value") is not None:
                item["units"] = _number(quantity["value"])
            price = row.get("priceOverride") or {}
            if isinstance(price, dict) and price.get("value") is not None:
                item["charge"] = _number(price["value"], money=True)
                if item["charge"] is not None:
                    item["charge_source"] = "visit"
    # A single explicit office E/M service on the selected Encounter is one
    # planned visit unit; free-text reason codes and other procedures are not.
    encounter_line = next(iter(lines.values())) if len(lines) == 1 else None
    service_type = visit.get("serviceType") or {}
    service_codes = _coding(service_type, "procedure")
    concepts = service_type if isinstance(service_type, list) else [service_type]
    visit_type = _visit_type(visit)
    if (encounter_line and encounter_line["id"].startswith("Encounter:")
            and encounter_line["units"] is None and len(service_codes) == 1
            and encounter_line["code"] == service_codes[0]
            and encounter_line["code"].isdigit()
            and ((99202 <= int(service_codes[0]) <= 99205 and visit_type != "established")
                 or (99212 <= int(service_codes[0]) <= 99215 and visit_type != "new"))
            and str(visit.get("status") or "").lower() not in {"cancelled", "entered-in-error", "finished", "completed"}
            and any(isinstance(concept, dict) and any(
                isinstance(coding, dict) and str(coding.get("code") or "").upper() == service_codes[0]
                and any(term in str(coding.get("system") or "").lower() for term in ("cpt", "hcpcs"))
                for coding in concept.get("coding") or []) for concept in concepts)):
        encounter_line["units"] = 1
        encounter_line["source"] = "eCW Encounter.serviceType"
    if not lines:
        # Appointment.serviceType is a scheduling hint. Only a single explicit
        # office E/M CPT establishes both the planned code and its one unit.
        # Other procedures need clinician-supplied units; notes are not codes.
        visit_type = _visit_type(visit)
        appointment_codes = [
            (row, str(coding.get("code") or "").upper())
            for row in linked_appointments or []
            if row.get("status") in {"proposed", "pending", "booked", "arrived", "checked-in"}
            for concept in row.get("serviceType") or []
            if isinstance(concept, dict)
            for coding in concept.get("coding") or []
            if isinstance(coding, dict)
            and any(term in str(coding.get("system") or "").lower() for term in ("cpt", "hcpcs"))
            and re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", str(coding.get("code") or "").upper())
            and str(coding.get("code") or "").isdigit()
            and (99202 <= int(coding["code"]) <= 99205 or 99212 <= int(coding["code"]) <= 99215)
            and (visit_type is None or
                 (visit_type == "new" and 99202 <= int(coding["code"]) <= 99205) or
                 (visit_type == "established" and 99212 <= int(coding["code"]) <= 99215))
        ]
        if len({code for _, code in appointment_codes}) == 1:
            row, code = appointment_codes[0]
            line_id = f"Appointment:{row.get('id', 'visit')}:{code}"
            lines[line_id] = {"id": line_id, "code": code, "units": 1, "charge": None,
                              "modifiers": [], "diagnosis": None, "service_status": "scheduled",
                              "source": "eCW Appointment.serviceType"}
    counts = {code: sum(item["code"] == code for item in lines.values()) for code in {item["code"] for item in lines.values()}}
    for item in lines.values():
        item["needs_confirmation"] = counts[item["code"]] > 1
    if len(diagnoses) == 1:
        for item in lines.values():
            item["diagnosis"] = item["diagnosis"] or diagnoses[0]

    return relevant_charges, diagnoses, list(lines.values()), relevant_conditions
