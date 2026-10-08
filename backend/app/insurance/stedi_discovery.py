"""Bounded demographic recovery after a payer rejects a member ID."""
from __future__ import annotations

import re
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from app.insurance import commercial
from app.insurance.identity import birth_date, normalized

URL = 'https://healthcare.us.stedi.com/2024-04-01/insurance-discovery/check/v1'
MBI_URL = 'https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3'


def mbi_request_body(inputs: dict, source: dict, provider: dict) -> dict | None:
    """Only recorded Original Medicare Part B, never age or a guessed plan.

    The caller must verify billing-NPI enrollment for CMS and MBILUNOSSN.
    This lookup does not recover a Medicare Advantage plan's member number.
    """
    patient = inputs.get('patient') or {}
    state = str((patient.get('address') or {}).get('state') or '').upper()
    if (source.get('plan') != 'MB' or source.get('active_coverages', 0) > 1
            or inputs.get('relationship') not in {None, '', 'unknown', '18', 'self'}
            or not re.fullmatch(r'[A-Z]{2}', state)
            or not re.fullmatch(r'\d{10}', str(provider.get('billing_npi') or ''))
            or not provider.get('organization_name')
            or not all(patient.get(key) for key in ('patient_first_name', 'patient_last_name', 'patient_birth_date'))):
        return None
    return {'tradingPartnerServiceId': 'MBILUNOSSN',
            'provider': {'npi': provider['billing_npi'], 'organizationName': provider['organization_name']},
            'subscriber': {'firstName': patient['patient_first_name'], 'lastName': patient['patient_last_name'],
                           'dateOfBirth': birth_date(patient['patient_birth_date']).replace('-', ''),
                           'address': {'state': state}},
            'encounter': {'dateOfService': inputs['service_date'].replace('-', ''), 'serviceTypeCodes': ['30']}}


def discovery_address(address: dict) -> dict:
    """Keep a known state even when an address has no usable ZIP code."""
    result = {key: str(address.get(key) or '').strip() for key in ('city', 'state', 'postalCode')}
    result['state'] = result['state'].upper()
    result['postalCode'] = re.sub(r'\D', '', result['postalCode'])
    if not re.fullmatch('[A-Z]{2}', result['state']):
        result.pop('state')
    if not re.fullmatch(r'\d{5}(?:\d{4})?', result['postalCode']):
        result.pop('postalCode')
    lines = address.get('line') or []
    if lines:
        result['address1'] = str(lines[0])
        if len(lines) > 1:
            result['address2'] = ' '.join(lines[1:])
    return {key: value for key, value in result.items() if value}


def previous_addresses(patient: dict) -> list[dict]:
    today = date.today().isoformat()
    rows = [discovery_address(row) for row in patient.get('address', [])
            if row.get('use') == 'old' or (row.get('period', {}).get('end') or today)[:10] < today]
    unique = {json.dumps(row, sort_keys=True): row for row in rows if row.get('state')}
    return list(unique.values())[:2]


def patient_address(patient: dict) -> dict:
    today = date.today().isoformat()
    addresses = [a for a in patient.get('address', []) if a.get('use') != 'old'
                 and (a.get('period', {}).get('start') or today)[:10] <= today
                 and (a.get('period', {}).get('end') or today)[:10] >= today]
    home = [a for a in addresses if a.get('use') == 'home']
    addresses = home or addresses
    if len(addresses) != 1:
        return {}
    return discovery_address(addresses[0])


def request_body(inputs: dict, provider: dict) -> dict | None:
    patient = inputs.get('patient') or {}
    # Discovery searches for the patient. Stedi may return the patient as a
    # subscriber or dependent, so an eCW relationship cannot gate the search.
    fields = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
              'dateOfBirth': str(patient.get('patient_birth_date') or '').replace('-', '')}
    npi = provider.get('rendering_npi') or provider.get('billing_npi')
    if not all(fields.values()) or not re.fullmatch(r'\d{10}', str(npi or '')):
        return None
    if patient.get('address'):
        fields['address'] = patient['address']
    if patient.get('gender') in {'M', 'F'}:
        fields['gender'] = patient['gender']
    body = {'provider': {'npi': npi}, 'subscriber': fields,
            'encounter': {'dateOfService': inputs['service_date'].replace('-', ''), 'serviceTypeCodes': ['30']}}
    subscriber = inputs.get('subscriber') or {}
    if inputs.get('relationship') not in {None, '', 'unknown', '18', 'self'} and all(
            subscriber.get(key) for key in ('firstName', 'lastName', 'birthDate')):
        body['dependent'] = fields
        body['subscriber'] = {'firstName': subscriber['firstName'], 'lastName': subscriber['lastName'],
                              'dateOfBirth': birth_date(subscriber['birthDate']).replace('-', '')}
        if subscriber.get('address'):
            body['subscriber']['address'] = subscriber['address']
    return body


def parse_discovery_candidate(item: object) -> dict | None:
    """Retain partial evidence. Parsing makes no claim about the patient's identity.

    The original item remains in protected storage; these fields are returned only
    through the patient/practice-scoped case API, never through audit logging.
    """
    if not isinstance(item, dict) or not any(isinstance(item.get(key), dict) and item[key]
                                           for key in ('subscriber', 'dependent', 'payer')):
        return None
    if any(item.get(key) is not None and not isinstance(item[key], dict)
           for key in ('subscriber', 'dependent', 'payer', 'planInformation', 'planDateInformation')):
        return None
    if any(item.get(key) is not None and not isinstance(item[key], list)
           for key in ('errors', 'dependents', 'plans', 'benefitsInformation')):
        return None
    try:
        subscriber = {**(item.get('subscriber') or {}), **commercial.payer_person(item.get('subscriber') or {})}
        dependent = {**item['dependent'], **commercial.payer_person(item['dependent'])} if item.get('dependent') else {}
        if any(person.get(key) is not None and not isinstance(person[key], dict)
               for person in (subscriber, dependent) for key in ('address', 'dates')):
            return None
        if any(person.get('aaaErrors') is not None and not isinstance(person['aaaErrors'], list)
               for person in (subscriber, dependent)):
            return None
        details = commercial.plan_details(item)
    except (AttributeError, TypeError, ValueError):
        return None
    person_keys = ('firstName', 'middleName', 'lastName', 'dateOfBirth', 'memberId', 'groupNumber')
    if any(person.get(key) is not None and not isinstance(person[key], str)
           for person in (subscriber, dependent) for key in person_keys):
        return None
    reasons = []
    for key, reason in [('memberId', 'MEMBER_ID_MISSING'), ('firstName', 'SUBSCRIBER_NAME_MISSING'),
                        ('lastName', 'SUBSCRIBER_NAME_MISSING'), ('dateOfBirth', 'SUBSCRIBER_DOB_MISSING')]:
        if not subscriber.get(key):
            reasons.append(reason)
    return {'subscriber': {key: subscriber.get(key) or None for key in person_keys},
            'dependent': {key: dependent.get(key) or None for key in person_keys} if dependent else None,
            'member_id': subscriber.get('memberId') or None,
            'group_number': subscriber.get('groupNumber') or (item.get('planInformation') or {}).get('groupNumber'),
            'relationship': 'dependent' if dependent else 'self',
            'relationship_code': dependent.get('relationToSubscriberCode') if dependent else '18',
            'payer': (item.get('payer') or {}).get('name') or (item.get('payer') or {}).get('lastName'),
            'payer_identifier': str(item.get('payerId') or (item.get('payer') or {}).get('payorIdentification') or ''),
            'plans': details.get('names') or [], 'coverage_dates': item.get('planDateInformation') or
                ((dependent or subscriber).get('dates') or {}).get('plan') or {},
            'source': item.get('evidence_source') or 'stedi_discovery',
            'state': 'CANDIDATE_INCOMPLETE' if reasons else 'CANDIDATE_FOUND',
            'reason_codes': list(dict.fromkeys(reasons))}


def candidate_identity(item: dict, inputs: dict) -> dict | None:
    """Return the returned policy identity only when it belongs to this patient.

    Automatic adoption also checks coverage and independent policy/address evidence.
    Exact patient demographics prevent another household member's policy from
    being adopted; the separate candidate list still retains it for review.
    """
    if not parse_discovery_candidate(item) or commercial.eligibility_errors(item):
        return None
    patient = inputs.get('patient') or {}
    subscriber = {**(item.get('subscriber') or {}), **commercial.payer_person(item.get('subscriber') or {})}
    dependent = {**item['dependent'], **commercial.payer_person(item['dependent'])} if item.get('dependent') else {}
    insured = dependent or subscriber
    expected = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
                'dateOfBirth': patient.get('patient_birth_date')}
    if not all(normalized(value) and normalized(insured.get(key)) == normalized(value) for key, value in expected.items()):
        return None
    if not all(subscriber.get(key) for key in ('memberId', 'firstName', 'lastName')):
        return None
    returned = insured.get('address') or {}
    returned = {**returned, 'address1': returned.get('address1') or returned.get('addressLine1')}
    addresses = [patient.get('address') or {}, *patient.get('previous_addresses', [])]
    address_matches = any(all(address.get(key) and returned.get(key) and
        normalized(address[key]) == normalized(returned[key]) for key in ('address1', 'city', 'state'))
        and bool(address.get('postalCode') and returned.get('postalCode'))
        and normalized(address['postalCode'])[:5] == normalized(returned['postalCode'])[:5] for address in addresses)
    address_conflict = bool(not address_matches and all(returned.get(key) for key in ('address1', 'city', 'state', 'postalCode'))
                            and any(all(address.get(key) for key in ('address1', 'city', 'state', 'postalCode')) for address in addresses))
    return {'member_id': subscriber['memberId'], 'subscriber_first': subscriber['firstName'],
            'subscriber_last': subscriber['lastName'], 'subscriber_birth_date': subscriber.get('dateOfBirth'),
            'relationship': 'dependent' if dependent else 'self', 'address_matches': address_matches,
            'address_conflict': address_conflict,
            'relationship_code': str(dependent.get('relationToSubscriberCode') or 'dependent') if dependent else '18',
            'group_number': subscriber.get('groupNumber') or (item.get('planInformation') or {}).get('groupNumber'),
            'plan_number': subscriber.get('planNumber') or (item.get('planInformation') or {}).get('planNumber')}


def active_medical(item: dict, inputs: dict, service_type: str = '30') -> bool:
    if not any(set(row.get('serviceTypeCodes', [])) & {'30', service_type} for row in commercial._stedi_entries(item)):
        return False
    benefits = commercial.normalize_stedi_271(item, service_type, False, service_date=inputs.get('service_date'))
    return benefits['active'] is True and benefits['service_date_matches'] is not False


def automatic_match(item: dict, inputs: dict, source: dict) -> bool:
    """A missing eCW policy cannot be required to recover that missing policy."""
    return verify_candidate(item, inputs, source)['status'] == 'VERIFIED'


def verify_candidate(item: dict, inputs: dict, source: dict | None = None) -> dict:
    """Separate patient identity, policy corroboration and dated payer evidence."""
    source = source or {}
    candidate = parse_discovery_candidate(item)
    if candidate is None:
        return {'status': 'REVIEW_REQUIRED', 'reason_codes': ['MALFORMED_RESPONSE'], 'pathway': None}
    reasons = []
    patient = inputs.get('patient') or {}
    insured = candidate['dependent'] or candidate['subscriber']
    for key, expected_key, code in [('firstName', 'patient_first_name', 'PATIENT_NAME'),
                                    ('lastName', 'patient_last_name', 'PATIENT_NAME'),
                                    ('dateOfBirth', 'patient_birth_date', 'PATIENT_DOB')]:
        expected, returned = patient.get(expected_key), insured.get(key)
        if not expected or not returned:
            reasons.append(code + '_MISSING')
        elif normalized(expected) != normalized(returned):
            reasons.append(code + '_MISMATCH')
    if commercial.eligibility_errors(item):
        reasons.append('PAYER_REJECTED')
    identity = candidate_identity(item, inputs)
    if identity and identity.get('address_conflict'):
        reasons.append('PATIENT_ADDRESS_MISMATCH')
    if not candidate['member_id']:
        reasons.append('MEMBER_ID_MISSING')
    if not all(candidate['subscriber'].get(key) for key in ('firstName', 'lastName')):
        reasons.append('SUBSCRIBER_NAME_MISSING')
    if not candidate['payer_identifier']:
        reasons.append('PAYER_ROUTE_MISSING')
    relationship = inputs.get('relationship')
    if relationship not in {None, '', 'unknown'} and (
            (relationship in {'18', 'self'}) != (candidate['relationship'] == 'self')):
        reasons.append('RELATIONSHIP_CONFLICT')
    if candidate['dependent'] and candidate['relationship_code'] == '18':
        reasons.append('RELATIONSHIP_CONFLICT')
    if relationship in {'01', '19', 'G8'} and candidate['relationship_code'] and relationship != candidate['relationship_code']:
        reasons.append('RELATIONSHIP_CONFLICT')
    if source.get('active_coverages', 0) > 1:
        reasons.append('MULTIPLE_COVERAGES')
    try:
        active = active_medical(item, inputs)
        benefits = commercial.normalize_stedi_271(item, '30', False, service_date=inputs.get('service_date'))
        if not active:
            reasons.append('ACTIVE_MEDICAL_COVERAGE_NOT_VERIFIED')
            state = (benefits.get('coverage_evidence') or {}).get('status')
            if state in {'INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'}:
                reasons.append('COVERAGE_' + state)
        person_dates = ((item.get('dependent') or item.get('subscriber') or {}).get('dates') or {})
        bounds = [item.get('planDateInformation') or {}, person_dates]
        for plan in item.get('plans') or []:
            bounds.extend([plan.get('dates') or {}, plan.get('planDateInformation') or {}])
        for row in commercial._stedi_entries(item):
            bounds.extend([row.get('benefitsDateInformation') or {}, row.get('dates') or {}])
        # An arbitrary or empty date object is not corroboration of this visit.
        dated = any(any(value and (not isinstance(value, dict) or value.get('start') or value.get('end'))
                        for key, value in bound.items() if key in {
                            'planBegin', 'planEnd', 'eligibilityBegin', 'eligibilityEnd', 'benefitBegin',
                            'benefitEnd', 'plan', 'eligibility', 'benefit', 'policyEffective', 'policyExpiration'})
                    and commercial._dates_apply(bound, inputs['service_date']) for bound in bounds)
        dated |= benefits.get('service_date_matches') is True
        if not dated or benefits.get('coverage_dates_invalid') or benefits.get('service_date_matches') is False:
            reasons.append('COVERAGE_DATE_NOT_VERIFIED')
        ecw_match = matches_ecw_policy(item, inputs, source)
    except (AttributeError, KeyError, TypeError, ValueError):
        reasons.append('COVERAGE_EVIDENCE_INVALID')
        ecw_match = False
    pathway = 'ecw_policy' if ecw_match else 'independent_discovery' if identity and identity['address_matches'] else None
    if not pathway:
        reasons.append('ADDITIONAL_IDENTITY_EVIDENCE_REQUIRED')
    return {'status': 'REVIEW_REQUIRED' if reasons else 'VERIFIED',
            'reason_codes': list(dict.fromkeys(reasons)), 'pathway': pathway}


def inquiry_eligible(item: dict, inputs: dict, source: dict) -> bool:
    """An exact demographic match permits an inquiry, not automatic adoption.

    Missing address/subscriber DOB are not mismatches. Do not repeat a search
    for an explicitly inactive/expired policy or select among conflicting people.
    """
    if not candidate_identity(item, inputs):
        return False
    verification = verify_candidate(item, inputs, source)
    allowed = {'ADDITIONAL_IDENTITY_EVIDENCE_REQUIRED', 'ACTIVE_MEDICAL_COVERAGE_NOT_VERIFIED',
               'COVERAGE_DATE_NOT_VERIFIED'}
    return not set(verification['reason_codes']) - allowed


def card_candidate(fields: dict, inputs: dict) -> dict | None:
    """OCR supplies request hints only; a payer response must validate them."""
    patient, subscriber = inputs.get('patient') or {}, inputs.get('subscriber') or {}
    if not all(fields.get(key) for key in ('member_id', 'payer', 'member_name')):
        return None
    dependent = inputs.get('relationship') not in {None, '', 'unknown', '18', 'self'}
    first = subscriber.get('firstName') if dependent else patient.get('patient_first_name')
    last = subscriber.get('lastName') if dependent else patient.get('patient_last_name')
    dob = subscriber.get('birthDate') if dependent else patient.get('patient_birth_date')
    if not first or not last or normalized(fields['member_name']) != normalized(f'{first} {last}'):
        return None
    if fields.get('birth_date') and dob and birth_date(fields['birth_date']) != birth_date(dob):
        return None
    item = {'subscriber': {'firstName': first, 'lastName': last, 'dateOfBirth': dob,
                          'memberId': fields['member_id'], 'groupNumber': fields.get('group_number'),
                          'address': patient.get('address') if not dependent else subscriber.get('address')},
            'payer': {'name': fields['payer']}, 'evidence_source': 'insurance_card'}
    if dependent:
        item['dependent'] = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
            'dateOfBirth': patient.get('patient_birth_date'), 'address': patient.get('address'),
            'relationToSubscriberCode': inputs['relationship']}
    return item


def candidates(payload: dict, inputs: dict, source: dict | None = None) -> list[dict]:
    """Keep parseable candidates even when verification fails; deduplicate copies."""
    rows = []
    seen = set()
    for index, item in enumerate(payload.get('items') or []):
        parsed = parse_discovery_candidate(item)
        if parsed is None:
            continue
        signature = json.dumps({key: value for key, value in item.items() if key != 'evidence_source'}, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        identity = candidate_identity(item, inputs)
        verification = verify_candidate(item, inputs, source)
        token = hashlib.sha256(signature.encode()).hexdigest()[:20]
        try:
            active = active_medical(item, inputs)
            coverage = commercial.normalize_stedi_271(item, '30', False, service_date=inputs.get('service_date'))['coverage_evidence']
        except (AttributeError, TypeError, ValueError):
            active = False
            coverage = {'status': 'UNKNOWN', 'reported_status': 'UNKNOWN', 'start': None, 'end': None}
        rows.append({**parsed, 'id': token, 'index': index,
                     'identity_status': 'MATCHED' if identity else 'REVIEW_REQUIRED',
                     'coverage_evidence': coverage,
                     'inquiry_eligible': inquiry_eligible(item, inputs, source or {}),
                     'address_matches': bool(identity and identity['address_matches']), 'active_medical': active,
                     'verification_status': verification['status'], 'verification': verification,
                     'reason_codes': list(dict.fromkeys(parsed['reason_codes'] + verification['reason_codes']))})
    return rows


def patient_matches(item: dict, inputs: dict) -> bool:
    """Patient identity and address match, for either subscriber or dependent."""
    identity = candidate_identity(item, inputs)
    return bool(identity and identity['address_matches'])


def matches_ecw_policy(item: dict, inputs: dict, source: dict) -> bool:
    """Permit automatic payer recovery only for the same active eCW policy."""
    identity = candidate_identity(item, inputs)
    if not (identity and source.get('active_coverages') == 1
            and source.get('coverage_id') and inputs.get('member_id')
            and normalized(identity['member_id']) == normalized(inputs['member_id'])):
        return False
    known = inputs.get('relationship')
    if known not in {None, '', 'unknown'} and ((known in {'18', 'self'}) != (identity['relationship'] == 'self')):
        return False
    if known in {'01', '19', 'G8'} and identity['relationship_code'] not in {known, 'dependent'}:
        return False
    subscriber = inputs.get('subscriber') or {}
    if any(subscriber.get(key) and normalized(subscriber[key]) != normalized(identity[identity_key])
           for key, identity_key in [('firstName', 'subscriber_first'), ('lastName', 'subscriber_last')]):
        return False
    payer = item.get('payer') or {}
    ecw_ids = {normalized(value) for value in source.get('payer_identifiers') or [] if normalized(value)}
    returned_ids = {normalized(value) for value in (item.get('payerId'), payer.get('payorIdentification')) if normalized(value)}
    # eCW's display label can be shorter than the discovered payer name.
    # An explicit payer ID must still agree; otherwise the unique active
    # patient/member/date match below can identify the eCW policy.
    if ecw_ids and not ecw_ids & returned_ids:
        return False
    benefits = commercial.normalize_stedi_271(item, '30', False, service_date=inputs.get('service_date'))
    return benefits['active'] is True and benefits['service_date_matches'] is not False


def public_status(discovery: dict) -> dict:
    state = discovery.get('request_state')
    if (discovery.get('exhausted') is False and state in {'complete', 'rejected'}
            and discovery.get('outcome') != 'recovered'):
        state = 'pending'  # The next automatic recovery stage has not finished.
    # ponytail: background work is bounded below ten minutes. Interrupted tasks
    # remain indeterminate; resume only GET by a known ID, never another POST.
    if state == 'pending' and discovery.get('started_at'):
        try:
            if datetime.fromisoformat(discovery['started_at']) + timedelta(minutes=10) < datetime.now(timezone.utc):
                state = 'indeterminate'
        except (ValueError, TypeError):
            state = 'indeterminate'
    outcome = discovery.get('outcome')
    # Older caches used no_match when ClinicForce filtered every returned item.
    # Only a completed, empty upstream search can establish NOT_FOUND.
    filtered_legacy_result = outcome == 'no_match' and bool(discovery.get('coverage_count') or discovery.get('candidates'))
    if filtered_legacy_result:
        outcome = 'review_needed'
    message = discovery.get('selection_error') or {
        'recovered': 'Coverage recovered from Stedi and rechecked with the scheduled provider and visit date.',
        'no_match': 'The additional Stedi search did not find a matching policy. Saved eCW insurance details are retained below. This search does not establish that coverage is inactive.',
        'review_needed': 'Returned insurance information requires identity, coverage-date or policy-order review before it can verify coverage.',
        'rejected': 'Stedi could not complete insurance discovery. The original payer response and clinic rates are retained.',
        'not_available': 'Coverage recovery could not start from the available information. Coverage remains unverified.',
    }.get(outcome, 'Searching Stedi using the patient details from eCW. This usually takes a few seconds and can take several minutes.'
          if state == 'pending' else 'Coverage remains unverified. Review the available insurance information.')
    if filtered_legacy_result:
        message = 'The saved search returned policies, but no usable match was established. Coverage requires review, not an uninsured classification.'
    if state == 'indeterminate':
        message = 'The recovery request outcome is unknown. Check Stedi activity before submitting another search.'
    elif discovery.get('exhausted') is False and state == 'pending':
        message = 'Continuing automatic insurance recovery using the available records and payer interfaces.'
    elif discovery.get('exhausted') and outcome != 'recovered':
        message = 'Available automatic insurance checks have finished. Coverage remains unverified; this does not establish that the patient is uninsured.'
    candidates = [{key: value for key, value in row.items() if key != 'index'} for row in discovery.get('candidates', [])]
    return {'state': state, 'outcome': outcome, 'message': message,
            'coverage_status': 'VERIFIED' if state == 'complete' and outcome == 'recovered' else
                'NOT_FOUND' if state == 'complete' and outcome == 'no_match' else 'REVIEW_REQUIRED',
            'card_status': discovery.get('card_status'),
            'exhausted': discovery.get('exhausted'),
            'steps': discovery.get('steps') or {},
            'payer_routes': [{'id': row['stediId'], 'name': row.get('displayName'),
                'payer_id': row.get('primaryPayerId'), 'aliases': row.get('aliases') or [],
                'match_basis': row.get('match_basis', 'name_or_alias'),
                'names': row.get('names') or [], 'eligibility': row.get('transactionSupport', {}).get('eligibilityCheck')}
                for row in discovery.get('payer_routes', [])] if outcome != 'recovered' else [],
            'attempt_count': len(discovery.get('attempts') or []),
            'started_at': discovery.get('started_at'), 'checked_at': discovery.get('checked_at'),
            'reference': discovery.get('discovery_id'), 'coverage_count': discovery.get('coverage_count'),
            'raw_candidate_count': discovery.get('raw_candidate_count', discovery.get('coverage_count')),
            'retained_candidate_count': len(candidates),
            'verified_candidate_count': sum(row.get('verification_status') == 'VERIFIED' for row in candidates),
            'stage': discovery.get('stage'), 'reason_code': 'identity_or_coverage_review' if filtered_legacy_result else discovery.get('reason_code'),
            'candidates': candidates}
