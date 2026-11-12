"""Deductible-first Cigna visit quote using clinic rates, then BCBS Texas fees."""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

from app.insurance import commercial
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


def estimate(result: dict, service_date: str) -> dict | None:
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    price = result.get('price_range') or {}
    codes = result.get('codes') or []
    lines = price.get('lines') or []
    reference = result.get('temporary_rate_reference') or {}
    contract = result.get('cigna_clinic_rates') or []
    place_of_service = ((result.get('appointment_context') or {}).get('place_of_service') or
                        (result.get('cpt_authorization') or {}).get('pos'))
    if (result.get('payment_mode') == 'cash' or not re.search(r'\bcigna\b', str(result.get('payer') or ''), re.I)
            or result.get('cigna_rate_conflict') is True
            or service_date < '2026-11-12'
            or not visit_coverage_bound(benefits, service_date)
            or not patient_benefits_bound(identity) or not verified_in_network(benefits)
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('source_errors') or benefits.get('coverage_status_conflict')
            or place_of_service != '11' or price.get('mode') != 'services' or price.get('service_issues')
            or not codes or len(codes) != len(lines)
            or any(line.get('conditions') for line in lines)):
        return None
    by_contract = {row['code']: row for row in contract if row.get('modifier', '') == ''}
    by_reference = {row.get('id'): row for row in reference.get('lines') or []}
    priced = []
    terms = []
    deductible_assumed = False
    for code, line in zip(codes, lines):
        units = code.get('units')
        if (line.get('id') != code.get('id') or line.get('code') != code.get('code')
                or line.get('units') != units or (line.get('modifiers') or []) != (code.get('modifiers') or [])
                or code.get('modifiers') or type(units) is not int or not 1 <= units <= 100):
            return None
        rate = by_contract.get(code['code'])
        if rate:
            amount = rate.get('allowed_cents')
            source = 'Cigna clinic contract'
            source_reference = rate.get('source_reference')
        else:
            rate = by_reference.get(code['id']) or {}
            if (rate.get('code') != code['code'] or rate.get('units') != units
                    or rate.get('modifiers') or not (
                        str(rate.get('source_reference') or '').startswith('library:') or
                        str(rate.get('source_reference') or '').startswith(
                            'https://www.bcbstx.com/docs/provider/tx/standards/general-reimbursement-information/'))):
                return None
            amount = rate.get('unit_reference_cents')
            source = 'BCBS Texas clinic fee proxy'
            source_reference = rate.get('source_reference')
        if type(amount) is not int or not 0 < amount <= 100_000_000:
            return None
        term = line.get('benefits') or {}
        if term.get('deductible_applies') is False:
            return None  # This quote is only for deductible-applicable services.
        deductible_assumed |= term.get('deductible_applies') is not True
        deductible = term.get('deductible_remaining_cents')
        oop = term.get('oop_remaining_cents')
        coinsurance = term.get('coinsurance_percent')
        if deductible is None: deductible = benefits.get('deductible_remaining_cents')
        if oop is None: oop = benefits.get('oop_remaining_cents')
        if coinsurance is None: coinsurance = benefits.get('coinsurance_percent')
        if (type(deductible) is not int or type(oop) is not int
                or coinsurance is None or term.get('copay_cents') is not None
                or term.get('requires_review') or term.get('source_errors')):
            return None
        terms.append((deductible, oop, str(coinsurance)))
        priced.append({'code': code['code'], 'units': units, 'unit_reference_cents': amount,
                       'reference_cents': amount * units, 'source': source,
                       'source_reference': source_reference})
    if len(set(terms)) != 1:
        return None
    deductible, oop, coinsurance = terms[0]
    try:
        calculation = commercial.estimate(
            [{'code': row['code'], 'units': row['units'], 'allowed_cents': row['unit_reference_cents']}
             for row in priced],
            {'active': True, 'coverage_level': 'individual', 'network_status': 'in_network',
             'deductible_applies': True, 'deductible_remaining_cents': deductible,
             'oop_remaining_cents': oop, 'coinsurance_percent': coinsurance,
             'copay_cents': None}, {'cost_share': 'coinsurance'})
    except commercial.CommercialError:
        return None
    deductible_left = calculation['deductible_cents']
    coinsurance_left = calculation['coinsurance_cents']
    for index, row in enumerate(priced):
        applied = min(row['reference_cents'], deductible_left)
        deductible_left -= applied
        row['deductible_cents'] = applied
        row['coinsurance_base_cents'] = row['reference_cents'] - applied
        share = (coinsurance_left if index == len(priced) - 1 else
                 min(coinsurance_left, int((Decimal(row['coinsurance_base_cents']) *
                      Decimal(calculation['coinsurance_percent']) / 100).quantize(
                          Decimal('1'), rounding=ROUND_HALF_UP))))
        row['coinsurance_cents'] = share
        coinsurance_left -= share
    return {'basis': 'cigna_clinic_fee_quote', 'status': 'provisional',
            'reference_total_cents': calculation['allowed_cents'],
            'deductible_cents': calculation['deductible_cents'],
            'coinsurance_percent': calculation['coinsurance_percent'],
            'coinsurance_cents': calculation['coinsurance_cents'],
            'copay_cents': 0, 'copay_unconfirmed': True,
            'oop_remaining_cents': oop, 'oop_reduction_cents': calculation['oop_adjustment_cents'],
            'patient_share_cents': calculation['patient_cents'], 'lines': priced,
            'rate_source': 'Clinic Cigna contract and BCBS Texas fee schedule',
            'deductible_source': (f"${calculation['deductible_cents'] / 100:.2f} applied before coinsurance from the "
                                  'individual in-network medical balance; '
                                  + ('applicability assumed' if deductible_assumed else 'applicability reported')),
            **({'deductible_applicability': 'assumed'} if deductible_assumed else {}),
            'message': ('Clinic Cigna contract rates are used for listed CPTs; other CPTs use matched BCBS Texas '
                        'clinic fees as a proxy. The individual medical deductible is '
                        + ('assumed to apply' if deductible_assumed else 'reported applicable') +
                        ' before coinsurance and the remaining '
                        'out-of-pocket limit caps the share. No additional copay was returned; final claim '
                        'adjudication may change this refundable quote.')}


def apply_collection(result: dict, guidance: dict, review: dict | None,
                     assumptions: list[str], *, safe: bool) -> None:
    quote = result.get('fee_proxy_estimate') or {}
    policy = result.get('collection_policy') or {}
    if (not safe or guidance.get('amount_cents') is not None
            or quote.get('basis') != 'cigna_clinic_fee_quote'
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or type(quote.get('patient_share_cents')) is not int):
        return
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    amount = max(0, quote['patient_share_cents'] - paid)
    guidance.update(amount_cents=amount, status='estimated', basis='cigna_clinic_fee_quote',
        reference_total_cents=quote['reference_total_cents'],
        reason=('Refundable clinic quote for the selected Cigna visit: apply the reported individual '
                'deductible first, then coinsurance, then the remaining out-of-pocket cap. '
                'Cigna contract rates are used where supplied; other codes use matched BCBS Texas '
                'clinic fees as a proxy. Reconcile and refund any excess after the claim.'),
        patient_message=('This is a refundable estimate for the planned services. The clinic will '
                         'apply it to your claim and refund any excess after processing.'),
        collection_rule={'mode': 'cigna_clinic_fee_quote',
                         'reference': 'Clinic-supplied Cigna rates, matched BCBS Texas fees and member benefits'})
    assumptions.append('No additional office copay was returned; the quote assumes none and is reconciled after the claim.')
    for key in ('cigna_estimate_required', 'policy_setup_required', 'positive_advance_policy_missing'):
        guidance.pop(key, None)
