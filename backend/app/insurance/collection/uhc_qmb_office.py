"""Zero arrival collection for the matched 2026 Texas UHC QMB office product."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.published_plan_benefits import UHC_TX_QMB_H4514_024_2026


def apply(result: dict, guidance: dict, amount: int | None, unresolved_order: bool,
          same_policy_conflict: bool, line_blocker: object, source_errors: list) -> int | None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    matched_office = (
        benefits.get('published_plan_rule') == UHC_TX_QMB_H4514_024_2026
        and benefits.get('active') is True and benefits.get('service_date_matches') is not False
        and patient_benefits_bound(identity)
        and not benefits.get('has_secondary') and not unresolved_order
        and not same_policy_conflict and not benefits.get('network_conflict')
        and not benefits.get('coverage_status_conflict')
        and not benefits.get('coverage_dates_invalid')
        and not benefits.get('historical_only') and not source_errors
        and not line_blocker and not price.get('service_issues')
        and result.get('refresh_requires_selection') is not True
        and price.get('mode') == 'services' and len(lines) == 1
        and (not result.get('selected_ids') or result['selected_ids'] == [lines[0].get('id')])
        and lines[0].get('code') == '99213' and lines[0].get('units') == 1
        and (lines[0].get('benefits') or {}).get('active') is True
        and (lines[0].get('benefits') or {}).get('copay_cents') == 0
    )
    if matched_office:
        guidance.update(status='zero_advance', basis='published_uhc_tx_qmb_office',
            reason='The matched 2026 UHC Dual Complete QMB plan publishes a $0 specialist office visit. QMB billing protection prohibits collecting Medicare cost sharing for covered care. Collect $0 for the proposed CPT 99213 visit; confirm the referral, provider participation and final billed services.')
        return 0
    return amount
