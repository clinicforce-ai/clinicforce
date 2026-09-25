"""Pre-service answers: synthetic payer terms and two independent clinic catalogs."""
from unittest.mock import patch

from app.api.routers import commercial_estimator as api
from app.insurance import commercial, stedi, visit_estimate
from test_visit_estimate import DAY, payload, priced


def test_both_stedi_builders_send_service_date_as_an_object():
    body, error = stedi.request_body({
        'payer_id': 'payer-b', 'provider': {'npi': '1234567890'}, 'service_date': '2026-10-01',
        'relationship': '18', 'subscriber': {'memberId': 'synthetic', 'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'},
    }, {'stedi_payer_ids': {'payer-b': 'another-payer'}, 'providers': {'1234567890': {'stedi_organization_name': 'Another Clinic'}}})
    assert error is None
    assert body['encounter']['dates'] == {'service': {'start': '2026-10-01'}}


def test_modern_plan_name_and_noncovered_service_are_not_lost():
    raw = {'plans': [{'name': 'Example Focus Product', 'benefits': {
        'statuses': [{'status': 'Active', 'service': {'system': 'STC', 'value': '30'}}],
        'nonCovered': [{'service': {'system': 'CPT', 'value': '95885'}}],
    }}]}
    assert commercial.plan_details(raw)['names'] == ['Example Focus Product']
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='95885')['active'] is False
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203')['active'] is True


def test_missing_service_terms_never_invent_a_deductible_only_price():
    raw = payload()
    raw['benefitsInformation'][1]['benefitAmount'] = '750'
    raw['benefitsInformation'][2]['benefitAmount'] = '9995'
    raw['benefitsInformation'][3]['additionalInformation'] = [{'description': 'Beginning with the eleventh visit'}]
    raw['benefitsInformation'].append({**raw['benefitsInformation'][3], 'benefitPercent': '.30'})
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    code = {'id': 'option-a', 'code': '95907', 'units': 1}
    result = visit_estimate.price_lines([code], [{'code': '95907', 'allowed_cents': 8440}], benefits,
        {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    assert not result['complete']
    assert 'patient_max_cents' not in result and 'plan_max_cents' not in result
    assert result['collection']['amount_cents'] is None
    line = result['lines'][0]
    assert line['calculation'] is None
    assert not any('Assumes this covered service' in condition for condition in line['conditions'])
    assert line['benefits']['coinsurance_percent'] is None  # no invented payer fact
    for changes in ({'network_status': 'unknown'}, {'has_secondary': True}, {'active': False},
                    {'review_reasons': ['Confirm the member identity: the payer response differs from eCW.']},
                    {'review_reasons': ['Saved benefit evidence could not be verified.']}):
        unsafe = visit_estimate.price_lines([code], [{'code': '95907', 'allowed_cents': 8440}],
            {**benefits, **changes}, {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
        assert not unsafe['complete']
    # More services consume the shared deductible; do not quote a made-up percentage
    # once the balance can no longer cover a later line.
    many = visit_estimate.price_lines([code, {**code, 'id': 'b', 'units': 10}],
        [{'code': '95907', 'allowed_cents': 8440}], benefits, {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    assert not many['complete'] and 'patient_max_cents' not in many


def test_exhausted_oop_produces_zero_without_inventing_missing_benefits():
    raw = payload()
    raw['benefitsInformation'][2]['benefitAmount'] = '0'
    raw['benefitsInformation'] = raw['benefitsInformation'][:1] + raw['benefitsInformation'][2:3]
    result = priced(raw)
    assert result['complete'] and result['patient_max_cents'] == 0
    assert result['plan_max_cents'] == result['allowed_max_cents'] == 20000
    assert result['lines'][0]['benefits']['deductible_remaining_cents'] is None
    assert result['conditional'] and result['collection']['amount_cents'] is None  # referral unknown
    raw['benefitsInformation'].append({'code': 'I', 'serviceTypeCodes': ['98']})
    assert not priced(raw)['complete']


def test_contract_options_come_from_scoped_clinic_configuration():
    for clinic, payer, code, amount in [('clinic-a', 'payer-a', '95907', 8440), ('clinic-b', 'payer-b', '93000', 6123)]:
        inputs = {'service_date': DAY, 'reason': 'Planned procedure', 'lines': [], 'member_id': 'M123', 'place_of_service': '11'}
        source = {'plan': 'Choice', 'active_coverages': 1}
        result = {'codes': [], 'benefits': commercial.normalize_stedi_271(payload(), '98', True), 'issues': []}
        rates = [{'code': code, 'allowed_cents': amount, 'description': 'Clinic procedure'}]
        with patch.object(api.practice_prices, 'current', return_value=None), patch.object(api.commercial_store, 'list_connections', return_value=[{'id': payer, 'stedi_payer_id': payer, 'status': 'staff_guidance_enabled'}]), \
             patch.object(api, '_practice_path', return_value=({}, {'plan_matcher': 'Choice'}, {'rules': {'cost_share': 'payer_returned', 'service_type_by_code': {code: '30'}}})), \
             patch.object(api.commercial_store, 'matching_rates', return_value=rates) as matched:
            api._finish_previsit(result, {}, inputs, source, payload(), {'practice_id': clinic}, payer)
        assert all(call.args[0] == clinic for call in matched.call_args_list)
        assert matched.call_args_list[0].args[-1] is None
        assert result['codes'][0]['code'] == code
        assert result['price_range']['mode'] == 'options'
        assert result['price_range']['lines'][0]['allowed_cents'] == amount
        assert result['price_range']['collection']['action'] == 'choose_services'


def test_modern_out_of_network_noncoverage_and_named_pa_do_not_override_visit():
    raw = {'plans': [{'name': 'Example Focus', 'benefits': {
        'statuses': [{'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '30'},
                      'dates': {'plan': {'start': '2021-01-01', 'end': '2028-01-01'}}}],
        'nonCovered': [{'network': {'indicator': 'OUT_OF_NETWORK'}, 'service': {'system': 'STC', 'value': '98'}}],
        'limitations': [{'network': {'indicator': 'IN_NETWORK'}, 'priorAuthIndicator': 'REQUIRED',
            'service': {'system': 'STC', 'value': '98'}, 'messages': ['APPLIED BEHAVIOR ANALYSIS'],
            'timePeriod': 'REMAINING', 'quantity': {'value': '200', 'qualifier': 'HOURS'}}],
    }}]}
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='95886', service_date='2026-09-28')
    assert benefits['active'] is True
    assert benefits['authorization_signal'] == 'unknown'
    assert 'named benefit' in benefits['authorization_basis']
    assert benefits['reported_options'][0]['quantity'] == {'value': '200', 'qualifier': 'HOURS'}
    expired = commercial.normalize_stedi_271(raw, '98', True, procedure_code='95886', service_date='2029-01-01')
    assert expired['active'] is None
    raw['plans'][0]['benefits']['limitations'][0]['service'] = {'system': 'CPT', 'value': '95886'}
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='95886')['authorization_signal'] == 'required'
