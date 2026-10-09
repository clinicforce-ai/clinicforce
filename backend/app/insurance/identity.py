"""Compare patient and subscriber separately; distinguish corroborated names from conflicts."""
import hashlib
import json
import unicodedata
from datetime import date, datetime

from app.insurance.commercial import payer_person, plan_details


def normalized(value: object) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold()
                   if c.isalnum())


def birth_date(value: object) -> str:
    try:
        text = str(value)
        return (datetime.strptime(text, '%Y%m%d').date() if len(text) == 8 and text.isdigit() else date.fromisoformat(text)).isoformat()
    except ValueError:
        return str(value or '')


def name_difference_only(comparison: dict) -> bool:
    differences = comparison.get('differences') or []
    return bool(differences) and all(row['field'] in {'first_name', 'last_name'} for row in differences)


def compare(payload: dict, coverage: dict, evidence: dict | None = None) -> dict:
    subscriber = payer_person(payload.get('subscriber') or {})
    dependent = payload.get('dependent') or {}
    patient = payer_person(dependent or payload.get('subscriber') or {})
    relationships = {'18': 'self', '01': 'spouse', '19': 'child', 'G8': 'other'}
    expected = {field: coverage.get(key) or '' for field, key in (
        ('first_name', 'patient_first'), ('last_name', 'patient_last'),
        ('birth_date', 'patient_birth_date'), ('member_id', 'member_id'),
        ('subscriber_first', 'subscriber_first'), ('subscriber_last', 'subscriber_last'))}
    expected['relationship'] = relationships.get(coverage.get('relationship'), coverage.get('relationship') or '')
    returned = {'first_name': patient.get('firstName') or '', 'last_name': patient.get('lastName') or '',
                'birth_date': patient.get('dateOfBirth') or '', 'member_id': subscriber.get('memberId') or '',
                'subscriber_first': subscriber.get('firstName') or '', 'subscriber_last': subscriber.get('lastName') or '',
                'relationship': str(dependent.get('relationToSubscriber') or ('self' if not dependent else '')).lower()}
    def key(field, value):
        return birth_date(value) if field == 'birth_date' else normalized(value)
    differences = [{'field': field, 'ecw': expected[field], 'payer': returned[field]}
                   for field in expected if expected[field] and returned[field]
                   and key(field, expected[field]) != key(field, returned[field])]
    policy_differences = [row for row in differences if row['field'].startswith('subscriber_') or row['field'] == 'relationship']
    differences = [row for row in differences if row not in policy_differences]
    # UHC may return its 9-digit member identifier without eCW's subscriber
    # suffix. Accept only the exact 00 suffix, self coverage, and all demographics.
    expected_member, returned_member = normalized(expected['member_id']), normalized(returned['member_id'])
    uhc_suffix = (str(payload.get('payerId') or '') in {'87726', 'KMQTZ'}
        and expected['relationship'] == returned['relationship'] == 'self'
        and len(expected_member) == 11 and len(returned_member) == 9
        and expected_member.isdigit() and returned_member.isdigit()
        and expected_member == returned_member + '00'
        and all(expected[k] and returned[k] and key(k, expected[k]) == key(k, returned[k])
                for k in ('first_name', 'last_name', 'birth_date'))
        and not policy_differences)
    # Cigna accepts a base ID with or without its two-digit person suffix.
    # Never equate two different explicit suffixes or use demographics alone.
    base, extended = sorted((expected_member, returned_member), key=len)
    payer_plan = plan_details(payload)
    group_matches = bool(coverage.get('group_number') and payer_plan.get('group_number')
        and normalized(coverage['group_number']) == normalized(payer_plan['group_number']))
    cigna_suffix = (str(payload.get('payerId') or '') == '62308'
        and len(base) == 9 and len(extended) == 11 and extended == base + extended[-2:]
        and extended[-2:].isdigit() and coverage.get('coverage_id') and coverage.get('active_coverages') == 1
        and group_matches and not policy_differences
        and expected['relationship'] == returned['relationship'] in {'self', 'spouse', 'child', 'other'}
        and all(expected[k] and returned[k] and key(k, expected[k]) == key(k, returned[k])
                for k in ('first_name', 'last_name', 'birth_date', 'subscriber_first', 'subscriber_last'))
        and not (coverage.get('subscriber_birth_date') and subscriber.get('dateOfBirth')
                 and birth_date(coverage['subscriber_birth_date']) != birth_date(subscriber['dateOfBirth']))
        and not (coverage.get('plan_number') and payer_plan.get('plan_number')
                 and normalized(coverage['plan_number']) != normalized(payer_plan['plan_number'])))
    # Name-only reconciliation requires all three independent anchors to be present.
    anchored = all(expected[k] and returned[k] and key(k, expected[k]) == key(k, returned[k])
                   for k in ('first_name', 'birth_date', 'member_id'))
    binding = hashlib.sha256(json.dumps({'expected': {k: key(k, v) for k, v in expected.items()},
        'returned': {k: key(k, v) for k, v in returned.items()},
        'coverage_id': coverage.get('coverage_id'), 'payer_id': coverage.get('payer_id')}, sort_keys=True).encode()).hexdigest()
    verified = bool(anchored and evidence and evidence.get('binding') == binding
                    and evidence.get('reference') and evidence.get('actor_id') and evidence.get('verified_at'))
    surname_warning = anchored and bool(differences) and all(row['field'] == 'last_name' for row in differences)
    return {'status': 'reconciled' if differences and verified else 'warning' if surname_warning or uhc_suffix or cigna_suffix else 'review' if differences else 'matched',
            'binding': binding, 'differences': differences, 'policy_differences': policy_differences,
            'message': 'Cigna returned the same base member ID with a different suffix representation; patient, subscriber, relationship and group match.' if cigna_suffix else 'UnitedHealthcare returned the matching nine-digit member ID without the eCW 00 suffix; name and date of birth match.' if uhc_suffix else 'Payer surname differs; first name, date of birth and member ID match.' if surname_warning and not verified else '',
            'reconciliation_rule': 'cigna_member_suffix' if cigna_suffix else None,
            'can_confirm': bool(differences and anchored),
            'evidence': evidence if verified else None}
