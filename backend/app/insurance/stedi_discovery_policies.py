"""Group insurance discovery responses by medical policy and visit evidence."""
from __future__ import annotations

import json

from app.insurance import commercial
from app.insurance.identity import normalized


def one_active_policy(candidates: list[dict], items: list[dict], service_types: list[str] | None = None) -> dict | None:
    """Pick one dated, verified policy, including duplicate discovery payer routes.

    Copies can be used automatically only when the requested benefit evidence
    agrees. A disagreement within one policy needs benefit review, not COB.
    """
    active = [row for row in candidates if row.get('active_medical')]
    verified = [row for row in active if row.get('verification_status') == 'VERIFIED']
    if not verified:
        return None
    if active_policy_count(candidates, items, service_types) != 1:
        return None
    # An incomplete copy cannot veto a fully verified copy of the same policy
    # when it supplies only a subset of the verified visit evidence. An
    # explicit patient, subscriber or address contradiction still blocks it.
    disallowed = {'PATIENT_NAME_MISMATCH', 'PATIENT_DOB_MISMATCH',
                  'PATIENT_ADDRESS_MISMATCH', 'RELATIONSHIP_CONFLICT',
                  'PAYER_REJECTED', 'COVERAGE_EVIDENCE_INVALID'}
    if any(disallowed.intersection(row.get('reason_codes') or []) for row in active):
        return None
    evidence = {row['index']: json.loads(_service_evidence(items[row['index']], service_types)) for row in active}
    if len(verified) == len(active) and len({_service_evidence(items[row['index']], service_types)
                                            for row in active}) != 1:
        return None
    chosen = next((row for row in sorted(verified, key=lambda row: row['index'])
                   if all(_contains_known_scope(evidence[row['index']]['dates'], evidence[other['index']]['dates'])
                          and _contains_known_scope(evidence[row['index']]['subscriber'], evidence[other['index']]['subscriber'])
                          and _contains_known_scope(evidence[row['index']]['addresses'], evidence[other['index']]['addresses'])
                          and set(evidence[other['index']]['benefits']).issubset(
                              evidence[row['index']]['benefits']) for other in active)), None)
    if chosen is None:
        return None
    return {**chosen, 'source_indices': [row['index'] for row in active],
            'source_candidate_ids': [row['id'] for row in active]}


def _policy_identity(item: dict) -> str | None:
    """Identify a medical policy independently of a discovery routing ID."""
    from app.insurance.stedi_discovery import parse_discovery_candidate
    parsed = parse_discovery_candidate(item)
    if not parsed or not parsed['member_id'] or not parsed['payer']:
        return None
    subscriber, dependent = parsed['subscriber'], parsed['dependent'] or {}
    if not all(subscriber.get(key) for key in ('firstName', 'lastName')):
        return None
    details = commercial.plan_details(item)
    identity = {
        'payer': normalized(parsed['payer']), 'member': normalized(parsed['member_id']),
        'group': normalized(parsed['group_number']),
        'plan_number': normalized((item.get('planInformation') or {}).get('planNumber')),
        'products': sorted(normalized(name) for name in details.get('names') or []),
        'relationship': parsed['relationship_code'],
        'dependent': [normalized(dependent.get(key)) for key in ('firstName', 'lastName', 'dateOfBirth')],
    }
    return json.dumps(identity, sort_keys=True)


def _service_evidence(item: dict, service_types: list[str] | None) -> str:
    requested = set(service_types or [])
    rows = [row for row in commercial._stedi_entries(item) if service_types is None
            or not row.get('serviceTypeCodes') or requested.intersection(row['serviceTypeCodes'])]
    dates = {'plan': item.get('planDateInformation'),
             'subscriber': (item.get('subscriber') or {}).get('dates'),
             'dependent': (item.get('dependent') or {}).get('dates')}
    subscriber = item.get('subscriber') or {}
    def address(person: dict) -> list[str]:
        value = person.get('address') or {}
        if not isinstance(value, dict):
            value = {}
        return [normalized(value.get(key)) for key in ('address1', 'city', 'state', 'postalCode')]
    return json.dumps({'dates': dates,
                       'subscriber': [normalized(subscriber.get(key)) for key in
                                      ('firstName', 'lastName', 'dateOfBirth')],
                       'addresses': {'subscriber': address(subscriber),
                                     'dependent': address(item.get('dependent') or {})},
                       'benefits': sorted(
        (json.dumps({key: value for key, value in row.items() if key != 'evidence_path'}, sort_keys=True)
         for row in rows))}, sort_keys=True)


def _contains_known_scope(complete: object, partial: object) -> bool:
    """Missing duplicate fields may be filled by a verified response; differences cannot."""
    if partial in (None, '', {}):
        return True
    if isinstance(partial, dict):
        return isinstance(complete, dict) and all(
            _contains_known_scope(complete.get(key), value) for key, value in partial.items())
    if isinstance(partial, list):
        return isinstance(complete, list) and len(complete) == len(partial) and all(
            _contains_known_scope(full, value) for full, value in zip(complete, partial))
    return complete == partial


def active_policy_evidence_conflict(candidates: list[dict], items: list[dict],
                                    service_types: list[str] | None = None) -> bool:
    active = [row for row in candidates if row.get('active_medical')]
    if len(active) < 2 or active_policy_count(candidates, items, service_types) != 1:
        return False
    evidence = [json.loads(_service_evidence(items[row['index']], service_types)) for row in active]
    if all(row.get('verification_status') == 'VERIFIED' for row in active):
        return len({_service_evidence(items[row['index']], service_types) for row in active}) > 1
    rows = [set(row['benefits']) for row in evidence]
    return not any(active[index].get('verification_status') == 'VERIFIED'
                   and all(other.issubset(candidate)
                           and _contains_known_scope(evidence[index]['dates'], evidence[other_index]['dates'])
                           and _contains_known_scope(evidence[index]['subscriber'], evidence[other_index]['subscriber'])
                           and _contains_known_scope(evidence[index]['addresses'], evidence[other_index]['addresses'])
                           for other_index, other in enumerate(rows))
                   for index, candidate in enumerate(rows))


def active_policy_count(candidates: list[dict], items: list[dict], service_types: list[str] | None = None) -> int:
    """Count medical policy identities, independently of service rows or disagreements."""
    signatures = set()
    for row in candidates:
        if not row.get('active_medical'):
            continue
        signatures.add(_policy_identity(items[row['index']]) or f"unbound:{row['index']}")
    return len(signatures)
