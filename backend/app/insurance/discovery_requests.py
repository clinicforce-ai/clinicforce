"""Construct bounded Stedi discovery and Medicare MBI requests."""
from __future__ import annotations

import json
import re
from datetime import date

from app.insurance.identity import birth_date


def mbi_request_body(inputs: dict, source: dict, provider: dict) -> dict | None:
    """Only recorded Original Medicare Part B, never age or a guessed plan.

    The caller must verify billing-NPI enrollment for CMS and MBILUNOSSN.
    This lookup does not recover a Medicare Advantage plan's member number.
    """
    patient = inputs.get('patient') or {}
    payer_name = str(source.get('payer') or '')
    plan_name = str(source.get('plan_name') or source.get('plan_display') or '')
    # A UHC Medicare Advantage member uses the UHC 270/271 route. A stale
    # eCW Medicare Part B code alone must not start CMS/HETS enrollment.
    if re.search(r'\b(?:united\s*health(?:care)?|uhc)\b|medicare\s+advantage',
                 f'{payer_name} {plan_name}', re.I):
        return None
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


def _valid_provider_npi(provider: dict) -> str | None:
    return next((str(provider[key]) for key in ('rendering_npi', 'billing_npi')
                 if re.fullmatch(r'\d{10}', str(provider.get(key) or ''))), None)


def missing_request_fields(inputs: dict, provider: dict) -> list[str]:
    patient = inputs.get('patient') or {}
    missing = [label for key, label in (
        ('patient_first_name', 'patient first name'),
        ('patient_last_name', 'patient surname'),
        ('patient_birth_date', 'patient date of birth')) if not patient.get(key)]
    if not _valid_provider_npi(provider):
        missing.append('10-digit rendering or billing NPI')
    if not inputs.get('service_date'):
        missing.append('visit date')
    return missing


def request_body(inputs: dict, provider: dict) -> dict | None:
    patient = inputs.get('patient') or {}
    # Discovery searches for the patient. Stedi may return the patient as a
    # subscriber or dependent, so an eCW relationship cannot gate the search.
    fields = {'firstName': patient.get('patient_first_name'), 'lastName': patient.get('patient_last_name'),
              'dateOfBirth': str(patient.get('patient_birth_date') or '').replace('-', '')}
    npi = _valid_provider_npi(provider)
    if missing_request_fields(inputs, provider):
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
