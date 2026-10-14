"""Extract visit-linked codes and diagnoses from returned eCW resources."""
from __future__ import annotations

from typing import Any

from app.insurance.ecw.ecw_fields import (
    _coding,
    _concept_labels,
    _norm,
    _number,
    _reference,
    _source_references,
)
from app.insurance.ecw.ecw_appointment_fields import _history_summary


def coded_visit(visit: dict, relevant_orders: list[dict],
                relevant_procedures: list[dict], charges: list[dict],
                conditions: list[dict],
                encounter_ref: str, visit_refs: set[str]
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
    counts = {code: sum(item["code"] == code for item in lines.values()) for code in {item["code"] for item in lines.values()}}
    for item in lines.values():
        item["needs_confirmation"] = counts[item["code"]] > 1
    if len(diagnoses) == 1:
        for item in lines.values():
            item["diagnosis"] = item["diagnosis"] or diagnoses[0]

    return relevant_charges, diagnoses, list(lines.values()), relevant_conditions
