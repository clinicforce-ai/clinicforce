import pytest
from pydantic import ValidationError

from app.api.schemas.commercial import VisitReviewInput
from app.insurance.collection.cash import build as cash_guidance
from app.insurance.pricing.cash import apply_visit_quote


def _visit():
    line = {'id': 'visit:99213', 'code': '99213', 'units': 1, 'modifiers': []}
    return {'payment_mode': 'cash', 'context_token': 'a' * 64, 'service_date': '2026-10-27',
            'benefits': {'active': False},
            'price_range': {'mode': 'services', 'lines': [line], 'service_issues': []}}


def _review():
    return {'cash_visit_quote': {'line_id': 'visit:99213', 'code': '99213', 'units': 1,
            'amount_cents': 15000, 'reason': 'inactive general medical coverage',
            'service_date': '2026-10-27', 'context_token': 'a' * 64,
            'approved_at': '2026-10-26T12:00:00+00:00', 'actor_id': 'clinician'}}


def test_clinician_quote_prices_only_selected_follow_up():
    result = _visit()
    review = _review()
    apply_visit_quote(result, review)
    assert result['price_range']['cash_min_cents'] == 15000
    assert result['price_range']['lines'][0]['cash_source'] == 'Clinician-approved visit quote'
    assert result['cash_quote']['reason'] == 'inactive general medical coverage'
    guidance = cash_guidance(result, review, {'services': []}, result['price_range'], result['price_range']['lines'])
    assert guidance['amount_cents'] == 15000
    assert guidance['reason'] == 'inactive general medical coverage'


def test_quote_does_not_price_changed_or_insured_visit():
    for change in ({'context_token': 'b' * 64}, {'payment_mode': 'insurance'}):
        result = {**_visit(), **change}
        apply_visit_quote(result, _review())
        assert 'cash_quote' not in result
    result = _visit()
    result['price_range']['lines'].append({'id': 'other', 'code': '95910', 'units': 1})
    apply_visit_quote(result, _review())
    assert 'cash_quote' not in result


def test_quote_input_requires_positive_whole_cents_and_reason():
    valid = {'context_token': 'a' * 64, 'revision': 0,
             'cash_visit_quote': {'amount_cents': 15000, 'reason': 'inactive general medical coverage'}}
    assert VisitReviewInput.model_validate(valid).cash_visit_quote.amount_cents == 15000
    for bad in (0, 150.5, '15000'):
        with pytest.raises(ValidationError):
            VisitReviewInput.model_validate({**valid, 'cash_visit_quote': {**valid['cash_visit_quote'], 'amount_cents': bad}})
