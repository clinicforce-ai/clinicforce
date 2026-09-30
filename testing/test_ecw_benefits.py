from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from app.insurance import ecw_backend, ecw_benefits, commercial, collection_guidance


def saved_response(day=None):
    def concept(name, code):
        return {'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/' + name, 'code': code}]}
    return {'resourceType': 'CoverageEligibilityResponse', 'id': 'saved-benefits',
        'status': 'active', 'outcome': 'complete', 'purpose': ['benefits'],
        'patient': {'reference': 'Patient/p'}, 'servicedDate': day or date.today().isoformat(),
        'created': datetime.now(timezone.utc).isoformat(),
        'requestor': {'identifier': {'system': 'http://hl7.org/fhir/sid/us-npi', 'value': '1234567890'}},
        'insurance': [{'coverage': {'reference': 'Coverage/coverage-1'}, 'inforce': True,
            'item': [{'category': concept('ex-benefitcategory', '98'),
                'network': concept('benefit-network', 'in'), 'unit': concept('benefit-unit', 'individual'),
                'authorizationRequired': False,
                'benefit': [{'type': concept('benefit-type', 'copay'), 'allowedMoney': {'value': 40, 'currency': 'USD'}}]}]}]}


def adapt(row):
    day = date.today().isoformat()
    summary = ecw_backend.saved_eligibility_summary([row], 'p', 'coverage-1', day)
    return ecw_benefits.payload(summary,
        {'coverage_id': 'coverage-1', 'rendering_provider': {'npi': '1234567890'}}, {'service_date': day})


def test_current_ecw_copay_adapts_without_inventing_accumulators():
    row = saved_response()
    payload = adapt(row)
    assert payload['_ecw_response'] == row
    terms = commercial.normalize_stedi_271(payload, '98', False, procedure_code='99203')
    assert terms['active'] is True and terms['copay_cents'] == 4000
    assert terms['copay_period'] == 'SERVICE'
    assert terms['deductible_remaining_cents'] is None and terms['oop_remaining_cents'] is None
    assert terms['network_status'] == 'unknown'


@pytest.mark.parametrize('change', ['stale', 'future', 'patient', 'coverage', 'provider', 'purpose', 'auth',
                                    'currency', 'negative', 'maximum', 'family', 'extension'])
def test_incomplete_or_wrong_scope_ecw_evidence_does_not_skip_stedi(change):
    row = saved_response()
    item = row['insurance'][0]['item'][0]
    benefit = item['benefit'][0]
    if change in {'stale', 'future'}:
        row['created'] = (datetime.now(timezone.utc) + timedelta(days=-2 if change == 'stale' else 1)).isoformat()
    elif change == 'patient': row['patient']['reference'] = 'Patient/other'
    elif change == 'coverage': row['insurance'][0]['coverage']['reference'] = 'Coverage/other'
    elif change == 'provider': row['requestor']['identifier']['value'] = '9999999999'
    elif change == 'purpose': row['purpose'] = ['validation']
    elif change == 'auth': item.pop('authorizationRequired')
    elif change == 'currency': benefit['allowedMoney']['currency'] = 'CAD'
    elif change == 'negative': benefit['allowedMoney']['value'] = -40
    elif change == 'maximum': benefit['type']['coding'][0]['code'] = 'copay-maximum'
    elif change == 'family': item['unit']['coding'][0]['code'] = 'family'
    elif change == 'extension': item['extension'] = [{'url': 'https://example.test/conditional'}]
    assert adapt(row) is None


def test_equally_current_conflicting_ecw_terms_require_a_new_check():
    row = saved_response()
    other = deepcopy(row)
    other['insurance'][0]['item'][0]['benefit'][0]['allowedMoney']['value'] = 80
    assert ecw_backend.saved_eligibility_summary([row, other], 'p', 'coverage-1', row['servicedDate'])['status'] == 'review'


@pytest.mark.parametrize('value', [-40, 'NaN', 'Infinity', 'not money'])
def test_invalid_payer_financial_value_is_an_explicit_error_not_a_missing_benefit(value):
    payload = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30']},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': value}]}
    terms = commercial.normalize_stedi_271(payload, '98', True)
    assert terms['source_errors'][0]['path'] == 'benefitsInformation[1].benefitAmount'
    result = collection_guidance.build({'benefits': terms, 'arrival_collection': {'amount_cents': 5000}})
    assert result['amount_cents'] == 0 and result['status'] == 'deferred_billing'
    assert 'Calculation/source error' in result['reason']


@pytest.mark.parametrize('value', [-1, float('nan'), True, '40'])
def test_invalid_calculated_amount_cannot_be_clamped_to_an_apparently_valid_zero(value):
    result = collection_guidance.build({'benefits': {'active': True}, 'arrival_collection': {'amount_cents': value}})
    assert result['amount_cents'] == 0
    assert result['source_errors'][0]['kind'] == 'invalid_calculation'
    assert 'Calculation/source error' in result['reason']


def test_other_payer_reported_only_by_stedi_still_requires_coordination():
    payload = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30']},
        {'code': 'R', 'serviceTypeCodes': ['98']},
        {'code': 'B', 'serviceTypeCodes': ['98'], 'inPlanNetworkIndicatorCode': 'Y', 'benefitAmount': '40'}]}
    terms = commercial.normalize_stedi_271(payload, '98', True, other_active_coverage=False)
    assert terms['has_secondary'] is True
    result = collection_guidance.build({'benefits': terms, 'arrival_collection': {'amount_cents': 4000}})
    assert result['status'] == 'deferred_billing'
    assert 'coordinate benefits' in result['reason']
    payload['benefitsInformation'][1]['serviceTypeCodes'] = ['35']
    assert commercial.normalize_stedi_271(payload, '98', True)['has_secondary'] is False


def test_explicit_unresolved_subscriber_is_not_replaced_with_patient():
    from app.api.routers.commercial_estimator import _coverage_snapshot
    patient = {'id': 'p', 'birthDate': '1980-01-01'}
    assert ecw_backend.coverage_subscriber({'subscriber': {'reference': 'Patient/other'},
        'relationship': {'coding': [{'code': 'self'}]}}, patient, {}) == {}
    snapshot = _coverage_snapshot({'relationship': '18', 'subscriber': {},
        'patient': {'patient_first_name': 'Pat', 'patient_last_name': 'Example'}},
        {'_patient_id': 'p', 'subscriber_evidence': {'reference': 'Patient/other'}})
    assert snapshot['subscriber_first'] is None


@pytest.mark.parametrize('malformed', [None, 'unreadable', {'category': []}, {'category': {'coding': [None]}}])
def test_malformed_optional_fhir_item_uses_payer_fallback(malformed):
    row = saved_response()
    row['insurance'][0]['item'] = [malformed]
    assert adapt(row) is None


def test_fhir_qualifications_and_additional_policy_cannot_be_dropped():
    from app.api.routers.commercial_estimator import _coverage_snapshot, _previsit_benefits
    row = saved_response()
    row['insurance'].append({'coverage': {'reference': 'Coverage/secondary'}, 'inforce': True})
    assert adapt(row) is None
    saved = ecw_backend.saved_eligibility_summary([row], 'p', 'coverage-1', row['servicedDate'])
    coverage = _coverage_snapshot({}, {'coverage_id': 'coverage-1', 'active_coverages': 1, 'saved_eligibility': saved})
    assert _previsit_benefits({}, coverage)['has_secondary'] is True
    row = saved_response()
    row['modifierExtension'] = [{'url': 'https://example.test/restriction'}]
    assert adapt(row) is None


def test_secondary_benefit_evidence_does_not_change_the_ecw_visit_identity_token():
    from app.api.routers.commercial_estimator import _visit_context_token
    source = {'coverage_id': 'primary', 'active_coverages': 1}
    token = _visit_context_token({}, source)
    source['saved_eligibility'] = {'_response': {'insurance': [
        {'coverage': {'reference': 'Coverage/secondary'}, 'inforce': True}]}}
    assert _visit_context_token({}, source) == token
