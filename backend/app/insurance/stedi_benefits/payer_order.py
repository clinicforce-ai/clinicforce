"""Bind a payer's secondary-plan statement to eCW's ordered visit coverages."""

import re

from app.insurance.stedi_benefits.fields import _stedi_entries


def _normal(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _active_on(row: dict, day: str) -> bool:
    period = row.get('period') or {}
    return (row.get('status') == 'active' and
            (not period.get('start') or str(period['start'])[:10] <= day) and
            (not period.get('end') or day <= str(period['end'])[:10]))


def _same_payer(returned: str, ecw_names: list[str]) -> bool:
    other = _normal(returned)
    return bool(other) and any(
        _normal(name) == other or other == 'medicare' and _normal(name).startswith('medicare')
        for name in ecw_names if isinstance(name, str))


def primary_secondary_order(payload: dict, coverage: dict, source: dict, day: str) -> dict | None:
    """Verify one primary eCW policy and one payer-named secondary policy."""
    if (not day or coverage.get('active_coverages') != 2 or not coverage.get('coverage_id')
            or len(payload.get('plans') or []) > 1):
        return None
    records = [row for row in source.get('coverage_records') or []
               if isinstance(row, dict) and _active_on(row, day)]
    if len(records) != 2:
        return None
    primary = next((row for row in records if row.get('id') == coverage['coverage_id']), None)
    secondary = next((row for row in records if row.get('id') != coverage['coverage_id']), None)
    if (not primary or not secondary or type(primary.get('order')) is not int
            or type(secondary.get('order')) is not int
            or primary['order'] >= secondary['order']):
        return None
    reported = []
    for row in _stedi_entries(payload):
        if row.get('code') != 'R' or row.get('serviceTypeCodes') and not {'30', '98'} & set(row['serviceTypeCodes']):
            continue
        dates = (row.get('dates') or {}).get('eligibility') or {}
        if dates.get('start') and str(dates['start'])[:10] > day or dates.get('end') and str(dates['end'])[:10] < day:
            continue
        for entity in row.get('relatedEntities') or row.get('benefitsRelatedEntities') or []:
            if not isinstance(entity, dict) or str(entity.get('type') or entity.get('entityIdentifier') or '').upper().replace(' ', '_') != 'SECONDARY_PAYER':
                continue
            name = ((entity.get('name') or {}).get('organization') if isinstance(entity.get('name'), dict)
                    else entity.get('entityName'))
            if isinstance(name, str) and name.strip():
                reported.append((name.strip(), row['evidence_path']))
    if len({_normal(name) for name, _ in reported}) != 1 or not reported:
        return None
    payer = reported[0][0]
    if not _same_payer(payer, secondary.get('payer') or []):
        return None
    return {'status': 'verified', 'primary_payer_id': payload.get('payerId'), 'secondary_payer': payer,
            'evidence_paths': list(dict.fromkeys(path for _, path in reported))}


def verified_primary_order(result: dict) -> bool:
    """Only the selected first eCW policy with one named secondary qualifies."""
    order = ((result.get('benefits') or {}).get('primary_secondary_order') or {})
    recovery = result.get('recovery') or {}
    if (order.get('status') != 'verified' or not order.get('primary_payer_id')
            or not order.get('secondary_payer') or not order.get('evidence_paths')
            or recovery.get('reason_code') not in {None, 'multiple_active_policies'}):
        return False
    return (recovery.get('active_policy_count') == 2 if
            recovery.get('reason_code') == 'multiple_active_policies' else True)
