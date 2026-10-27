"""Disclosed BCBS Texas fee proxy for a scoped UHC diagnostic visit.

This is a planning calculation. It never changes a payer allowance or grants
permission to collect Medicare cost sharing.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance import commercial, cpt_rules, insurance_product
from app.insurance.benefit_review import selected_service_blocker
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_out_of_network


FRB_2026_BENEFITS = {
    'group_number': '13705',
    'effective_from': '2026-01-01',
    'effective_to': '2026-12-31',
    'deductible_cents': 0,
    'source': 'UnitedHealthcare Federal Reserve Banks 2026 plan materials',
    'url': 'https://retiree.uhc.com/content/dam/retiree/pdf/frb/2026/2026-ANOC-FRB-13705.pdf',
}


def _oop_remaining(benefits: dict) -> int | None:
    direct = benefits.get('oop_remaining_cents')
    if type(direct) is int and 0 <= direct <= 100_000_000:
        return direct
    values = {row.get('amount_cents') for row in benefits.get('reported_options') or []
              if row.get('kind') == 'out_of_pocket'
              and row.get('service_type_code') == '30'
              and row.get('coverage_level') in {'INDIVIDUAL', 'IND'}
              and row.get('time_period') in {'REMAINING', '29'}
              and row.get('network') in {'OUT_OF_NETWORK', 'N', 'IN_AND_OUT_OF_NETWORK', 'W'}
              and row.get('plan_association') in {'medical', 'medical_candidate'}
              and type(row.get('amount_cents')) is int}
    return next(iter(values)) if len(values) == 1 else None


def estimate(result: dict, service_date: str) -> dict | None:
    """Return an exact-line fee proxy only when diagnostic benefit terms agree."""
    benefits = result.get('benefits') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    insurance = result.get('insurance_details') or {}
    plan = (result.get('plan_details') or {}).get('stedi') or {}
    reference = result.get('temporary_rate_reference') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    group_ppo = (insurance_product.classify(names) == 'medicare_advantage_group_ppo'
                 or len(names) == 1 and bool(re.search(
                     r'\bLPPO\s*[- ]\s*UNITED\s*HEALTHCARE\s+GROUP\s+MEDICARE\s+ADVANTAGE\s*\(?(?:PP|PPO)\b',
                     str(names[0]), re.I)))
    if (result.get('payment_mode') == 'cash'
            or not commercial.is_united_healthcare_payer(result)
            or not group_ppo
            or str(insurance.get('group_number') or plan.get('group_number') or '') != FRB_2026_BENEFITS['group_number']
            or not FRB_2026_BENEFITS['effective_from'] <= service_date <= FRB_2026_BENEFITS['effective_to']
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('source_errors')
            or not patient_benefits_bound(identity)
            or benefits.get('identity') and identity.get('binding') != benefits['identity'].get('binding')
            or not verified_out_of_network(benefits) or benefits.get('has_secondary')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or result.get('refresh_requires_selection') is True
            or price.get('mode') != 'services' or price.get('service_issues') or price.get('conditional')
            or selected_service_blocker('out_of_network', identity, lines)
            or reference.get('complete') is not True or not lines
            or len(lines) != len(reference.get('lines') or [])
            or {(row.get('id'), row.get('code')) for row in result.get('codes') or []}
                != {(row.get('id'), row.get('code')) for row in lines}):
        return None
    codes = [str(line.get('code') or '') for line in lines]
    if (cpt_rules.service_selection_issues(codes)
            or not any(code in cpt_rules.NCS_CODES for code in codes)
            or any(code not in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for code in codes)
            or sum((line.get('units') or 0) for line in lines if line.get('code') in {'95885', '95886'}) > 4):
        return None
    by_id = {row.get('id'): row for row in reference['lines']}
    if len(by_id) != len(lines):
        return None
    total = 0
    percentages = set()
    sources = []
    for line in lines:
        row = by_id.get(line.get('id'))
        terms = line.get('benefits') or {}
        units = line.get('units')
        rate = (row or {}).get('unit_reference_cents')
        if (not row or row.get('code') != line.get('code') or row.get('units') != units
                or (row.get('modifiers') or []) != (line.get('modifiers') or [])
                or type(units) is not int or not 1 <= units <= 4
                or type(rate) is not int or not 0 < rate <= 100_000_000
                or not str(row.get('source_reference') or '').startswith('library:')
                or terms.get('active') is not True or terms.get('service_date_matches') is False
                or terms.get('network_status') != 'out_of_network'
                or terms.get('copay_cents') != 0
                or terms.get('deductible_applies') is True
                or terms.get('deductible_remaining_cents') not in {None, 0}
                or terms.get('coverage_status_conflict') or terms.get('coverage_dates_invalid')
                or terms.get('network_conflict') or terms.get('has_secondary')
                or terms.get('historical_only') or terms.get('source_errors')):
            return None
        try:
            percent = Decimal(str(terms['coinsurance_percent']))
        except (KeyError, TypeError, InvalidOperation):
            return None
        if not percent.is_finite() or not 0 <= percent <= 100:
            return None
        percentages.add(percent)
        total += rate * units
        sources.append(row['source_reference'])
    if len(percentages) != 1 or total > 100_000_000:
        return None
    remaining = _oop_remaining(benefits)
    if type(remaining) is not int or not 0 <= remaining <= 100_000_000:
        return None
    percent = percentages.pop()
    coinsurance = int((Decimal(total) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    share = min(coinsurance, remaining)
    return {
        'basis': 'bcbstx_fee_proxy', 'status': 'provisional',
        'reference_total_cents': total, 'coinsurance_percent': str(percent),
        'copay_cents': 0, 'deductible_cents': 0,
        'coinsurance_cents': coinsurance, 'oop_reduction_cents': coinsurance - share,
        'patient_share_cents': share, 'oop_remaining_cents': remaining,
        'rate_source': reference.get('source'), 'rate_references': list(dict.fromkeys(sources)),
        'deductible_source': FRB_2026_BENEFITS['source'],
        'deductible_url': FRB_2026_BENEFITS['url'],
        'message': 'Provisional patient share using BCBS Texas clinic fees and UHC diagnostic benefits. The UHC allowed amount and actual claim result are unknown.',
    }
