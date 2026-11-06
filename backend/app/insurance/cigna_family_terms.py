"""Unqualified Cigna family accumulator terms for a conditional office scenario."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def scenario_message(assumed: bool) -> str:
    scope = ('plan-wide family benefits. It assumes the family deductible applies to this office visit; '
             'the exact-CPT response did not establish this visit’s cost sharing. ' if assumed else
             'exact-CPT family-only benefits. The family deductible applies before coinsurance. ')
    return ('Provisional office visit share using the BCBS Texas fee reference and Cigna ' + scope
            + 'No fixed office copay was returned. The Cigna allowed amount and any HRA payment are unknown; '
              'obtain Cigna’s Explanation of Estimate before collecting deductible or coinsurance.')


def deductible_source(assumed: bool) -> str:
    return ('Plan-wide Stedi family accumulator; office applicability assumed' if assumed
            else 'Exact CPT Stedi family-only accumulator')


def general_family_terms(benefits: dict, exact: dict) -> dict | None:
    """Use unscoped plan accumulators only as an assumed office scenario."""
    if exact.get('copay_unresolved') or any(exact.get(key) is not None for key in (
            'coinsurance_percent', 'copay_cents', 'deductible_remaining_cents', 'oop_remaining_cents')):
        return None
    if any(row.get('kind') == 'copay' and row.get('network') == 'IN_NETWORK'
           and row.get('service_type_code') in {'30', '98'} and not row.get('procedure_code')
           and row.get('diagnosis_scoped') is False
           and (not row.get('places_of_service') or '11' in row['places_of_service'])
           and type(row.get('amount_cents')) is int and row['amount_cents'] > 0
           for row in benefits.get('reported_options') or []):
        return None
    options = [row for row in benefits.get('reported_options') or []
               if row.get('service_type_code') == '30' and not row.get('procedure_code')
               and row.get('diagnosis_scoped') is False and not row.get('places_of_service')
               and row.get('network') == 'IN_NETWORK'
               and (row.get('plan_association') == 'medical' or
                    row.get('kind') == 'coinsurance' and row.get('plan_association') == 'unassigned')
               and type(row.get('plan_index')) is int]
    plans = {row['plan_index'] for row in options}
    if len(plans) != 1:
        return None
    def one(kind: str, level: str, *, period: str | None = None) -> dict | None:
        rows = [row for row in options if row.get('kind') == kind and row.get('coverage_level') == level
                and (period is None or row.get('time_period') == period)]
        return rows[0] if len(rows) == 1 else None
    coinsurance = one('coinsurance', 'INDIVIDUAL')
    deductible = one('deductible', 'FAMILY', period='REMAINING')
    oop = one('out_of_pocket', 'FAMILY', period='REMAINING')
    if (not coinsurance or not deductible or not oop
            or type(deductible.get('amount_cents')) is not int
            or type(oop.get('amount_cents')) is not int
            or not all('this plan has only family accumulators' in str(row.get('payer_note') or '').casefold()
                       for row in (deductible, oop))):
        return None
    try:
        percent = Decimal(str(coinsurance.get('percent')))
        summary = Decimal(str(benefits.get('coinsurance_percent')))
    except (TypeError, InvalidOperation):
        return None
    if percent != summary or not 0 <= percent <= 100:
        return None
    return {'coverage_level': 'FAMILY', 'family_only_accumulators': True,
            'coinsurance_percent': str(percent), 'copay_cents': None,
            'deductible_remaining_cents': deductible['amount_cents'],
            'oop_remaining_cents': oop['amount_cents']}


