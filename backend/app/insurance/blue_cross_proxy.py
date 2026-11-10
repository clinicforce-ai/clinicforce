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
from app.insurance.network_participation import verified_in_network, verified_out_of_network
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


FRB_2026_BENEFITS = {
    'group_number': '13705',
    'effective_from': '2026-01-01',
    'effective_to': '2026-12-31',
    'deductible_cents': 0,
    'source': 'UnitedHealthcare Federal Reserve Banks 2026 plan materials',
    'url': 'https://retiree.uhc.com/content/dam/retiree/pdf/frb/2026/2026-ANOC-FRB-13705.pdf',
}
TRS_2026_SPECIALIST_COPAY = {
    'group_number': '15725', 'copay_cents': 1000,
    'source': '2026 TRS-Care Medicare Advantage Plan Highlights',
    'url': 'https://trs.texas.gov/files/documents/plan-highlights-trs-care-2026.pdf',
}


def _oop_remaining(benefits: dict, network: str = 'out_of_network') -> int | None:
    direct = benefits.get('oop_remaining_cents')
    if type(direct) is int and 0 <= direct <= 100_000_000:
        return direct
    values = {row.get('amount_cents') for row in benefits.get('reported_options') or []
              if row.get('kind') == 'out_of_pocket'
              and row.get('service_type_code') == '30'
              and row.get('coverage_level') in {'INDIVIDUAL', 'IND'}
              and row.get('time_period') in {'REMAINING', '29'}
              and row.get('network') in ({'IN_NETWORK', 'Y', 'IN_AND_OUT_OF_NETWORK', 'W'}
                                         if network == 'in_network' else
                                         {'OUT_OF_NETWORK', 'N', 'IN_AND_OUT_OF_NETWORK', 'W'})
              and row.get('plan_association') in {'medical', 'medical_candidate'}
              and type(row.get('amount_cents')) is int}
    return next(iter(values)) if len(values) == 1 else None


def _saved_diagnostic_terms(benefits: dict, codes: list[str], network: str) -> dict | None:
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
                   or row.get('network') not in ({'IN_NETWORK', 'IN_AND_OUT_OF_NETWORK'}
                                                if network == 'in_network' else
                                                {'OUT_OF_NETWORK', 'IN_AND_OUT_OF_NETWORK'})
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


def biopsy_collection_target(result: dict, service_date: str) -> dict | None:
    """Show full deductible exposure on a scoped UHC biopsy fee reference."""
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    price = result.get('price_range') or {}
    codes, lines = result.get('codes') or [], price.get('lines') or []
    reference = result.get('temporary_rate_reference') or {}
    rates = reference.get('lines') or []
    if (result.get('payment_mode') == 'cash' or not commercial.is_united_healthcare_payer(result)
            or len(names) != 1 or not re.fullmatch(
                r'LPPO\s*[- ]\s*UNITED\s*HEALTHCARE\s+GROUP\s+MEDICARE\s+ADVANTAGE\s*\(?PPO?\)?',
                str(names[0]).strip(), re.I)
            or not visit_coverage_bound(benefits, service_date)
            or not verified_in_network(benefits) or not patient_benefits_bound(identity)
            or benefits.get('coverage_status_conflict') or benefits.get('network_conflict')
            or benefits.get('source_errors') or benefits.get('deductible_applies') is False
            or benefits.get('deductible_remaining_cents') is not None
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or price.get('mode') != 'services' or len(codes) not in {1, 2}
            or len(lines) != len(codes)):
        return None
    biopsies = [row for row in codes if row.get('code') == '11104']
    if (len(biopsies) != 1 or any(row.get('code') != '11104'
            and (row.get('code') not in {str(n) for n in range(99212, 99216)}
                 or row.get('units') != 1) for row in codes)):
        return None
    code = biopsies[0]
    matched_lines = [row for row in lines if row.get('id') == code.get('id')]
    matched_rates = [row for row in rates if row.get('id') == code.get('id')]
    if len(matched_lines) != 1 or len(matched_rates) != 1:
        return None
    line, rate = matched_lines[0], matched_rates[0]
    terms = line.get('benefits') or {}
    if (code.get('code') != '11104' or code.get('units') != 1 or code.get('modifiers')
            or any(row.get('id') != code.get('id') or row.get('code') != '11104'
                   or row.get('units') != 1 or row.get('modifiers') for row in (line, rate))
            or terms.get('active') is not True or terms.get('service_date_matches') is False
            or terms.get('network_status') != 'in_network' or terms.get('copay_cents') != 0
            or terms.get('deductible_applies') is False
            or terms.get('deductible_remaining_cents') is not None
            or terms.get('coverage_status_conflict') or terms.get('source_errors')
            or str(terms.get('coinsurance_percent') or '') not in {'0', '0.0', '0.00'}
            or type(rate.get('unit_reference_cents')) is not int
            or not 0 < rate['unit_reference_cents'] <= 100_000_000
            or not str(rate.get('source_reference') or '').startswith('library:')):
        return None
    remaining = _oop_remaining(benefits, 'in_network')
    if type(remaining) is not int or remaining <= 0:
        return None
    included = {'11104'}
    total_reference = rate['unit_reference_cents']
    office = next((row for row in codes if row.get('code') != '11104'), None)
    if office:
        office_lines = [row for row in lines if row.get('id') == office.get('id')]
        office_rates = [row for row in rates if row.get('id') == office.get('id')]
        if len(office_lines) == len(office_rates) == 1:
            office_line, office_rate = office_lines[0], office_rates[0]
            office_terms = office_line.get('benefits') or {}
            if (office_line.get('code') == office_rate.get('code') == office['code']
                    and office_line.get('units') == office_rate.get('units') == 1
                    and (office_line.get('modifiers') or []) == (office_rate.get('modifiers') or []) == (office.get('modifiers') or [])
                    and office_terms.get('active') is not False
                    and office_terms.get('network_status') != 'out_of_network'
                    and office_terms.get('service_date_matches') is not False
                    and office_terms.get('copay_cents') in {None, 0}
                    and str(office_terms.get('coinsurance_percent') or '0') in {'0', '0.0', '0.00'}
                    and office_terms.get('deductible_applies') is not False
                    and office_terms.get('deductible_remaining_cents') is None
                    and not office_terms.get('coverage_status_conflict')
                    and not office_terms.get('source_errors')
                    and type(office_rate.get('unit_reference_cents')) is int
                    and 0 < office_rate['unit_reference_cents'] <= 100_000_000
                    and str(office_rate.get('source_reference') or '').startswith('library:')):
                total_reference += office_rate['unit_reference_cents']
                included.add(office['code'])
    pending = [row['code'] for row in codes if row['code'] not in included]
    return {'basis': 'bcbstx_uhc_biopsy_deductible_target', 'status': 'provisional',
            'amount_cents': min(total_reference, remaining),
            'reference_cents': total_reference, 'oop_remaining_cents': remaining,
            'included_codes': [row['code'] for row in codes if row['code'] in included],
            'pending_codes': pending,
            'assumption': 'full_deductible_applies', 'rate_source': reference.get('source'),
            'message': 'Planning target using the clinic BCBS Texas CPT fees as a proxy. '
                       'Assumes the full deductible applies to the included services because UHC did not return a deductible rule. '
                       'UHC allowance, payer order and final patient responsibility remain unverified; '
                       + ('the office follow-up is excluded until its rate and benefit scope are checked. '
                          if pending else '') + 'Pathology is excluded.'}


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
    in_network = (verified_in_network(benefits) and len(names) == 1 and bool(re.fullmatch(
        r'LPPO\s*[- ]\s*UNITED\s*HEALTHCARE\s+GROUP\s+MEDICARE\s+ADVANTAGE\s*\(?PPO?\)?',
        str(names[0]).strip(), re.I)))
    if (result.get('payment_mode') == 'cash'
            or not commercial.is_united_healthcare_payer(result)
            or not group_ppo
            or not in_network and str(insurance.get('group_number') or plan.get('group_number') or '') != FRB_2026_BENEFITS['group_number']
            or not FRB_2026_BENEFITS['effective_from'] <= service_date <= FRB_2026_BENEFITS['effective_to']
            or not visit_coverage_bound(benefits, service_date)
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('source_errors')
            or not patient_benefits_bound(identity)
            or benefits.get('identity') and identity.get('binding') != benefits['identity'].get('binding')
            or not (in_network or verified_out_of_network(benefits)) or benefits.get('has_secondary')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or result.get('refresh_requires_selection') is True
            or price.get('mode') != 'services' or price.get('service_issues') or price.get('conditional')
            or selected_service_blocker('in_network' if in_network else 'out_of_network', identity, lines)
            or not lines
            or {(row.get('id'), row.get('code')) for row in result.get('codes') or []}
                != {(row.get('id'), row.get('code')) for row in lines}):
        return None
    office_lines = [line for line in lines if line.get('code') == '99213']
    follow_up = bool(office_lines)
    if follow_up:
        office = office_lines[0]
        terms = office.get('benefits') or {}
        if (len(office_lines) != 1 or result.get('separate_em_planned') is not True
                or result.get('visit_type') == 'new' or not in_network
                or str(insurance.get('group_number') or plan.get('group_number') or '') != TRS_2026_SPECIALIST_COPAY['group_number']
                or office.get('units') != 1 or office.get('modifiers')
                or terms.get('copay_cents') not in {None, TRS_2026_SPECIALIST_COPAY['copay_cents']}
                or terms.get('deductible_remaining_cents') not in {None, 0}
                or str(terms.get('coinsurance_percent') or '0') not in {'0', '0.0', '0.00'}):
            return None
        lines = [line for line in lines if line is not office]
    reference_lines = [row for row in reference.get('lines') or []
                       if row.get('code') in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS]
    if (not follow_up and reference.get('complete') is not True
            or len(lines) != len(reference_lines)):
        return None
    codes = [str(line.get('code') or '') for line in lines]
    if (cpt_rules.service_selection_issues(codes)
            or not any(code in cpt_rules.NCS_CODES for code in codes)
            or any(code not in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for code in codes)
            or sum((line.get('units') or 0) for line in lines if line.get('code') in {'95885', '95886'}) > 4):
        return None
    by_id = {row.get('id'): row for row in reference_lines}
    if len(by_id) != len(lines):
        return None
    saved_terms = (_saved_diagnostic_terms(benefits, codes, 'in_network' if in_network else 'out_of_network')
                   if all(not line.get('benefits') for line in lines) else None)
    total = 0
    percentages = set()
    sources = []
    shares = []
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
                or terms.get('network_status') != ('in_network' if in_network else 'out_of_network')
                or terms.get('copay_cents') != 0
                or (in_network and (terms.get('deductible_remaining_cents') if terms.get('deductible_remaining_cents') is not None
                                    else benefits.get('deductible_remaining_cents')) != 0)
                or (not in_network and (terms.get('deductible_applies') is True
                    or terms.get('deductible_remaining_cents') not in {None, 0}))
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
        shares.append({'code': line['code'], 'units': units, 'reference_cents': rate * units,
                       'coinsurance_cents': int((Decimal(rate * units) * percent / 100).quantize(
                           Decimal('1'), rounding=ROUND_HALF_UP))})
        sources.append(row['source_reference'])
    if len(percentages) != 1 or total > 100_000_000:
        return None
    remaining = _oop_remaining(benefits, 'in_network' if in_network else 'out_of_network')
    if type(remaining) is not int or not 0 <= remaining <= 100_000_000:
        return None
    percent = percentages.pop()
    coinsurance = (sum(row['coinsurance_cents'] for row in shares) if in_network else
                   int((Decimal(total) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
    office_copay = TRS_2026_SPECIALIST_COPAY['copay_cents'] if follow_up else 0
    share = min(coinsurance + office_copay, remaining)
    return {
        'basis': 'bcbstx_uhc_in_network_fee_proxy' if in_network else 'bcbstx_fee_proxy',
        'status': 'provisional',
        'reference_total_cents': total, 'coinsurance_percent': str(percent),
        'copay_cents': office_copay, 'deductible_cents': 0,
        'coinsurance_cents': coinsurance, 'oop_reduction_cents': coinsurance + office_copay - share,
        'patient_share_cents': share, 'oop_remaining_cents': remaining,
        **({'lines': shares} if in_network else {}),
        'rate_source': reference.get('source'), 'rate_references': list(dict.fromkeys(sources)),
        'deductible_source': ('No deductible applied because the member-specific Stedi remaining deductible is $0'
                              if in_network else FRB_2026_BENEFITS['source']),
        **({} if in_network else {'deductible_url': FRB_2026_BENEFITS['url']}),
        **({'office_copay_source': TRS_2026_SPECIALIST_COPAY['source'],
            'office_copay_url': TRS_2026_SPECIALIST_COPAY['url']} if follow_up else {}),
        'message': ('Provisional patient share using the published TRS-Care specialist visit copay, BCBS Texas clinic fees, and UHC diagnostic benefits. '
                    if follow_up else 'Provisional patient share using BCBS Texas clinic fees and UHC diagnostic benefits. ')
                   + 'The UHC allowed amount and actual claim result are unknown.',
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
                           ) + 'This is a planning share only; Cigna allowed amounts are not supplied and the fee proxy does not authorize check-in collection.' + cap_note)}


def cigna_office_fee_scenarios(result: dict, service_date: str) -> dict | None:
    """Price separate office E/M scenarios from family-only plan terms and BCBS fees."""
    benefits = result.get('benefits') or {}
    reference = result.get('temporary_rate_reference') or {}
    selected = result.get('codes') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    if (result.get('payment_mode') == 'cash' or not re.search(r'\bcigna\b', str(result.get('payer') or ''), re.I)
            or not visit_coverage_bound(benefits, service_date) or not verified_in_network(benefits)
            or not patient_benefits_bound(identity) or benefits.get('has_secondary')
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('source_errors') or result.get('refresh_requires_selection') is True
            or len(selected) != 1 or selected[0].get('units') != 1
            or selected[0].get('modifiers') or reference.get('complete') is not True
            or len(reference.get('lines') or []) != 1):
        return None
    selected_code = str(selected[0].get('code') or '')
    family = next((codes for codes in
                   ({str(code) for code in range(99202, 99206)},
                    {str(code) for code in range(99212, 99216)})
                   if selected_code in codes), set())
    if not family:
        return None
    exact = (result.get('cpt_authorization') or {}).get('financial_terms') or {}
    if any(value is not None for terms in exact.values() for value in
           (terms.get('copay_cents'), terms.get('coinsurance_percent'),
            terms.get('deductible_remaining_cents'), terms.get('oop_remaining_cents'))):
        return None
    options = benefits.get('reported_options') or []
    def one_row(kind: str, level: str, period: str | None = None) -> dict | None:
        matches = [row for row in options if row.get('kind') == kind
                   and row.get('service_type_code') == '30' and not row.get('procedure_code')
                   and row.get('coverage_level') == level and row.get('network') == 'IN_NETWORK'
                   and not row.get('places_of_service') and not row.get('effective_dates')
                   and (period is None or row.get('time_period') == period)
                   and row.get('plan_association') in {'medical', 'unassigned'}]
        return matches[0] if len(matches) == 1 else None
    deductible = one_row('deductible', 'FAMILY', 'REMAINING')
    oop = one_row('out_of_pocket', 'FAMILY', 'REMAINING')
    coinsurance = one_row('coinsurance', 'INDIVIDUAL')
    if (not deductible or not oop or not coinsurance
            or not all('only family accumulators' in str(row.get('payer_note') or '').casefold()
                       for row in (deductible, oop))
            or len({row.get('plan_index') for row in (deductible, oop, coinsurance)}) != 1
            or type(deductible.get('plan_index')) is not int
            or type(deductible.get('amount_cents')) is not int
            or type(oop.get('amount_cents')) is not int
            or not 0 <= deductible['amount_cents'] <= 100_000_000
            or not 0 <= oop['amount_cents'] <= 100_000_000):
        return None
    try:
        percent = Decimal(str(coinsurance['percent']))
    except (KeyError, TypeError, InvalidOperation):
        return None
    if not percent.is_finite() or not 0 <= percent <= 100:
        return None
    rows = []
    for line in [*reference['lines'], *(reference.get('alternatives') or [])]:
        fee = line.get('unit_reference_cents')
        if (line.get('code') not in family or line.get('units') != 1
                or line.get('modifiers') or type(fee) is not int
                or not 0 < fee <= 100_000_000
                or not str(line.get('source_reference') or '').startswith('library:')):
            return None
        deductible_share = min(fee, deductible['amount_cents'])
        coinsurance_share = int((Decimal(fee - deductible_share) * percent / 100)
                                .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        rows.append({'code': line['code'], 'selected': line['code'] == selected_code,
                     'fee_reference_cents': fee, 'deductible_cents': deductible_share,
                     'coinsurance_cents': coinsurance_share,
                     'patient_share_cents': min(deductible_share + coinsurance_share,
                                                oop['amount_cents'])})
    if not rows or rows[0]['code'] != selected_code or len({row['code'] for row in rows}) != len(rows):
        return None
    return {'basis': 'bcbstx_cigna_family_only_office', 'status': 'conditional',
            'deductible_remaining_cents': deductible['amount_cents'],
            'oop_remaining_cents': oop['amount_cents'], 'coinsurance_percent': str(percent),
            'lines': rows,
            'message': 'Conditional check-in calculation using BCBSTX fees and Cigna family-only plan terms. Confirm the final CPT, deductible applicability, Cigna allowed amount, health-account payment and Explanation of Estimate before charging.'}


def cigna_oap_office_scenario(result: dict, service_date: str) -> dict | None:
    """Show a conditional office-copay scenario when exact CPT terms are unavailable."""
    benefits = result.get('benefits') or {}
    check = result.get('cpt_authorization') or {}
    reference = result.get('temporary_rate_reference') or {}
    price = result.get('price_range') or {}
    codes, lines = result.get('codes') or [], price.get('lines') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    plan_names = [result.get('plan'), *((result.get('plan_details') or {}).get('stedi') or {}).get('names', [])]
    if (result.get('payment_mode') == 'cash' or not re.search(r'\bcigna\b', str(result.get('payer') or ''), re.I)
            or not any(re.search(r'open access plus|\boap\b', str(name or ''), re.I) for name in plan_names)
            or not verified_in_network(benefits) or not visit_coverage_bound(benefits, service_date)
            or not patient_benefits_bound(identity) or benefits.get('has_secondary')
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('source_errors') or result.get('refresh_requires_selection') is True
            or check.get('state') != 'complete' or check.get('service_date') != service_date
            or check.get('pos') != '11' or check.get('network') != 'in_network'
            or check.get('patient_binding') != identity.get('binding')
            or len(check.get('check_ids') or []) != len(codes)
            or price.get('mode') != 'services' or price.get('service_issues') or price.get('conditional')
            or reference.get('complete') is not True or not codes or len(codes) != len(lines)
            or len(codes) != len(reference.get('lines') or [])
            or not any(row.get('code') in cpt_rules.NCS_CODES for row in codes)
            or any(row.get('code') not in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for row in codes)
            or cpt_rules.service_selection_issues([row.get('code') for row in codes])
            or benefits.get('deductible_remaining_cents') != 0
            or type(benefits.get('oop_remaining_cents')) is not int):
        return None
    options = benefits.get('reported_options') or []
    diagnostic_rows = [row for row in options
                       if row.get('service_type_code') == '73' and not row.get('procedure_code')
                       and row.get('coverage_level') == 'INDIVIDUAL'
                       and row.get('network') == 'IN_NETWORK'
                       and '11' in (row.get('places_of_service') or [])
                       and row.get('plan_association') == 'medical'
                       and re.search(r'\bnon[- ]tier 1 specialist\b', str(row.get('payer_note') or ''), re.I)
                       and not re.search(r'\bsleep stud(?:y|ies)\b', str(row.get('payer_note') or ''), re.I)]
    diagnostic_coinsurance = [row for row in diagnostic_rows if row.get('kind') == 'coinsurance']
    diagnostic_copays = [row for row in diagnostic_rows if row.get('kind') == 'copay'
                         and row.get('time_period') in {'VISIT', '27'}]
    diagnostic_plan_ids = {row.get('plan_index') for row in diagnostic_coinsurance + diagnostic_copays}
    diagnostic_amounts = {row.get('amount_cents') for row in diagnostic_copays}
    diagnostic_match = (bool(diagnostic_coinsurance and diagnostic_copays)
                        and {str(row.get('percent')) for row in diagnostic_coinsurance} <= {'0', '0.0', '0.00'}
                        and len(diagnostic_plan_ids) == 1
                        and type(next(iter(diagnostic_plan_ids))) is int
                        and len(diagnostic_amounts) == 1
                        and all(type(amount) is int and amount > 0 for amount in diagnostic_amounts))
    general_rows = [row for row in options
                    if row.get('kind') == 'coinsurance' and row.get('service_type_code') == '30'
                    and not row.get('procedure_code') and row.get('coverage_level') == 'INDIVIDUAL'
                    and row.get('network') == 'IN_NETWORK' and not row.get('places_of_service')
                    and row.get('plan_association') == 'medical']
    plan_ids = {row.get('plan_index') for row in general_rows}
    general_match = (bool(general_rows)
                     and {str(row.get('percent')) for row in general_rows} <= {'0', '0.0', '0.00'}
                     and len(plan_ids) == 1 and type(next(iter(plan_ids))) is int)
    if not diagnostic_match and not general_match:
        return None
    terms = check.get('financial_terms') or {}
    if set(terms) != {row.get('code') for row in codes}:
        return None
    if any(any((terms.get(row['code']) or {}).get(field) is not None for field in
               ('coinsurance_percent', 'copay_cents', 'deductible_remaining_cents', 'oop_remaining_cents'))
           for row in codes):
        return None
    copays = {row.get('amount_cents') for row in options
              if row.get('kind') == 'copay' and row.get('service_type_code') == '98'
              and row.get('plan_index') in plan_ids
              and not row.get('procedure_code') and row.get('coverage_level') == 'INDIVIDUAL'
              and row.get('network') == 'IN_NETWORK' and '11' in (row.get('places_of_service') or [])
              and row.get('time_period') in {'VISIT', '27'}
              and str(row.get('payer_note') or '').split(' Benefit does apply')[0].strip()
                  in {'Specialist', 'Specialist Visit or Evaluation'}
              and row.get('plan_association') != 'supplementary'
              and type(row.get('amount_cents')) is int and row['amount_cents'] > 0}
    if diagnostic_match:
        copay = diagnostic_amounts.pop()
        scenario_kind = 'oap_diagnostic_office_copay'
    elif len(copays) == 1:
        copay = copays.pop()
        scenario_kind = 'oap_office_copay_general_coinsurance'
    else:
        return None
    if benefits['oop_remaining_cents'] < copay:
        return None
    rates = {row.get('id'): row for row in reference['lines']}
    shares = []
    for line in lines:
        rate = rates.get(line.get('id')) or {}
        units, unit_cents = line.get('units'), rate.get('unit_reference_cents')
        if (rate.get('code') != line.get('code') or rate.get('units') != units
                or (rate.get('modifiers') or []) != (line.get('modifiers') or [])
                or type(units) is not int or not 1 <= units <= 4
                or type(unit_cents) is not int or not 0 < unit_cents <= 100_000_000
                or not str(rate.get('source_reference') or '').startswith('library:')):
            return None
        shares.append({'code': line['code'], 'units': units,
                       'reference_cents': unit_cents * units, 'coinsurance_cents': 0})
    total = sum(row['reference_cents'] for row in shares)
    if total < copay or total > 100_000_000:
        return None
    return {'basis': 'bcbstx_cigna_fee_proxy', 'status': 'provisional',
            'scenario_kind': scenario_kind,
            'reference_total_cents': total, 'coinsurance_percent': '0',
            'copay_cents': copay, 'deductible_cents': 0, 'coinsurance_cents': 0,
            'oop_reduction_cents': 0, 'oop_remaining_cents': benefits['oop_remaining_cents'],
            'patient_share_cents': copay, 'lines': shares,
            'rate_source': reference.get('source'), 'deductible_source': 'General individual Stedi 271 balance',
            'message': (('Conditional planning scenario: one in-network diagnostic specialist office copay plus the '
                         'reported diagnostic 0% coinsurance and $0 remaining individual deductible. '
                         if diagnostic_match else
                         'Conditional planning scenario: one in-network specialist office copay plus the general 0% '
                         'coinsurance and $0 remaining individual deductible, applied to selected diagnostic CPTs. '
                         'The office copay and general coinsurance may not apply to these procedures. ')
                        + 'BCBS Texas fees are reference prices, not Cigna allowances; obtain Cigna’s Explanation '
                          'of Estimate before collection.')}
