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


def patient_plan_applies(benefits: dict, comparison: dict | None, service_date: str) -> bool:
    """Bind an active payer product to this patient and visit date."""
    if (benefits.get('active') is not True or benefits.get('coverage_dates_invalid')
            or benefits.get('coverage_status_conflict') or not patient_benefits_bound(comparison)
            or not service_date):
        return False
    if benefits.get('service_date_matches') is True:
        return True
    if benefits.get('service_date_matches') is not None:
        return False
    coverage = benefits.get('coverage_evidence') or {}
    return (coverage.get('status') == 'ACTIVE' and bool(coverage.get('start'))
            and coverage['start'] <= service_date
            and (not coverage.get('end') or coverage['end'] >= service_date))


def payer_identity_verified(comparison: dict | None) -> bool:
    """Payer-specific rules require a current patient-bound identity decision."""
    return patient_benefits_bound(comparison) and (
        comparison.get('status') in {'matched', 'reconciled'}
        or comparison.get('status') == 'warning'
        and comparison.get('reconciliation_rule') in {'payer_matched_patient', 'cigna_member_suffix',
                                                      'payer_registration_variant', 'aetna_payer_demographics',
                                                      'uhc_middle_name_variant'}
    )


def unresolved_policy_difference(comparison: dict | None) -> bool:
    """Recover a different member ID; bound payer identities need no rediscovery."""
    comparison = comparison or {}
    return comparison.get('status') == 'review' and bool(
        comparison.get('policy_differences') or any(
            isinstance(row, dict) and row.get('field') == 'member_id'
            for row in comparison.get('differences') or []))


def normalized(value: object) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold()
                   if c.isalnum())


def card_name_matches(printed: str | None, first: str | None, last: str | None) -> bool:
    if not (printed and first and last):
        return False
    if normalized(printed) == normalized(f'{first} {last}'):
        return True
    parts = printed.split()
    return (len(parts) == 3 and bool(re.fullmatch(r'[A-Za-z]\.?', parts[1]))
            and normalized(f'{parts[0]} {parts[2]}') == normalized(f'{first} {last}'))


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
    # Other active policies affect payer order, not this selected policy's identity.
    # Never equate two different explicit suffixes or use demographics alone.
    base, extended = sorted((expected_member, returned_member), key=len)
    payer_plan = plan_details(payload)
    # Stedi may leave the Cigna 271 REF group qualifier as groupOrPolicyNumber.
    # Use it only as an exact corroborating identifier when no typed group was returned.
    returned_group = payer_plan.get('group_number') or (
        ((payload.get('subscriber') or {}).get('additionalInformation') or {}).get('groupOrPolicyNumber')
        if str(payload.get('payerId') or '') == '62308' else None)
    group_matches = bool(coverage.get('group_number') and returned_group
        and normalized(coverage['group_number']) == normalized(returned_group))
    # Cigna may append a two-digit person suffix when eCW has only the base ID
    # and no group. For suffixes other than 00, require eCW's patient/subscriber
    # link as an independent anchor. A conflicting eCW group still blocks reuse.
    missing_ecw_group_self_suffix = bool(not coverage.get('group_number') and returned_group
        and expected_member == base and returned_member == extended
        and (extended.endswith('00') or (extended[-2:].isdigit()
            and coverage.get('patient_id') and coverage.get('subscriber_patient_linked') is True)))
    cigna_suffix = (str(payload.get('payerId') or '') == '62308'
        and len(base) in {9, 12} and len(extended) == len(base) + 2 and extended == base + extended[-2:]
        and extended[-2:].isdigit() and coverage.get('coverage_id')
        and (group_matches or missing_ecw_group_self_suffix) and not policy_differences
        and expected['relationship'] == returned['relationship'] in {'self', 'spouse', 'child', 'other'}
        and all(expected[k] and returned[k] and key(k, expected[k]) == key(k, returned[k])
                for k in ('first_name', 'last_name', 'birth_date', 'subscriber_first', 'subscriber_last'))
        and not (coverage.get('subscriber_birth_date') and subscriber.get('dateOfBirth')
                 and birth_date(coverage['subscriber_birth_date']) != birth_date(subscriber['dateOfBirth']))
        and not (coverage.get('plan_number') and payer_plan.get('plan_number')
                 and normalized(coverage['plan_number']) != normalized(payer_plan['plan_number'])))
    member_matches = bool(expected_member and returned_member and expected_member == returned_member)
    payer_middle = normalized(patient.get('middleName'))
    expected_given = str(expected['first_name']).split()
    aetna_given_variant = bool(str(payload.get('payerId') or '').upper() in {'60054', 'HPQRS'}
        and len(expected_given) == 2 and payer_middle
        and normalized(expected_given[0]) == normalized(returned['first_name'])
        and normalized(expected_given[1]).startswith(payer_middle))
    first_matches = bool(expected['first_name'] and returned['first_name']
                         and (key('first_name', expected['first_name']) == key('first_name', returned['first_name'])
                              or aetna_given_variant))
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
    # The payer can identify the patient as a dependent even when eCW still
    # calls that same patient the subscriber. Keep the registration difference
    # visible, but bind the returned benefits to the matched patient.
    corrected_surname = bool(surname_warning and len(differences) == 1
        and one_name_edit(normalized(expected['last_name']), normalized(returned['last_name']))
        and group_matches)
    payer_patient_bound = bool(
        policy_differences and enough_identity and anchored
        and ((last_matches and not differences) or corrected_surname) and bool(dependent)
        and returned['relationship'] in {'spouse', 'child', 'other', 'life_partner'}
        and coverage.get('active_coverages') == 1 and coverage.get('coverage_id')
        and (not coverage.get('group_number') or not payer_plan.get('group_number') or group_matches)
        and (not coverage.get('plan_number') or not payer_plan.get('plan_number')
             or normalized(coverage['plan_number']) == normalized(payer_plan['plan_number']))
    )
    payer_registration_variant = bool(
        str(payload.get('payerId') or '') in {'87726', 'KMQTZ'}
        and not dependent and expected['relationship'] == returned['relationship'] == 'self'
        and coverage.get('subscriber_patient_linked') is True
        and coverage.get('active_coverages') == 1 and not coverage.get('other_active_coverage')
        and coverage.get('patient_id') and coverage.get('coverage_id')
        and member_matches and dob_matches
        and expected['subscriber_first'] and expected['subscriber_last']
        and normalized(expected['subscriber_first']) == normalized(expected['first_name'])
        and normalized(expected['subscriber_last']) == normalized(expected['last_name'])
        and (not coverage.get('subscriber_birth_date')
             or birth_date(coverage['subscriber_birth_date']) == returned['birth_date'])
        and (not coverage.get('payer_id') or str(coverage['payer_id']) in {'87726', 'KMQTZ'})
        and len(payer_plan['names']) == 1 and normalized(payer_plan['names'][0]) == 'choiceplus'
        and payer_plan.get('group_number')
        and (not coverage.get('group_number') or group_matches)
        and (not coverage.get('plan_number') or not payer_plan.get('plan_number')
             or normalized(coverage['plan_number']) == normalized(payer_plan['plan_number']))
        and one_name_edit(normalized(expected['first_name']), normalized(returned['first_name']))
        and (parts := re.split(r'[-\s]+', str(returned['last_name']).strip()))
        and len(parts) >= 2 and normalized(parts[0]) == normalized(expected['last_name'])
        and all(len(normalized(part)) >= 2 for part in parts[1:]))
    aetna_payer_demographics = bool(
        aetna_given_variant and member_matches and first_matches and last_matches
        and expected['relationship'] == returned['relationship'] == 'self'
        and coverage.get('patient_id') and coverage.get('coverage_id')
        and coverage.get('active_coverages') == 1 and not coverage.get('other_active_coverage')
        and coverage.get('subscriber_patient_linked') is True
        and normalized(expected['subscriber_first']) == normalized(expected['first_name'])
        and normalized(expected['subscriber_last']) == normalized(expected['last_name'])
        and expected['birth_date'] and returned['birth_date']
        and birth_date(coverage.get('subscriber_birth_date')) == birth_date(expected['birth_date'])
        and all(row['field'] in {'first_name', 'birth_date'} for row in differences)
        and all(row['field'] == 'subscriber_first' for row in policy_differences)
        and (not coverage.get('group_number') or group_matches)
        and (not coverage.get('plan_number') or not payer_plan.get('plan_number')
             or normalized(coverage['plan_number']) == normalized(payer_plan['plan_number'])))
    uhc_middle_name_variant = bool(
        str(payload.get('payerId') or '') in {'87726', 'KMQTZ'} and not dependent
        and expected['relationship'] == returned['relationship'] == 'self'
        and coverage.get('patient_id') and coverage.get('coverage_id')
        and coverage.get('active_coverages') == 1 and not coverage.get('other_active_coverage')
        and member_matches and dob_matches and last_matches
        and normalized(expected['subscriber_first']) == normalized(expected['first_name'])
        and normalized(expected['subscriber_last']) == normalized(expected['last_name'])
        and normalized(returned['subscriber_first']) == normalized(returned['first_name'])
        and normalized(returned['subscriber_last']) == normalized(returned['last_name'])
        and (not coverage.get('subscriber_birth_date') or
             birth_date(coverage['subscriber_birth_date']) == returned['birth_date'])
        and (not coverage.get('payer_id') or str(coverage['payer_id']) in {'87726', 'KMQTZ'})
        and len(expected_given) == 2 and len(expected_given[1]) >= 2
        and normalized(expected_given[0]) == normalized(returned['first_name'])
        and len(payer_middle) == 1 and normalized(expected_given[1]).startswith(payer_middle)
        and normalized(subscriber.get('middleName')) == payer_middle
        and [row['field'] for row in differences] == ['first_name']
        and [row['field'] for row in policy_differences] == ['subscriber_first']
        and (not coverage.get('group_number') or not payer_plan.get('group_number') or group_matches)
        and (not coverage.get('plan_number') or not payer_plan.get('plan_number')
             or normalized(coverage['plan_number']) == normalized(payer_plan['plan_number'])))
    return {'status': 'warning' if payer_registration_variant or aetna_payer_demographics or uhc_middle_name_variant else 'review' if (policy_differences and not payer_patient_bound) or not enough_identity else 'reconciled' if differences and verified else 'warning' if payer_patient_bound or surname_warning or uhc_suffix or cigna_suffix else 'review' if differences else 'matched',
            'match_version': 2,
            'binding': binding, 'differences': differences, 'policy_differences': policy_differences,
            'missing_fields': [field for field in missing_fields if field != 'patient_demographics'] if payer_registration_variant or uhc_middle_name_variant else missing_fields,
            'message': 'The payer matched this self-subscribed Choice Plus member and birth date. Use the payer spelling for this estimate; eCW registration differs.' if payer_registration_variant else 'Aetna matched one self-subscribed member and the patient name. Use Aetna’s returned name and birth date for this estimate; eCW registration differs.' if aetna_payer_demographics else 'UnitedHealthcare returned the second given name as a middle initial. The member ID, birth date and surname match; use the payer name for this estimate.' if uhc_middle_name_variant else 'The payer matched this patient, member ID, date of birth and group. Use the payer-reported spelling and policyholder for this estimate; eCW registration differs.' if payer_patient_bound and corrected_surname else 'The payer matched this patient and member ID. Use the payer-reported policyholder and relationship for this estimate; eCW registration differs.' if payer_patient_bound else 'The subscriber relationship could not be verified automatically from eCW and payer evidence.' if policy_differences else 'The eCW and payer response lack enough matching member and patient identifiers to bind these benefits to this patient.' if not enough_identity else 'Cigna returned the matching base member ID with a two-digit suffix; patient and subscriber details match, and any eCW group agrees.' if cigna_suffix else 'UnitedHealthcare returned the matching nine-digit member ID without the eCW 00 suffix; name and date of birth match.' if uhc_suffix else 'Payer surname differs; first name, date of birth and member ID match.' if surname_warning and not verified else '',
            'reconciliation_rule': 'payer_registration_variant' if payer_registration_variant else 'aetna_payer_demographics' if aetna_payer_demographics else 'uhc_middle_name_variant' if uhc_middle_name_variant else 'payer_matched_patient' if payer_patient_bound else 'cigna_member_suffix' if cigna_suffix else None,
            'can_confirm': bool(differences and anchored and not policy_differences and not aetna_payer_demographics and not uhc_middle_name_variant),
            'evidence': evidence if verified else None}
