"""Read eCW resources linked to one verified visit with bounded requests."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from types import ModuleType
from typing import Any

import httpx

from app.insurance.ecw.ecw_appointment_fields import _appointment_date
from app.insurance.ecw.ecw_fields import _period_contains, _reference, _source_references


@dataclass
class VisitResources:
    history_timeout: str | None
    history_unavailable: str | None
    completed_history: list[dict]
    coverage_result: tuple
    coverage_rows: list[dict]
    orders: list[dict]
    procedures: list[dict]
    charges: list[dict]
    query_timeouts: set[str]
    query_unavailable: set[str]
    eligible: list[dict]
    coverage: dict
    visit_refs: set[str]
    relevant_orders: list[dict]
    relevant_procedures: list[dict]
    refs: list[str]
    resolved: dict[str, dict]
    reference_timeouts: list[str]
    conditions: list[dict]
    role_resources: list[dict]
    role_provider_refs: list[str]
    provider_refs: list[str]



async def read_visit_resources(backend: ModuleType, client: httpx.AsyncClient, issuer: str, token: str,
                               patient_id: str, patient_timeout: str | None,
                               visit: dict, encounter_ref: str, encounter_id: str,
                               service_date: str, today_date: date) -> VisitResources:
    """Collect visit evidence and retain timeout/unavailable provenance."""
    today = today_date.isoformat()
    query = {"patient": patient_id}
    HISTORY_DAYS = backend.HISTORY_DAYS
    HISTORY_TIMEOUT_SECONDS = backend.HISTORY_TIMEOUT_SECONDS
    # History is context only. A short independent query avoids letting
    # an eCW history stall block the appointment insurance workflow.
    history_start = (today_date - timedelta(days=HISTORY_DAYS)).isoformat()
    history_rows, history_timeout, history_unavailable = await backend._optional_search(
        client, issuer, token, "Encounter",
        [("patient", patient_id), ("date", f"ge{history_start}"), ("date", f"le{today_date - timedelta(days=1)}")],
        timeout_seconds=HISTORY_TIMEOUT_SECONDS,
    )
    completed_history = sorted(
        (row for row in history_rows if _appointment_date(row) < today
         and str(row.get("status") or "").casefold() in {"finished", "completed"}),
        key=_appointment_date, reverse=True,
    )[:5]
    # eCW can stall unrelated patient searches when Procedure is requested
    # concurrently; collect it first so it can still enrich the visit.
    procedure_result = await backend._optional_search(client, issuer, token, "Procedure", query)
    coverage_result, order_result, charge_result = await asyncio.gather(
        backend._optional_search(client, issuer, token, "Coverage", query),
        backend._optional_search(client, issuer, token, "ServiceRequest", query),
        backend._optional_search(client, issuer, token, "ChargeItem", {
            # eCW rejects resource-prefixed references in these search parameters.
            "subject": patient_id, "context": encounter_id,
        }) if encounter_ref else asyncio.sleep(0, result=([], None, None)),
    )
    coverage_rows, orders, procedures, charges = (result[0] for result in
                                                  (coverage_result, order_result, procedure_result, charge_result))
    query_timeouts = {result[1] for result in (coverage_result, order_result, procedure_result, charge_result)
                      if result[1]}
    query_unavailable = {result[2] for result in (coverage_result, order_result, procedure_result, charge_result)
                         if result[2]}
    coverage_rows = [row for row in coverage_rows if row.get('resourceType') == 'Coverage'
                     and _reference(row.get('beneficiary')) == f'Patient/{patient_id}']
    eligible = [row for row in coverage_rows if row.get("status") == "active" and _period_contains(row, service_date)]
    ordered = sorted(eligible, key=lambda row: row.get("order", 999999))
    coverage = ordered[0] if ordered and (len(ordered) == 1 or ordered[0].get("order") != ordered[1].get("order")) else {}

    if not coverage:
        # Retain the saved policy even if it does not cover this future date.
        saved_policies = [row for row in coverage_rows if row.get('resourceType') == 'Coverage'
                          and _reference(row.get('beneficiary')) == 'Patient/' + patient_id
                          and row.get('status') != 'entered-in-error']
        if len(saved_policies) == 1:
            coverage = saved_policies[0]
    visit_refs = set(_source_references(visit))
    relevant_orders = [row for row in orders if f"ServiceRequest/{row.get('id')}" in visit_refs or
                       _reference(row.get("encounter")) == encounter_ref]
    relevant_procedures = [row for row in procedures if f"Procedure/{row.get('id')}" in visit_refs or
                           _reference(row.get("encounter")) == encounter_ref]
    resolvable = ("Patient/", "RelatedPerson/", "Organization/", "Practitioner/", "PractitionerRole/",
                  "Location/", "Condition/")
    refs = [ref for ref in dict.fromkeys(
        ref for row in [visit, *coverage_rows, *relevant_orders, *relevant_procedures] for ref in _source_references(row)
    ) if ref != f"Patient/{patient_id}" and ref.startswith(resolvable)]
    resolved_results = await asyncio.gather(*(backend._bounded_read(client, issuer, token, ref) for ref in refs))
    resolved_list = [row for row, _ in resolved_results]
    timeout_resources = ({resource for _, resource in resolved_results if resource} | query_timeouts |
                         ({patient_timeout} if patient_timeout else set()))
    resolved = {f"{row.get('resourceType')}/{row.get('id')}": row for row in resolved_list if row.get("resourceType") and row.get("id")}

    provider_refs = [ref for ref in refs if ref.startswith("Practitioner/")]
    role_searches = await asyncio.gather(*(
        backend._bounded_read(client, issuer, token, f"PractitionerRole/{role_id}")
        for role_id in {
            ref.split("/", 1)[1] for ref in refs if ref.startswith("PractitionerRole/") and ref not in resolved
        }
    ))
    timeout_resources.update(resource for _, resource in role_searches if resource)
    for row, _ in role_searches:
        if row.get("resourceType") and row.get("id"):
            resolved[f"{row['resourceType']}/{row['id']}"] = row
    role_resources = [row for row in resolved.values() if row.get("resourceType") == "PractitionerRole"]
    role_provider_refs = [ref for row in role_resources if (ref := _reference(row.get("practitioner")))]
    unresolved_provider_refs = [ref for ref in role_provider_refs if ref not in resolved]
    provider_reads = await asyncio.gather(*(
        backend._bounded_read(client, issuer, token, ref) for ref in unresolved_provider_refs
    ))
    timeout_resources.update(resource for _, resource in provider_reads if resource)
    for row, _ in provider_reads:
        if row.get("resourceType") and row.get("id"):
            resolved[f"{row['resourceType']}/{row['id']}"] = row
    reference_timeouts = sorted(timeout_resources)
    conditions = list({
        f"Condition/{row.get('id')}": row for row in resolved.values()
        if row.get("resourceType") == "Condition" and row.get("id")
    }.values())

    return VisitResources(
        history_timeout=history_timeout,
        history_unavailable=history_unavailable,
        completed_history=completed_history,
        coverage_result=coverage_result,
        coverage_rows=coverage_rows,
        orders=orders,
        procedures=procedures,
        charges=charges,
        query_timeouts=query_timeouts,
        query_unavailable=query_unavailable,
        eligible=eligible,
        coverage=coverage,
        visit_refs=visit_refs,
        relevant_orders=relevant_orders,
        relevant_procedures=relevant_procedures,
        refs=refs,
        resolved=resolved,
        reference_timeouts=reference_timeouts,
        conditions=conditions,
        role_resources=role_resources,
        role_provider_refs=role_provider_refs,
        provider_refs=provider_refs,
    )
