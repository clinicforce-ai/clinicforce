"""Resolve one confirmed eCW patient without broad roster reads."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from types import ModuleType
from typing import Any

import httpx

from app.insurance.ecw.ecw_identity_fields import _candidate_given_name, _patient_matches
from app.insurance.ecw.ecw_fields import _name, _norm
from app.insurance.identity import one_name_edit as _one_name_edit
from app.logs.collection_audit import collection_event

async def _patient_for_profile(backend: ModuleType, client: httpx.AsyncClient, issuer: str, token: str, profile: dict[str, Any], *, allow_suggestions: bool = False) -> tuple[dict, str, str | None]:
    """Resolve exactly one confirmed patient without exposing a broad patient search."""
    considered = []
    cached_patient_id = str(profile.get("ecw_patient_id") or "")
    if cached_patient_id:
        if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", cached_patient_id):
            raise backend.EcwBackendError("The saved eClinicalWorks patient link is invalid.")
        patient, patient_timeout = await backend._cached_patient(client, issuer, token, cached_patient_id, profile)
        if patient.get("resourceType") != "Patient" or patient.get("id") != cached_patient_id:
            raise backend.EcwBackendError("The saved eClinicalWorks patient link returned a different patient.")
        if profile.get("ecw_account_number") and patient_account_number(patient) != profile["ecw_account_number"]:
            raise backend.EcwBackendError("The selected patient's eCW account changed. Search again and confirm the patient.",
                                  reason_code="candidate_changed", stage="patient_identity")
        patient_rows = [patient]
    elif profile.get("ecw_account_number"):
        patient_timeout = None
        patient_rows = [row for row in await backend._search(client, issuer, token, "Patient", {
            "identifier": str(profile["ecw_account_number"]), "birthdate": str(profile.get("patient_birth_date") or ""),
        }) if patient_account_number(row) == profile["ecw_account_number"]]
    else:
        patient_timeout = None
        patient_rows = await backend._search(client, issuer, token, "Patient", {
            "given": str(profile.get("patient_first_name") or ""),
            "family": str(profile.get("patient_last_name") or ""),
            "birthdate": str(profile.get("patient_birth_date") or ""),
        })
        considered.extend(patient_rows)
        if not any(_patient_matches(row, profile) for row in patient_rows):
            patient_rows = await backend._search(client, issuer, token, "Patient", {
                "family": str(profile.get("patient_last_name") or ""),
                "birthdate": str(profile.get("patient_birth_date") or ""),
            })
    considered.extend(patient_rows)
    matches = {str(row.get("id") or ""): row for row in patient_rows
               if _patient_matches(row, profile, family_suffix=bool(cached_patient_id))}
    if not matches and not cached_patient_id and not profile.get("ecw_account_number"):
        # eCW can store a multiword family name when the patient supplies only
        # its last word. eCW may index a multiword name by its first word, so
        # search both supplied name parts with DOB and require a unique match.
        for term in dict.fromkeys((str(profile.get("patient_last_name") or ""),
                                   str(profile.get("patient_first_name") or ""))):
            patient_rows = await backend._search(client, issuer, token, "Patient", {
                "name": term, "birthdate": str(profile.get("patient_birth_date") or ""),
            })
            considered.extend(patient_rows)
            matches.update({str(row.get("id") or ""): row for row in patient_rows
                            if _patient_matches(row, profile, family_suffix=True)})
        # eCW does not support given+birthdate alone. The name+birthdate queries
        # above cover the supported demographic fallbacks.
    if allow_suggestions and not cached_patient_id and len(matches) != 1:
        candidates = _patient_suggestions(list(matches.values()) if matches else considered, profile)
        if not candidates:
            # Documented name-only search can find a DOB typo. Read only the
            # first page, then require both names locally; never scan the roster.
            for term in dict.fromkeys((str(profile.get("patient_last_name") or ""),
                                       str(profile.get("patient_first_name") or ""))):
                if len(_norm(term)) < 3:
                    continue
                rows = await backend._search(client, issuer, token, "Patient", {"name": term}, first_page_only=True)
                candidates = _patient_suggestions(rows, profile)
                if candidates:
                    break
        collection_event("ecw_patient_match", stage="patient_identity", outcome="review", count=len(candidates))
        raise backend.EcwPatientSelectionRequired(candidates)
    if not matches:
        collection_event("ecw_patient_match", stage="patient_identity", outcome="error", reason_code="no_match", count=0)
        raise backend.EcwBackendError("No eClinicalWorks patient matched the entered name and date of birth. Check the patient's eCW record before continuing.",
                              reason_code="no_match", stage="patient_identity")
    if len(matches) != 1:
        collection_event("ecw_patient_match", stage="patient_identity", outcome="error", reason_code="ambiguous_match", count=len(matches))
        raise backend.EcwBackendError("Multiple eClinicalWorks patients matched the entered name and date of birth. Resolve the identity in eCW before continuing.",
                              reason_code="ambiguous_match", stage="patient_identity")
    patient = next(iter(matches.values()))
    patient_id = str(patient.get("id", ""))
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", patient_id):
        raise backend.EcwBackendError("eClinicalWorks returned an invalid patient identifier.")
    collection_event("ecw_patient_match", stage="patient_identity", outcome="success", count=1)
    return patient, patient_id, patient_timeout

def _patient_suggestions(rows: list[dict], profile: dict) -> list[dict]:
    """Rank bounded search results for human review; never establish identity."""
    first, family = _norm(profile.get("patient_first_name")), _norm(profile.get("patient_last_name"))
    ranked = {}
    for row in rows:
        given, last = _candidate_given_name(row, profile), _name(row)[1]
        patient_id, dob = str(row.get("id") or ""), row.get("birthDate")
        if (row.get("resourceType") != "Patient" or row.get("active") is False
                or not re.fullmatch(r"[A-Za-z0-9.\-]{1,200}", patient_id) or not given or not last
                or not isinstance(dob, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob)):
            continue
        g, f = _norm(given), _norm(last)
        same_dob = dob == profile.get("patient_birth_date")
        family_tail = _norm(last.split()[-1])
        g_score = SequenceMatcher(None, first, g).ratio()
        f_score = max(SequenceMatcher(None, family, f).ratio(), SequenceMatcher(None, family, family_tail).ratio())
        same_account = bool(profile.get("ecw_account_number")) and patient_account_number(row) == profile["ecw_account_number"]
        first_typo = _one_name_edit(first, g) and family in {f, family_tail}
        family_typo = g == first and (_one_name_edit(family, f) or _one_name_edit(family, family_tail))
        corrected = {**profile, "patient_first_name": given, "patient_last_name": last}
        if profile.get("ecw_account_number") and not same_account:
            continue
        if not (same_account or (_patient_matches(row, corrected) and (first_typo or family_typo))
                or _patient_matches(row, {**profile, "patient_birth_date": dob}, family_suffix=True)):
            continue
        differences = [label for label, supplied, equal in (("First name", first, g == first), ("Last name", family, f == family),
                                                             ("Date of birth", profile.get("patient_birth_date"), same_dob)) if supplied and not equal]
        ranked[patient_id] = (g_score + f_score + int(same_dob), {
            "id": patient_id, "given_name": given, "family_name": last, "birth_date": dob,
            "ecw_account_number": patient_account_number(row), "differences": differences,
        })
    return [item for _, item in sorted(ranked.values(), key=lambda pair: (-pair[0], pair[1]["id"]))[:8]]

def patient_account_number(patient: dict) -> str | None:
    # eCW's Patient specification identifies the ACCT number with use=secondary.
    # The usual identifier and resource ID can be encrypted; never substitute them.
    values = {str(row['value']).strip() for row in patient.get('identifier', [])
              if isinstance(row, dict) and row.get('use') == 'secondary' and row.get('value')}
    return next(iter(values)) if len(values) == 1 else None
