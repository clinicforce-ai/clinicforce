"""Synthetic Stedi COB request and interpretation checks; no payer calls."""
from copy import deepcopy
from datetime import date, datetime, timezone
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow.commercial_previsit_submit import submit_previsit_eligibility
from app.api.routers.commercial_flow.commercial_previsit_reuse import reuse_previsit_case
from app.insurance import stedi_cob


PAYER = {'stediId': 'SYNTHETIC', 'transactionSupport': {'coordinationOfBenefits': 'SUPPORTED'}}
PROVIDER = {'rendering_npi': '1234567890', 'rendering_first': 'Ada', 'rendering_last': 'Clinician'}
RESPONSE = {'subscriber': {'name': {'person': {'firstName': 'Pat', 'lastName': 'Example'}},
                           'dateOfBirth': '19800101', 'memberId': 'MEMBER-1'}}
BENEFITS = {'active': True, 'service_date_matches': True,
            'identity': {'status': 'matched', 'match_version': 2, 'binding': 'synthetic-current-binding',
                         'differences': [], 'policy_differences': []}}


def test_cob_request_uses_exact_returned_member_and_bounded_visit_date():
    body = stedi_cob.request_body(PAYER, PROVIDER, RESPONSE, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15))
    assert body == {'tradingPartnerServiceId': 'SYNTHETIC',
                    'provider': {'npi': '1234567890', 'firstName': 'Ada', 'lastName': 'Clinician'},
                    'subscriber': {'firstName': 'Pat', 'lastName': 'Example',
                                   'dateOfBirth': '1980-01-01', 'memberId': 'MEMBER-1'},
                    'encounter': {'dateOfService': '2026-10-15', 'serviceTypeCode': '30'}}
    assert stedi_cob.request_body(PAYER, PROVIDER, RESPONSE, BENEFITS,
                                  '2026-10-16', today=date(2026, 10, 15)) is None
    assert stedi_cob.request_body(PAYER, PROVIDER, RESPONSE, BENEFITS,
                                  '2024-10-14', today=date(2026, 10, 15)) is None


def test_cob_request_refuses_mismatch_missing_member_and_unsupported_route():
    mismatch = deepcopy(BENEFITS)
    mismatch['identity'] = {'status': 'review', 'policy_differences': [{'field': 'subscriber_first'}]}
    assert stedi_cob.request_body(PAYER, PROVIDER, RESPONSE, mismatch, '2026-10-15', today=date(2026, 10, 15)) is None
    unsupported = {**PAYER, 'transactionSupport': {'coordinationOfBenefits': 'NOT_SUPPORTED'}}
    assert stedi_cob.request_body(unsupported, PROVIDER, RESPONSE, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15)) is None
    missing_member = deepcopy(RESPONSE)
    missing_member['subscriber'].pop('memberId')
    assert stedi_cob.request_body(PAYER, PROVIDER, missing_member, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15)) is None
    unknown_dob = deepcopy(RESPONSE)
    unknown_dob['subscriber'].pop('dateOfBirth')
    assert stedi_cob.request_body(PAYER, PROVIDER, unknown_dob, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15)) is None
    body, reason, fields = stedi_cob.prepare_request(PAYER, PROVIDER, unknown_dob, BENEFITS,
                                                      '2026-10-15', today=date(2026, 10, 15))
    assert body is None and reason == 'cob_subscriber_fields_missing'
    assert fields == ('subscriber date of birth',)
    legacy = deepcopy(BENEFITS)
    legacy['identity'] = {'status': 'matched', 'binding': 'legacy-binding'}
    body, reason, _ = stedi_cob.prepare_request(PAYER, PROVIDER, RESPONSE, legacy,
                                                 '2026-10-15', today=date(2026, 10, 15))
    assert body is None and reason == 'cob_identity_mismatch'
    legacy['identity']['match_version'] = 2
    assert stedi_cob.request_body(PAYER, PROVIDER, RESPONSE, legacy,
                                  '2026-10-15', today=date(2026, 10, 15)) is not None


@pytest.mark.parametrize('service_date,reason', [
    ('2026-10-16', 'cob_future_visit_date'),
    ('2024-10-14', 'cob_visit_date_too_old'),
    ('not-a-date', 'cob_visit_date_invalid'),
])
def test_cob_preflight_distinguishes_service_date_blockers(service_date, reason):
    body, actual, fields = stedi_cob.prepare_request(PAYER, PROVIDER, RESPONSE, BENEFITS,
                                                      service_date, today=date(2026, 10, 15))
    assert body is None and actual == reason and fields == ()


def test_cob_request_keeps_subscriber_and_dependent_separate():
    dependent = {**RESPONSE, 'dependent': {'firstName': 'Young', 'lastName': 'Example',
                                          'dateOfBirth': '2015-02-03'}}
    body = stedi_cob.request_body(PAYER, PROVIDER, dependent, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15))
    assert body['subscriber']['memberId'] == 'MEMBER-1'
    assert body['dependent'] == {'firstName': 'Young', 'lastName': 'Example', 'dateOfBirth': '2015-02-03'}


def test_cob_request_uses_dependent_as_subscriber_when_payer_returns_unique_member_id():
    response = {**RESPONSE, 'dependent': {'firstName': 'Young', 'lastName': 'Example',
                                         'dateOfBirth': '2015-02-03', 'memberId': 'CHILD-1'}}
    body = stedi_cob.request_body(PAYER, PROVIDER, response, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15))
    assert body['subscriber'] == {'firstName': 'Young', 'lastName': 'Example',
                                  'dateOfBirth': '2015-02-03', 'memberId': 'CHILD-1'}
    assert 'dependent' not in body
    without_parent_id = deepcopy(response)
    without_parent_id['subscriber'].pop('memberId')
    assert stedi_cob.request_body(PAYER, PROVIDER, without_parent_id, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15))['subscriber']['memberId'] == 'CHILD-1'
    missing_dependent_dob = deepcopy(response)
    missing_dependent_dob['dependent'].pop('dateOfBirth')
    body, reason, fields = stedi_cob.prepare_request(PAYER, PROVIDER, missing_dependent_dob, BENEFITS,
                                                      '2026-10-15', today=date(2026, 10, 15))
    assert body is None and reason == 'cob_dependent_fields_missing'
    assert fields == ('dependent date of birth',)


def test_matched_ecw_dob_fills_only_an_absent_payer_dob_with_source_label():
    response = deepcopy(RESPONSE)
    response['subscriber'].pop('dateOfBirth')
    filled, sources = stedi_cob.with_ecw_birth_dates(response, {'subscriber_birth_date': '1980-01-01'})
    assert filled['subscriber']['dateOfBirth'] == '1980-01-01'
    assert sources == ('subscriber date of birth from eCW Coverage',)
    assert 'dateOfBirth' not in response['subscriber']
    assert stedi_cob.request_body(PAYER, PROVIDER, filled, BENEFITS,
                                  '2026-10-15', today=date(2026, 10, 15))['subscriber']['dateOfBirth'] == '1980-01-01'
    response['subscriber']['dateOfBirth'] = '1979-01-01'
    unchanged, sources = stedi_cob.with_ecw_birth_dates(response, {'subscriber_birth_date': '1980-01-01'})
    assert unchanged is response and sources == ()


def test_cob_response_reports_payer_roles_without_assigning_candidate_order():
    response = {'coordinationOfBenefits': {'primacyDetermined': True,
                                           'classification': 'CobInstanceExistsPrimacyDetermined',
                                           'coverageOverlap': True},
                'benefitsInformation': [{'code': 'R', 'serviceTypeCodes': ['1'],
                                        'serviceTypes': ['Medical Care'], 'benefitsRelatedEntities': [
                    {'entityIdentifier': 'Primary Payer', 'entityName': 'SYNTHETIC HEALTH',
                     'entityIdentificationValue': 'PROPRIETARY-ID'},
                    {'entityIdentifier': 'Insured or Subscriber', 'entityFirstname': 'Pat'},
                ]}]}
    summary = stedi_cob.response_summary(response)
    assert summary['status'] == 'primacy_reported'
    assert summary['payer_roles'] == [{'role': 'Primary Payer', 'payer_name': 'SYNTHETIC HEALTH'}]
    assert 'PROPRIETARY-ID' not in str(summary)
    conflicting = deepcopy(response)
    conflicting['benefitsInformation'].append({'code': 'R', 'serviceTypeCodes': ['98'],
        'benefitsRelatedEntities': [{'entityIdentifier': 'Primary Payer', 'entityName': 'OTHER HEALTH'}]})
    conflict_summary = stedi_cob.response_summary(conflicting)
    assert conflict_summary['status'] == 'primacy_conflict'
    assert len(conflict_summary['payer_roles']) == 2
    response['coordinationOfBenefits']['primacyDetermined'] = False
    undetermined = stedi_cob.response_summary(response)
    assert undetermined['status'] == 'primacy_undetermined'
    assert undetermined['payer_roles'] == []
    response['coordinationOfBenefits']['primacyDetermined'] = True
    response['benefitsInformation'] = []
    assert stedi_cob.response_summary(response)['status'] == 'primacy_unmapped'
    response['benefitsInformation'] = [{'code': 'R', 'serviceTypeCodes': ['35'],
                                       'serviceTypes': ['Dental Care'],
                                       'benefitsRelatedEntities': [{'entityIdentifier': 'Primary Payer',
                                                                    'entityName': 'DENTAL PLAN'}]}]
    assert stedi_cob.response_summary(response)['status'] == 'primacy_unmapped'
    assert stedi_cob.response_summary(response)['payer_roles'] == []
    response['benefitsInformation'][0].update(code='1', serviceTypeCodes=['1'], serviceTypes=['Medical Care'])
    assert stedi_cob.response_summary(response)['status'] == 'primacy_unmapped'
    response['coordinationOfBenefits'].update(classification='CoverageOverlapNoBenefitOverlap',
                                               primacyDetermined=False, coverageOverlap=True,
                                               benefitOverlap=False)
    assert stedi_cob.response_summary(response)['status'] == 'no_benefit_overlap'
    response['coordinationOfBenefits']['classification'] = 'MemberFoundNoCob'
    assert stedi_cob.response_summary(response)['status'] == 'no_cob_found'
    response['coordinationOfBenefits']['classification'] = 'CoverageOverlapExistsNotSubjectToCob'
    assert stedi_cob.response_summary(response)['status'] == 'cob_not_required'
    response['coordinationOfBenefits']['primacyDetermined'] = True
    assert stedi_cob.response_summary(response)['status'] == 'invalid_response'
    assert stedi_cob.response_summary({'errors': [{'code': '75'}]})['status'] == 'rejected'
    assert stedi_cob.response_summary({'benefitsInformation': []})['status'] == 'invalid_response'


def _saved_case(identity=None):
    today = date.today().isoformat()
    benefits = {**BENEFITS, 'has_secondary': True, 'identity': identity or BENEFITS['identity']}
    case = {'id': 'synthetic-case', 'revision': 1,
            'previsit': {'request_state': 'complete'},
            'previsit_context': {'payer_id': 'SYNTHETIC', 'source': {}, 'inputs': {'service_date': today},
                                 'result': {'payer': 'UnitedHealthcare', 'payment_mode': 'insurance',
                                            'service_date': today, 'benefits': benefits,
                                            'price_range': {'complete': False, 'lines': []}}}}
    session = {'practice_id': 'synthetic-clinic', 'patient_id': 'synthetic-patient',
               'intake': {'id': 'synthetic-intake'}}
    return case, session


@pytest.mark.parametrize('prior_future_preflight', [False, True])
@pytest.mark.parametrize('payer_label', ['UnitedHealthcare', 'United Health Care'])
def test_automatic_cob_followup_persists_request_response_and_safe_summary(prior_future_preflight, payer_label):
    case, session = _saved_case()
    case['previsit_context']['result']['payer'] = payer_label
    if prior_future_preflight:
        case['previsit_cob'] = {'state': 'not_ready', 'reason_code': 'cob_future_visit_date'}
    objects = {}
    def put(*args):
        key = f'protected-{len(objects)}'
        objects[key] = args[3]
        return key
    answer = {'coordinationOfBenefits': {'primacyDetermined': True, 'coverageOverlap': True},
              'benefitsInformation': [{'code': 'R', 'serviceTypeCodes': ['1'],
                                      'benefitsRelatedEntities': [
                  {'entityIdentifier': 'Primary Payer', 'entityName': 'SYNTHETIC HEALTH'}]}]}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=httpx.Response(200, json=answer))
    with patch.object(api.httpx, 'AsyncClient', return_value=client), \
         patch.object(api.object_store, 'put_staff_document', side_effect=put), \
         patch.object(api, '_save', side_effect=lambda value, *_: {**value, 'revision': value['revision'] + 1}), \
         patch.object(api, '_recovery_event'):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=PAYER,
                                            payload=RESPONSE, api_key='synthetic-key'))
    assert client.post.await_count == 1
    assert client.post.call_args.args[0] == stedi_cob.URL
    assert len(objects) == 2
    assert case['previsit_cob']['state'] == 'complete'
    assert case['previsit_cob']['summary']['status'] == 'primacy_reported'
    public = api._previsit_result(case)
    assert public['coordination']['payer_roles'] == [{'role': 'Primary Payer', 'payer_name': 'SYNTHETIC HEALTH'}]
    assert 'request_object_key' not in public['coordination']
    assert public['collection_guidance']['amount_cents'] is None


@pytest.mark.parametrize('other_coverage', [False, True])
def test_reused_fresh_271_rechecks_cob_preflight_without_repeating_eligibility(other_coverage):
    case, session = _saved_case()
    case['previsit'].update(checked_at=datetime.now(timezone.utc).isoformat(),
                            requested_service_types=['30'], benefits=BENEFITS)
    case['previsit_raw_object_key'] = 'protected-271'
    case['previsit_cob'] = {'state': 'not_ready', 'reason_code': 'cob_future_visit_date'}
    result = {'issues': []}
    refreshed_benefits = {**BENEFITS, 'has_secondary': other_coverage}
    with patch.object(api.visit_estimate, 'reconcile_eligibility', return_value={'requires_review': False}), \
         patch.object(api.object_store, 'get_object_bytes', return_value=api.json.dumps(RESPONSE).encode()) as raw, \
         patch.object(api, '_case_benefits', return_value=refreshed_benefits), \
         patch.object(api, '_finish_previsit'), \
         patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api, '_previsit_result', return_value={'status': 'reused'}), \
         patch.object(api, '_check_previsit_cob', new=AsyncMock()) as followup:
        reused = asyncio.run(reuse_previsit_case(api, case=case, result=result,
            inputs={'service_date': date.today().isoformat()}, source={}, session=session,
            coverage={}, provider=PROVIDER, background_tasks=None,
            requested_stc=['30'], payer_id='SYNTHETIC', refresh_benefits=False))
    assert reused == {'status': 'reused'}
    raw.assert_called_once_with('protected-271')
    assert followup.await_count == int(other_coverage)


def test_discovery_case_reuse_rechecks_cob_from_fresh_saved_271():
    case, session = _saved_case()
    session['intake']['updated_at'] = datetime.now(timezone.utc).isoformat()
    case['previsit'].update(checked_at=datetime.now(timezone.utc).isoformat())
    case['previsit_context']['context_token'] = 'synthetic-token'
    case['previsit_raw_object_key'] = 'protected-271'
    case['previsit_cob'] = {'state': 'not_ready', 'reason_code': 'cob_future_visit_date'}
    result = {'payer': 'UnitedHealthcare'}
    with patch.object(api.store, 'create_case', return_value=case), \
         patch.object(api, '_scope', return_value={}), \
         patch.object(api, '_coverage_snapshot', return_value={}), \
         patch.object(api, '_visit_context_token', return_value='synthetic-token'), \
         patch.object(api.object_store, 'get_object_bytes', return_value=api.json.dumps(RESPONSE).encode()) as raw, \
         patch.object(api, '_case_benefits', return_value={**BENEFITS, 'has_secondary': True}), \
         patch.object(api, '_finish_previsit'), \
         patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api, '_recovery_event'), \
         patch.object(api, '_previsit_result', return_value={'status': 'reused'}), \
         patch.object(api, '_check_previsit_cob', new=AsyncMock()) as followup:
        reused = asyncio.run(api._begin_coverage_discovery(result, session,
            {'service_date': date.today().isoformat()}, {'appointment_id': 'synthetic-visit'},
            PROVIDER, None, 'synthetic route missing'))
    assert reused == {'status': 'reused'}
    raw.assert_called_once_with('protected-271')
    followup.assert_awaited_once()


def test_automatic_cob_refuses_mismatched_member_without_submission():
    case, session = _saved_case({'status': 'review', 'policy_differences': [{'field': 'subscriber_first'}]})
    with patch.object(api.object_store, 'put_staff_document') as put, \
         patch.object(api.httpx, 'AsyncClient') as client, \
         patch.object(api, '_save', side_effect=lambda value, *_: value):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=PAYER,
                                            payload=RESPONSE, api_key='synthetic-key'))
    assert case['previsit_cob']['reason_code'] == 'cob_identity_mismatch'
    put.assert_not_called()
    client.assert_not_called()


def test_uhc_directory_name_keeps_cob_route_when_ecw_uses_product_alias():
    case, session = _saved_case()
    case['previsit_context']['result']['payer'] = 'AARP Regional PPO'
    directory = {**PAYER, 'displayName': 'UnitedHealthcare',
                 'transactionSupport': {'coordinationOfBenefits': 'NOT_SUPPORTED'}}
    with patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api.httpx, 'AsyncClient') as client:
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=directory,
                                            api_key='synthetic-key'))
    assert case['previsit_cob']['reason_code'] == 'cob_payer_route_unsupported'
    client.assert_not_called()


def test_missing_payer_subscriber_dob_is_named_without_a_cob_submission():
    case, session = _saved_case()
    response = deepcopy(RESPONSE)
    response['subscriber'].pop('dateOfBirth')
    with patch.object(api.object_store, 'put_staff_document') as put, \
         patch.object(api.httpx, 'AsyncClient') as client, \
         patch.object(api, '_save', side_effect=lambda value, *_: value):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=PAYER,
                                            payload=response, api_key='synthetic-key'))
    assert case['previsit_cob']['reason_code'] == 'cob_subscriber_fields_missing'
    assert case['previsit_cob']['missing_fields'] == ['subscriber date of birth']
    assert api._previsit_result(case)['coordination']['missing_fields'] == ['subscriber date of birth']
    put.assert_not_called()
    client.assert_not_called()


def test_automatic_cob_uses_matched_ecw_dob_when_271_omits_it():
    case, session = _saved_case()
    case['previsit_context']['inputs']['subscriber'] = {'birthDate': '1980-01-01'}
    response = deepcopy(RESPONSE)
    response['subscriber'].pop('dateOfBirth')
    answer = {'coordinationOfBenefits': {'primacyDetermined': False}, 'benefitsInformation': []}
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=httpx.Response(200, json=answer))
    with patch.object(api.object_store, 'put_staff_document', return_value='protected-key'), \
         patch.object(api.httpx, 'AsyncClient', return_value=client), \
         patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api, '_recovery_event'):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=PAYER,
                                            payload=response, api_key='synthetic-key'))
    assert client.post.call_args.kwargs['json']['subscriber']['dateOfBirth'] == '1980-01-01'
    assert case['previsit_cob']['source_fields'] == ['subscriber date of birth from eCW Coverage']


def test_directory_failure_is_not_reported_as_unsupported_cob():
    case, session = _saved_case()
    with patch.object(api.stedi_payers, 'search_payer', new=AsyncMock(side_effect=httpx.ConnectError('offline'))), \
         patch.object(api.object_store, 'put_staff_document') as put, \
         patch.object(api.httpx, 'AsyncClient') as client, \
         patch.object(api, '_save', side_effect=lambda value, *_: value):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER,
                                            payload=RESPONSE, api_key='synthetic-key'))
    assert case['previsit_cob']['reason_code'] == 'cob_payer_route_lookup_failed'
    put.assert_not_called()
    client.assert_not_called()


def test_malformed_payer_support_does_not_submit_cob():
    case, session = _saved_case()
    with patch.object(api.object_store, 'put_staff_document') as put, \
         patch.object(api.httpx, 'AsyncClient') as client, \
         patch.object(api, '_save', side_effect=lambda value, *_: value):
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER,
                                            payer={**PAYER, 'transactionSupport': 'unknown'},
                                            payload=RESPONSE, api_key='synthetic-key'))
    assert case['previsit_cob']['reason_code'] == 'cob_payer_support_unreported'
    put.assert_not_called()
    client.assert_not_called()


def test_indeterminate_cob_request_is_never_replayed():
    case, session = _saved_case()
    case['previsit_cob'] = {'state': 'indeterminate', 'reason_code': 'cob_outcome_unknown'}
    with patch.object(api.object_store, 'put_staff_document') as put, \
         patch.object(api.httpx, 'AsyncClient') as client:
        asyncio.run(api._check_previsit_cob(case, session, PROVIDER, payer=PAYER,
                                            payload=RESPONSE, api_key='synthetic-key'))
    put.assert_not_called()
    client.assert_not_called()


def test_active_uhc_271_with_other_coverage_starts_cob_followup():
    case, session = _saved_case()
    result = case['previsit_context']['result']
    result['issues'] = []
    inputs = {'service_date': date.today().isoformat()}
    response = httpx.Response(200, json=RESPONSE)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    def finish(current_result, current_case, *_args):
        current_case['previsit_context']['result'] = current_result
    with patch.object(api.httpx, 'AsyncClient', return_value=client), \
         patch.object(api, '_post_stedi_eligibility', new=AsyncMock(return_value=(response, RESPONSE, False))), \
         patch.object(api, '_previsit_benefits', return_value={**BENEFITS, 'has_secondary': True}), \
         patch.object(api, '_finish_previsit', side_effect=finish), \
         patch.object(api, '_save', side_effect=lambda value, *_: value), \
         patch.object(api.object_store, 'put_staff_document', return_value='protected-key'), \
         patch.object(api, '_check_previsit_cob', new=AsyncMock()) as check, \
         patch.object(api, '_previsit_result', return_value={'followup': 'started'}), \
         patch.object(api, 'collection_event'):
        public = asyncio.run(submit_previsit_eligibility(api, payer=PAYER,
            provider={**PROVIDER, 'billing_npi': '1234567890'},
            api_key='synthetic-key', session=session, case=case, result=result,
            coverage={'member_id': 'MEMBER-1'}, inputs=inputs, source={},
            background_tasks=None, requested_stc=['30'], body={}, basic_body={}))
    assert public == {'followup': 'started'}
    check.assert_awaited_once()
    assert check.await_args.kwargs['payload'] == RESPONSE
