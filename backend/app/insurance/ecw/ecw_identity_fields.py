"""Verify patient names and dates returned by eCW."""
from __future__ import annotations

from typing import Any

from app.insurance.ecw.ecw_fields import _given_words, _name, _norm


def _patient_matches(resource: dict[str, Any], profile: dict[str, Any], *, family_suffix: bool = False) -> bool:
    if resource.get("active") is False:
        return False
    _, family = _name(resource)
    # FHIR given names are ordered and may contain spaces within each element.
    # Consume the entire entered first name before checking the middle initial.
    words = _given_words(resource)
    expected_words = str(profile.get("patient_first_name") or "").split()
    expected_family = _norm(profile.get("patient_last_name"))
    family_matches = _norm(family) == expected_family or (family_suffix and expected_family and family and family.strip() and
                      _norm(family.split()[-1]) == expected_family)
    if (not expected_words or not all(_norm(word) for word in expected_words)
            or [_norm(word) for word in words[:len(expected_words)]] != [_norm(word) for word in expected_words]
            or not family_matches):
        return False
    middle = words[len(expected_words)] if len(words) > len(expected_words) else ""
    expected = str(profile.get("patient_middle_initial") or "").strip()
    return (not expected or not middle or _norm(middle[0]) == _norm(expected[0])) and resource.get("birthDate") == profile.get("patient_birth_date")

def _candidate_given_name(resource: dict, profile: dict) -> str:
    count = len(str(profile.get("patient_first_name") or "").split())
    return " ".join(_given_words(resource)[:count]) if count else (_name(resource)[0] or "")
