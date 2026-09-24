"""Bounded demographic recovery after a payer rejects a member ID."""
from __future__ import annotations

import re
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from app.insurance import commercial

URL = 'https://healthcare.us.stedi.com/2024-04-01/insurance-discovery/check/v1'


def normalized(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def patient_address(patient: dict) -> dict:
    today = date.today().isoformat()
    addresses = [a for a in patient.get('address', []) if a.get('use') != 'old'
                 and (a.get('period', {}).get('start') or today)[:10] <= today
                 and (a.get('period', {}).get('end') or today)[:10] >= today]
    home = [a for a in addresses if a.get('use') == 'home']
    addresses = home or addresses
    if len(addresses) != 1:
        return {}
    address = addresses[0]
    lines = address.get('line') or []
    result = {key: str(address.get(key) or '').strip() for key in ('city', 'state', 'postalCode')}
    result['state'] = result['state'].upper()
    result['postalCode'] = re.sub(r'\D', '', result['postalCode'])
    if not re.fullmatch('[A-Z]{2}', result['state']) or not re.fullmatch(r'\d{5}(?:\d{4})?', result['postalCode']):
        return {}
    if lines:
        result['address1'] = str(lines[0])
        if len(lines) > 1:
            result['address2'] = ' '.join(lines[1:])
    return {key: value for key, value in result.items() if value}


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
    return {'provider': {'npi': npi}, 'subscriber': fields,
            'encounter': {'dateOfService': inputs['service_date'].replace('-', ''), 'serviceTypeCodes': ['30']}}


def candidate_identity(item: dict, inputs: dict) -> dict | None:
    """Return the returned policy identity only when it belongs to this patient.

    A candidate is still reviewed by staff before a different payer is used.
    Exact patient demographics keep another household member's policy out of
    the selectable list without assuming that every payer returns an address.
    """
    if commercial.eligibility_errors(item):
        return None
    patient = inputs.get('patient') or {}
    subscriber = item.get('subscriber') if isinstance(item.get('subscriber'), dict) else {}
    dependent = item.get('dependent') if isinstance(item.get('dependent'), dict) else {}
    insured = dependent or subscriber
    expected = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
                'dateOfBirth': patient.get('patient_birth_date')}
    if not all(normalized(value) and normalized(insured.get(key)) == normalized(value) for key, value in expected.items()):
        return None
    if not all(subscriber.get(key) for key in ('memberId', 'firstName', 'lastName', 'dateOfBirth')):
        return None
    address, returned = patient.get('address') or {}, insured.get('address') or {}
    address_matches = bool(address and returned) and all(
        normalized(address.get(key)) == normalized(returned.get(key)) for key in ('address1', 'city', 'state')
    ) and normalized(address.get('postalCode'))[:5] == normalized(returned.get('postalCode'))[:5]
    return {'member_id': subscriber['memberId'], 'subscriber_first': subscriber['firstName'],
            'subscriber_last': subscriber['lastName'], 'subscriber_birth_date': subscriber['dateOfBirth'],
            'relationship': 'dependent' if dependent else 'self', 'address_matches': address_matches}


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
                     'address_matches': identity['address_matches']})
    return rows


def patient_matches(item: dict, inputs: dict) -> bool:
    """An automatic retry is limited to an exact self-policy address match."""
    identity = candidate_identity(item, inputs)
    return bool(identity and identity['relationship'] == 'self' and identity['address_matches'])


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
        'no_match': 'Stedi searched using the eCW demographics and found no coverage. This does not mean coverage is inactive. Check the current card or the saved eCW eligibility report to correct the payer/member ID.',
        'review_needed': 'Stedi found coverage for this patient. Select the policy to use for this estimate; ClinicForce will not replace the eCW payer automatically.',
        'rejected': 'Stedi could not complete insurance discovery. The original payer response and clinic rates are retained.',
    }.get(outcome, 'Searching Stedi using the patient details from eCW. This usually takes a few seconds and can take several minutes.')
    if state == 'indeterminate':
        message = 'The recovery request outcome is unknown. Check Stedi activity before submitting another search.'
    candidates = [{key: value for key, value in row.items() if key != 'index'} for row in discovery.get('candidates', [])]
    return {'state': state, 'outcome': outcome, 'message': message,
            'started_at': discovery.get('started_at'), 'checked_at': discovery.get('checked_at'),
            'reference': discovery.get('discovery_id'), 'coverage_count': discovery.get('coverage_count'),
            'candidates': candidates}
