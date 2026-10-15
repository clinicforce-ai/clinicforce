"""Automatic recovery contracts with synthetic records and intercepted payer calls."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.requests import Request

from app.api.routers import commercial_estimator as api
from app.insurance import stedi_discovery as discovery
from test_stedi_discovery import setup


@pytest.fixture
def recovery(monkeypatch):
    case, session, provider, item = setup()
    context = case['previsit_context']
    context['inputs'].update(member_id=None, subscriber={}, relationship=None)
    context['source'].update(active_coverages=0, coverage_id=None, payer=None)
    context['payer_id'] = None
    context['result'].update(eligibility_error='coverage_identity_missing', payment_mode='insurance',
        price_range={'cash_complete': True, 'cash_min_cents': 16000, 'cash_max_cents': 16000})
    case['previsit_discovery']['identity_binding'] = api._recovery_binding(context['inputs'], context['source'])
    objects, snapshots = {}, []

    def put(*args):
        key = f'protected-{len(objects)}'
        objects[key] = args[3]
        return key

    def save(value, *_):
        snapshots.append(api._previsit_result(value))
        return {**value, 'revision': value.get('revision', 0) + 1}

    monkeypatch.setattr(api, '_save', save)
    monkeypatch.setattr(api.object_store, 'put_staff_document', put)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda key: objects[key])
    monkeypatch.setattr(api.store, 'patient_checks', lambda *_: [])
    monkeypatch.setattr(api.staff_store, 'patient_card_intakes', lambda *_: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *_: None)
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *_: [])
    monkeypatch.setattr(api.stedi_financial, 'credentials', lambda *_: 'synthetic-key')
    monkeypatch.setattr(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE', 'displayName': 'Example'}))
    monkeypatch.setattr(api.stedi_payers, 'eligibility_enrollment', AsyncMock(return_value={'status': 'LIVE', 'reason_code': 'enrollment_live'}))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=httpx.Response(200, json={'status': 'COMPLETE', 'coveragesFound': 0, 'items': []}))
    client.get = AsyncMock(side_effect=AssertionError('Unexpected GET'))
    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **_: client)
    return case, session, provider, item, client, objects, snapshots


@pytest.mark.parametrize('member_id', [None, 'new-id'])
def test_empty_coverage_can_query_note_payer_without_inventing_policyholder(recovery, member_id):
    case, session, provider, item, client, _, _ = recovery
    context = case['previsit_context']
    context['source']['payer'] = 'Example Payer'
    context['inputs']['member_id'] = member_id
    case['previsit_discovery']['identity_binding'] = api._recovery_binding(context['inputs'], context['source'])
    client.post.return_value = httpx.Response(200, json=item)
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1
    assert client.post.call_args.args[0] == api.STEDI_URL
    assert ('memberId' in client.post.call_args.kwargs['json']['subscriber']) == bool(member_id)
    assert context['inputs']['relationship'] is None and context['inputs']['subscriber'] == {}
    result = api._previsit_result(case)
    assert result['coverage_status'] == 'VERIFIED'
    assert result['insurance_details']['member_id'] == 'new-id'
    assert result['payment_mode'] == 'cash'  # Separate price fallback preserves verified coverage.


def test_cash_only_after_all_applicable_checks_finish(recovery):
    case, session, provider, _, client, _, snapshots = recovery
    asyncio.run(api._recover_previsit(case, session, provider))
    result = api._previsit_result(case)
    assert client.post.await_count == 1
    assert result['recovery']['exhausted'] and result['payment_mode'] == 'cash'
    assert result['coverage_status'] == 'NOT_FOUND'
    assert result['collection_guidance']['amount_cents'] == 16000
    assert all(row['payment_mode'] == 'insurance' for row in snapshots[:-1])
    assert all(row['recovery']['state'] == 'pending' for row in snapshots[:-1])
    assert 'completed insurance search returned no policy' in result['cash_fallback']['reason']
    case['previsit_context']['result']['price_range'] = {}
    assert api._previsit_result(case)['collection_guidance']['amount_cents'] is None


@pytest.mark.parametrize('route_found', [True, False])
def test_conflicting_card_checks_only_its_unique_route(recovery, monkeypatch, route_found):
    case, session, provider, item, client, _, _ = recovery
    context = case['previsit_context']
    context['inputs'].update(member_id='new-id', group_number='G7', relationship='18',
        subscriber={'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'})
    context['source'].update(_patient_id='ecw-patient', coverage_id='coverage', active_coverages=1,
        payer='BCBS Texas')
    card = {'status': 'read', 'count': 1, 'source': 'eCW card', 'evidence': {'source': 'eCW card'},
            'fields': {'payer': 'BCBS Illinois', 'member_id': 'new-id', 'group_number': 'G7',
                       'member_name': 'Pat Example', 'birth_date': '1980-01-01'}}
    check = api._compare_card_hints(card, context['inputs'], context['source'])
    assert check['status'] == 'conflict' and check['alternative_supported']
    context['result'].update(card_check=check, eligibility_error='card_conflict',
        eligibility_failure={'kind': 'card_conflict', 'detail': 'The card and eCW insurers differ.', 'codes': []})
    case['previsit_discovery'].update(card_conflict=True,
        card_fingerprint=check['alternative_fingerprint'], request_state='pending')
    monkeypatch.setattr(api, '_read_card_hints', AsyncMock(return_value=card))
    monkeypatch.setattr(api.stedi_payers, 'search_payer', AsyncMock(return_value=(
        {'stediId': 'ILLINOIS', 'displayName': 'BCBS Illinois'} if route_found else None)))
    item['payer'] = {'name': 'BCBS Illinois'}
    client.post.return_value = httpx.Response(200, json=item)
    asyncio.run(api._recover_previsit(case, session, provider))
    result = api._previsit_result(case)
    assert client.post.await_count == (1 if route_found else 0)
    if route_found:
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert result['payment_explanation']['notice']
    else:
        assert case['previsit_discovery']['outcome'] == 'review_needed'
        assert result['payment_mode'] == 'cash'


def test_discovery_with_requested_services_reuses_item_without_paid_recheck(recovery):
    case, session, provider, item, client, objects, _ = recovery
    case['previsit_context']['result']['enrollment'] = {'status': 'MISSING', 'reason_code': 'enrollment_missing'}
    for row in item['benefitsInformation']:
        row['serviceTypeCodes'] = api._previsit_service_types(case['previsit_context']['inputs'])
    client.post.return_value = httpx.Response(200, json={'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]})
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1
    assert case['previsit_discovery']['outcome'] == 'recovered'
    diagnosis = api._previsit_result(case)['insurance_diagnosis']
    assert 'ENROLLMENT_BLOCKED' not in diagnosis['states']
    assert diagnosis['verifiedCandidateCount'] == 1
    saved = json.loads(objects[case['previsit_raw_object_key']])
    assert saved['subscriber']['memberId'] == 'new-id' and 'items' not in saved


@pytest.mark.parametrize('fresh', [True, False])
def test_linked_patient_policy_reuses_current_benefits_or_rechecks_old_identifier(recovery, monkeypatch, fresh):
    case, session, provider, item, client, objects, _ = recovery
    old = deepcopy(case)
    old.update(id='older-case', previsit_raw_object_key='old-response')
    old['previsit'].update(request_state='complete',
        checked_at=(datetime.now(timezone.utc) - timedelta(hours=1 if fresh else 25)).isoformat(),
        requested_service_types=api._previsit_service_types(case['previsit_context']['inputs']))
    old['previsit_context']['source']['rendering_provider'] = {'npi': provider['rendering_npi']}
    old['previsit_context']['payer_id'] = 'EXAMPLE'
    objects['old-response'] = json.dumps(item).encode()
    monkeypatch.setattr(api.store, 'patient_checks', lambda *args: [{'case': old, 'intake_id': 'prior-linked-intake'}])
    client.post.return_value = httpx.Response(200, json=item)
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == (0 if fresh else 1)
    assert api._previsit_result(case)['insurance_details']['member_id'] == 'new-id'


def medicare(recovery):
    case, _, provider, item, *_ = recovery
    case['previsit_context']['source']['plan'] = 'MB'
    case['previsit_context']['source']['_request_ip_chain'] = '192.0.2.1, 192.0.2.2'
    case['previsit_discovery']['identity_binding'] = api._recovery_binding(case['previsit_context']['inputs'], case['previsit_context']['source'])
    provider.update(billing_npi='1999999984', organization_name='Synthetic Practice')
    item['subscriber']['memberId'] = '1EG4TE5MK73'
    item['tradingPartnerServiceId'] = 'CMS'


def test_mbi_lookup_after_discovery_recovers_identifier_without_ssn(recovery):
    medicare(recovery)
    case, session, provider, item, client, objects, snapshots = recovery
    client.post.side_effect = [client.post.return_value, httpx.Response(200, json=item)]
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 2
    call = client.post.call_args
    assert call.args[0] == discovery.MBI_URL
    assert call.kwargs['json']['tradingPartnerServiceId'] == 'MBILUNOSSN'
    assert call.kwargs['json']['subscriber']['address'] == {'state': 'TX'}
    assert call.kwargs['headers']['X-Forwarded-For'] == '192.0.2.1, 192.0.2.2'
    assert 'ssn' not in json.dumps(call.kwargs['json'])
    result = api._previsit_result(case)
    assert result['insurance_details']['member_id'] == '1EG4TE5MK73'
    assert result['insurance_details']['member_source'] == 'medicare_mbi_lookup'
    assert result['payment_mode'] == 'cash'  # Separate price fallback preserves verified coverage.
    assert all(row['payment_mode'] != 'cash' for row in snapshots if row.get('coverage_status') != 'VERIFIED')
    assert case['previsit']['requested_service_types'] == ['30']


def test_missing_mbi_enrollment_skips_paid_lookup_and_finishes_cash(recovery, monkeypatch):
    medicare(recovery)
    case, session, provider, _, client, *_ = recovery
    monkeypatch.setattr(api.stedi_payers, 'eligibility_enrollment', AsyncMock(return_value={'status': 'MISSING', 'reason_code': 'enrollment_missing'}))
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1
    assert api._previsit_result(case)['payment_mode'] == 'cash'
    assert case['previsit_discovery']['steps']['mbi'] == 'enrollment_unavailable'


def test_mbi_timeout_stays_unknown_and_cannot_start_another_request(recovery):
    medicare(recovery)
    case, session, provider, _, client, *_ = recovery
    client.post.side_effect = [client.post.return_value, httpx.ReadTimeout('synthetic')]
    asyncio.run(api._recover_previsit(case, session, provider))
    result = api._previsit_result(case)
    assert result['recovery']['state'] == 'indeterminate'
    assert result['payment_mode'] == 'insurance' and not result['recovery']['exhausted']
    tasks = api.BackgroundTasks()
    asyncio.run(api._start_previsit_recovery(case, session, provider, tasks))
    assert not tasks.tasks and client.post.await_count == 2


@pytest.mark.parametrize('change', ['wrong_dob', 'payer_error', 'inactive', 'wrong_payer', 'managed_plan'])
def test_unverified_mbi_response_cannot_establish_coverage(recovery, change):
    medicare(recovery)
    case, session, provider, item, client, *_ = recovery
    if change == 'wrong_dob':
        item['subscriber']['dateOfBirth'] = '19800102'
    elif change == 'payer_error':
        item['errors'] = [{'code': '72'}]
    elif change == 'wrong_payer':
        item['tradingPartnerServiceId'] = 'OTHER'
    elif change == 'managed_plan':
        item['benefitsInformation'].append({'code': 'U', 'insuranceTypeCode': 'HN', 'serviceTypeCodes': ['30']})
    else:
        item['benefitsInformation'] = [{'code': '6', 'serviceTypeCodes': ['30']}]
    client.post.side_effect = [client.post.return_value, httpx.Response(200, json=item)]
    asyncio.run(api._recover_previsit(case, session, provider))
    assert api._previsit_result(case)['coverage_status'] == 'REVIEW_REQUIRED'
    assert case['previsit_discovery']['steps']['mbi'] == 'not_verified'
    assert not case['previsit_discovery'].get('verified_coverage')


def test_multiple_policies_do_not_trigger_mbi_or_select_a_primary(recovery):
    medicare(recovery)
    case, session, provider, item, client, *_ = recovery
    other = deepcopy(item)
    other['subscriber']['memberId'] = 'OTHER123'
    client.post.return_value = httpx.Response(200, json={'status': 'COMPLETE', 'coveragesFound': 2, 'items': [item, other]})
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1
    assert api._previsit_result(case)['payment_mode'] == 'cash'
    assert api._previsit_result(case)['coverage_status'] == 'REVIEW_REQUIRED'
    assert not case['previsit_discovery'].get('verified_coverage')


def test_mbi_never_uses_age_or_unknown_plan_as_evidence(recovery):
    case, _, provider, *_ = recovery
    provider.update(billing_npi='1999999984', organization_name='Synthetic Practice')
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['patient']['patient_birth_date'] = '1940-01-01'
    for plan in (None, 'CI', 'Medicare Advantage'):
        assert discovery.mbi_request_body(inputs, {**source, 'plan': plan}, provider) is None
    assert discovery.mbi_request_body(inputs, {**source, 'plan': 'MB', 'active_coverages': 2}, provider) is None


def test_missing_request_trace_skips_mbi_without_fabricating_an_origin(recovery):
    medicare(recovery)
    case, session, provider, _, client, *_ = recovery
    case['previsit_context']['source'].pop('_request_ip_chain')
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1
    assert case['previsit_discovery']['steps']['mbi'] == 'request_trace_unavailable'


def test_request_trace_is_validated_and_not_part_of_public_results(recovery):
    request = Request({'type': 'http', 'headers': [(b'x-forwarded-for', b'192.0.2.1')],
                       'client': ('192.0.2.2', 1000)})
    assert api._request_ip_chain(request) == '192.0.2.1, 192.0.2.2'
    request = Request({'type': 'http', 'headers': [(b'x-forwarded-for', b'bad-value')],
                       'client': ('192.0.2.2', 1000)})
    assert api._request_ip_chain(request) is None
    case = recovery[0]
    case['previsit_context']['source']['_request_ip_chain'] = '192.0.2.1'
    assert '192.0.2.1' not in json.dumps(api._previsit_result(case))


def test_another_person_in_saved_evidence_is_never_used(recovery, monkeypatch):
    case, session, provider, item, client, objects, _ = recovery
    old = deepcopy(case)
    old.update(id='old', previsit_raw_object_key='old-response')
    old['previsit']['request_state'] = 'complete'
    item['subscriber']['dateOfBirth'] = '19800102'
    objects['old-response'] = json.dumps(item).encode()
    monkeypatch.setattr(api.store, 'patient_checks', lambda *_: [{'case': old}])
    asyncio.run(api._recover_previsit(case, session, provider))
    assert client.post.await_count == 1 and client.post.call_args.args[0] == discovery.URL
    assert not case['previsit_discovery'].get('verified_coverage')
