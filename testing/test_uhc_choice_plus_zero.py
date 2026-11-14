"""Synthetic UHC office response shape; no patient details or payer payloads."""
import pytest

from app.insurance import amount_explanation, collection_guidance, commercial, visit_estimate
from app.api.routers import commercial_estimator
from app.api.routers.commercial_flow import commercial_previsit_status as status


def row(amount=None, *, stc='BY', level='INDIVIDUAL', network='IN_NETWORK', period=None,
        messages=None, **extra):
    return {'service': {'system': 'STC', 'value': stc}, 'coverageLevel': level,
            'network': {'indicator': network}, 'messages': messages or [],
            **({'amount': amount} if amount is not None else {}),
            **({'timePeriod': period} if period else {}), **extra}


def estimate(*, payer='UnitedHealthcare', plan='POS CHOICE PLUS', copay='0',
             family_oop='0', family_deductible='0', code='99203', network='in_network',
             secondary=False):
    raw = {'payerId': '87726', 'plans': [{'benefits': {
        'statuses': [row(stc='BY', status='ACTIVE_COVERAGE'),
                     row(stc='30', status='ACTIVE_COVERAGE', planCoverageDescription=plan)],
        'coPayment': [row(copay, messages=['HIGHEST BENEFIT'], period='VISIT'),
                      row('0', network='IN_AND_OUT_OF_NETWORK', period='VISIT'),
                      row('50', stc='98', messages=['Specialist'], period='VISIT')],
        'coInsurance': [row(stc='BY', percent='0', messages=['HIGHEST BENEFIT'], period='VISIT'),
                        row(stc='BY', percent='.2', period='VISIT'),
                        row(stc='98', percent='.2', messages=['Specialist'], period='VISIT')],
        'deductible': [row(family_deductible, stc='30', level='FAMILY', period='REMAINING')],
        'outOfPocket': [row(family_oop, stc='30', level='FAMILY', period='REMAINING')],
        'limitations': [row('0', messages=['HIGHEST BENEFIT'], period='VISIT')]}}]}
    benefits = commercial.normalize_stedi_271(raw, '98', False, office_visit=True,
        procedure_code=code, service_date='2026-11-12', place_of_service='11',
        office_service_types=['BQ', 'BY', '98'])
    benefits.update(network_status=network, network_source='clinic_library_contract',
                    has_secondary=secondary,
                    identity={'status': 'matched', 'binding': 'synthetic-binding', 'match_version': 2})
    price = visit_estimate.price_lines([{'id': 'visit', 'code': code, 'units': 1}], [], benefits,
        {'office_service_types': ['BQ', 'BY', '98'], 'place_of_service': '11'},
        payload=raw, service_date='2026-11-12')
    result = {'payer': payer, 'plan_details': {'stedi': benefits['plan_details']},
              'service_date': '2026-11-12', 'checked_at': '2026-11-12T15:00:00Z',
              'appointment_context': {'place_of_service': '11'},
              'benefits': benefits, 'identity': benefits['identity'], 'price_range': price,
              'payment_mode': 'insurance', 'codes': [{'id': 'visit', 'code': code, 'units': 1}]}
    return result


def test_choice_plus_office_by_copay_and_met_family_cap_give_zero_check_in():
    result = estimate()
    assert result['benefits']['copay_cents'] == 0
    assert result['benefits']['family_oop_zero_applies'] is True
    assert result['price_range']['lines'][0]['benefits']['copay_cents'] == 0
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'claim_after_service'
    assert guidance['collection_rule']['mode'] == 'uhc_choice_plus_family_oop_deferral'
    assert 'Collect $0' in amount_explanation.build(result, guidance)['sentence']


@pytest.mark.parametrize('change', [
    {'payer': 'Aetna'}, {'plan': 'OTHER PLAN'}, {'copay': '25'},
    {'family_oop': '100'}, {'family_deductible': '100'},
    {'code': '99204'}, {'network': 'unknown'}, {'secondary': True},
])
def test_choice_plus_zero_rule_does_not_apply_to_other_cases(change):
    result = estimate(**change)
    guidance = collection_guidance.build(result)
    assert (guidance.get('collection_rule') or {}).get('mode') != 'uhc_choice_plus_family_oop_deferral'


def test_choice_plus_zero_requires_patient_binding_and_clear_card():
    result = estimate()
    result['identity'] = {'status': 'review', 'binding': 'synthetic-binding', 'match_version': 2}
    assert (collection_guidance.build(result).get('collection_rule') or {}).get('mode') != 'uhc_choice_plus_family_oop_deferral'
    result = estimate()
    result['card_check'] = {'status': 'conflict'}
    assert (collection_guidance.build(result).get('collection_rule') or {}).get('mode') != 'uhc_choice_plus_family_oop_deferral'


@pytest.mark.parametrize('plan,zero_supported', [('POS CHOICE PLUS', True), ('OTHER PLAN', False)])
def test_saved_rate_change_keeps_only_matching_zero_check_in_evidence(monkeypatch, plan, zero_supported):
    result = estimate(plan=plan)
    line = result['price_range']['lines'][0]
    line.update(allowed_basis='clinic_contract', allowed_cents=10117,
                unit_allowed_cents=10117, rate_version='old')
    result.update(calculated_at='2026-11-12T12:00:00Z',
                  network_evidence={'document_id': 'synthetic-contract'})
    case = {'previsit_context': {'payer_id': '87726',
                                 'inputs': {'service_date': '2026-11-12'}, 'source': {}}}
    monkeypatch.setattr(status._api.commercial_store, 'list_connections',
        lambda *_: [{'id': 'synthetic-connection', 'stedi_payer_id': '87726',
                     'status': 'staff_guidance_enabled'}])
    monkeypatch.setattr(status, '_payer_plan_pricing_source', lambda *_: {})
    monkeypatch.setattr(status._api, '_previsit_practice_path',
        lambda *_: ({}, {'updated_at': '2026-11-11T12:00:00Z'},
                    {'updated_at': '2026-11-11T12:00:00Z'}))
    monkeypatch.setattr(status._api, '_network_contract_evidence',
        lambda *_: {'document_id': 'synthetic-contract'})
    monkeypatch.setattr(status._api.commercial_store, 'matching_rates',
        lambda *_: [{'code': '99203', 'modifier': '', 'version_id': 'new',
                     'allowed_cents': 9999}])
    status._recheck_saved_clinic_pricing(result, case, 'synthetic-practice')
    assert result['benefits']['network_status'] == 'in_network'
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    if zero_supported:
        assert collection_guidance.build(result)['amount_cents'] == 0
    else:
        assert collection_guidance.build(result)['amount_cents'] is None
