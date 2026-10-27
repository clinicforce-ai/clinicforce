"""Build cash collection guidance from an explicit practice schedule."""
from __future__ import annotations


def build(result: dict, review: dict | None, guidance: dict, price: dict, lines: list[dict]) -> dict:
    amounts = [price.get(key) for key in ('cash_min_cents', 'cash_max_cents')]
    complete = (price.get('cash_complete') and not price.get('service_issues')
                and all(type(value) is int and 0 <= value <= 100_000_000 for value in amounts)
                and amounts[0] <= amounts[1])
    message = ((result.get('cash_quote') or {}).get('reason')
               or (result.get('cash_fallback') or {}).get('reason')
               or 'Cash pricing for the selected services.')
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents', 0) if payments.get('reference') else 0
    guidance.update(status='cash_estimate' if complete else 'cash_price_missing', basis='practice_cash_price',
        amount_cents=max(0, amounts[0] - paid) if complete else None,
        range_min_cents=max(0, amounts[0] - paid) if complete else None,
        range_max_cents=max(0, amounts[1] - paid) if complete else None,
        reason=message if complete else message + ' The practice has no complete cash price for the selected services.',
        patient_message=message, network='Cash pricing', network_source='Practice cash schedule or explicit clinic cash rates',
        requirements={kind: 'Insurance requirements do not establish the cash price' for kind in ('referral', 'authorization')})
    if payments.get('reference') and complete:
        guidance['reason'] += ' The recorded payment was deducted once.'
    for line in lines:
        terms = line.get('benefits') or {}
        guidance['services'].append({'id': line.get('id'), 'code': line.get('code'),
            'benefit_source': line.get('benefit_source') or result.get('benefit_source'),
            'benefit': line.get('benefit_basis') or 'Applicable service benefits',
            'patient_cents': None, 'copay_cents': terms.get('copay_cents'),
            'coinsurance_percent': terms.get('coinsurance_percent'),
            'deductible_remaining_cents': terms.get('deductible_remaining_cents'),
            'deductible_applicability': terms.get('deductible_applicability', 'UNKNOWN'),
            'oop_remaining_cents': terms.get('oop_remaining_cents'),
            'selected_benefit_ids': terms.get('selected_benefit_ids') or []})
    return guidance
