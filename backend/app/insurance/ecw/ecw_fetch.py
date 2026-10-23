"""Fetch and assemble one scheduled eCW visit for an estimate."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.insurance import provider_specialty
from app.insurance.stedi_discovery import patient_address, previous_addresses
from app.insurance.ecw.ecw_fields import (
    _name,
    _phone,
    _reference,
    _period_contains,
    _npi,
    _tin,
    _tin_conflict,
    _display,
    _concept_labels,
)
from app.insurance.ecw.ecw_appointment_fields import (
    _appointment_date,
    _visit_context,
    appointment_fields,
    _visit_type,
    _history_summary,
)
from app.insurance.ecw.ecw_coverage_fields import (
    _payer_id,
    _relationship,
    _group,
    _plan_number,
    _plan_details,
    _plan,
)
from app.insurance.ecw.ecw_coverage import (
    coverage_subscriber, coverage_member_id, coverage_record_status,
    appointment_insurance_candidates, appointment_insurance_fallback,
)
from app.insurance.ecw.ecw_patients import patient_account_number
from app.insurance.ecw.ecw_appointments import select_upcoming_visit
from app.insurance.ecw.ecw_visit_coding import coded_visit
from app.insurance.ecw.ecw_visit_resources import read_visit_resources
from app.logs.collection_audit import collection_event



async def fetch_inputs(backend: ModuleType, profile: dict[str, Any], practice: dict[str, Any], appointment_id: str | None = None,
                       *, transport: httpx.AsyncBaseTransport | None = None,
                       review_service_date: str | None = None) -> tuple[dict, list[str], dict]:
    """Fetch and reduce eCW data; never infer codes, payer IDs, prices or provider roles."""
    client_id, issuer = backend.settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer)
            patient, patient_id, patient_timeout = await backend._patient_for_profile(client, issuer, token, profile)
            now = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York")))
            today_date = now.date()
            today = today_date.isoformat()
            visit, encounters, next_visits = await select_upcoming_visit(
                backend, client, issuer, token, patient_id, profile, appointment_id, now,
                review_service_date=review_service_date,
            )
            service_date = _appointment_date(visit) or today
            encounter_id = str(visit.get("id", ""))
            encounter_ref = f"Encounter/{encounter_id}" if encounter_id else ""
            linked_appointments = []
            appointment_notes_status = 'not_exposed'
            # Optional entitlement: never add a scope to the main intake token.
            if visit.get('appointment') and os.getenv('ECW_APPOINTMENT_READ_ENABLED', 'true').lower() == 'true':
                try:
                    note_token = await asyncio.wait_for(backend._access_token(client, client_id, issuer, backend.SCOPES + ('system/Appointment.read',)), 4)
                    for ref in (visit.get('appointment') or [])[:3]:
                        reference = _reference(ref) or ''
                        if not re.fullmatch(r'Appointment/[A-Za-z0-9.\-]{1,200}', reference):
                            continue
                        row, timed_out = await asyncio.wait_for(backend._bounded_read(client, issuer, note_token, reference), 4)
                        if timed_out:
                            appointment_notes_status = 'timed_out'
                        elif (row.get('resourceType') == 'Appointment' and row.get('status') != 'entered-in-error'
                              and any(_reference(p.get('actor')) == f'Patient/{patient_id}' for p in row.get('participant', []))):
                            linked_appointments.append(row)
                            appointment_notes_status = 'returned'
                except (backend.EcwBackendError, httpx.HTTPError, asyncio.TimeoutError):
                    appointment_notes_status = 'unavailable'
            visit_resources = await read_visit_resources(
                backend, client, issuer, token, patient_id, patient_timeout, visit,
                encounter_ref, encounter_id, service_date, today_date,
            )
            history_timeout = visit_resources.history_timeout
            history_unavailable = visit_resources.history_unavailable
            completed_history = visit_resources.completed_history
            coverage_result = visit_resources.coverage_result
            coverage_rows = visit_resources.coverage_rows
            orders = visit_resources.orders
            procedures = visit_resources.procedures
            charges = visit_resources.charges
            query_timeouts = visit_resources.query_timeouts
            query_unavailable = visit_resources.query_unavailable
            eligible = visit_resources.eligible
            coverage = visit_resources.coverage
            visit_refs = visit_resources.visit_refs
            relevant_orders = visit_resources.relevant_orders
            relevant_procedures = visit_resources.relevant_procedures
            refs = visit_resources.refs
            resolved = visit_resources.resolved
            reference_timeouts = visit_resources.reference_timeouts
            conditions = visit_resources.conditions
            role_resources = visit_resources.role_resources
            role_provider_refs = visit_resources.role_provider_refs
            provider_refs = visit_resources.provider_refs

            subscriber_ref = _reference(coverage.get("subscriber"))
            subscriber_resource = coverage_subscriber(coverage, patient, resolved)
            relationship = _relationship(coverage)
            sub_given, sub_family = _name(subscriber_resource)
            payer_ref = next((_reference(item) for item in coverage.get("payor", []) if _reference(item)), None)
            payer = resolved.get(payer_ref or "", {})

            provider_refs = list(dict.fromkeys([*provider_refs, *role_provider_refs]))
            provider_resources = [resolved[ref] for ref in provider_refs if ref in resolved]
            providers = practice.get("providers", {}) if isinstance(practice.get("providers"), dict) else {}
            candidate_npis = [_npi(row) for row in provider_resources]
            ecw_rendering_npi = next((value for value in candidate_npis if value), None)
            rendering_resource = next((row for row in provider_resources if _npi(row) == ecw_rendering_npi),
                                      provider_resources[0] if provider_resources else {})
            rendering_npi = next((value for value in candidate_npis if value and value in providers), None)

            location_ref = next((ref for ref in refs if ref.startswith("Location/")), None)
            location = resolved.get(location_ref or "", {})
            locations = practice.get("locations", {}) if isinstance(practice.get("locations"), dict) else {}
            location_config = locations.get(location_ref or "") or locations.get(str(location.get("id") or "")) or locations.get(str(location.get("name") or "")) or {}
            billing_ref = _reference(visit.get("serviceProvider"))
            billing_resource = resolved.get(billing_ref or "", {})
            ecw_billing_npi = _npi(billing_resource)
            billing_npi = ecw_billing_npi or location_config.get("billing_provider_npi")
            billing = providers.get(billing_npi, {}) if billing_npi else {}
            rendering = providers.get(rendering_npi, {}) if rendering_npi else {}

            recent_completed_appointments = [_history_summary(row, procedures, orders) for row in completed_history]
            (relevant_charges, diagnoses, coded_lines, relevant_conditions) = coded_visit(
                 visit, relevant_orders, relevant_procedures, charges,
                 conditions, encounter_ref, visit_refs, linked_appointments,
             )
            imported_fields = appointment_fields(visit, linked_appointments)
            insurance_candidates = appointment_insurance_candidates(imported_fields)
            insurance_fallback = appointment_insurance_fallback(imported_fields, coverage_member_id(coverage), _display(payer), len(eligible))
            collection_event('ecw_insurance_resolution', stage='coverage', resource='coverage',
                             outcome='returned' if coverage_rows else 'unavailable' if 'Coverage' in query_timeouts or any(
                                 value.startswith('Coverage') for value in query_unavailable) else 'empty',
                             count=len(coverage_rows))
            collection_event('ecw_insurance_resolution', stage='appointment_notes', count=len(insurance_candidates),
                             outcome='candidate' if insurance_fallback else 'not_selected' if insurance_candidates else 'empty',
                             reason_code='unverified_note_candidate' if insurance_fallback else
                             'note_candidate_not_selected' if insurance_candidates else 'no_note_candidates')
            collection_event('ecw_insurance_resolution', stage='subscriber',
                             outcome='returned' if sub_given and sub_family else 'unavailable',
                             reason_code='subscriber_returned' if sub_given and sub_family else 'subscriber_not_returned')
            member_id = coverage_member_id(coverage) or insurance_fallback.get('member_id')
            payer_name = _display(payer) or insurance_fallback.get('payer')
            first, family = _name(patient)
            gender = {"male": "M", "female": "F"}.get(str(patient.get("gender", "")).lower(), "U")
            inputs = {
                "patient": {"patient_first_name": first, "patient_last_name": family,
                            "patient_birth_date": patient.get("birthDate"), "gender": gender, "address": patient_address(patient),
                            "previous_addresses": previous_addresses(patient)},
                "subscriber": {"firstName": sub_given, "lastName": sub_family,
                               "birthDate": subscriber_resource.get("birthDate"), "memberId": member_id},
                "relationship": relationship, "member_id": member_id,
                "payer_id": _payer_id(payer, practice.get("payer_aliases")), "group_number": _group(coverage), "plan_number": _plan_number(coverage),
                "provider": {**billing, "npi": billing_npi}, "submitter_id": practice.get("submitter_id"),
                "rendering_provider": {**rendering, "npi": rendering_npi},
                "service_type": location_config.get("service_type"), "service_date": service_date,
                "place_of_service": location_config.get("place_of_service"), "diagnoses": diagnoses,
                "patient_phone": _phone(patient),
                "reason": _visit_context(visit, relevant_orders, relevant_procedures, relevant_conditions),
                "appointment_reason": "; ".join(_concept_labels(visit.get("reasonCode"))) or None,
                "appointment_type": "; ".join(_concept_labels(visit.get("type"))) or None,
                # Do not infer new-versus-established from an incomplete FHIR
                # history search. eCW must label the appointment explicitly.
                "visit_type": _visit_type(visit),
                "lines": coded_lines,
            }
    except backend.EcwResourceUnavailable as exc:
        raise backend.EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
    except httpx.HTTPError as exc:
        raise backend.EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc

    coverage_missing: list[str] = []
    if not appointment_id and len(next_visits) > 1:
        coverage_missing.append("eClinicalWorks returned more than one appointment on the next service date; an appointment-specific identifier is required to choose safely.")
    elif not visit:
        coverage_missing.append("eClinicalWorks did not return a scheduled appointment or encounter for this patient.")
    if not coverage:
        coverage_missing.append("eClinicalWorks did not identify one active primary coverage for this appointment.")
    if not inputs["member_id"] or not relationship or not all(inputs["subscriber"].values()):
        coverage_missing.append("The eClinicalWorks coverage is missing the subscriber identity, relationship or member ID required by Availity.")
    if not inputs["payer_id"]:
        coverage_missing.append(f"Administrator: map the exact eClinicalWorks payer name or identifier ({_display(payer) or 'not returned'}) to its verified Availity payer ID.")
    if not billing_npi or not billing:
        coverage_missing.append("The appointment's billing provider NPI is not in the approved Availity practice configuration.")
    if not practice.get("submitter_id"):
        coverage_missing.append("Administrator: add the Availity-issued submitter ID to the practice configuration.")
    review_missing = list(coverage_missing)
    if not inputs["lines"]:
        review_missing.append("eClinicalWorks did not return clinician-coded CPT/HCPCS services for this appointment.")
    if not diagnoses:
        review_missing.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if any(not row.get("diagnosis") or row["diagnosis"] not in diagnoses for row in inputs["lines"]):
        review_missing.append("eClinicalWorks did not associate every coded service with one of the returned ICD-10 diagnoses.")
    if any(not row.get("units") for row in inputs["lines"]):
        review_missing.append("eClinicalWorks did not return the intended units for every coded service.")
    if not rendering_npi:
        review_missing.append("The appointment's rendering provider NPI is not in the approved Availity practice configuration.")
    if not inputs["place_of_service"]:
        review_missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
    if not inputs["service_type"]:
        review_missing.append("Administrator: map this appointment to the Availity service type used for service reviews.")
    if reference_timeouts:
        review_missing.append("eClinicalWorks did not respond in time while resolving " + ", ".join(reference_timeouts) + "; retry to complete those details.")
    missing = list(review_missing)
    if not inputs["patient_phone"]:
        missing.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
    if any(row.get("charge") is None for row in inputs["lines"]):
        missing.append("eClinicalWorks FHIR did not return each billed line amount required by Availity PCE. Enable the practice-management/billing API field for this app; Availity does not supply this request input.")
    inputs["coverage_missing"] = list(dict.fromkeys(coverage_missing))
    inputs["review_missing"] = list(dict.fromkeys(review_missing))

    ecw_missing: list[str] = []
    if not visit:
        ecw_missing.append("No single upcoming eClinicalWorks encounter could be selected for this patient.")
    if not coverage:
        ecw_missing.append("No single active eClinicalWorks coverage was linked to this appointment.")
    if coverage and (not inputs["member_id"] or not relationship or not all(inputs["subscriber"].values())):
        ecw_missing.append("The eClinicalWorks connection did not return complete subscriber identity, relationship, or member ID. Review the policy or retry unavailable references; this does not establish that the chart lacks these fields.")
    if coverage and not _display(payer) and "Organization" not in reference_timeouts:
        ecw_missing.append("The eClinicalWorks coverage did not return its payer organization.")
    if visit and not inputs["reason"]:
        ecw_missing.append("The eClinicalWorks encounter has no reason, visit type, service, order, procedure, or linked condition.")
    if visit and not inputs["lines"]:
        ecw_missing.append("No single CPT/HCPCS-coded ServiceRequest, Procedure, ChargeItem, Encounter service, or readable linked Appointment service is available in eClinicalWorks.")
    if visit and not diagnoses:
        ecw_missing.append("No ICD-10 diagnosis is linked to this eClinicalWorks appointment, order, or procedure.")
    if "Patient" not in reference_timeouts and not inputs["patient_phone"]:
        ecw_missing.append("The eClinicalWorks Patient record did not return a phone number.")
    if "Practitioner" not in reference_timeouts and not ecw_rendering_npi:
        ecw_missing.append("The eClinicalWorks appointment did not return a practitioner NPI.")
    if billing_ref and "Organization" not in reference_timeouts and not ecw_billing_npi:
        ecw_missing.append("The eClinicalWorks service-provider organization did not return an NPI.")
    if visit and "Location" not in reference_timeouts and not location:
        ecw_missing.append("The eClinicalWorks appointment did not return its location details.")
    if reference_timeouts:
        ecw_missing.append("eClinicalWorks timed out while retrieving or resolving " + ", ".join(reference_timeouts) + "; reload the appointment to retry.")
    if history_timeout:
        ecw_missing.append("eClinicalWorks did not respond in time while loading recent completed appointment history; reload the appointment to retry.")
    elif history_unavailable:
        ecw_missing.append("eClinicalWorks rejected or could not return recent completed appointment history for this app.")
    if "ChargeItem" in query_unavailable:
        ecw_missing.append("ClinicForce cannot read eClinicalWorks charge items for this appointment. Confirm system/ChargeItem.read is active for this practice.")
    remaining_unavailable = sorted(query_unavailable - {"ChargeItem"})
    if remaining_unavailable:
        ecw_missing.append("eClinicalWorks rejected or could not return " + ", ".join(remaining_unavailable) + " for this appointment.")

    rendering_roles = [role for role in role_resources if role.get('active') is not False
        and _period_contains(role, service_date)
        and _reference(role.get('practitioner')) == 'Practitioner/' + str(rendering_resource.get('id') or '')]
    role_labels = list(dict.fromkeys(
        label for role in rendering_roles
        for field in (role.get("code"), role.get("specialty"))
        for label in _concept_labels(field)
    ))
    # Do not override a reported but ambiguous eCW specialty with a registry default.
    has_specialty = any(role.get('specialty') for role in rendering_roles) or any(provider_specialty.office_role([label]) for label in role_labels)
    specialty = (await provider_specialty.lookup(str(ecw_rendering_npi or '')) if not has_specialty else {})
    if specialty:
        role_labels = specialty['roles']
    source = {"system": "eClinicalWorks", "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "_patient_id": patient_id,
              "timezone": practice.get('timezone', 'America/New_York'),
              "appointment_fields": imported_fields,
              "insurance_fallback": insurance_fallback,
              "insurance_candidates": insurance_candidates,
              "coverage_retrieval": {"raw_count": len(coverage_result[0]), "retained_count": len(coverage_rows),
                  "status": "unavailable" if 'Coverage' in query_timeouts or any(value.startswith('Coverage') for value in query_unavailable)
                      else "returned" if coverage_result[0] else "empty"},
              "appointment_notes_status": appointment_notes_status,
              "subscriber_evidence": {"reference": subscriber_ref, "status": 'returned' if sub_given and sub_family else 'not_returned',
                  "patient_linked": bool(subscriber_resource),
                  "member_id_source": 'Coverage.subscriberId' if str(coverage.get('subscriberId') or '').strip() else 'Coverage.identifier' if coverage_member_id(coverage) else 'eCW appointment General Notes' if insurance_fallback.get('member_id') else None},
              "coverage_records": [{"id": row.get('id'), "status": row.get('status'), "order": row.get('order'),
                  "member_id": coverage_member_id(row), "group_number": _group(row), "plan": _plan_details(row),
                  "relationship": _relationship(row), "period": row.get('period') or {},
                  "subscriber": _display(coverage_subscriber(row, patient, resolved)) or (row.get('subscriber') or {}).get('display'),
                  "subscriber_reference": _reference(row.get('subscriber')),
                  "subscriber_birth_date": coverage_subscriber(row, patient, resolved).get('birthDate'),
                  "payer": [_display(resolved.get(_reference(p) or '', {})) or p.get('display')
                            for p in row.get('payor', []) if _display(resolved.get(_reference(p) or '', {})) or p.get('display')],
                  "cost_to_beneficiary": row.get('costToBeneficiary') or []}
                  for row in coverage_rows if _reference(row.get('beneficiary')) == f'Patient/{patient_id}'],
              "appointment_id": encounter_id, "coverage_id": str(coverage.get("id") or ""),
              "active_coverages": len(eligible), "coverage_record_status": coverage_record_status(coverage),
              "self_pay_recorded": bool(coverage and (coverage.get('kind') == 'self-pay' or
                  re.fullmatch(r'(?:self[ -]?pay|cash|uninsured)', payer_name or '', re.I)) and len(eligible) <= 1),
              "patient_details": {"ecw_account_number": patient_account_number(patient),
                                  "gender": patient.get("gender"), "phone": _phone(patient), "address": patient_address(patient),
                                  "email": next((r.get("value") for r in patient.get("telecom", []) if r.get("system") == "email"), None)},
              "coverage_period": coverage.get("period") or {}, "plan": _plan(coverage), "plan_details": _plan_details(coverage),
              "appointment_date": service_date, "appointment_status": visit.get("status"),
              "visit_type": inputs.get("visit_type") or "not_returned",
              "referral_evidence": [
                  {"reference": "ServiceRequest/" + str(row.get("id", "")), "status": row.get("status"),
                   "authored_on": row.get("authoredOn"), "source": "eClinicalWorks"}
                  for row in relevant_orders
                  if row.get("resourceType") == "ServiceRequest" and row.get("status") in {"active", "completed"}
                  and _reference(row.get("subject")) == f"Patient/{patient_id}"
                  and any("referral" in text.lower() for text in _concept_labels(row.get("category")))],
              "recent_completed_appointments": recent_completed_appointments,
              "history_status": "timed_out" if history_timeout else "unavailable" if history_unavailable else "loaded",
              "payer": payer_name,
              "payer_source": 'Coverage.payor' if _display(payer) else 'eCW appointment notes' if insurance_fallback.get('payer') else None,
              "payer_identifiers": [str(item["value"]) for item in (payer.get("identifier") or [])
                                    if isinstance(item, dict) and item.get("value") and
                                    ("payer" in str(item.get("system") or "").casefold() or
                                     "x12" in str(item.get("system") or "").casefold())],
              "location": location.get("name"), "location_id": str(location.get("id") or location_ref or ""),
              "cms_service_zip": ((location.get('address') or {}).get('postalCode')
                                  if isinstance(location.get('address'), dict) else None),
              "cms_locality": location_config.get("cms_locality"),
              "cms_qp_status": rendering.get("cms_qp_status"),
              "billing_provider": {"name": _display(billing_resource), "npi": ecw_billing_npi,
                                   "tin": _tin(billing_resource),
                                   "tin_conflict": _tin_conflict(billing_resource)},
              "rendering_provider": {"name": _display(rendering_resource), "npi": ecw_rendering_npi,
                                     "first_name": _name(rendering_resource)[0],
                                     "last_name": _name(rendering_resource)[1],
                                     "roles": role_labels[:10], "specialty_evidence": specialty or None},
              "missing": list(dict.fromkeys(ecw_missing)),
              "timed_out_resources": reference_timeouts,
              "unavailable_resources": sorted(query_unavailable),
              "resources": {"Coverage": len(coverage_rows), "Encounter": len(encounters),
                            "PastEncounter": len(completed_history),
                            "ServiceRequest": len(relevant_orders), "Procedure": len(relevant_procedures),
                            "Condition": len(conditions), "PractitionerRole": len(role_resources),
                            "ChargeItem": len(charges)}}
    return inputs, list(dict.fromkeys(missing)), source
