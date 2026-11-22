"""NMCT provisional office copay from a patient-bound Stedi benefit row."""
from __future__ import annotations

import re
from datetime import date

from app.insurance import insurance_product
from app.insurance.collection.requirements import requirement_blocker
from app.insurance.identity import patient_plan_applies, patient_plan_without_response_day
from app.insurance.network_participation import verified_out_of_network


_OFFICE_CODES = {str(code) for code in (*range(99202, 99206), *range(99212, 99216))}


def eligible(result: dict, review: dict | None = None) -> tuple[int, int, str] | None:
    """Return a provisional copay only from a scoped, sourced office benefit."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    day = str(result.get('service_date') or '')
    returned = str((benefits.get('returned_service_dates') or {}).get('start') or '')
    try:
        date.fromisoformat(returned)
    except ValueError:
        return None
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    if (result.get('nmct_fixed_office_copay_advance_enabled') is not True
            or result.get('payment_mode') != 'insurance'
            or (result.get('billing_note') or {}).get('kind') == 'no_collection'
            or (result.get('collection_policy') or {}).get('mode') == 'defer_to_claim'
            or not (patient_plan_applies(benefits, identity, day)
                    or patient_plan_without_response_day(benefits, identity, day))
            or verified_out_of_network(benefits) or benefits.get('network_status') == 'out_of_network'
            or benefits.get('historical_only')
            or benefits.get('source_errors') or benefits.get('has_secondary')
            or benefits.get('network_conflict') or result.get('agreement_exclusion')
            or (result.get('network_evidence') or {}).get('decision') == 'conflicting_network_evidence'
            or (result.get('recovery') or {}).get('reason_code') in
               {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or (result.get('card_check') or {}).get('conflicts')
            or (result.get('card_check') or {}).get('independent_ecw_check') is True
            or result.get('refresh_requires_selection') is True
            or benefits.get('qmb_status') == 'protected'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or benefits.get('original_medicare') is True
            or insurance_product.is_medicare_advantage(names)
            or any(re.search(r'\bmedicare\b', str(name), re.I) for name in names)
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or requirement_blocker(result) is not None):
        return None
    line = lines[0]
    selected = [code for code in result.get('codes') or []
                if code.get('id') == line.get('id') and code.get('code') == line.get('code')
                and code.get('units') == line.get('units') == 1]
    if (line.get('code') not in _OFFICE_CODES or line.get('units') != 1
            or line.get('modifiers') or any('date' not in str(value).lower()
                                                for value in line.get('conditions') or [])
            or len(selected) != 1
            or not (result.get('service_selection_status') == 'approved'
                    or selected[0].get('source') == 'eCW scheduled service')):
        return None
    terms = line.get('benefits') or {}
    copay = terms.get('copay_cents')
    if (terms.get('active') is not True or terms.get('network_status') == 'out_of_network'
            or terms.get('source_errors') or terms.get('coverage_status_conflict')
            or terms.get('coverage_dates_invalid') or terms.get('has_secondary')
            or type(copay) is not int or not 0 < copay <= 100_000
            or terms.get('copay_period') not in {'VISIT', '27'}
            or terms.get('deductible_applies') is True and terms.get('deductible_remaining_cents') != 0
            or terms.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'}
            or terms.get('cost_share') in {'coinsurance', 'cumulative'}
            or type(terms.get('oop_remaining_cents')) is not int
            or terms.get('oop_remaining_cents') <= 0
            or any('date' not in str(reason).lower()
                   for reason in terms.get('review_reasons') or [])):
        return None
    rows = [row for row in terms.get('reported_options') or benefits.get('reported_options') or []
            if row.get('id') == terms.get('copay_id') and row.get('kind') == 'copay']
    if len(rows) != 1:
        return None
    row = rows[0]
    note = str(row.get('payer_note') or '')
    if (row.get('amount_cents') != copay or row.get('service_type_code') != '98'
            or row.get('network') not in {'IN_NETWORK', 'Y'}
            or str(row.get('coverage_level') or '').upper() not in {'INDIVIDUAL', 'IND'}
            or row.get('time_period') not in {'VISIT', '27'}
            or row.get('places_of_service') and '11' not in row['places_of_service']
            or not re.search(r'\boffice\b', note, re.I)
            or not re.search(r'\b(?:specialist|primary care|pcp)\b', note, re.I)):
        return None
    caps = [copay]
    for value in (terms.get('oop_remaining_cents'), benefits.get('oop_remaining_cents'),
                  line.get('allowed_cents')):
        if value is not None:
            if type(value) is not int or value < 0:
                return None
            caps.append(value)
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents') if payments.get('reference') else 0
    prior_payment = (result.get('arrival_collection') or {}).get('payments_received_cents')
    if type(prior_payment) is int and prior_payment > 0 and not payments.get('reference'):
        return None
    if type(paid) is not int or paid < 0:
        return None
    return max(0, min(caps) - paid), copay, returned


def apply(result: dict, guidance: dict, review: dict | None = None) -> dict:
    if guidance.get('amount_cents') is not None:
        return guidance
    decision = eligible(result, review)
    if decision is None:
        return guidance
    amount, copay, returned = decision
    return {**guidance, 'amount_cents': amount, 'status': 'estimated', 'basis': 'office_copay',
            'payer_reported_office_copay_cents': copay, 'payer_response_date': returned,
            'unresolved_collection_reason': guidance.get('reason'),
            'collection_rule': {'mode': 'nmct_stedi_provisional_office_copay',
                                'reference': 'NMCT provisional office-copay collection from returned Stedi benefits'},
            'reason': ('Collect the Stedi-reported office copay as a provisional advance using the returned '
                       f'{returned} benefit date, less verified payments. The provider network tier may remain '
                       'unconfirmed; reconcile the visit-date claim and refund any excess.'),
            'patient_message': ('The clinic requests a provisional office copay from the payer benefit response '
                                f'dated {returned}; final responsibility follows the processed claim.')}
