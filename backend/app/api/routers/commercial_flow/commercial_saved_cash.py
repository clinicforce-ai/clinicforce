"""Reprice older saved visits whose completed empty eCW read was left in Insurance mode."""
from app.insurance import ecw_billing, practice_prices


def restore_empty_coverage_cash(result: dict, case: dict, practice_id: str | None) -> None:
    source = case['previsit_context'].get('source') or {}
    review = case.get('visit_review') or {}
    if (not practice_id or review.get('payment_mode') is not None
            or result.get('payment_mode') != 'insurance'
            or not ecw_billing.cash_without_coverage(source)
            or result.get('checked_at') or case.get('previsit_discovery')
            or (result.get('benefits') or {}).get('active') is True
            or (result.get('reference_benefits') or {}).get('active') is True):
        return
    result['payment_mode'] = 'cash'
    for key in ('cash_quote', 'cash_schedule', 'practice_cash_source'):
        result.pop(key, None)
    price = result.setdefault('price_range', {})
    price.update(cash_complete=False, cash_min_cents=None, cash_max_cents=None)
    if (price.get('lines') and isinstance(result.get('available_codes'), list)
            and (schedule := practice_prices.current(practice_id, result['service_date']))):
        practice_prices.apply(result, schedule, review)
    practice_prices.apply_visit_quote(result, review)
