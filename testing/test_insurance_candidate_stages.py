"""Partial insurance evidence must survive without authorizing an insured price."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.routers import commercial_estimator as api
from app.insurance import stedi_discovery as discovery, stedi_payers, ecw_backend, commercial
from test_stedi_discovery import setup


def dependent_case(address=False):
    case, session, provider, item = setup()
    context = case['previsit_context']
    context['inputs'].update(member_id=None, relationship=None, subscriber={})
    context['source'].update(active_coverages=0, coverage_id=None, payer=None)
    context['payer_id'] = None
    item['dependent'] = {key: value for key, value in item['subscriber'].items() if key != 'memberId'}
    if not address:
        item['dependent'].pop('address')
    item['dependent']['relationToSubscriberCode'] = '19'
    item['subscriber'] = {'firstName': 'Parent', 'lastName': 'Example', 'memberId': 'SYNTHETIC-FAMILY'}
    return case, session, provider, item


def test_partial_dependent_is_retained_but_not_adopted_without_corroboration():
    case, _, _, item = dependent_case()
    context = case['previsit_context']
    parsed = discovery.parse_discovery_candidate(item)
    assert parsed['member_id'] == 'SYNTHETIC-FAMILY'
    assert parsed['subscriber']['dateOfBirth'] is None
    assert parsed['dependent']['dateOfBirth'] == '19800101'
    assert parsed['coverage_dates'] and parsed['source'] == 'stedi_discovery'
    rows = discovery.candidates({'items': [item]}, context['inputs'], context['source'])
    assert len(rows) == 1 and rows[0]['state'] == 'CANDIDATE_INCOMPLETE'
    assert rows[0]['verification_status'] == 'REVIEW_REQUIRED'
    assert {'SUBSCRIBER_DOB_MISSING', 'ADDITIONAL_IDENTITY_EVIDENCE_REQUIRED'} <= set(rows[0]['reason_codes'])
    assert not discovery.automatic_match(item, context['inputs'], context['source'])


def test_dependent_independent_and_ecw_verification_paths_keep_unknown_subscriber_dob():
    case, _, provider, item = dependent_case(address=True)
    context = case['previsit_context']; inputs, source = context['inputs'], context['source']
    assert discovery.verify_candidate(item, inputs, source)['pathway'] == 'independent_discovery'
    assert discovery.automatic_match(item, inputs, source)
    coverage = api._discovered_coverage(inputs, source, item)
    body = api.stedi_request_body({'stedi_payer_id': 'EXAMPLE'}, provider, coverage, inputs['service_date'], '98')
    assert 'dateOfBirth' not in body['subscriber']
    assert body['dependent']['dateOfBirth'] == '1980-01-01'
    item['dependent'].pop('address')
    inputs.update(member_id='SYNTHETIC-FAMILY', relationship='19',
                  subscriber={'firstName': 'Parent', 'lastName': 'Example'})
    source.update(active_coverages=1, coverage_id='coverage', payer_identifiers=['example'])
    assert discovery.verify_candidate(item, inputs, source)['pathway'] == 'ecw_policy'
    assert discovery.automatic_match(item, inputs, source)


@pytest.mark.parametrize('change,reason', [
    ('dob', 'PATIENT_DOB_MISMATCH'), ('name', 'PATIENT_NAME_MISMATCH'),
    ('dates', 'COVERAGE_DATE_NOT_VERIFIED'), ('relationship', 'RELATIONSHIP_CONFLICT'),
    ('member', 'MEMBER_ID_MISSING'), ('route', 'PAYER_ROUTE_MISSING'),
])
def test_failed_verification_preserves_candidate_without_authorizing_it(change, reason):
    case, _, _, item = dependent_case(address=True)
    context = case['previsit_context']
    if change == 'dob': item['dependent']['dateOfBirth'] = '19810101'
    if change == 'name': item['dependent']['firstName'] = 'Unrelated'
    if change == 'dates': item.pop('planDateInformation')
    if change == 'relationship': item['dependent']['relationToSubscriberCode'] = '18'
    if change == 'member': item['subscriber'].pop('memberId')
    if change == 'route': item['payer'].pop('payorIdentification')
    row = discovery.candidates({'items': [item]}, context['inputs'], context['source'])[0]
    assert row['verification_status'] == 'REVIEW_REQUIRED' and reason in row['reason_codes']
    assert not discovery.automatic_match(item, context['inputs'], context['source'])


def test_exact_duplicates_do_not_create_false_multiple_coverage_but_distinct_policies_survive():
    case, _, _, item = dependent_case(address=True)
    other = deepcopy(item); other['subscriber']['memberId'] = 'SECOND-POLICY'
    rows = discovery.candidates({'items': [item, deepcopy(item), other]}, case['previsit_context']['inputs'])
    assert len(rows) == 2
    assert {row['member_id'] for row in rows} == {'SYNTHETIC-FAMILY', 'SECOND-POLICY'}
    assert discovery.parse_discovery_candidate('not a policy') is None
    assert discovery.candidates({'items': [{'subscriber': {'memberId': 'ONLY-ID'}}]}, {})[0]['member_id'] == 'ONLY-ID'


def test_cached_discarded_response_reparses_without_payer_calls_or_promoting_coverage():
    case, session, _, item = dependent_case()
    case['previsit_discovery'].update(request_state='complete', outcome='no_match', raw_object_key='protected')
    with patch.object(api.object_store, 'get_object_bytes', return_value=json.dumps(
            {'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]}).encode()), \
         patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api.httpx, 'AsyncClient', side_effect=AssertionError('No network during cache repair')):
        asyncio.run(api._reconcile_saved_discovery(case, session))
    result = api._previsit_result(case)
    assert result['recovery']['coverage_status'] == 'REVIEW_REQUIRED'
    assert result['recovery']['candidates'][0]['member_id'] == 'SYNTHETIC-FAMILY'
    assert result['insurance_diagnosis']['rawCandidateCount'] == 1
    assert result['insurance_diagnosis']['retainedCandidateCount'] == 1
    assert result['insurance_diagnosis']['verifiedCandidateCount'] == 0
    assert result.get('insurance_details', {}).get('member_id') is None
    assert result['collection_guidance']['amount_cents'] is None


def test_note_hints_are_independent_deduplicated_and_never_override_known_coverage():
    fields = [{'label': 'General Notes', 'text': 'Patient payment preference: Example Payer, Subscriber No: NOTE123', 'source': 'Encounter/synthetic'}]
    assert ecw_backend.appointment_insurance_fallback(fields, None, None, 0)['member_id'] == 'NOTE123'
    assert not ecw_backend.appointment_insurance_fallback(fields, 'CONFIRMED', 'Example Payer', 1)
    conflict = fields + [{**fields[0], 'text': fields[0]['text'].replace('NOTE123', 'OTHER123')}]
    assert not ecw_backend.appointment_insurance_fallback(conflict, None, None, 0)
    case, _, _, _ = setup()
    case['previsit_context']['source'].update(insurance_candidates=ecw_backend.appointment_insurance_candidates(fields),
        coverage_records=[{'payer': ['Example Payer'], 'member_id': 'NOTE123'}])
    case['previsit_discovery']['card_candidates'] = [{'payer': 'Example Payer', 'member_id': 'NOTE123', 'source': 'insurance_card'}]
    hints = api._previsit_result(case)['insurance_hints']
    assert len(hints) == 1 and len(hints[0]['sources']) == 3


@pytest.mark.parametrize('status,reason', [('MISSING', 'enrollment_missing'), ('PROVISIONING', 'enrollment_pending'),
    ('REJECTED', 'enrollment_rejected'), ('PROVIDER_ACTION_REQUIRED', 'enrollment_provider_action_required'),
    ('LIVE', 'enrollment_live')])
def test_enrollment_reports_exact_state_and_ignores_other_provider(status, reason):
    def handler(request):
        assert 'status' not in request.url.params
        row = {'status': status, 'payer': {'stediPayerId': 'route'}, 'provider': {'npi': '1234567890'},
               'transactions': {'eligibilityCheck': {'enroll': True}}}
        wrong = {**row, 'status': 'LIVE', 'provider': {'npi': '0000000000'}}
        return httpx.Response(200, json={'items': [wrong] + ([] if status == 'MISSING' else [row])})
    actual = asyncio.run(stedi_payers.eligibility_enrollment('route', '1234567890', 'synthetic', transport=httpx.MockTransport(handler)))
    assert actual == {'status': status, 'reason_code': reason}


def test_enrollment_lookup_failure_is_not_missing_enrollment_and_pagination_is_checked():
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    result = asyncio.run(stedi_payers.eligibility_enrollment('route', '1234567890', 'synthetic', transport=transport))
    assert result['status'] == 'LOOKUP_FAILED'
    calls = []
    def handler(request):
        calls.append(request)
        if 'pageToken' not in request.url.params:
            return httpx.Response(200, json={'items': [], 'nextPageToken': 'next'})
        return httpx.Response(200, json={'items': [{'status': 'LIVE', 'payer': {'stediPayerId': 'route'},
            'provider': {'npi': '1234567890'}, 'transactions': {'eligibilityCheck': {'enroll': True}}}]})
    result = asyncio.run(stedi_payers.eligibility_enrollment('route', '1234567890', 'synthetic', transport=httpx.MockTransport(handler)))
    assert result['status'] == 'LIVE' and len(calls) == 2


def test_verified_coverage_without_rate_has_explicit_diagnosis_and_separate_cash_price():
    case, _, _, _ = setup(); case.pop('previsit_discovery')
    case['previsit']['request_state'] = 'complete'
    result = case['previsit_context']['result']
    result.pop('eligibility_error'); result.pop('eligibility_failure')
    result.update(payment_mode='insurance', identity={'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
        benefits={'active': True, 'network_status': 'in_network',
                  'network_source': 'clinic_library_contract', 'service_date_matches': True},
        price_range={'complete': False, 'lines': [{'code': '99213', 'allowed_cents': None, 'issues': ['No contracted rate for 99213']}],
                     'cash_complete': True, 'cash_min_cents': 16000, 'cash_max_cents': 16000})
    public = api._previsit_result(case)
    diagnosis = public['insurance_diagnosis']
    assert diagnosis['identityStatus'] == 'VERIFIED' and diagnosis['eligibilityStatus'] == 'BENEFITS_VERIFIED'
    assert diagnosis['pricingStatus'] == 'NOT_CALCULATED' and 'CONTRACTED_RATE_MISSING' in diagnosis['reasonCodes']
    assert 'No contracted rate for 99213' in diagnosis['pricingReasons']
    assert public['payment_mode'] == 'insurance' and public['collection_guidance']['amount_cents'] is None
    result['arrival_collection'] = {'amount_cents': 2500, 'status': 'estimate'}
    result['collection_policy'] = {'mode': 'collect_supported_advance',
                                   'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01T12:00:00Z',
                                   'reference': 'Synthetic advance collection policy'}
    result['price_range'].update(complete=True)
    public = api._previsit_result(case)
    assert public['payment_mode'] == 'insurance' and public['collection_guidance']['amount_cents'] == 2500
    assert public['insurance_diagnosis']['pricingStatus'] == 'ESTIMATE_READY'


def test_diagnosis_has_no_candidate_identifiers_and_is_in_saved_snapshot():
    case, session, _, item = dependent_case()
    case['previsit_discovery'].update(request_state='complete', outcome='review_needed', raw_candidate_count=1,
        candidates=discovery.candidates({'items': [item]}, case['previsit_context']['inputs']))
    with patch.object(api.store, 'save_case', side_effect=lambda value, *args, **kwargs: value) as save, \
         patch.object(api.staff_store, 'save_ecw_account_number'), patch.object(api, 'collection_event') as audit:
        api._save(case, session, 'synthetic-save')
    assert 'insurance_diagnosis' in case
    assert save.call_args.kwargs['snapshot']['result']['insurance_diagnosis']['retainedCandidateCount'] == 1
    assert 'SYNTHETIC-FAMILY' not in json.dumps(case['insurance_diagnosis'])
    assert 'SYNTHETIC-FAMILY' not in str(audit.call_args_list)


def test_conflicting_coverage_has_a_specific_pricing_reason_without_inventing_a_rate():
    payload = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['98']},
        {'code': 'I', 'serviceTypeCodes': ['98'], 'additionalInformation': [{'description': 'Restricted service'}]},
    ]}
    benefits = commercial.normalize_stedi_271(payload, '98', False)
    assert benefits['active'] is None and benefits['coverage_status_conflict']
    assert any('both active coverage' in reason for reason in benefits['review_reasons'])
    case, _, _, _ = setup(); case.pop('previsit_discovery')
    result = case['previsit_context']['result']
    result.update(benefits=benefits, issues=['Exact product mapping is missing.'])
    diagnosis = api._insurance_diagnosis(case, result, {'amount_cents': None})
    assert 'COVERAGE_STATUS_CONFLICT' in diagnosis['reasonCodes']
    assert 'Exact product mapping is missing.' in diagnosis['pricingReasons']
    assert 'CONTRACTED_RATE_MISSING' not in diagnosis['reasonCodes']


@pytest.mark.parametrize('dates', [{'unknown': '20261008'}, {'plan': {}}, {'planBegin': 'not-a-date'}])
def test_unrecognized_or_invalid_dates_cannot_verify_discovered_coverage(dates):
    case, _, _, item = dependent_case(address=True)
    item['planDateInformation'] = dates
    row = discovery.candidates({'items': [item]}, case['previsit_context']['inputs'])[0]
    assert row['verification_status'] == 'REVIEW_REQUIRED'


def test_malformed_candidate_does_not_hide_the_next_valid_partial_candidate():
    case, _, _, item = dependent_case()
    malformed = {**item, 'errors': 3}
    rows = discovery.candidates({'items': [malformed, item]}, case['previsit_context']['inputs'])
    assert len(rows) == 1 and rows[0]['index'] == 1
    assert rows[0]['subscriber']['dateOfBirth'] is None


def test_document_timeout_is_reported_separately_from_absent_cards():
    case, session, provider, _ = setup()
    case['previsit_context']['source']['_patient_id'] = 'synthetic-patient'
    with patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api.staff_store, 'patient_card_intakes', return_value=[]), \
         patch.object(api.ecw_backend, 'insurance_cards', AsyncMock(side_effect=TimeoutError)), \
         patch.object(api, '_recheck_discovery_candidate', AsyncMock()) as eligibility:
        assert not asyncio.run(api._recover_card(case, session, provider))
    diagnosis = api._previsit_result(case)['insurance_diagnosis']
    assert 'CARD_READ_TIMEOUT' in diagnosis['reasonCodes']
    eligibility.assert_not_called()


def test_expired_dependent_policy_keeps_patient_match_member_group_and_dates():
    case, _, _, item = dependent_case()
    item['subscriber'].update(groupNumber='SYNTHETIC-GROUP', middleName='Q')
    item['planDateInformation'] = {'planBegin': '20000101', 'planEnd': '20001231'}
    item['benefitsInformation'] = [{'code': '6', 'name': 'Inactive', 'serviceTypeCodes': ['30']}]
    item.pop('plans', None)
    context = case['previsit_context']
    row = discovery.candidates({'items': [item]}, context['inputs'], context['source'])[0]
    assert row['member_id'] == 'SYNTHETIC-FAMILY' and row['group_number'] == 'SYNTHETIC-GROUP'
    assert row['identity_status'] == 'MATCHED'
    assert row['subscriber']['middleName'] == 'Q' and row['subscriber']['dateOfBirth'] is None
    assert row['coverage_evidence'] == {'status': 'EXPIRED', 'reported_status': 'INACTIVE', 'start': '2000-01-01', 'end': '2000-12-31'}
    assert 'COVERAGE_EXPIRED' in row['reason_codes'] and not row['inquiry_eligible']
    case['previsit_discovery'].update(request_state='complete', outcome='review_needed', exhausted=True, candidates=[row])
    result = api._previsit_result(case)
    assert result['insurance_diagnosis']['identityStatus'] == 'VERIFIED'
    assert result['insurance_diagnosis']['eligibilityStatus'] == 'EXPIRED'
    assert result['payment_mode'] == 'cash'


def test_missing_corroborating_address_allows_inquiry_but_conflicting_identity_does_not():
    case, _, _, item = dependent_case()
    context = case['previsit_context']
    assert discovery.inquiry_eligible(item, context['inputs'], context['source'])
    assert not discovery.automatic_match(item, context['inputs'], context['source'])
    item['dependent']['address'] = {**context['inputs']['patient']['address'], 'address1': 'Unrelated address'}
    assert not discovery.inquiry_eligible(item, context['inputs'], context['source'])
    item['dependent'].pop('address'); item['dependent']['dateOfBirth'] = '19990101'
    assert not discovery.inquiry_eligible(item, context['inputs'], context['source'])
