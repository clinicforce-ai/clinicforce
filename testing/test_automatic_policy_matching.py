"""Synthetic regression cases for conflicting subscriber evidence and its correction."""
import asyncio
import json
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx

from app.api.routers import commercial_estimator as api
from app.insurance import ecw_backend, stedi_discovery
from test_stedi_discovery import setup
from test_automatic_estimate_prerequisites import appointment
from test_result_first_estimate import response


def test_subscriber_conflict_cannot_be_reported_as_matched():
    case, _, _, item = setup()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['member_id'] = item['subscriber']['memberId']
    item['dependent'] = {**item['subscriber'], 'relationToSubscriber': '01'}
    item['subscriber'] = {'firstName': 'Sam', 'lastName': 'Example', 'memberId': inputs['member_id']}
    benefits = api._previsit_benefits(item, api._coverage_snapshot(inputs, source), inputs['service_date'])
    assert benefits['identity']['status'] == 'review'
    assert benefits['requires_review']
    assert not benefits['identity']['can_confirm']  # A name acknowledgement cannot change a policyholder.


def test_saved_eligibility_requests_only_its_own_entitlement(monkeypatch):
    requested = []
    async def token(client, client_id, issuer, scopes):
        requested.append(scopes)
        if scopes != ('system/CoverageEligibilityResponse.read',):
            raise ecw_backend.EcwBackendError('Synthetic combined-scope rejection')
        return 'synthetic'
    monkeypatch.setattr(ecw_backend, 'settings', lambda: ('client', 'https://example.test/fhir'))
    monkeypatch.setattr(ecw_backend, '_access_token', token)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(ecw_backend, '_search', search)
    asyncio.run(ecw_backend.fetch_saved_eligibility({'_patient_id': 'p', 'coverage_id': 'c'}, date.today().isoformat()))
    assert search.await_count == 1
    assert requested == [('system/CoverageEligibilityResponse.read',)]


def test_denied_saved_eligibility_still_falls_through_without_retry(monkeypatch):
    monkeypatch.setattr(ecw_backend, 'settings', lambda: ('client', 'https://example.test/fhir'))
    token = AsyncMock(side_effect=ecw_backend.EcwBackendError('Synthetic entitlement denied'))
    monkeypatch.setattr(ecw_backend, '_access_token', token)
    result = asyncio.run(ecw_backend.fetch_saved_eligibility({'_patient_id': 'p', 'coverage_id': 'c'}, date.today().isoformat()))
    assert token.await_count == 1
    assert result['status'] == 'check_with_stedi'
    assert '_response' not in result


def dependent_policy():
    case, session, provider, item = setup()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['member_id'] = item['subscriber']['memberId']
    item['dependent'] = {**item['subscriber'], 'relationToSubscriberCode': '01'}
    item['subscriber'] = {'firstName': 'Sam', 'lastName': 'Other', 'memberId': inputs['member_id']}
    return case, session, provider, item


def test_payer_can_resolve_recorded_self_to_dependent_without_a_form():
    case, _, _, item = dependent_policy()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    assert stedi_discovery.verify_candidate(item, inputs, source)['status'] == 'VERIFIED'
    benefits = api._previsit_benefits(item, api._coverage_snapshot(inputs, source), inputs['service_date'], inputs=inputs, source=source)
    assert benefits['identity']['status'] == 'matched'
    assert benefits['policy_match']['status'] == 'verified'
    assert inputs['relationship'] == '18'  # Original eCW evidence is preserved.
    assert inputs['subscriber']['firstName'] == 'Pat'


@pytest.mark.parametrize('failure', ['wrong_dob', 'wrong_patient', 'subscriber_only', 'wrong_member',
    'wrong_group', 'wrong_plan', 'multiple_policies', 'inactive', 'expired', 'unknown_dates', 'wrong_address', 'unlinked'])
def test_another_family_member_or_unverified_policy_cannot_resolve_conflict(failure):
    case, _, _, item = dependent_policy()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    if failure == 'wrong_dob': item['dependent']['dateOfBirth'] = '19800102'
    if failure == 'wrong_patient': item['dependent']['firstName'] = 'Another'
    if failure == 'subscriber_only': item.pop('dependent')
    if failure == 'wrong_member': item['subscriber']['memberId'] = 'UNRELATED'
    if failure == 'wrong_group':
        inputs['group_number'] = 'GROUP-A'; item['subscriber']['groupNumber'] = 'GROUP-B'
    if failure == 'wrong_plan':
        inputs['plan_number'] = 'PLAN-A'; item['subscriber']['planNumber'] = 'PLAN-B'
    if failure == 'multiple_policies': source['active_coverages'] = 2
    if failure == 'inactive':
        for row in item['benefitsInformation']: row['code'] = '6'
    if failure == 'expired': item['planDateInformation']['planEnd'] = '20000101'
    if failure == 'unknown_dates': item.pop('planDateInformation')
    if failure == 'wrong_address': item['dependent']['address'] = {**inputs['patient']['address'], 'address1': 'Other street'}
    if failure == 'unlinked':
        item['dependent'].pop('address'); source['payer'] = 'Other Payer'
    assert stedi_discovery.verify_candidate(item, inputs, source)['status'] == 'REVIEW_REQUIRED'
    benefits = api._previsit_benefits(item, api._coverage_snapshot(inputs, source), inputs['service_date'], inputs=inputs, source=source)
    assert not benefits.get('policy_match')


def test_surname_typo_needs_matching_dob_member_and_independent_address():
    case, _, _, item = dependent_policy()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['patient']['patient_last_name'] = 'Exmple'
    assert stedi_discovery.automatic_match(item, inputs, source)
    benefits = api._previsit_benefits(item, api._coverage_snapshot(inputs, source), inputs['service_date'], inputs=inputs, source=source)
    assert benefits['identity']['status'] == 'warning'
    assert not benefits['identity']['policy_differences']
    item['dependent'].pop('address')
    assert not stedi_discovery.automatic_match(item, inputs, source)


@pytest.mark.parametrize('invalid', [None, 'other_patient', 'inactive', 'wrong_type', 'wrong_id', 'unlinked_contact'])
def test_ecw_subscriber_must_be_linked_to_this_beneficiary(invalid):
    patient = {'resourceType': 'Patient', 'id': 'patient'}
    coverage = {'beneficiary': {'reference': 'Patient/patient'}, 'subscriber': {'reference': 'RelatedPerson/sub'}}
    person = {'resourceType': 'RelatedPerson', 'id': 'sub', 'patient': {'reference': 'Patient/patient'},
              'name': [{'given': ['Sam'], 'family': 'Other'}]}
    if invalid == 'other_patient': person['patient']['reference'] = 'Patient/other'
    if invalid == 'inactive': person['active'] = False
    if invalid == 'wrong_type': person['resourceType'] = 'Organization'
    if invalid == 'wrong_id': person['id'] = 'other'
    if invalid == 'unlinked_contact': coverage['subscriber']['reference'] = 'RelatedPerson/different'
    found = ecw_backend.coverage_subscriber(coverage, patient, {'RelatedPerson/sub': person})
    assert bool(found) == (invalid is None)
    if found: assert not found.get('birthDate')  # eCW's documented RelatedPerson schema need not return DOB.


def test_linked_ecw_subscriber_card_uses_separate_patient_with_unknown_subscriber_dob():
    case, _, provider, _ = dependent_policy()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['subscriber'] = {'firstName': 'Sam', 'lastName': 'Other', 'birthDate': None}
    source['subscriber_evidence'] = {'reference': 'RelatedPerson/sub', 'patient_linked': True}
    fields = {'member_name': 'Sam Other', 'member_id': inputs['member_id'], 'payer': 'Example Payer'}
    item = stedi_discovery.card_candidate(fields, inputs, source)
    assert item and item['dependent']['firstName'] == 'Pat'
    assert item['subscriber']['dateOfBirth'] is None
    assert not stedi_discovery.automatic_match(item, inputs, source)  # A card cannot prove benefits.
    assert 'RELATIONSHIP_CONFLICT' not in stedi_discovery.verify_candidate(item, inputs, source)['reason_codes']
    body = api.stedi_request_body({'stedi_payer_id': 'route'}, provider, api._coverage_snapshot(inputs, source), inputs['service_date'], '98')
    assert 'dateOfBirth' not in body['subscriber']
    assert body['dependent']['dateOfBirth'] == inputs['patient']['patient_birth_date']
    assert not stedi_discovery.card_candidate(fields, inputs, {})


def test_automatic_dependent_match_reaches_real_pricing_without_clinician_input(appointment):
    inputs, source, payer, _ = appointment
    inputs['relationship'] = '18'
    inputs['subscriber'] = {'firstName': 'Alex', 'lastName': 'Morgn', 'birthDate': '2001-02-03'}
    inputs['patient']['patient_last_name'] = 'Morgn'
    address = {'address1': '100 Test St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'}
    inputs['patient']['address'] = address
    payload = response()
    payload.update(payer={'name': source['payer'], 'payorIdentification': payer['stedi_payer_id']},
                   planDateInformation={'planBegin': inputs['service_date'].replace('-', '')})
    payload['dependent']['address'] = address
    payload['dependent']['relationToSubscriberCode'] = '19'
    for row in payload['benefitsInformation']: row['serviceTypeCodes'] = ['30', '98']
    case = {'revision': 1}
    result = {'issues': [], 'codes': api._previsit_codes(inputs), 'checked_at': api._now(),
        'benefits': api._previsit_benefits(payload, api._coverage_snapshot(inputs, source), inputs['service_date'], inputs=inputs, source=source)}
    api._finish_previsit(result, case, inputs, source, payload, {'practice_id': 'clinic-a', 'intake': {'patient': {}}}, payer['stedi_payer_id'])
    assert result['identity']['status'] == 'warning'
    assert result['benefits']['policy_match']['status'] == 'verified'
    assert result['collection_guidance']['amount_cents'] == 4000
    assert inputs['lines'] == []
    assert not case['visit_review'].get('identity_confirmation')


@pytest.mark.parametrize('subscriber_only', [False, True])
def test_patient_bound_card_can_query_another_subscriber_but_only_accept_patient_benefits(monkeypatch, subscriber_only):
    case, session, provider, item = dependent_policy()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    source['_patient_id'] = 'synthetic-patient'
    case['previsit_discovery']['identity_binding'] = api._recovery_binding(inputs, source)
    fields = {'member_name': 'Sam Other', 'member_id': inputs['member_id'], 'payer': 'Example Payer'}
    hint = stedi_discovery.card_candidate(fields, inputs, source)
    assert hint and hint['subscriber']['dateOfBirth'] is None
    assert hint['dependent']['firstName'] == 'Pat'
    candidates = stedi_discovery.candidates({'items': [hint]}, inputs, source)
    case['previsit_discovery'].update(candidates=candidates, selected_candidate_id=candidates[0]['id'])
    if subscriber_only: item.pop('dependent')
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=httpx.Response(200, json=item))
    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **kwargs: client)
    monkeypatch.setattr(api.stedi_financial, 'credentials', lambda *args: 'synthetic')
    monkeypatch.setattr(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE'}))
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *args: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *args: None)
    monkeypatch.setattr(api.object_store, 'put_staff_document', lambda *args: 'protected')
    monkeypatch.setattr(api, '_save', lambda value, *_: value)
    asyncio.run(api._recheck_discovery_candidate(case, session, provider, discovery_payload={'items': [hint]}, inquiry_only=True))
    assert client.post.await_count == 1
    body = client.post.call_args.kwargs['json']
    assert body['subscriber']['name']['person']['firstName'] == 'Sam'
    assert 'dateOfBirth' not in body['subscriber']
    assert body['dependent']['name']['person']['firstName'] == 'Pat'
    assert (case['previsit_discovery']['outcome'] == 'recovered') is (not subscriber_only)
    assert inputs['relationship'] == '18'


def test_documented_ecw_coverage_relatedperson_reads_preserve_two_identities(monkeypatch):
    day = (date.today() + timedelta(days=1)).isoformat()
    patient = {'resourceType': 'Patient', 'id': 'p', 'birthDate': '1980-01-01',
               'name': [{'given': ['Pat'], 'family': 'Example'}]}
    coverage = {'resourceType': 'Coverage', 'id': 'c', 'status': 'active', 'subscriberId': 'MEMBER123',
        'beneficiary': {'reference': 'Patient/p'}, 'subscriber': {'reference': 'RelatedPerson/sub'},
        'relationship': {'coding': [{'code': 'spouse'}]}, 'payor': [{'reference': 'Organization/payer'}]}
    person = {'resourceType': 'RelatedPerson', 'id': 'sub', 'active': True,
        'patient': {'reference': 'Patient/p'}, 'name': [{'given': ['Sam'], 'family': 'Other'}]}
    visit = {'resourceType': 'Encounter', 'id': 'v', 'status': 'planned',
        'subject': {'reference': 'Patient/p'}, 'period': {'start': day + 'T15:00:00Z'}}
    requested = []
    def handler(request):
        requested.append((request.url.path, dict(request.url.params)))
        reads = {'/fhir/Patient/p': patient, '/fhir/RelatedPerson/sub': person,
                 '/fhir/Encounter/v': visit, '/fhir/Organization/payer': {'resourceType': 'Organization', 'id': 'payer', 'name': 'Example Payer'}}
        if request.url.path in reads: return httpx.Response(200, json=reads[request.url.path])
        rows = [coverage] if request.url.path == '/fhir/Coverage' else []
        return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': row} for row in rows]})
    monkeypatch.setattr(ecw_backend, 'settings', lambda: ('client', 'https://example.test/fhir'))
    monkeypatch.setattr(ecw_backend, '_access_token', AsyncMock(return_value='synthetic'))
    monkeypatch.setattr(ecw_backend.provider_specialty, 'lookup', AsyncMock(return_value={}))
    inputs, _, source = asyncio.run(ecw_backend.fetch_inputs({'ecw_patient_id': 'p', 'patient_first_name': 'Pat',
        'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01'}, {}, 'v', transport=httpx.MockTransport(handler)))
    assert ('/fhir/Coverage', {'patient': 'p'}) in requested
    assert ('/fhir/RelatedPerson/sub', {}) in requested
    assert not any(path == '/fhir/RelatedPerson' for path, _ in requested)
    assert inputs['patient']['patient_first_name'] == 'Pat'
    assert inputs['subscriber']['firstName'] == 'Sam' and inputs['subscriber']['birthDate'] is None
    assert inputs['relationship'] == '01'
    assert source['subscriber_evidence']['patient_linked'] is True
    assert source['subscriber_evidence']['status'] == 'returned'


def test_manual_policy_endpoint_is_absent():
    assert not any('policy-reconciliation' in route.path for route in api.router.routes)


def test_relatedperson_timeout_does_not_use_undocumented_family_search(monkeypatch):
    monkeypatch.setattr(ecw_backend, '_read', AsyncMock(side_effect=asyncio.TimeoutError))
    search = AsyncMock()
    monkeypatch.setattr(ecw_backend, '_search', search)
    assert asyncio.run(ecw_backend._bounded_read(None, 'issuer', 'token', 'RelatedPerson/sub')) == ({}, 'RelatedPerson')
    search.assert_not_awaited()


@pytest.mark.parametrize('multiple', [False, True])
def test_discovery_automatically_prices_single_dependent_policy_and_holds_multiple(appointment, monkeypatch, multiple):
    inputs, source, payer, _ = appointment
    inputs.update(relationship='18', subscriber={'firstName': 'Alex', 'lastName': 'Morgn', 'birthDate': '2001-02-03'})
    inputs['patient'].update(patient_last_name='Morgn', address={
        'address1': '100 Test St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'})
    item = response()
    item.update(payer={'name': source['payer'], 'payorIdentification': payer['stedi_payer_id']},
                planDateInformation={'planBegin': inputs['service_date'].replace('-', '')})
    item['dependent']['address'] = inputs['patient']['address']
    for row in item['benefitsInformation']: row['serviceTypeCodes'] = ['30', '98']
    items = [item]
    if multiple:
        another = deepcopy(item); another['subscriber']['memberId'] = 'OTHER'; items.append(another)
    session = {'practice_id': 'clinic-a', 'patient_id': 'patient', 'intake': {'patient': {}, 'fields': {}}, 'staff': {'id': 'staff'}}
    provider = {'rendering_npi': '2222222222', 'rendering_first': 'Test', 'rendering_last': 'Clinician'}
    case = {'id': 'case', 'revision': 1, 'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'rejected'}}
    result = {'issues': [], 'service_date': inputs['service_date'], 'benefits': api._previsit_benefits({}, api._coverage_snapshot(inputs, source))}
    api._finish_previsit(result, case, inputs, source, None, session, payer['stedi_payer_id'])
    case['previsit_discovery'] = {'request_state': 'pending', 'raw_object_key': 'saved',
        'identity_binding': api._recovery_binding(inputs, source), 'context_token': case['previsit_context']['context_token']}
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(); client.get = AsyncMock()
    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **kwargs: client)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *args: json.dumps({'status': 'COMPLETE', 'coveragesFound': len(items), 'items': items}).encode())
    monkeypatch.setattr(api.object_store, 'put_staff_document', lambda *args: 'protected')
    monkeypatch.setattr(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': payer['stedi_payer_id'], 'displayName': source['payer']}))
    monkeypatch.setattr(api, '_save', lambda value, *_: value)
    asyncio.run(api._recover_discovery(case, session, provider))
    assert (case['previsit_discovery']['outcome'] == 'recovered') is (not multiple)
    if not multiple:
        assert api._previsit_result(case)['collection_guidance']['amount_cents'] == 4000
        assert inputs['subscriber']['firstName'] == 'Alex'
        assert case['previsit_discovery']['verified_coverage']['subscriber_first'] == 'Sam'
    client.post.assert_not_awaited()  # Sufficient saved benefits avoid another paid transaction.
    client.get.assert_not_awaited()
