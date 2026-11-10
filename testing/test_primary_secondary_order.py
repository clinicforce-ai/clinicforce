"""Synthetic payer-order evidence for a commercial primary office visit."""

from copy import deepcopy

from app.insurance import commercial
from app.api.routers import commercial_estimator as api
from app.insurance.stedi_benefits.payer_order import primary_secondary_order, reported_medicare_part_b_secondary
from app.insurance.identity import compare, patient_benefits_bound


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


def test_mbi_only_other_payer_row_does_not_create_secondary_medical_policy():
    raw = response()
    raw['plans'][0]['benefits']['otherOrAdditionalPayer'] = [
        {'coverageLevel': 'INDIVIDUAL', 'additionalInformation': {'mbi': '1EG4TE5MK73'}}]
    assert commercial.normalize_stedi_271(raw, '98', True)['has_secondary'] is False
    raw['plans'][0]['benefits']['otherOrAdditionalPayer'][0]['relatedEntities'] = [
        {'type': 'SECONDARY_PAYER', 'name': {'organization': 'MEDICARE'}}]
    assert commercial.normalize_stedi_271(raw, '98', True)['has_secondary'] is True


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


def test_part_b_report_supports_zero_advance_with_three_policies_and_dependent():
    raw = response()
    rows = raw['plans'][0]['benefits']['otherOrAdditionalPayer']
    rows[0]['relatedEntities'][0]['name']['organization'] = 'MEDICARE A'
    rows.append(deepcopy(rows[0]))
    rows[1]['relatedEntities'][0]['name']['organization'] = 'MEDICARE B'
    raw['subscriber'] = {'memberId': 'SYN123', 'firstName': 'Alex', 'lastName': 'Example'}
    raw['dependent'] = {'firstName': 'Robin', 'lastName': 'Example',
                        'dateOfBirth': '1989-03-23', 'relationToSubscriberCode': '01'}
    coverage = {'coverage_id': 'blue', 'active_coverages': 3, 'member_id': 'SYN123',
                'patient_first': 'Robin', 'patient_last': 'Example',
                'patient_birth_date': '1989-03-23', 'subscriber_first': 'Robin',
                'subscriber_last': 'Example', 'relationship': '18'}
    comparison = compare(raw, coverage)
    assert comparison['status'] == 'review'
    benefits = api._previsit_benefits(raw, coverage, DAY, ['98', '30'], source=source())
    assert benefits['identity']['reconciliation_rule'] == 'payer_matched_patient'
    assert patient_benefits_bound(benefits['identity'])
    assert benefits['medicare_part_b_secondary']['status'] == 'reported'
    assert primary_secondary_order(raw, coverage, source(), DAY) is None
    assert reported_medicare_part_b_secondary(raw, DAY) == {
        'status': 'reported', 'payer': 'Original Medicare Part B',
        'evidence_paths': ['plans[0].benefits.otherOrAdditionalPayer[1]']}
    assert reported_medicare_part_b_secondary(raw, '2016-01-01') is None
    rows[1]['relatedEntities'][0]['name']['organization'] = 'Other Plan'
    assert reported_medicare_part_b_secondary(raw, DAY) is None
