"""Conditional Cigna family-only office fee scenarios."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


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
                   and row.get('diagnosis_scoped') is not True
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
