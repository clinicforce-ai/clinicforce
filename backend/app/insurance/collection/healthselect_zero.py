"""Guard the HealthSelect MA zero-arrival virtual visit instruction."""

from app.insurance.network_participation import verified_in_network
from app.insurance.published_plan_benefits import HEALTHSELECT_MA_13546_2026, healthselect_ma_group


def applies(result: dict, *, member_bound: bool, source_errors: object,
            adverse: object, note: object, line_blocker: object,
            same_policy_conflict: bool) -> bool:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    card = result.get('card_check') or {}
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    visit_terms = (lines[0].get('benefits') or {}) if len(lines) == 1 else {}
    return bool(
        benefits.get('published_plan_rule') == HEALTHSELECT_MA_13546_2026
        and healthselect_ma_group(plan, str(result.get('service_date') or ''), member_bound)
        and 'united' in str(result.get('payer') or '').casefold()
        and benefits.get('active') is True and benefits.get('service_date_matches') is not False
        and verified_in_network(benefits)
        and not benefits.get('network_conflict') and not benefits.get('coverage_status_conflict')
        and not benefits.get('coverage_dates_invalid') and not benefits.get('historical_only')
        and not source_errors and not adverse and not note
        and (not line_blocker or line_blocker == ('service_secondary_coverage', str(lines[0].get('code'))))
        and not same_policy_conflict and not price.get('service_issues')
        and result.get('refresh_requires_selection') is not True
        and card.get('status') not in {'conflict', 'multiple_cards'}
        and not card.get('differences') and card.get('cms_plan_id') in {None, 'H2001-817'}
        and card.get('patient_name_matches') is not False
        and price.get('mode') == 'services'
        and len(lines) == len(result.get('codes') or []) == 1
        and lines[0].get('code') == result['codes'][0].get('code')
        and lines[0].get('code') in {'98006', '99212', '99213', '99214', '99215'}
        and lines[0].get('units') == 1 and not lines[0].get('modifiers')
        and (str(result.get('reason') or '').casefold() in {'televisit', 'telehealth', 'virtual visit', 'video visit'}
             or str(result.get('place_of_service') or '') in {'02', '10'})
        and visit_terms.get('active') is True and visit_terms.get('service_date_matches') is not False
        and not visit_terms.get('source_errors') and not visit_terms.get('coverage_status_conflict')
        and visit_terms.get('copay_cents') == 0
        and str(visit_terms.get('coinsurance_percent')) in {'0', '0.0', '0.00'}
        and visit_terms.get('deductible_applies') is not True
        and benefits.get('deductible_applies') is False
    )
