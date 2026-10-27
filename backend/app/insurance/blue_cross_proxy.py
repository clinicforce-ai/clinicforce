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
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


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


def _saved_diagnostic_terms(benefits: dict, codes: list[str]) -> dict | None:
    """Use selected diagnostic rows when a saved 271 cannot rehydrate CPT lines."""
    ids = benefits.get('selected_benefit_ids') or []
    if (benefits.get('resolved_service_type') != '73' or len(ids) != 2
            or len(set(ids)) != 2 or benefits.get('copay_cents') != 0):
        return None
    options = benefits.get('reported_options') or []
    selected = [row for row in options if row.get('id') in ids]
    if (len(selected) != 2 or {row.get('kind') for row in selected} != {'copay', 'coinsurance'}
            or len({row.get('plan_index') for row in selected}) != 1
            or any(row.get('service_type_code') != '73'
                   or row.get('network') not in {'OUT_OF_NETWORK', 'IN_AND_OUT_OF_NETWORK'}
                   or row.get('coverage_level') not in {'IND', 'INDIVIDUAL'}
                   or row.get('procedure_code') or row.get('places_of_service')
                   or row.get('effective_dates') for row in selected)
            or any(row.get('procedure_code') in codes and row.get('kind') in {'copay', 'coinsurance'}
                   for row in options)):
        return None
    copay = next(row for row in selected if row['kind'] == 'copay')
    coinsurance = next(row for row in selected if row['kind'] == 'coinsurance')
    try:
        same_percent = Decimal(str(coinsurance.get('percent'))) == Decimal(
            str(benefits.get('coinsurance_percent')))
    except (InvalidOperation, TypeError):
        return None
    return benefits if copay.get('amount_cents') == 0 and same_percent else None


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
            or not visit_coverage_bound(benefits, service_date)
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
    saved_terms = (_saved_diagnostic_terms(benefits, codes)
                   if all(not line.get('benefits') for line in lines) else None)
    total = 0
    percentages = set()
    sources = []
    for line in lines:
        row = by_id.get(line.get('id'))
        terms = line.get('benefits') or saved_terms or {}
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


def cigna_estimate(result: dict, service_date: str) -> dict | None:
    """Disclose an exact-CPT Cigna cost-share scenario on BCBS Texas reference fees."""
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    check = result.get('cpt_authorization') or {}
    reference = result.get('temporary_rate_reference') or {}
    price = result.get('price_range') or {}
    codes = result.get('codes') or []
    lines = price.get('lines') or []
    if (result.get('payment_mode') == 'cash'
            or not re.search(r'\bcigna\b', str(result.get('payer') or ''), re.I)
            or benefits.get('network_status') != 'in_network'
            or not visit_coverage_bound(benefits, service_date)
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('source_errors')
            or not patient_benefits_bound(identity)
            or check.get('state') != 'complete' or check.get('service_date') != service_date
            or not re.fullmatch(r'\d{2}', str(check.get('pos') or ''))
            or check.get('network') != 'in_network'
            or check.get('patient_binding') != identity.get('binding')
            or reference.get('complete') is not True
            or price.get('mode') != 'services' or price.get('service_issues')
            or not codes or len(codes) != len(lines) or len(codes) != len(reference.get('lines') or [])
            or not any(row.get('code') in cpt_rules.NCS_CODES for row in codes)
            or any(row.get('code') not in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for row in codes)
            or sum((row.get('units') or 0) for row in codes if row.get('code') in cpt_rules.EMG_ADD_ONS) > 4
            or cpt_rules.service_selection_issues([row.get('code') for row in codes])):
        return None
    rates = {row.get('id'): row for row in reference['lines']}
    terms_by_code = check.get('financial_terms') or {}
    if len(rates) != len(codes):
        return None
    shares = []
    percentages = set()
    oop_balances = set()
    copay_assumed = False
    for line in lines:
        rate = rates.get(line.get('id')) or {}
        terms = terms_by_code.get(line.get('code')) or {}
        units = line.get('units')
        unit_cents = rate.get('unit_reference_cents')
        if (rate.get('code') != line.get('code') or rate.get('units') != units
                or (rate.get('modifiers') or []) != (line.get('modifiers') or [])
                or type(units) is not int or not 1 <= units <= 4
                or type(unit_cents) is not int or not 0 < unit_cents <= 100_000_000
                or not str(rate.get('source_reference') or '').startswith('library:')
                or terms.get('deductible_remaining_cents') != 0
                or terms.get('copay_cents') not in {None, 0}
                or terms.get('copay_unresolved')
                or type(terms.get('oop_remaining_cents')) is not int):
            return None
        try:
            percent = Decimal(str(terms['coinsurance_percent']))
        except (KeyError, TypeError, InvalidOperation):
            return None
        if not percent.is_finite() or not 0 <= percent <= 100:
            return None
        percentages.add(percent)
        oop_balances.add(terms['oop_remaining_cents'])
        copay_assumed |= terms['copay_cents'] is None
        base = unit_cents * units
        share = int((Decimal(base) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        shares.append({'code': line['code'], 'units': units, 'reference_cents': base,
                       'coinsurance_cents': share})
    if len(percentages) != 1 or len(oop_balances) != 1:
        return None
    total = sum(row['reference_cents'] for row in shares)
    coinsurance = sum(row['coinsurance_cents'] for row in shares)
    oop_remaining = oop_balances.pop()
    general_oop = benefits.get('oop_remaining_cents')
    if type(general_oop) is int and general_oop != oop_remaining and min(general_oop, oop_remaining) < coinsurance:
        return None
    share = min(coinsurance, oop_remaining)
    cap_note = f' The exact CPT out-of-pocket balance reduced the provisional share by ${(coinsurance - share) / 100:.2f}.' if share < coinsurance else ''
    return {'basis': 'bcbstx_cigna_fee_proxy', 'status': 'provisional',
            'reference_total_cents': total, 'coinsurance_percent': str(percentages.pop()),
            'copay_cents': 0, 'copay_unconfirmed': copay_assumed,
            'deductible_cents': 0, 'coinsurance_cents': coinsurance,
            'oop_reduction_cents': coinsurance - share, 'oop_remaining_cents': oop_remaining,
            'patient_share_cents': share, 'lines': shares,
            'rate_source': reference.get('source'), 'deductible_source': 'Exact CPT Stedi 271',
            'message': ('Provisional patient share using BCBS Texas clinic fees and Cigna exact-CPT benefits. '
                        + ('No CPT copay was returned; $0 additional copay is an assumption. '
                           if copay_assumed else 'The exact CPT copay is $0. '
                           ) + 'The check-in amount uses this fee proxy; Cigna allowed amounts are not supplied.' + cap_note)}
