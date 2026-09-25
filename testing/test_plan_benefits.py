"""Product-specific rules must preserve member accumulators and payer overrides."""
from copy import deepcopy
from app.insurance import commercial, plan_benefits, visit_estimate


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


def test_product_rule_uses_member_remaining_visits_and_keeps_neurology_distinct():
    raw = fep_payload()
    office = plan_benefits.apply(normalize(raw), raw, '99203', '2026-09-28')
    assert office['copay_cents'] == 1000 and office['visits_remaining'] == 10
    assert office['deductible_remaining_cents'] == 0 and not office['deductible_applies']
    for code in ('95910', '95886'):
        neuro = plan_benefits.apply(normalize(raw, code), raw, code, '2026-09-28')
        assert neuro['coinsurance_percent'] == '30' and neuro['copay_cents'] is None
        assert neuro['deductible_remaining_cents'] == 30060
        assert neuro['oop_remaining_cents'] == 954481
        assert not neuro['review_reasons']
    used = fep_payload('0')
    later = plan_benefits.apply(normalize(used), used, '99203', '2026-09-28')
    assert later['cost_share'] == 'coinsurance' and later['deductible_remaining_cents'] == 30060
    bad = fep_payload('unknown')
    assert 'cost_share' not in plan_benefits.apply(normalize(bad), bad, '99203', '2026-09-28')


def test_rules_never_cross_product_year_secondary_or_override_specific_payer_evidence():
    raw = fep_payload(); value = normalize(raw)
    for date, code in [('2027-01-01', '99203'), ('2025-12-31', '99203'), ('2026-09-28', '64615')]:
        assert plan_benefits.apply(value, raw, code, date) is value
    for changes in ({'active': False}, {'has_secondary': True}):
        original = {**value, **changes}
        assert plan_benefits.apply(original, raw, '99203', '2026-09-28') is original
    other = deepcopy(raw); other['plans'][0]['name'] = 'FEP BLUE BASIC'
    assert plan_benefits.apply(value, other, '99203', '2026-09-28') is value
    raw['benefitsInformation'] = [{'code': 'B', 'compositeMedicalProcedureIdentifier': {'procedureCode': '99203'}, 'benefitAmount': '50'}]
    assert plan_benefits.apply(value, raw, '99203', '2026-09-28') is value
    broad = fep_payload(); broad['plans'][0]['benefits']['limitations'].append({'priorAuthIndicator': 'REQUIRED', 'service': {'value': '98', 'system': 'STC'}})
    assert plan_benefits.apply(value, broad, '95910', '2026-09-28') is value
    assert plan_benefits.network_match('QDTRP', {'fepbluefocus'}, 'Blue Choice PPO')
    assert not plan_benefits.network_match('OTHER', {'fepbluefocus'}, 'Blue Choice PPO')
    assert not plan_benefits.network_match('QDTRP', {'fepbluebasic'}, 'Blue Choice PPO')
    assert not plan_benefits.network_match('QDTRP', {'fepbluefocus'}, 'Blue Advantage HMO')


def test_office_and_emg_allocate_deductible_once_without_inventing_zero_insurance():
    raw = fep_payload()
    codes = [{'id': code, 'code': code, 'units': 1} for code in ('99203', '95910', '95886')]
    rates = [{'code': code, 'allowed_cents': amount} for code, amount in [('99203', 15000), ('95910', 30000), ('95886', 10000)]]
    result = visit_estimate.price_lines(codes, rates, normalize(raw), {'cost_share': 'payer_returned'}, payload=raw, service_date='2026-09-28')
    assert result['complete'] and not result['conditional']
    assert result['patient_max_cents'] == 34042  # $10 + $300.60 + 30% of $99.40
    assert result['plan_max_cents'] == 20958
    assert result['patient_max_cents'] + result['plan_max_cents'] == 55000
    assert result['lines'][0]['calculation']['plan_cents'] == 14000


def test_emg_scope_does_not_quote_more_than_four_limbs():
    raw = fep_payload()
    codes = [{'code':'95910','units':1}, {'code':'95885','units':3}, {'code':'95886','units':2}]
    result = visit_estimate.price_lines(codes, [{'code':r['code'],'allowed_cents':10000} for r in codes], normalize(raw), {'cost_share':'payer_returned'}, payload=raw, service_date='2026-09-28')
    assert not result['complete'] and not result['cash_complete']
    assert 'patient_max_cents' not in result
    assert 'four distinct limbs' in result['service_issues'][0]
