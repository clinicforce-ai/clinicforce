"""Read live 835 evidence; never enroll a payer or move payment routing.

Stedi may hold a platform account for many practices. The caller must supply
the practice's approved billing identity; only matching patient records leave
this module. Historical allowances are not current contracted fee schedules.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx
from anyio import fail_after

TRANSACTIONS = 'https://core.us.stedi.com/2023-08-01/transactions'
REPORTS = 'https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/reports/v2'


def credentials(practice_id: str) -> str:
    # Optional per-practice integrated-account keys override the platform key.
    # Configure through secrets injection, never return this mapping to the UI.
    raw = os.getenv('STEDI_PRACTICE_API_KEYS', '').strip()
    if raw:
        try:
            keys = json.loads(raw)
            key = keys.get(practice_id) if isinstance(keys, dict) else None
            return key.strip() if isinstance(key, str) else ''
        except ValueError:
            return ''
    return (os.getenv('STEDI_API_KEY', '').strip()
            or os.getenv('STEDI_PROD_API_KEY', '').strip())


def _normal(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _amount(value: object) -> int | None:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > 100_000_000 or number.as_tuple().exponent < -2:
            return None
        return int(number * 100)
    except (InvalidOperation, ValueError):
        return None


def patient_lines(payload: dict, identity: dict, transaction_id: str, received_at: str) -> list[dict]:
    """Require practice, member/name and a claim linked to the eCW patient."""
    if (payload.get('meta') or {}).get('applicationMode') != 'production':
        return []
    if not all(identity.get(key) for key in ('billing_npi', 'tin', 'member_id', 'patient_first', 'patient_last')):
        return []
    rows = []
    for transaction in payload.get('transactions', []):
        payee = transaction.get('payee') or {}
        if (_normal(payee.get('npi')) != _normal(identity['billing_npi']) or
                _normal(payee.get('federalTaxPayersIdentificationNumber')) != _normal(identity['tin'])):
            continue
        for detail in transaction.get('detailInfo', []):
            for payment in detail.get('paymentInfo', []):
                patient = payment.get('patientName') or {}
                if any(_normal(patient.get(field)) != _normal(identity[key]) for field, key in
                       [('memberId', 'member_id'), ('firstName', 'patient_first'), ('lastName', 'patient_last')]):
                    continue
                claim = payment.get('claimPaymentInfo') or {}
                if str(claim.get('patientControlNumber') or '') not in identity.get('claim_identifiers', []):
                    continue
                for index, line in enumerate(payment.get('serviceLines', [])):
                    service = line.get('servicePaymentInformation') or {}
                    responsibility = []
                    for adjustment in line.get('serviceAdjustments', []):
                        if adjustment.get('claimAdjustmentGroupCode') == 'PR':
                            for n in range(1, 7):
                                if 'adjustmentAmount' + str(n) in adjustment:
                                    responsibility.append(_amount(adjustment['adjustmentAmount' + str(n)]))
                    rows.append({'transaction_id': transaction_id,
                        'claim_reference': claim.get('payerClaimControlNumber'), 'line_index': index,
                        'claim_status': claim.get('claimStatusCode'),
                        'code': service.get('adjudicatedProcedureCode'),
                        'modifiers': service.get('adjudicatedProcedureModifierCodes') or [],
                        'service_date': line.get('serviceDate'),
                        'payer': (transaction.get('payer') or {}).get('name'),
                        'allowed_cents': _amount((line.get('serviceSupplementalAmounts') or {}).get('allowedActual')),
                        'insurer_paid_cents': _amount(service.get('lineItemProviderPaymentAmount')),
                        'patient_responsibility_cents': sum(responsibility) if responsibility and all(v is not None for v in responsibility) else None,
                        'source': 'Stedi 835 remittance', 'received_at': received_at})
    return rows


async def fetch_patient_financials(practice_id: str, identity: dict, *, transport=None) -> dict:
    now = datetime.now(timezone.utc)
    result = {'source': 'Stedi 835 remittances', 'checked_at': now.isoformat(), 'lines': [],
              'status': 'not_connected', 'partial': False, 'transactions_checked': 0,
              'message': 'Connect this practice’s Stedi financial account to retrieve insurer payments.'}
    key = credentials(practice_id)
    if not key:
        return result
    if not all(identity.get(field) for field in ('billing_npi', 'tin', 'member_id', 'patient_first', 'patient_last')):
        return {**result, 'status': 'practice_setup', 'message': 'Practice billing identity must be linked before financial records can be matched.'}
    seen = set()
    try:
        with fail_after(20):
            async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), transport=transport) as client:
                params = {'transactionSetId': '835', 'mode': 'production', 'direction': 'INBOUND',
                          'status': 'succeeded', 'pageSize': 20, 'from': (now - timedelta(days=365)).isoformat()}
                # ponytail: bounded on-demand history; use persisted polling cursors
                # for a full financial ledger when practices need bulk reconciliation.
                for _ in range(3):
                    response = await client.get(TRANSACTIONS, params=params, headers={'Authorization': key})
                    response.raise_for_status()
                    data = response.json()
                    for item in data.get('items', []):
                        transaction_id = str(UUID(item['transactionId']))
                        if transaction_id in seen or item.get('mode') != 'production':
                            continue
                        seen.add(transaction_id)
                        report = await client.get(f'{REPORTS}/{transaction_id}/835', headers={'Authorization': key})
                        report.raise_for_status()
                        result['lines'].extend(patient_lines(report.json(), identity, transaction_id, item.get('processedAt', '')))
                        result['transactions_checked'] += 1
                    token = data.get('nextPageToken')
                    if not token:
                        break
                    params['pageToken'] = token
                else:
                    result['partial'] = True
        result.update(status='available' if result['lines'] else 'no_matching_remittances',
                      message='Previous insurer payments for this patient. Historical amounts are not today’s quote.' if result['lines'] else
                      'No matching remittances in the records checked. Only claims routed to this Stedi account can appear here.')
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError):
        result.update(status='partial' if result['lines'] else 'connection_error', partial=True,
                      message='Some Stedi financial records could not be retrieved. Retry the financial check.')
    return result
