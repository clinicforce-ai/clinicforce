"""Compare patient and subscriber separately; distinguish corroborated names from conflicts."""
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime

from app.insurance.commercial import payer_person, plan_details


def patient_benefits_bound(comparison: dict | None) -> bool:
    """A missing comparison cannot bind member financial rows to this patient."""
    if not isinstance(comparison, dict) or comparison.get('match_version') != 2:
        return False
    status = comparison.get('status')
    return (status in {'matched', 'warning', 'reconciled'} and bool(comparison.get('binding'))
            or status == 'matched' and comparison.get('binding') is None
            and comparison.get('reconciliation_rule') == 'ecw_patient_coverage_reference')


def payer_identity_verified(comparison: dict | None) -> bool:
    """Payer-specific rules require a current exact or reconciled patient decision."""
    return patient_benefits_bound(comparison) and comparison.get('status') in {'matched', 'reconciled'}


def normalized(value: object) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold()
                   if c.isalnum())


def generic_plan_label(value: object) -> bool:
    """Labels that identify a payer or product family, not an exact product."""
    label = normalized(value)
    return (label in {'', 'ci', 'commercial', 'ppo', 'hmo', 'uhc', 'unitedhealthcare',
                      'medicare', 'medicareadvantage', 'uhcmedicareadvantage',
                      'unitedhealthcaremedicareadvantage'} or bool(re.fullmatch(
                          r'(?:ci)?(?:preferredprovider(?:option|organization)(?:plus)?|healthmaintenanceorganization)(?:medical)?', label)))


def birth_date(value: object) -> str:
    try:
        text = str(value)
        return (datetime.strptime(text, '%Y%m%d').date() if len(text) == 8 and text.isdigit() else date.fromisoformat(text)).isoformat()
    except ValueError:
        return str(value or '')


def one_name_edit(left: str, right: str) -> bool:
    """One insertion, deletion, substitution or adjacent swap; requires independent identity anchors."""
    if min(len(left), len(right)) < 4 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        different = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(different) == 1 or (len(different) == 2 and different[1] == different[0] + 1
            and left[different[0]] == right[different[1]] and left[different[1]] == right[different[0]])
    short, long = sorted((left, right), key=len)
    index = next((i for i, (a, b) in enumerate(zip(short, long)) if a != b), len(short))
    return short[index:] == long[index + 1:]


def name_difference_only(comparison: dict) -> bool:
    differences = comparison.get('differences') or []
    return bool(differences) and not comparison.get('policy_differences') and all(row['field'] in {'first_name', 'last_name'} for row in differences)


def compare(payload: dict, coverage: dict, evidence: dict | None = None) -> dict:
    ecw = payload.get('_ecw_response') or {}
    if ecw and coverage.get('patient_id') and coverage.get('coverage_id'):
        policies = [row for row in ecw.get('insurance', []) if isinstance(row, dict)
                    and (row.get('coverage') or {}).get('reference') == 'Coverage/' + str(coverage['coverage_id'])]
        if (ecw.get('resourceType') == 'CoverageEligibilityResponse' and ecw.get('status') == 'active'
                and ecw.get('outcome') == 'complete' and 'benefits' in (ecw.get('purpose') or [])
                and ecw.get('patient', {}).get('reference') == 'Patient/' + str(coverage['patient_id'])
                and len(policies) == 1 and policies[0].get('inforce') is True):
            return {'status': 'matched', 'match_version': 2, 'binding': None, 'differences': [], 'policy_differences': [],
                    'missing_fields': [], 'message': '', 'reconciliation_rule': 'ecw_patient_coverage_reference',
                    'can_confirm': False, 'evidence': None}
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
                'relationship': str(dependent.get('relationToSubscriberCode') or dependent.get('relationToSubscriber') or ('self' if not dependent else '')).lower()}
    returned['relationship'] = relationships.get(returned['relationship'].upper(), returned['relationship'])
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
    member_matches = bool(expected_member and returned_member and expected_member == returned_member)
    first_matches = bool(expected['first_name'] and returned['first_name']
                         and key('first_name', expected['first_name']) == key('first_name', returned['first_name']))
    last_matches = bool(expected['last_name'] and returned['last_name']
                        and key('last_name', expected['last_name']) == key('last_name', returned['last_name']))
    dob_matches = bool(expected['birth_date'] and returned['birth_date']
                       and key('birth_date', expected['birth_date']) == key('birth_date', returned['birth_date']))
    missing_fields = []
    if not expected_member or not returned_member:
        missing_fields.append('member_id')
    if not (last_matches and (first_matches or dob_matches)):
        missing_fields.append('patient_demographics')
    if expected['relationship'] and expected['relationship'] != 'self' and not returned['relationship']:
        missing_fields.append('relationship')
    # Name-only reconciliation requires all three independent anchors to be present.
    anchored = all(expected[k] and returned[k] and key(k, expected[k]) == key(k, returned[k])
                   for k in ('first_name', 'birth_date', 'member_id'))
    binding = hashlib.sha256(json.dumps({'expected': {k: key(k, v) for k, v in expected.items()},
        'returned': {k: key(k, v) for k, v in returned.items()},
        'coverage_id': coverage.get('coverage_id'), 'payer_id': coverage.get('payer_id')}, sort_keys=True).encode()).hexdigest()
    verified = bool(anchored and not policy_differences and evidence and evidence.get('binding') == binding
                    and evidence.get('reference') and evidence.get('actor_id') and evidence.get('verified_at'))
    surname_warning = anchored and bool(differences) and all(row['field'] == 'last_name' for row in differences)
    if surname_warning:
        missing_fields = [field for field in missing_fields if field != 'patient_demographics']
    enough_identity = ((member_matches or uhc_suffix or cigna_suffix)
                       and 'patient_demographics' not in missing_fields
                       and 'relationship' not in missing_fields)
    return {'status': 'review' if policy_differences or not enough_identity else 'reconciled' if differences and verified else 'warning' if surname_warning or uhc_suffix or cigna_suffix else 'review' if differences else 'matched',
            'match_version': 2,
            'binding': binding, 'differences': differences, 'policy_differences': policy_differences,
            'missing_fields': missing_fields,
            'message': 'The subscriber relationship could not be verified automatically from eCW and payer evidence.' if policy_differences else 'The eCW and payer response lack enough matching member and patient identifiers to bind these benefits to this patient.' if not enough_identity else 'Cigna returned the same base member ID with a different suffix representation; patient, subscriber, relationship and group match.' if cigna_suffix else 'UnitedHealthcare returned the matching nine-digit member ID without the eCW 00 suffix; name and date of birth match.' if uhc_suffix else 'Payer surname differs; first name, date of birth and member ID match.' if surname_warning and not verified else '',
            'reconciliation_rule': 'cigna_member_suffix' if cigna_suffix else None,
            'can_confirm': bool(differences and anchored and not policy_differences),
            'evidence': evidence if verified else None}
