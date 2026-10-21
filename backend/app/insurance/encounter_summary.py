"""One visit-facing summary derived only from the saved estimate inputs."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance.network_participation import participation_conflict, verified_in_network, verified_out_of_network
from app.insurance.identity import patient_benefits_bound
from app.insurance.benefit_review import selected_service_blocker
from app.insurance.collection.requirements import requirement_blocker


def _coinsurance_rate(percent: object) -> Decimal | None:
    try:
        rate = Decimal(str(percent))
        return rate if rate.is_finite() and 0 <= rate <= 100 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _coinsurance_matches(base: object, percent: object, cents: object) -> bool:
    rate = _coinsurance_rate(percent)
    if type(base) is not int or base < 0 or type(cents) is not int or cents < 0 or rate is None:
        return False
    try:
        expected = int((Decimal(base) * rate / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return expected == cents


def build(result: dict, review: dict | None = None) -> dict:
    guidance = result.get('collection_guidance') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    cash = result.get('payment_mode') == 'cash'
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    unsupported_insurance = not cash and (
        not patient_benefits_bound(identity) or benefits.get('has_secondary')
        or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
        or benefits.get('active') is not True or benefits.get('service_date_matches') is False
        or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
        or selected_service_blocker(benefits.get('network_status') or 'unknown', identity, lines) is not None
        or participation_conflict(benefits) or benefits.get('historical_only') or benefits.get('source_errors')
        or any((line.get('benefits') or {}).get('source_errors') for line in lines))
    priced = bool(price.get('cash_complete') if cash else price.get('complete'))
    priced = priced and not price.get('service_issues') and not unsupported_insurance
    line_amounts = [(line.get('calculation') or {}).get('patient_cents') for line in lines]
    checkable = bool(not cash and priced and price.get('mode') == 'services' and lines
                     and all(type(value) is int and 0 <= value <= 100_000_000 for value in line_amounts))
    integrity_error = guidance.get('integrity_error') if guidance.get('integrity_error') in {
        'patient_total_mismatch', 'line_amount_mismatch', 'collection_amount_mismatch'} else None
    if checkable:
        total = sum(line_amounts)
        if price.get('patient_min_cents') != total or price.get('patient_max_cents') != total:
            integrity_error = 'patient_total_mismatch'
        else:
            for line in lines:
                calc = line['calculation']
                if (calc.get('coinsurance_base_cents') is not None
                        and calc.get('coinsurance_percent') is not None
                        and calc.get('coinsurance_cents') is not None
                        and not _coinsurance_matches(calc['coinsurance_base_cents'],
                                                    calc['coinsurance_percent'], calc['coinsurance_cents'])):
                    integrity_error = 'line_amount_mismatch'
                    break
                keys = ('copay_cents', 'deductible_cents', 'coinsurance_cents', 'oop_adjustment_cents')
                if all(type(calc.get(key)) is int for key in keys):
                    parts = [calc[key] for key in keys]
                    allowed_cap = calc.get('allowed_cap_adjustment_cents', 0)
                    if (type(allowed_cap) is not int or any(value < 0 for value in (*parts, allowed_cap))
                            or sum(parts[:3]) - parts[3] - allowed_cap != calc['patient_cents']):
                        integrity_error = 'line_amount_mismatch'
                        break
    if integrity_error in {'patient_total_mismatch', 'line_amount_mismatch'}:
        priced = False
    # A payer-scoped out-of-network allowance supports an advance for the
    # known plan share, but cannot establish the full balance-billed total.
    out_of_network_share = bool(not cash and priced and verified_out_of_network(benefits)
        and price.get('mode') == 'services' and not price.get('conditional') and lines
        and all(line.get('allowed_basis') in {'payer_out_of_network', 'cms_original_medicare_estimate'}
                and type(line.get('allowed_cents')) is int and line['allowed_cents'] >= 0
                and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                and isinstance(line.get('calculation'), dict) for line in lines))
    complete = priced and (cash or verified_in_network(benefits))
    minimum = price.get('cash_min_cents' if cash else 'patient_min_cents') if complete else None
    maximum = price.get('cash_max_cents' if cash else 'patient_max_cents') if complete else None
    if not (type(minimum) is int and type(maximum) is int and 0 <= minimum <= maximum <= 100_000_000):
        minimum = maximum = None
        complete = False
    calculated = not cash and (complete or out_of_network_share) and price.get('mode') == 'services' and all(
        isinstance(line.get('calculation'), dict) for line in lines)
    components = {key: sum(line['calculation'][key] for line in lines)
                  if calculated and all(type(line['calculation'].get(key)) is int for line in lines) else None
                  for key in ('copay_cents', 'deductible_cents', 'coinsurance_cents',
                              'allowed_cap_adjustment_cents', 'oop_adjustment_cents')}
    coinsurance_base = (sum(line['calculation']['coinsurance_base_cents'] for line in lines)
                        if calculated and all(type(line['calculation'].get('coinsurance_base_cents')) is int for line in lines) else None)
    rates = [_coinsurance_rate(line['calculation'].get('coinsurance_percent')) for line in lines] if calculated else []
    coinsurance_percent = (format(rates[0].normalize(), 'f') if rates and rates[0] is not None
                            and all(rate == rates[0] for rate in rates) else None)
    coinsurance_rounded_per_line = False
    if (coinsurance_base is not None and coinsurance_percent is not None
            and components['coinsurance_cents'] is not None
            and not _coinsurance_matches(coinsurance_base, coinsurance_percent, components['coinsurance_cents'])):
        # Each CPT can round correctly while the rounded aggregate differs.
        # Keep the dollar component but do not present one false combined base.
        coinsurance_base = None
        coinsurance_rounded_per_line = True
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents') if payments.get('reference') else (result.get('arrival_collection') or {}).get('payments_received_cents')
    if type(paid) is not int or paid < 0:
        paid = None
    collect = guidance.get('amount_cents')
    if type(collect) is not int or collect < 0:
        collect = None
    clinic_zero_instruction = (collect == 0 and guidance.get('basis') in {'claim_after_service', 'ecw_billing_note'})
    if (checkable and integrity_error is None and collect is not None
            and guidance.get('basis') in {'patient_obligations', 'anticipated_services'}
            and collect != max(0, total - (paid or 0))):
        integrity_error = 'collection_amount_mismatch'
    if (integrity_error and guidance.get('basis') in {'patient_obligations', 'anticipated_services',
                                                      'calculation_conflict'} and not clinic_zero_instruction):
        collect = None
    if (unsupported_insurance or requirement_blocker(result) is not None) and collect is not None and not clinic_zero_instruction:
        collect = None
    office = [line for line in lines if str(line.get('code') or '').isdigit() and
              (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)]
    independent_office_copay = (guidance.get('basis') == 'office_copay' and bool(office)
                                and all((line.get('benefits') or {}).get('network_independent') for line in office))
    out_of_network_office_copay = verified_out_of_network(benefits) and guidance.get('basis') == 'office_copay'
    if (not cash and collect is not None and collect > 0 and not verified_in_network(benefits)
            and not independent_office_copay and not out_of_network_share and not out_of_network_office_copay):
        collect = None
    if guidance.get('basis') == 'office_copay' and components['copay_cents'] is None:
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
            'coinsurance_base_cents': coinsurance_base, 'coinsurance_percent': coinsurance_percent,
            'coinsurance_rounded_per_line': coinsurance_rounded_per_line,
            'unpriced_codes': [line.get('code') for line in lines if
                               line.get('cash_cents' if cash else 'calculation') is None
                               and not (cash and line.get('cash_included'))],
            'complete': complete, 'conditional': bool(price.get('conditional') or price.get('mode') == 'alternatives'
                                                   or requirement_blocker(result) is not None),
            'integrity_error': integrity_error}
