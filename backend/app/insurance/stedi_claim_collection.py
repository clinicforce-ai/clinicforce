"""Bound a paid Aetna claim to an office visit and explain its inferred share."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.insurance import identity, stedi
from app.insurance.stedi_benefits.normalize import normalize_stedi_271

CLAIM_STATUS_URL = 'https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/claimstatus/v2'


def _cents(value: object) -> int | None:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1_000_000 or amount.as_tuple().exponent < -2:
            return None
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _same(left: object, right: object) -> bool:
    return bool(left and right and identity.normalized(left) == identity.normalized(right))


def paid_office_scenario(claim_response: dict, eligibility: dict, coverage: dict,
                         inputs: dict, source: dict, financials: dict) -> dict | None:
    """Return a labeled estimate only for one fully matched, paid office line."""
    service_date = str(inputs.get('service_date') or '')
    rendering = source.get('rendering_provider') or {}
    billing = source.get('billing_provider') or {}
    if (str(eligibility.get('payerId') or '') != '60054' or not service_date
            or coverage.get('active_coverages') != 1 or coverage.get('other_active_coverage')
            or coverage.get('relationship') not in {'18', 'self'}
            or not _same((eligibility.get('subscriber') or {}).get('memberId'), coverage.get('member_id'))
            or not _same((claim_response.get('subscriber') or {}).get('memberId'), coverage.get('member_id'))
            or not _same((claim_response.get('subscriber') or {}).get('firstName'), coverage.get('subscriber_first'))
            or not _same((claim_response.get('subscriber') or {}).get('lastName'), coverage.get('subscriber_last'))
            or not any(row.get('providerType') == 'BillingProvider'
                       and _same(row.get('npi'), billing.get('npi'))
                       for row in claim_response.get('providers') or [])):
        return None
    comparison = identity.compare(eligibility, coverage)
    if not identity.patient_benefits_bound(comparison):
        return None
    benefits = normalize_stedi_271(eligibility, '98', False, office_visit=True,
        service_date=service_date, place_of_service='11', provider_npi=str(rendering.get('npi') or ''),
        provider_specialties=['2084N0400X'])
    if (benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or benefits.get('network_status') != 'in_network'
            or benefits.get('network_provider_npi') != rendering.get('npi')
            or benefits.get('source_errors') or benefits.get('coverage_status_conflict')
            or benefits.get('copay_cents') != 0 or benefits.get('deductible_remaining_cents') != 0
            or benefits.get('has_secondary') or benefits.get('network_conflict')):
        return None
    try:
        percent = Decimal(str(benefits['coinsurance_percent']))
    except (InvalidOperation, KeyError, TypeError):
        return None
    if not 0 < percent < 100:
        return None
    claims = claim_response.get('claims') or []
    if len(claims) != 1:
        return None
    claim = claims[0]
    status = claim.get('claimStatus') or {}
    lines = claim.get('serviceDetails') or []
    if (status.get('statusCategoryCode') != 'F1' or len(lines) != 1
            or str(status.get('claimServiceDate') or '').replace('-', '') not in
                {service_date.replace('-', ''), service_date.replace('-', '') * 2}):
        return None
    service = lines[0].get('service') or {}
    code = str(service.get('procedureId') or '')
    if (not code.isdigit() or len(code) != 5 or not (99202 <= int(code) <= 99205
            or 99212 <= int(code) <= 99215) or str(service.get('submittedUnits') or '') not in {'1', '1.0'}):
        return None
    paid = _cents(service.get('amountPaid'))
    submitted = _cents(service.get('submittedAmount'))
    if paid is None or paid <= 0 or submitted is None or submitted < paid:
        return None
    implied = Decimal(paid) / (Decimal(1) - percent / 100)
    if implied != implied.to_integral_value() or not paid < implied <= submitted:
        return None
    allowed = int(implied)
    recorded_payments = financials.get('payments_verified_cents')
    if recorded_payments is not None and (type(recorded_payments) is not int or recorded_payments < 0):
        return None
    payments = recorded_payments if recorded_payments is not None else 0
    patient = allowed - paid
    source_kind = 'inferred_from_paid_claim'
    matching_835 = [row for row in financials.get('lines') or []
        if row.get('code') == code and row.get('service_date') in {service_date, service_date.replace('-', '')}
        and row.get('insurer_paid_cents') == paid
        and type(row.get('patient_responsibility_cents')) is int]
    allowed_confirmed = False
    if len(matching_835) == 1:
        row = matching_835[0]
        patient = row['patient_responsibility_cents']
        allowed_confirmed = type(row.get('allowed_cents')) is int
        allowed = row['allowed_cents'] if allowed_confirmed else allowed
        source_kind = 'adjudicated_835'
    elif len(matching_835) > 1:
        return None
    due = max(0, patient - payments)
    claim_explanation = (f'The linked 835 reports ${patient / 100:,.2f} patient responsibility.'
        if source_kind == 'adjudicated_835' else
        f'The payment is consistent with a ${allowed / 100:,.2f} allowance and '
        f'${patient / 100:,.2f} patient share; the 277 does not report either value, so this is an inference.')
    payment_explanation = (f'Subtract ${payments / 100:,.2f} recorded patient payments.'
        if recorded_payments is not None else
        'No verified patient payment amount was present in the saved visit; assume $0 pending a clinic ledger check.')
    reason = (f'Aetna finalized one CPT {code} office claim and reported paying ${paid / 100:,.2f}. '
              f'The office benefit reports a $0 copay, $0 remaining deductible and {percent:g}% '
              f'in-network coinsurance. {claim_explanation} '
              f'{payment_explanation} Confirm the EOB and clinic ledger before charging.')
    return {'status': 'adjudicated' if source_kind == 'adjudicated_835' else 'estimated',
            'source': source_kind, 'code': code, 'units': 1,
            'submitted_cents': submitted, 'insurer_paid_cents': paid,
            'allowed_cents': allowed, 'allowed_confirmed': allowed_confirmed,
            'patient_share_cents': patient,
            'payments_verified_cents': payments, 'amount_cents': due,
            'copay_cents': 0, 'coinsurance_percent': str(percent),
            'deductible_remaining_cents': benefits.get('deductible_remaining_cents'),
            'oop_remaining_cents': benefits.get('oop_remaining_cents'),
            'benefits_checked_at': datetime.now(timezone.utc).isoformat(),
            'reason': reason}


async def lookup(case: dict, coverage: dict, financials: dict, key: str) -> dict | None:
    context = case['previsit_context']
    inputs, source = context['inputs'], context['source']
    rendering = source.get('rendering_provider') or {}
    billing = source.get('billing_provider') or {}
    try:
        day = date.fromisoformat(str(inputs.get('service_date') or ''))
    except ValueError:
        return None
    if (not key or day >= date.today() or str(context.get('payer_id') or '') not in {'HPQRS', '60054'}
            or str(inputs.get('place_of_service') or '') != '11'
            or not all((rendering.get('npi'), rendering.get('first_name'), rendering.get('last_name'),
                         billing.get('npi'), coverage.get('member_id'), coverage.get('subscriber_first'),
                         coverage.get('subscriber_last'), coverage.get('subscriber_birth_date')))):
        return None
    eligible = {'payerId': context['payer_id'],
        'provider': {'npi': rendering['npi'], 'name': {'person': {
            'firstName': rendering['first_name'], 'lastName': rendering['last_name']}}},
        'subscriber': {'memberId': coverage['member_id'],
            'dateOfBirth': identity.birth_date(coverage['subscriber_birth_date']),
            'name': {'person': {'firstName': coverage['subscriber_first'],
                                'lastName': coverage['subscriber_last']}}},
        'encounter': {'dates': {'service': {'start': day.isoformat()}},
                      'services': [{'system': 'STC', 'value': '98'}], 'placeOfService': {'code': '11'}}}
    status_request = {'tradingPartnerServiceId': context['payer_id'],
        'providers': [{'providerType': 'BillingProvider', 'npi': billing['npi']}],
        'subscriber': {'firstName': coverage['subscriber_first'],
            'lastName': coverage['subscriber_last'],
            'dateOfBirth': identity.birth_date(coverage['subscriber_birth_date']).replace('-', ''),
            'memberId': coverage['member_id']},
        'encounter': {'beginningDateOfService': (day - timedelta(days=7)).strftime('%Y%m%d'),
                      'endDateOfService': min(day + timedelta(days=7), date.today()).strftime('%Y%m%d')}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
            headers = {'Authorization': key, 'Content-Type': 'application/json'}
            eligibility = await client.post(stedi.ELIGIBILITY_URL, headers=headers, json=eligible)
            if eligibility.status_code != 200:
                return None
            claim = await client.post(CLAIM_STATUS_URL, headers=headers, json=status_request)
            if claim.status_code != 200:
                return None
            return paid_office_scenario(claim.json(), eligibility.json(), coverage, inputs, source, financials)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError):
        return None
