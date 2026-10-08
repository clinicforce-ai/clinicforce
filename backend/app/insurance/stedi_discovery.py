"""Bounded demographic recovery after a payer rejects a member ID."""
from __future__ import annotations

import re
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from app.insurance import commercial
from app.insurance.identity import birth_date, normalized

URL = 'https://healthcare.us.stedi.com/2024-04-01/insurance-discovery/check/v1'


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


def candidate_identity(item: dict, inputs: dict) -> dict | None:
    """Return the returned policy identity only when it belongs to this patient.

    A candidate is still reviewed by staff before a different payer is used.
    Exact patient demographics keep another household member's policy out of
    the selectable list without assuming that every payer returns an address.
    """
    if commercial.eligibility_errors(item):
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
    return {'member_id': subscriber['memberId'], 'subscriber_first': subscriber['firstName'],
            'subscriber_last': subscriber['lastName'], 'subscriber_birth_date': subscriber.get('dateOfBirth'),
            'relationship': 'dependent' if dependent else 'self', 'address_matches': address_matches,
            'relationship_code': str(dependent.get('relationToSubscriberCode') or 'G8') if dependent else '18',
            'group_number': subscriber.get('groupNumber') or (item.get('planInformation') or {}).get('groupNumber'),
            'plan_number': subscriber.get('planNumber') or (item.get('planInformation') or {}).get('planNumber')}


def active_medical(item: dict, inputs: dict, service_type: str = '30') -> bool:
    if not any(set(row.get('serviceTypeCodes', [])) & {'30', service_type} for row in commercial._stedi_entries(item)):
        return False
    benefits = commercial.normalize_stedi_271(item, service_type, False, service_date=inputs.get('service_date'))
    return benefits['active'] is True and benefits['service_date_matches'] is not False


def automatic_match(item: dict, inputs: dict, source: dict) -> bool:
    """A missing eCW policy cannot be required to recover that missing policy."""
    identity = candidate_identity(item, inputs)
    return bool(identity and active_medical(item, inputs) and
                (identity['address_matches'] or matches_ecw_policy(item, inputs, source)))


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


def candidates(payload: dict, inputs: dict) -> list[dict]:
    """Create safe, stable candidate summaries while raw identities stay protected."""
    rows = []
    for index, item in enumerate(payload.get('items') or []):
        identity = candidate_identity(item, inputs)
        payer = item.get('payer') or {}
        if not identity or not (payer.get('name') or payer.get('lastName')):
            continue
        details = commercial.plan_details(item)
        token = hashlib.sha256(json.dumps([index, identity['member_id'], payer], sort_keys=True).encode()).hexdigest()[:20]
        rows.append({'id': token, 'index': index, 'payer': payer.get('name') or payer.get('lastName'),
                     'payer_identifier': str(payer.get('payorIdentification') or ''),
                     'plans': details.get('names') or [], 'relationship': identity['relationship'],
                     'address_matches': identity['address_matches'], 'active_medical': active_medical(item, inputs),
                     'verification_status': 'VERIFIED' if identity['address_matches'] and active_medical(item, inputs)
                         else 'REVIEW_REQUIRED'})
    return rows


def patient_matches(item: dict, inputs: dict) -> bool:
    """Patient identity and address match, for either subscriber or dependent."""
    identity = candidate_identity(item, inputs)
    return bool(identity and identity['address_matches'])


def matches_ecw_policy(item: dict, inputs: dict, source: dict) -> bool:
    """Permit automatic payer recovery only for the same active eCW policy."""
    identity = candidate_identity(item, inputs)
    if not (identity and identity['relationship'] == 'self' and source.get('active_coverages') == 1
            and source.get('coverage_id') and inputs.get('member_id')
            and normalized(identity['member_id']) == normalized(inputs['member_id'])):
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
    # ponytail: background work is bounded below ten minutes. Interrupted tasks
    # remain indeterminate; resume only GET by a known ID, never another POST.
    if state == 'pending' and discovery.get('started_at'):
        try:
            if datetime.fromisoformat(discovery['started_at']) + timedelta(minutes=10) < datetime.now(timezone.utc):
                state = 'indeterminate'
        except (ValueError, TypeError):
            state = 'indeterminate'
    outcome = discovery.get('outcome')
    message = discovery.get('selection_error') or {
        'recovered': 'Coverage recovered from Stedi and rechecked with the scheduled provider and visit date.',
        'no_match': 'The additional Stedi search did not find a matching policy. Saved eCW insurance details are retained below. This search does not establish that coverage is inactive.',
        'review_needed': 'Stedi found coverage for this patient. Select the policy to use for this estimate; ClinicForce will not replace the eCW payer automatically.',
        'rejected': 'Stedi could not complete insurance discovery. The original payer response and clinic rates are retained.',
    }.get(outcome, 'Searching Stedi using the patient details from eCW. This usually takes a few seconds and can take several minutes.')
    if state == 'indeterminate':
        message = 'The recovery request outcome is unknown. Check Stedi activity before submitting another search.'
    candidates = [{key: value for key, value in row.items() if key != 'index'} for row in discovery.get('candidates', [])]
    return {'state': state, 'outcome': outcome, 'message': message,
            'coverage_status': 'VERIFIED' if outcome == 'recovered' else
                'NOT_FOUND' if outcome == 'no_match' else 'REVIEW_REQUIRED',
            'card_status': discovery.get('card_status'),
            'payer_routes': [{'id': row['stediId'], 'name': row.get('displayName'),
                'payer_id': row.get('primaryPayerId'), 'aliases': row.get('aliases') or [],
                'match_basis': row.get('match_basis', 'name_or_alias'),
                'names': row.get('names') or [], 'eligibility': row.get('transactionSupport', {}).get('eligibilityCheck')}
                for row in discovery.get('payer_routes', [])] if outcome != 'recovered' else [],
            'attempt_count': len(discovery.get('attempts') or []),
            'started_at': discovery.get('started_at'), 'checked_at': discovery.get('checked_at'),
            'reference': discovery.get('discovery_id'), 'coverage_count': discovery.get('coverage_count'),
            'stage': discovery.get('stage'), 'reason_code': discovery.get('reason_code'),
            'candidates': candidates}
