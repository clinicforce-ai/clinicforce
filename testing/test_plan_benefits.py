"""Plan names must not inject benefit values absent from payer evidence."""
from copy import deepcopy
from app.insurance import commercial, visit_estimate


def fep_payload(remaining='10'):
    scope = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}, 'service': {'value': '98', 'system': 'STC'}}
    message = ['THE FIRST TEN PROFESSIONAL MEDICAL AND MENTAL HEALTH AND SUBSTANCE USE DISORDER VISITS COMBINED']
    balance = {**scope, 'service': {'value': '30', 'system': 'STC'}, 'timePeriod': 'REMAINING'}
    return {'plans': [{'name': 'FEP BLUE FOCUS', 'benefits': {
        'statuses': [{**scope, 'status': 'ACTIVE'}],
        'deductible': [{**balance, 'amount': '300.60'}],
        'outOfPocket': [{**balance, 'amount': '9544.81'}],
        'coPayment': [{**scope, 'amount': '10', 'timePeriod': 'VISIT', 'messages': message}],
        'coInsurance': [{**scope, 'percent': '0.30', 'messages': ['PROFESSIONAL VISITS BEGINNING WITH THE ELEVENTH VISIT']}],
        'limitations': [{**scope, 'timePeriod': 'REMAINING', 'quantity': {'value': remaining, 'qualifier': 'VISITS'}, 'messages': message},
                       {**scope, 'priorAuthIndicator': 'REQUIRED', 'messages': ['APPLIED BEHAVIOR ANALYSIS FOR AUTISM SPECTRUM DISORDER']}],
    }}]}


def normalize(raw, code='99203'):
    return commercial.normalize_stedi_271(raw, '98', True, procedure_code=code, service_date='2026-09-28')


def test_plan_name_does_not_supply_missing_procedure_benefits_or_clear_conditions():
    raw = fep_payload()
    codes = [{'id': code, 'code': code, 'units': 1} for code in ('99203', '95910', '95886')]
    rates = [{'code': code, 'allowed_cents': 10000} for code in ('99203', '95910', '95886')]
    result = visit_estimate.price_lines(codes, rates, normalize(raw), {'cost_share': 'payer_returned'}, payload=raw, service_date='2026-09-28')
    assert not result['complete']
    assert 'patient_max_cents' not in result
    assert result['lines'][0]['benefits']['requires_review']
    assert all('policy_evidence' not in line['benefits'] for line in result['lines'])
    other = deepcopy(raw)
    other['plans'][0]['name'] = 'Another plan'
    comparison = visit_estimate.price_lines(codes, rates, normalize(other), {'cost_share': 'payer_returned'}, payload=other, service_date='2026-09-28')
    for first, second in zip(result['lines'], comparison['lines']):
        for key in ('copay_cents', 'coinsurance_percent', 'deductible_remaining_cents', 'authorization_signal'):
            assert first['benefits'].get(key) == second['benefits'].get(key)


def test_emg_scope_does_not_quote_more_than_four_limbs():
    raw = fep_payload()
    codes = [{'code':'95910','units':1}, {'code':'95885','units':3}, {'code':'95886','units':2}]
    result = visit_estimate.price_lines(codes, [{'code':r['code'],'allowed_cents':10000} for r in codes], normalize(raw), {'cost_share':'payer_returned'}, payload=raw, service_date='2026-09-28')
    assert not result['complete'] and not result['cash_complete']
    assert 'patient_max_cents' not in result
    assert 'four distinct limbs' in result['service_issues'][0]
