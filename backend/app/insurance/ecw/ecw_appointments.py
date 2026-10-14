"""List and verify patient-bound eCW encounters and visit history."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.insurance.ecw.ecw_patients import patient_account_number
from app.insurance.ecw.ecw_appointment_fields import (
    _appointment_date,
    _history_summary,
    _is_upcoming_appointment,
    _visit_context,
)
from app.insurance.ecw.ecw_identity_fields import _candidate_given_name
from app.insurance.ecw.ecw_fields import _name, _reference
from app.logs.collection_audit import collection_event



async def patient_appointment_history(backend: ModuleType, profile: dict[str, Any], practice: dict[str, Any], *,
                                      transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Patient-scoped encounter history, without the estimator's recent-visit cutoff."""
    client_id, issuer = backend.settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer)
            patient, patient_id, _ = await backend._patient_for_profile(client, issuer, token, profile)
            encounters, timed_out = await backend._bounded_search(client, issuer, token, 'Encounter',
                {'patient': patient_id}, timeout_seconds=20)
            if timed_out:
                raise backend.EcwBackendError('The eCW appointment history did not finish loading.')
    except httpx.HTTPError as exc:
        raise backend.EcwBackendError('The eCW appointment history did not finish loading.') from exc
    visits = []
    for encounter in encounters:
        encounter_id = str(encounter.get('id') or '')
        if (_reference(encounter.get('subject')) != f'Patient/{patient_id}'
                or not re.fullmatch(r'[A-Za-z0-9.\-]{1,200}', encounter_id)
                or not _appointment_date(encounter)
                or encounter.get('status') == 'entered-in-error'):
            continue
        visits.append({'id': encounter_id, 'start': (encounter.get('period') or {}).get('start'),
                       **_history_summary(encounter, [], [])})
    return {'patient_id': patient_id, 'ecw_account_number': patient_account_number(patient),
            'appointments': sorted(visits, key=lambda row: (row['start'] or '', row['id']), reverse=True)}

async def lookup_patient_and_appointments(backend: ModuleType, profile: dict[str, Any], practice: dict[str, Any], *,
                                          allow_suggestions: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Verify one eCW patient and return only current/future appointments; no eligibility request."""
    client_id, issuer = backend.settings()
    phase = "backend_authorization"
    started = time.monotonic()
    async def read() -> dict[str, Any]:
        nonlocal phase
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer)
            phase = "patient_identity"
            patient, patient_id, _ = await backend._patient_for_profile(client, issuer, token, profile, allow_suggestions=allow_suggestions)
            now = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York")))
            today = now.date()
            phase = "appointment_search"
            encounters, timed_out = await backend._bounded_search(
                client, issuer, token, "Encounter",
                [("patient", patient_id), ("date", f"ge{today}"), ("date", f"le{today + timedelta(days=365)}")],
            )
            if timed_out:
                raise backend.EcwBackendError("eClinicalWorks timed out while loading the patient's scheduled encounters. Reload the appointment to retry.")
        appointments = []
        for visit in encounters:
            appointment_id, start = str(visit.get("id") or ""), str((visit.get("period") or {}).get("start") or "")
            if (_reference(visit.get("subject")) != f"Patient/{patient_id}"
                    or not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", appointment_id)
                    or not _is_upcoming_appointment(visit, now)):
                continue
            appointments.append({"id": appointment_id, "start": start, "status": str(visit.get("status") or "scheduled"),
                                 "reason": _visit_context(visit, [], [], []) or ""})
        collection_event("ecw_appointments_selected", stage="appointment_search", outcome="success", count=len(appointments))
        first, family = _candidate_given_name(patient, profile), _name(patient)[1]
        return {"patient": {"id": patient_id, "given_name": first, "family_name": family,
                            "birth_date": patient.get("birthDate"), "ecw_account_number": patient_account_number(patient)},
                "appointments": sorted(appointments, key=lambda row: (row["start"], row["id"]))}

    try:
        return await asyncio.wait_for(read(), backend.LOOKUP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        collection_event("ecw_lookup_deadline", stage=phase, outcome="error", reason_code="lookup_deadline",
                         duration_ms=int((time.monotonic() - started) * 1000))
        label = {"backend_authorization": "backend authorization", "patient_identity": "patient verification",
                 "appointment_search": "appointment search"}[phase]
        raise backend.EcwBackendError(f"eClinicalWorks lookup timed out during {label} at ClinicForce's {backend.LOOKUP_TIMEOUT_SECONDS}-second limit. Retry the lookup.",
                              reason_code="lookup_deadline", stage=phase) from exc
    except backend.EcwResourceUnavailable as exc:
        raise backend.EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
    except httpx.HTTPError as exc:
        raise backend.EcwBackendError("The eClinicalWorks request could not be completed. Retry the lookup.",
                              reason_code=backend._failure_reason(exc), stage=phase) from exc

async def list_appointments(backend: ModuleType, profile: dict[str, Any], practice: dict[str, Any], *, transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, str]]:
    return (await lookup_patient_and_appointments(backend, profile, practice, transport=transport))["appointments"]

async def list_completed_appointments(backend: ModuleType, profile: dict[str, Any], practice: dict[str, Any], *,
                                      transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    """Return recent completed visits for display only; never send eligibility."""
    client_id, issuer = backend.settings()
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, transport=transport) as client:
            token = await backend._access_token(client, client_id, issuer)
            _, patient_id, _ = await backend._patient_for_profile(client, issuer, token, profile)
            today = datetime.now(ZoneInfo(practice.get("timezone", "America/New_York"))).date()
            encounters, timed_out = await backend._bounded_search(
                client, issuer, token, "Encounter",
                [("patient", patient_id), ("date", f"ge{today - timedelta(days=backend.HISTORY_DAYS)}"),
                 ("date", f"le{today - timedelta(days=1)}")],
                timeout_seconds=backend.HISTORY_TIMEOUT_SECONDS,
            )
            if timed_out:
                raise backend.EcwBackendError("eClinicalWorks timed out while loading the patient's completed visit history. Reload to retry.")
            procedure_result, order_result = await asyncio.gather(
                backend._optional_search(client, issuer, token, "Procedure", {"patient": patient_id}),
                backend._optional_search(client, issuer, token, "ServiceRequest", {"patient": patient_id}),
            )
    except backend.EcwResourceUnavailable as exc:
        raise backend.EcwBackendError(f"eClinicalWorks did not authorize ClinicForce to read {exc} data. Confirm the activated app scope and practice activation.") from exc
    except httpx.HTTPError as exc:
        raise backend.EcwBackendError("eClinicalWorks is temporarily unavailable.") from exc
    procedures, orders = procedure_result[0], order_result[0]
    completed = sorted(
        (visit for visit in encounters if _appointment_date(visit) < today.isoformat()
         and str(visit.get("status") or "").casefold() in {"finished", "completed"}),
        key=lambda visit: (_appointment_date(visit), str(visit.get("id") or "")), reverse=True,
    )[:backend.HISTORY_VISIT_LIMIT]
    return [{"id": str(visit.get("id") or ""), "start": str((visit.get("period") or {}).get("start") or ""),
             **_history_summary(visit, procedures, orders)} for visit in completed
            if re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", str(visit.get("id") or ""))]


async def select_upcoming_visit(backend: ModuleType, client: httpx.AsyncClient, issuer: str, token: str,
                                patient_id: str, profile: dict[str, Any],
                                appointment_id: str | None, now: datetime) -> tuple[dict, list[dict], list[dict]]:
    """Choose only a verified upcoming encounter for this patient."""
    today_date = now.date()
    today = today_date.isoformat()
    preferred_date = str(profile.get("ecw_appointment_date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", preferred_date) or preferred_date < today:
        preferred_date = ""
    # eCW serves an exact date quickly but its date-range search can stall.
    # Reuse the last verified appointment date when refreshing an existing case.
    if appointment_id:
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", appointment_id):
            raise backend.EcwBackendError("The selected eClinicalWorks appointment identifier is invalid.")
        selected, encounter_timeout = await backend._bounded_read(client, issuer, token, f"Encounter/{appointment_id}")
        if not selected or _reference(selected.get("subject")) != f"Patient/{patient_id}":
            raise backend.EcwBackendError("The selected eClinicalWorks appointment does not belong to the confirmed patient.")
        encounters = [selected]
    elif preferred_date:
        encounters, encounter_timeout = await backend._bounded_search(
            client, issuer, token, "Encounter", {"patient": patient_id, "date": preferred_date}, attempts=2,
        )
    else:
        # ponytail: Bound eCW's slow open-ended search; widen if clinics schedule over a year ahead.
        encounter_dates = [("date", f"ge{today}"), ("date", f"le{today_date + timedelta(days=365)}")]
        encounters, encounter_timeout = await backend._bounded_search(
            client, issuer, token, "Encounter", [("patient", patient_id), *encounter_dates],
        )
    if encounter_timeout:
        raise backend.EcwBackendError("eClinicalWorks timed out while loading the patient's scheduled encounters. Reload the appointment to retry.")
    scheduled = [row for row in encounters if _is_upcoming_appointment(row, now)]
    next_date = min((_appointment_date(row) for row in scheduled), default="")
    next_visits = [row for row in scheduled if _appointment_date(row) == next_date]
    if appointment_id:
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", appointment_id):
            raise backend.EcwBackendError("The selected eClinicalWorks appointment is invalid.")
        visit = next((row for row in scheduled if row.get("id") == appointment_id), None)
        if visit is None:
            raise backend.EcwBackendError("The selected eClinicalWorks appointment is no longer available. Refresh the appointment list.")
    else:
        visit = next_visits[0] if len(next_visits) == 1 else {}
    return visit, encounters, next_visits
