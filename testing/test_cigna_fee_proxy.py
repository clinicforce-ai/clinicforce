"""Synthetic exact-CPT Cigna benefit and BCBS Texas planning-rate checks."""

from copy import deepcopy

from app.insurance import blue_cross_proxy, stedi_cpt_authorization


def _result():
    binding = 'synthetic-current-binding'
    lines = [{'id': 'ncs', 'code': '95908', 'units': 1, 'modifiers': []},
             {'id': 'emg', 'code': '95886', 'units': 2, 'modifiers': []}]
    return {'payer': 'Cigna', 'payment_mode': 'insurance', 'codes': lines,
            'identity': {'status': 'matched', 'binding': binding, 'match_version': 2},
            'benefits': {'active': True, 'service_date_matches': True,
                         'network_status': 'in_network'},
            'price_range': {'mode': 'services', 'lines': lines, 'service_issues': []},
            'temporary_rate_reference': {'complete': True, 'source': 'BCBS Texas clinic schedule',
                'lines': [{'id': 'ncs', 'code': '95908', 'units': 1, 'modifiers': [],
                           'unit_reference_cents': 10511, 'source_reference': 'library:synthetic'},
                          {'id': 'emg', 'code': '95886', 'units': 2, 'modifiers': [],
                           'unit_reference_cents': 8918, 'source_reference': 'library:synthetic'}]},
            'cpt_authorization': {'state': 'complete', 'service_date': '2026-10-27',
                'pos': '11', 'network': 'in_network', 'patient_binding': binding,
                'financial_terms': {code: {'coinsurance_percent': '20.0',
                    'copay_cents': None, 'deductible_remaining_cents': 0,
                    'oop_remaining_cents': 375000} for code in ('95908', '95886')}}}


def test_cigna_exact_cpt_fee_proxy_is_provisional_and_rounded_per_line():
    value = blue_cross_proxy.cigna_estimate(_result(), '2026-10-27')
    assert value['basis'] == 'bcbstx_cigna_fee_proxy'
    assert value['reference_total_cents'] == 28347
    assert value['coinsurance_cents'] == value['patient_share_cents'] == 5669
    assert [row['coinsurance_cents'] for row in value['lines']] == [2102, 3567]
    assert value['copay_unconfirmed'] is True


def test_cigna_proxy_withholds_on_missing_exact_terms_or_unmatched_scope():
    for change in (
        lambda result: result['cpt_authorization']['financial_terms']['95886'].pop('coinsurance_percent'),
        lambda result: result['cpt_authorization']['financial_terms']['95886'].update(copay_cents=2500),
        lambda result: result['cpt_authorization']['financial_terms']['95886'].update(copay_unresolved=True),
        lambda result: result['cpt_authorization'].update(service_date='2026-10-28'),
        lambda result: result['temporary_rate_reference']['lines'][1].update(code='95885'),
        lambda result: result['benefits'].update(oop_remaining_cents=1000),
        lambda result: result['codes'][0].update(code='99213'),
    ):
        result = deepcopy(_result())
        change(result)
        assert blue_cross_proxy.cigna_estimate(result, '2026-10-27') is None


def test_cigna_proxy_caps_provisional_share_at_exact_cpt_oop_balance():
    result = _result()
    for terms in result['cpt_authorization']['financial_terms'].values():
        terms['oop_remaining_cents'] = 1000
    value = blue_cross_proxy.cigna_estimate(result, '2026-10-27')
    assert value['coinsurance_cents'] == 5669
    assert value['oop_reduction_cents'] == 4669
    assert value['patient_share_cents'] == 1000


def test_exact_cpt_financial_terms_do_not_inherit_unrelated_copay():
    def row(code, value, *, period=None):
        return {'service': {'system': 'CPT', 'value': code},
                'network': {'indicator': 'IN_NETWORK'},
                'placesOfService': [{'code': '11'}], 'coverageLevel': 'INDIVIDUAL',
                **({'timePeriod': period} if period else {}), **value}
    broad_pos_coinsurance = row('95908', {'percent': 0.2, 'priorAuthIndicator': 'NOT_REQUIRED'})
    broad_pos_coinsurance.pop('placesOfService')
    payload = {'plans': [{'benefits': {
        'coInsurance': [broad_pos_coinsurance],
        'coPayment': [row('99213', {'amount': '25'})],
        'deductible': [row('95908', {'amount': '0'}, period='REMAINING')],
        'outOfPocket': [row('95908', {'amount': '3750'}, period='REMAINING')],
    }}]}
    assert stedi_cpt_authorization.financial_terms(payload, '95908', '11', 'in_network') == {
        'coinsurance_percent': '20.0', 'copay_cents': None, 'copay_unresolved': False,
        'deductible_remaining_cents': 0, 'oop_remaining_cents': 375000}
    assert stedi_cpt_authorization.decisions(payload, ['95908'], '11', 'in_network') == {'95908': 'Not required'}
