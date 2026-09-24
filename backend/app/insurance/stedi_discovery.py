"""Bounded demographic recovery after a payer rejects a member ID."""
from __future__ import annotations

import re
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
    # A dependent's policyholder must not silently change during recovery.
    if inputs.get('relationship') not in {'18', 'self'}:
        return None
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


def patient_matches(item: dict, inputs: dict) -> bool:
    """Only an exact self-policy identity/address can be used automatically."""
    if item.get('dependent') or item.get('dependents') or commercial.eligibility_errors(item):
        return False
    subscriber, patient = item.get('subscriber') or {}, inputs['patient']
    expected = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
                'dateOfBirth': patient.get('patient_birth_date')}
    if not all(normalized(value) and normalized(subscriber.get(key)) == normalized(value) for key, value in expected.items()):
        return False
    address, returned = patient.get('address') or {}, subscriber.get('address') or {}
    if not address.get('address1') or not all(normalized(address.get(key)) == normalized(returned.get(key))
            for key in ('address1', 'city', 'state')):
        return False
    if not address.get('postalCode') or normalized(address['postalCode'])[:5] != normalized(returned.get('postalCode'))[:5]:
        return False
    if address.get('address2') or returned.get('address2'):
        if normalized(address.get('address2')) != normalized(returned.get('address2')):
            return False
    return bool(subscriber.get('memberId'))


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
    message = {
        'recovered': 'Coverage recovered from Stedi and rechecked with the scheduled provider and visit date.',
        'no_match': 'Stedi searched using the eCW demographics and found no coverage. This does not mean coverage is inactive. Check the current card or the saved eCW eligibility report to correct the payer/member ID.',
        'review_needed': 'Stedi found possible coverage, but could not establish one matching patient and payer. Compare the current card or saved eCW eligibility report before using it.',
        'rejected': 'Stedi could not complete insurance discovery. The original payer response and clinic rates are retained.',
    }.get(outcome, 'Searching Stedi using the patient details from eCW. This usually takes a few seconds and can take several minutes.')
    if state == 'indeterminate':
        message = 'The recovery request outcome is unknown. Check Stedi activity before submitting another search.'
    return {'state': state, 'outcome': outcome, 'message': message,
            'started_at': discovery.get('started_at'), 'checked_at': discovery.get('checked_at'),
            'reference': discovery.get('discovery_id'), 'coverage_count': discovery.get('coverage_count')}
