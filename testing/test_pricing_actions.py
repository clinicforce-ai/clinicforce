from app.insurance.collection.pricing_actions import priced_payer_gap


def test_priced_visit_missing_both_balances_asks_for_both():
    result = {'price_range': {'lines': [{
        'code': '99203', 'allowed_cents': 10117, 'calculation': None,
        'issues': [
            'Confirm the remaining individual deductible that applies to this visit.',
            'Confirm the remaining individual out-of-pocket maximum.',
        ],
    }]}}

    reason, code, action = priced_payer_gap(result, 'BCBSTX')
    assert code == 'cost_share_term_unresolved'
    assert 'deductible and out-of-pocket balances' in reason
    assert 'obtain both balances from BCBSTX for CPT 99203' in action
