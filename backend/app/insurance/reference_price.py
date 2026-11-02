"""Select one approved BCBSTX rate for an exactly scoped visit line."""
from __future__ import annotations


def select_reference(line: dict, references: list[dict], service_date: str | None,
                     scope: dict | None) -> dict | None:
    scope = scope or {}
    dimensions = ('place_of_service', 'rendering_npi', 'billing_npi', 'location_id')
    if not service_date or any(not scope.get(key) for key in dimensions):
        return None
    matching = [row for row in references if row.get('id') == line.get('id')
                and row.get('code') == line.get('code')
                and row.get('units') == line.get('units')
                and (row.get('modifiers') or []) == (line.get('modifiers') or [])
                and row.get('reference_basis') == 'BCBSTX_CROSS_PAYER_REFERENCE'
                and row.get('rate_version')
                and (not row.get('effective_from') or row['effective_from'] <= service_date)
                and (not row.get('effective_to') or row['effective_to'] >= service_date)
                and all(row.get(key) == scope[key] for key in dimensions)
                and str(row.get('source_reference') or '').startswith('library:')
                and type(row.get('unit_reference_cents')) is int and row['unit_reference_cents'] >= 0]
    return matching[0] if len(matching) == 1 else None
