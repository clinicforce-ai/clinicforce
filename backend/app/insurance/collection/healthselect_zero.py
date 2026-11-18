"""Guard zero check-in for the exact HealthSelect Medicare Advantage plan."""

import re

from app.insurance.network_participation import verified_in_network
from app.insurance.published_plan_benefits import HEALTHSELECT_MA_13546_2026, healthselect_ma_group


def applies(result: dict, *, member_bound: bool, source_errors: object,
            adverse: object, note: object, line_blocker: object,
            same_policy_conflict: bool) -> str | None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    card = result.get('card_check') or {}
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    if not (
        benefits.get('published_plan_rule') == HEALTHSELECT_MA_13546_2026
        and healthselect_ma_group(plan, str(result.get('service_date') or ''), member_bound)
        and 'united' in str(result.get('payer') or '').casefold()
        and benefits.get('active') is True and benefits.get('service_date_matches') is not False
        and verified_in_network(benefits)
        and not benefits.get('network_conflict') and not benefits.get('coverage_status_conflict')
        and not benefits.get('coverage_dates_invalid') and not benefits.get('historical_only')
        and not source_errors and not adverse and not note
        and not same_policy_conflict and not price.get('service_issues')
        and result.get('refresh_requires_selection') is not True
        and (card.get('status') == 'conflict' and card.get('independent_ecw_check') is True or
             (card.get('status') not in {'conflict', 'multiple_cards'}
              and not card.get('differences') and card.get('cms_plan_id') in {None, 'H2001-817'}
              and card.get('patient_name_matches') is not False))
        and price.get('mode') == 'services'
        and len(lines) == len(codes)
        and benefits.get('deductible_applies') is False
    ):
        return None

    def zero_terms(line: dict) -> bool:
        terms = line.get('benefits') or {}
        return (terms.get('active') is True and terms.get('service_date_matches') is not False
                and not terms.get('source_errors') and not terms.get('coverage_status_conflict')
                and terms.get('copay_cents') == 0
                and str(terms.get('coinsurance_percent')) in {'0', '0.0', '0.00'}
                and terms.get('deductible_applies') is not True)

    if (len(lines) == 1 and lines[0].get('code') == codes[0].get('code')
            and lines[0].get('code') in {'98006', '99212', '99213', '99214', '99215'}
            and lines[0].get('units') == 1 and not lines[0].get('modifiers')
            and (str(result.get('reason') or '').casefold() in {'televisit', 'telehealth', 'virtual visit', 'video visit'}
                 or str(result.get('place_of_service') or '') in {'02', '10'})
            and (not line_blocker or line_blocker == ('service_secondary_coverage', str(lines[0].get('code'))))
            and zero_terms(lines[0])):
        return 'healthselect_ma_zero_televisit'

    if (len(lines) != 2 or price.get('conditional') is True
            or str((result.get('appointment_context') or {}).get('place_of_service')
                   or result.get('place_of_service') or '') != '11'
            or not re.search(r'\bskin\s+(?:punch\s+)?biops', str(result.get('reason') or ''), re.I)
            or {line.get('code') for line in lines} != {'99213', '11104'}
            or {code.get('id') for code in codes} != set(result.get('selected_ids') or [])
            or line_blocker not in (None, ('service_secondary_coverage', '99213'),
                                    ('service_secondary_coverage', '11104'))):
        return None
    for code, line in zip(codes, lines):
        terms = line.get('benefits') or {}
        if (line.get('id') != code.get('id') or line.get('code') != code.get('code')
                or line.get('units') != code.get('units') or line.get('units') != 1
                or line.get('modifiers') or code.get('modifiers')
                or terms.get('network_status') != 'in_network'
                or terms.get('resolved_service_type') != ('98' if line['code'] == '99213' else '2')
                or not terms.get('copay_id') or not terms.get('coinsurance_id')
                or not zero_terms(line)):
            return None
    return 'healthselect_ma_zero_office_biopsy'
