"""Synthetic UHC office response shape; no patient details or payer payloads."""
import pytest

from app.insurance import amount_explanation, collection_guidance, commercial, encounter_summary, visit_estimate
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
             secondary=False, mixed_vision=False):
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
    if mixed_vision:
        terms = raw['plans'][0]['benefits']
        terms['statuses'].append(row(stc='30', status='ACTIVE_COVERAGE',
                                     planCoverageDescription='SPECTERA VISION PLAN'))
        terms['coPayment'].append(row('0', stc='96', messages=['SPECIALIST'], period='VISIT'))
        terms['deductible'].extend([
            row('3600', stc='30', level='FAMILY', period='CALENDAR_YEAR'),
            row('580.47', stc='30', period='REMAINING')])
        terms['outOfPocket'].extend([
            row('0', stc='30', period='REMAINING'),
            row('5400', stc='30', period='CALENDAR_YEAR'),
            row('10800', stc='30', level='FAMILY', period='CALENDAR_YEAR')])
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


@pytest.mark.parametrize('checked_at', ['2026-11-11T15:00:00Z', '2026-11-12T15:00:00Z'])
def test_choice_plus_followup_with_mixed_vision_rows_defers_check_in(checked_at):
    result = estimate(code='99213', mixed_vision=True)
    assert result['benefits']['oop_remaining_cents'] is None
    assert result['benefits']['family_oop_zero_applies'] is False
    result['checked_at'] = checked_at
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'claim_after_service'
    assert guidance['collection_rule']['mode'] == 'uhc_choice_plus_family_oop_deferral'
    assert '99213' in guidance['reason']
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None


@pytest.mark.parametrize('change', [
    'positive_individual_oop', 'positive_family_oop', 'positive_family_deductible',
    'positive_office_copay',
    'positive_specialist_copay', 'unverified_network', 'stale_check',
    'wrong_product', 'different_plan_row', 'secondary_coverage', 'card_conflict', 'second_service',
])
def test_choice_plus_followup_deferral_requires_matching_evidence(change):
    result = estimate(code='99213', mixed_vision=True)
    benefits = result['benefits']
    if change == 'positive_individual_oop':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'out_of_pocket'
             and row.get('coverage_level') == 'INDIVIDUAL' and row.get('time_period') == 'REMAINING'
             and row.get('network') == 'IN_NETWORK')['amount_cents'] = 100
    elif change == 'positive_family_oop':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'out_of_pocket'
             and row.get('coverage_level') == 'FAMILY' and row.get('time_period') == 'REMAINING'
             and row.get('network') == 'IN_NETWORK')['amount_cents'] = 100
    elif change == 'positive_family_deductible':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'deductible'
             and row.get('coverage_level') == 'FAMILY' and row.get('time_period') == 'REMAINING'
             and row.get('network') == 'IN_NETWORK')['amount_cents'] = 100
    elif change == 'positive_office_copay':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'copay'
             and row.get('service_type_code') == 'BY' and row.get('network') == 'IN_NETWORK')['amount_cents'] = 100
    elif change == 'positive_specialist_copay':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'copay'
             and row.get('service_type_code') == '96' and row.get('network') == 'IN_NETWORK')['amount_cents'] = 100
    elif change == 'unverified_network':
        benefits['network_status'] = 'unknown'
    elif change == 'stale_check':
        result['checked_at'] = '2026-11-09T15:00:00Z'
    elif change == 'wrong_product':
        result['plan_details']['stedi']['names'] = ['OTHER PLAN']
    elif change == 'different_plan_row':
        next(row for row in benefits['reported_options'] if row.get('kind') == 'out_of_pocket'
             and row.get('coverage_level') == 'INDIVIDUAL' and row.get('time_period') == 'REMAINING'
             and row.get('network') == 'IN_NETWORK')['plan_index'] = 1
    elif change == 'secondary_coverage':
        benefits['has_secondary'] = True
    elif change == 'card_conflict':
        result['card_check'] = {'status': 'conflict'}
    elif change == 'second_service':
        result['price_range']['lines'].append({'id': 'extra', 'code': '95908', 'units': 1})
    assert collection_guidance.build(result)['amount_cents'] is None


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


def test_choice_plus_unassigned_individual_oop_zero_defers_unpriced_office_visit():
    result = estimate(plan='UNITEDHEALTHCARE CHOICE PLUS', code='99214', family_oop='3500',
                      family_deductible='500')
    result['benefits'].update(oop_remaining_cents=None, oop_id=None, copay_cents=None,
        reported_options=[{'id': 'synthetic-oop', 'kind': 'out_of_pocket',
                           'service_type_code': '30', 'coverage_level': 'INDIVIDUAL',
                           'time_period': 'REMAINING', 'network': 'IN_NETWORK',
                           'plan_association': 'unassigned', 'amount_cents': 0},
                          {'id': 'family-deductible', 'kind': 'deductible',
                           'service_type_code': '30', 'coverage_level': 'FAMILY',
                           'time_period': 'REMAINING', 'network': 'IN_NETWORK',
                           'amount_cents': 50000},
                          {'id': 'family-oop', 'kind': 'out_of_pocket',
                           'service_type_code': '30', 'coverage_level': 'FAMILY',
                           'time_period': 'REMAINING', 'network': 'IN_NETWORK',
                           'amount_cents': 350000}])
    result['price_range'].update(complete=False, patient_min_cents=None, patient_max_cents=None)
    result['price_range']['lines'][0].update(allowed_cents=None, calculation=None,
        rate_resolution={'status': 'RATE_UNAVAILABLE'})
    result['price_range']['lines'][0]['benefits']['copay_cents'] = None
    result.update(service_selection_status='preview', selected_ids=['visit'],
        service_plan={'recommended_ids': ['visit']})
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'claim_after_service'
    assert guidance['collection_rule']['mode'] == 'uhc_choice_plus_unassigned_oop_deferral'
    assert guidance['services'][0]['patient_cents'] is None
    assert encounter_summary.build({**result, 'collection_guidance': guidance})['collect_today_cents'] == 0
    assert 'Collect $0' in amount_explanation.build(result, guidance)['sentence']
    assert collection_guidance.build({**result, 'checked_at': '2026-11-10T15:00:00Z'})['amount_cents'] == 0
    for change in ({'service_date_matches': False}, {'has_secondary': True},
                   {'active': False}, {'network_status': 'unknown'}):
        altered = {**result, 'benefits': {**result['benefits'], **change}}
        assert collection_guidance.build(altered)['amount_cents'] is None
    assert collection_guidance.build({**result, 'card_check': {'status': 'conflict'}})['amount_cents'] is None
    assert collection_guidance.build({**result, 'plan_details': {'stedi': {'names': ['OTHER PLAN']}}})['amount_cents'] is None
    assert collection_guidance.build({**result, 'checked_at': '2026-11-09T15:00:00Z'})['amount_cents'] is None
    assert collection_guidance.build({**result, 'benefits': {**result['benefits'], 'copay_cents': 2500}})['amount_cents'] is None
    no_family = {**result, 'benefits': {**result['benefits'], 'reported_options':
        result['benefits']['reported_options'][:1]}}
    assert collection_guidance.build(no_family)['amount_cents'] is None
    conflicting = {**result, 'benefits': {**result['benefits'], 'reported_options': [
        *result['benefits']['reported_options'],
        {**result['benefits']['reported_options'][0], 'id': 'other-oop', 'amount_cents': 2500}]}}
    assert collection_guidance.build(conflicting)['amount_cents'] is None


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
