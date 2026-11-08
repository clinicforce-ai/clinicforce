"""Compact completed-visit evidence for CPT planning."""
from __future__ import annotations

from typing import Any


def prior_appointments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    appointments = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        codes = [str(code).upper() for code in item.get('coded_services', []) if isinstance(code, str)]
        diagnoses = [str(code).upper() for code in item.get('diagnoses') or [] if isinstance(code, str)]
        appointments.append({'date': str(item.get('date') or ''), 'reason': str(item.get('reason') or ''),
                             'coded_services': codes, **({'diagnoses': diagnoses[:12]} if diagnoses else {})})
    return appointments


def established_visit_for_planning(inputs: dict, source: dict) -> bool:
    if inputs.get('visit_type') == 'established':
        return True
    if inputs.get('visit_type') is not None:
        return False
    if inputs.get('separate_em_planned') is True:
        return True
    return any(any(code.isdigit() and (99202 <= int(code) <= 99205 or 99212 <= int(code) <= 99215)
                   for code in row['coded_services'])
               for row in prior_appointments(source.get('recent_completed_appointments')))
