"""Scoped UHC biopsy collection planning from BCBS Texas fee references."""
from __future__ import annotations

import re

from app.insurance import commercial
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


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
                    and (str(office_rate.get('source_reference') or '').startswith('library:')
                         or office_rate.get('source_reference') == 'clinic_supplied:blue_choice_ppo_fee_export')):
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
