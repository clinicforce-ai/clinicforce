"""Provisional in-network UHC Choice Plus EMG amount on clinic BCBSTX fees."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance import commercial
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network
from app.insurance.stedi_cpt_authorization import visit_coverage_bound
from app.insurance.uhc_biopsy_target import _oop_remaining


def choice_plus_emg_estimate(result: dict, service_date: str) -> dict | None:
    """Price the editable two-code EMG scenario on clinic BCBSTX reference fees."""
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    price = result.get('price_range') or {}
    codes, lines = result.get('codes') or [], price.get('lines') or []
    reference = result.get('temporary_rate_reference') or {}
    rates = reference.get('lines') or []
    if (result.get('payment_mode') == 'cash'
            or not commercial.is_united_healthcare_payer(result)
            or len(names) != 1 or str(names[0]).strip().casefold() != 'unitedhealthcare choice plus'
            or not verified_in_network(benefits)
            or not visit_coverage_bound(benefits, service_date)
            or not patient_benefits_bound(identity)
            or benefits.get('has_secondary') or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('source_errors')
            or benefits.get('deductible_applies') is False
            or result.get('refresh_requires_selection') is True
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or price.get('mode') != 'services' or price.get('service_issues')
            or price.get('allowed_min_cents') is not None
            or any(line.get('allowed_cents') is not None for line in lines)
            or reference.get('complete') is not True
            or len(codes) != 2 or len(lines) != 2 or len(rates) != 2
            or {row.get('code') for row in codes} != {'95908', '95886'}
            or any(row.get('units') != 1 or row.get('modifiers') for row in codes)
            or {(row.get('id'), row.get('code')) for row in codes} !=
               {(row.get('id'), row.get('code')) for row in lines}):
        return None
    deductible = benefits.get('deductible_remaining_cents')
    remaining = _oop_remaining(benefits, 'in_network')
    if (type(deductible) is not int or not 0 <= deductible <= 100_000_000
            or type(remaining) is not int or not 0 <= remaining <= 100_000_000):
        return None
    by_id = {row.get('id'): row for row in rates}
    if len(by_id) != 2:
        return None
    shares, percentages = [], set()
    for line in lines:
        rate = by_id.get(line.get('id')) or {}
        terms = line.get('benefits') or benefits
        cents = rate.get('unit_reference_cents')
        if (rate.get('code') != line.get('code') or rate.get('units') != 1
                or rate.get('modifiers') or line.get('units') != 1 or line.get('modifiers')
                or type(cents) is not int or not 0 < cents <= 100_000_000
                or not str(rate.get('source_reference') or '').startswith('library:')
                or terms.get('active') is not True or terms.get('service_date_matches') is False
                or terms.get('network_status') != 'in_network' or terms.get('copay_cents') != 0
                or terms.get('deductible_applies') is False
                or terms.get('deductible_remaining_cents') not in {None, deductible}
                or terms.get('coverage_status_conflict') or terms.get('network_conflict')
                or terms.get('source_errors')):
            return None
        try:
            percent = Decimal(str(terms['coinsurance_percent']))
        except (KeyError, TypeError, InvalidOperation):
            return None
        if not percent.is_finite() or not 0 <= percent <= 100:
            return None
        percentages.add(percent)
        shares.append({'code': line['code'], 'units': 1, 'reference_cents': cents,
                       'coinsurance_cents': 0})
    if len(percentages) != 1:
        return None
    total = sum(row['reference_cents'] for row in shares)
    applied = min(total, deductible)
    percent = percentages.pop()
    deductible_to_allocate = applied
    for row in shares:
        line_deductible = min(row['reference_cents'], deductible_to_allocate)
        deductible_to_allocate -= line_deductible
        row['coinsurance_base_cents'] = row['reference_cents'] - line_deductible
        row['coinsurance_cents'] = int((Decimal(row['coinsurance_base_cents']) * percent / 100).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP))
    coinsurance = sum(row['coinsurance_cents'] for row in shares)
    share = min(applied + coinsurance, remaining)
    return {'basis': 'bcbstx_uhc_choice_plus_fee_proxy', 'status': 'provisional',
            'reference_total_cents': total, 'copay_cents': 0,
            'deductible_cents': applied, 'coinsurance_percent': str(percent),
            'coinsurance_cents': coinsurance,
            'oop_reduction_cents': applied + coinsurance - share,
            'patient_share_cents': share, 'oop_remaining_cents': remaining,
            'lines': shares, 'rate_source': reference.get('source'),
            'rate_references': [row['source_reference'] for row in rates],
            'deductible_source': 'Payer-reported individual deductible remaining; applicability to these CPTs is assumed',
            'deductible_applicability': 'assumed',
            'message': ('Provisional visit amount using clinic BCBSTX fees and payer-reported individual benefits. '
                        'Deductible applicability to these CPTs is assumed. The UHC allowed amount and final claim result are unknown.')}
