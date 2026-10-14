"""Normalize eCW coverage and saved eligibility evidence."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from app.insurance.ecw.ecw_fields import _norm, _reference
from app.insurance.ecw.ecw_coverage_fields import _relationship

def coverage_subscriber(coverage: dict, patient: dict, resolved: dict) -> dict:
    """Follow this beneficiary's Coverage link, never a household/name search."""
    patient_ref = 'Patient/' + str(patient.get('id'))
    if _reference(coverage.get('beneficiary')) != patient_ref:
        return {}
    ref = _reference(coverage.get('subscriber'))
    if ref == patient_ref or not ref and _relationship(coverage) == '18':
        return patient
    if ref and ref.startswith('#'):
        rows = [row for row in coverage.get('contained', []) if '#' + str(row.get('id', '')) == ref]
        person = rows[0] if len(rows) == 1 else {}
    else:
        person = resolved.get(ref or '', {})
        if ref != f"{person.get('resourceType')}/{person.get('id')}":
            return {}
    if person.get('resourceType') not in {'Patient', 'RelatedPerson'} or person.get('active') is False:
        return {}
    if person.get('resourceType') == 'RelatedPerson' and _reference(person.get('patient')) != patient_ref:
        return {}
    return person

def coverage_member_id(coverage: dict) -> str | None:
    if str(coverage.get('subscriberId') or '').strip():
        return str(coverage['subscriberId']).strip()
    values = {str(row['value']).strip() for row in coverage.get('identifier', []) if row.get('value') and
              (any(c.get('code') == 'MB' for c in row.get('type', {}).get('coding', [])) or
               str(row.get('system', '')).endswith('/memberidentifier'))}
    return next(iter(values)) if len(values) == 1 else None

def coverage_record_status(coverage: dict) -> dict:
    values = [row.get('valueString') for row in coverage.get('extension', [])
              if str(row.get('url', '')).endswith('/coverage-eligibilityStatus') and row.get('valueString')]
    return {'status': values[0] if len(values) == 1 else 'Not returned',
            'updated_at': coverage.get('meta', {}).get('lastUpdated'),
            'message': 'Saved eCW insurance status; the record does not specify the service date of this check.'}

def saved_eligibility_summary(rows: list[dict], patient_id: str, coverage_id: str, service_date: str) -> dict:
    """A Coverage record alone is not a saved payer verification."""
    matching = []
    now = datetime.now(timezone.utc)
    for row in rows:
        if (row.get('resourceType') != 'CoverageEligibilityResponse' or row.get('status') != 'active'
                or row.get('outcome') != 'complete' or row.get('error')
                or _reference(row.get('patient')) != f'Patient/{patient_id}'):
            continue
        serviced = row.get('servicedDate')
        if serviced != service_date:
            period = row.get('servicedPeriod')
            try:
                if not isinstance(period, dict) or not (date.fromisoformat(period['start'][:10])
                        <= date.fromisoformat(service_date) <= date.fromisoformat(period['end'][:10])):
                    continue
            except (KeyError, ValueError, TypeError):
                continue
        try:
            created = datetime.fromisoformat(str(row['created']).replace('Z', '+00:00'))
            if created.tzinfo is None or not timedelta(0) <= now - created <= timedelta(hours=24):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if not isinstance(row.get('insurance'), list):
            continue
        policies = [item for item in row['insurance'] if isinstance(item, dict)
                    and _reference(item.get('coverage')) == f'Coverage/{coverage_id}']
        if len(policies) == 1 and type(policies[0].get('inforce')) is bool:
            matching.append((created, row, policies[0]['inforce']))
    if not matching:
        return {'status': 'check_with_stedi', 'message': 'No current eCW verification matches this policy and service date. Use the Stedi result below.'}
    newest = max(item[0] for item in matching)
    latest = [item for item in matching if item[0] == newest]
    if len({json.dumps(item[1].get('insurance'), sort_keys=True) for item in latest}) != 1:
        return {'status': 'review', 'message': 'eCW contains conflicting eligibility results. Resolve them against the current Stedi check.'}
    created, row, active = latest[0]
    return {'status': 'verified_active' if active else 'verified_inactive', 'active': active,
            'checked_at': created.isoformat(), '_response': row, 'reference': 'CoverageEligibilityResponse/' + str(row.get('id', '')),
            'message': 'Saved eCW payer verification matches this policy and visit date. Benefit amounts and service approvals are checked separately.'}

def appointment_insurance_candidates(fields: list[dict]) -> list[dict]:
    """Extract unverified hints independently of Coverage, never patient comments."""
    candidates = {}
    for field in fields:
        if re.sub(r'[^a-z]', '', str(field.get('label') or '').lower()) != 'generalnotes':
            continue
        header = re.split(r'Patient entered comments on healow\s*:', str(field.get('text') or ''), flags=re.I)[0]
        for match in re.finditer(r'Patient payment preference\s*:\s*([^,;\r\n]{2,160})[,;\r\n]+\s*Subscriber No\.?\s*:\s*\(?\s*([A-Za-z0-9][A-Za-z0-9-]{1,79})\s*\)?(?=\s*(?:$|[\r\n]))', header, re.I):
            name, member = match.group(1).strip(), match.group(2)
            candidates[(_norm(name), _norm(member))] = {'payer': name, 'member_id': member, 'source': field.get('source')}
    return list(candidates.values())

def appointment_insurance_fallback(fields: list[dict], member_id: str | None, payer: str | None,
                                   active_coverages: int) -> dict:
    """A unique note can fill missing inquiry hints, not establish a policy or subscriber."""
    candidates = appointment_insurance_candidates(fields)
    if active_coverages > 1 or len(candidates) != 1:
        return {}
    hint = candidates[0]
    if (member_id and _norm(member_id) != _norm(hint['member_id'])) or (payer and _norm(payer) != _norm(hint['payer'])):
        return {}
    fallback = {key: hint[key] for key, value in [('member_id', member_id), ('payer', payer)] if not value}
    return {**fallback, 'source': hint['source']} if fallback else {}
