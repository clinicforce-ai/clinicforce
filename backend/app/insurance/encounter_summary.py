"""One visit-facing summary derived only from the saved estimate inputs."""
from __future__ import annotations


def build(result: dict, review: dict | None = None) -> dict:
    guidance = result.get('collection_guidance') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    cash = result.get('payment_mode') == 'cash'
    complete = bool(price.get('cash_complete') if cash else price.get('complete'))
    complete = complete and not price.get('service_issues')
    if not cash:
        complete = complete and (result.get('benefits') or {}).get('network_status') == 'in_network'
    minimum = price.get('cash_min_cents' if cash else 'patient_min_cents') if complete else None
    maximum = price.get('cash_max_cents' if cash else 'patient_max_cents') if complete else None
    if not (type(minimum) is int and type(maximum) is int and 0 <= minimum <= maximum <= 100_000_000):
        minimum = maximum = None
        complete = False
    calculated = not cash and complete and price.get('mode') == 'services' and all(
        isinstance(line.get('calculation'), dict) for line in lines)
    components = {key: sum(line['calculation'][key] for line in lines)
                  if calculated and all(type(line['calculation'].get(key)) is int for line in lines) else None
                  for key in ('copay_cents', 'deductible_cents', 'coinsurance_cents')}
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents') if payments.get('reference') else (result.get('arrival_collection') or {}).get('payments_received_cents')
    if type(paid) is not int or paid < 0:
        paid = None
    collect = guidance.get('amount_cents')
    if type(collect) is not int or collect < 0:
        collect = None
    if guidance.get('basis') == 'office_copay' and components['copay_cents'] is None:
        office = [line for line in lines if str(line.get('code') or '').isdigit() and
                  (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)]
        amounts = {line.get('benefits', {}).get('copay_cents') for line in office}
        if len(amounts) == 1 and type(next(iter(amounts))) is int and collect is not None:
            # The stated plan copay is capped before verified payments. Do not
            # mistake the remaining collection amount for the copay itself.
            caps = [line.get('benefits', {}).get('copay_remaining_cents')
                    if line.get('benefits', {}).get('separate_copay_limit') else line.get('benefits', {}).get('oop_remaining_cents')
                    for line in office]
            allowed = [line.get('allowed_cents') for line in office]
            components['copay_cents'] = min([next(iter(amounts)),
                                            *(cap for cap in caps if type(cap) is int and cap >= 0),
                                            *(allowed if all(type(value) is int and value >= 0 for value in allowed) else [])])
    later = max(0, minimum - paid - collect) if complete and not price.get('conditional') and minimum == maximum and paid is not None and collect is not None else None
    return {'collect_today_cents': collect, 'estimated_total_patient_min_cents': minimum,
            'estimated_total_patient_max_cents': maximum, 'estimated_later_balance_cents': later,
            'payments_verified_cents': paid, 'components': components,
            'unpriced_codes': [line.get('code') for line in lines if
                               line.get('cash_cents' if cash else 'calculation') is None
                               and not (cash and line.get('cash_included'))],
            'complete': complete, 'conditional': bool(price.get('conditional') or price.get('mode') == 'alternatives')}
