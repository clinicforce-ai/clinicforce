"""Synthetic payer-order evidence for a commercial primary office visit."""

from copy import deepcopy

from app.insurance import commercial
from app.insurance.stedi_benefits.payer_order import primary_secondary_order


DAY = '2026-11-06'


def response():
    return {'payerId': '84980', 'plans': [{'name': 'Example POS Medical', 'benefits': {
        'statuses': [{'status': 'ACTIVE_COVERAGE', 'coverageLevel': 'INDIVIDUAL',
                      'service': {'system': 'STC', 'value': '30'}}],
        'otherOrAdditionalPayer': [{'coverageLevel': 'INDIVIDUAL',
            'relatedEntities': [{'type': 'SECONDARY_PAYER', 'name': {'organization': 'MEDICARE'}}],
            'dates': {'eligibility': {'start': '2017-01-01'}},
            'service': {'system': 'STC', 'value': '30'}}],
    }}]}


def source():
    return {'coverage_records': [
        {'id': 'blue', 'status': 'active', 'order': 1, 'payer': ['Blue Cross of Texas'],
         'period': {'start': '2017-01-01'}},
        {'id': 'medicare', 'status': 'active', 'order': 2, 'payer': ['Medicare Part B'],
         'period': {'start': '2014-01-01'}},
    ]}


def test_stedi_secondary_row_and_ecw_order_establish_primary_plan():
    raw = response()
    coverage = {'coverage_id': 'blue', 'active_coverages': 2}
    assert commercial.normalize_stedi_271(raw, '98', True)['has_secondary'] is True
    assert primary_secondary_order(raw, coverage, source(), DAY) == {
        'status': 'verified', 'primary_payer_id': '84980', 'secondary_payer': 'MEDICARE',
        'evidence_paths': ['plans[0].benefits.otherOrAdditionalPayer[0]']}


def test_primary_order_rejects_wrong_secondary_or_third_policy():
    raw = response()
    coverage = {'coverage_id': 'blue', 'active_coverages': 2}
    wrong = deepcopy(source())
    wrong['coverage_records'][1]['payer'] = ['Aetna']
    assert primary_secondary_order(raw, coverage, wrong, DAY) is None
    third = deepcopy(source())
    third['coverage_records'].append({'id': 'third', 'status': 'active', 'order': 3,
                                      'payer': ['Aetna'], 'period': {'start': '2020-01-01'}})
    assert primary_secondary_order(raw, {**coverage, 'active_coverages': 3}, third, DAY) is None
    raw['plans'][0]['benefits']['otherOrAdditionalPayer'][0]['relatedEntities'][0]['type'] = 'PAYER'
    assert primary_secondary_order(raw, coverage, source(), DAY) is None
