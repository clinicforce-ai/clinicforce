import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import BackgroundTasks, HTTPException
from app.api.routers import commercial_estimator as api
from app.insurance import stedi_discovery as discovery
from test_visit_estimate import payload


def setup():
    patient = {'patient_first_name': 'Pat', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01',
               'address': {'address1': '100 Test St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'}}
    inputs = {'patient': patient, 'subscriber': {'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'},
              'member_id': 'old-id', 'relationship': '18', 'service_date': date.today().isoformat(),
              'lines': [], 'visit_type': 'new', 'place_of_service': '11'}
    source = {'appointment_id': 'visit', 'coverage_id': 'coverage', 'active_coverages': 1, 'plan': 'Choice'}
    session = {'practice_id': 'clinic', 'intake_id': 'intake', 'patient_id': 'patient', 'share_manifest_id': '',
               'staff': {'id': 'staff', 'display_name': 'Staff'}, 'intake': {'patient': {'given_name': 'Pat', 'family_name': 'Example', 'birth_date': '1980-01-01'}, 'fields': {}}}
    result = {'check_id': 'case', 'appointment_id': 'visit', 'service_date': inputs['service_date'],
              'codes': api._previsit_codes(inputs), 'issues': [], 'benefits': None,
              'eligibility_error': 'eligibility_rejected', 'eligibility_failure': {'codes': ['72']}}
    token = api._visit_context_token(inputs, source)
    result.update(context_token=token, available_codes=result['codes'])
    case = {'id': 'case', 'revision': 1, 'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'rejected'},
            'previsit_raw_object_key': 'original-error', 'previsit_context': {'inputs': inputs, 'source': source, 'result': result, 'payer_id': 'payer', 'context_token': token},
            'previsit_discovery': {'request_state': 'pending', 'context_token': token, 'identity_binding': api._recovery_binding(inputs, source)}}
    provider = {'rendering_npi': '1234567890', 'rendering_first': 'Ada', 'rendering_last': 'Clinician'}
    item = {**payload(), 'subscriber': {'firstName': 'PAT', 'lastName': 'EXAMPLE', 'dateOfBirth': '19800101', 'memberId': 'new-id', 'address': patient['address']},
            'payer': {'name': 'Example Payer', 'payorIdentification': 'example'}}
    return case, session, provider, item


def test_discovery_identity_and_address_are_verified_before_adoption():
    case, _, provider, item = setup(); inputs = case['previsit_context']['inputs']
    assert discovery.request_body(inputs, provider)['encounter']['serviceTypeCodes'] == ['30']
    assert discovery.patient_matches(item, inputs)
    for field, value in [('dateOfBirth', '19800102'), ('firstName', 'Someone'), ('memberId', '')]:
        wrong = deepcopy(item); wrong['subscriber'][field] = value
        assert not discovery.patient_matches(wrong, inputs)
    wrong = deepcopy(item); wrong['subscriber']['address']['address1'] = '200 Other St'
    assert not discovery.patient_matches(wrong, inputs)
    assert not discovery.patient_matches({**item, 'dependent': {'firstName': 'Pat'}}, inputs)
    assert discovery.request_body({**inputs, 'relationship': '19'}, provider) is None
    assert not discovery.patient_address({'address': [{'state': 'TX', 'postalCode': '78701'}, {'state': 'NY', 'postalCode': '10001'}]})


class RecoveryTest(IsolatedAsyncioTestCase):
    async def exercise(self, response, *, directory='payer', timeout=False, saved=False):
        case, session, provider, _ = setup()
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        final = {**payload(), 'id': 'verified', 'subscriber': {'memberId': 'new-id'}}
        client.post = AsyncMock(side_effect=httpx.ReadTimeout('timeout') if timeout else
                               [httpx.Response(200, json=response), httpx.Response(200, json=final)])
        client.get = AsyncMock(return_value=httpx.Response(200, json=response))
        if saved:
            case['previsit_discovery'].update(raw_object_key='saved', discovery_id='discovery-1')
        events = []
        def save(value, _, event):
            events.append(event)
            return value
        with patch.object(api.httpx, 'AsyncClient', return_value=client), patch.object(api, '_save', side_effect=save), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api.object_store, 'get_object_bytes', return_value=json.dumps({'status': 'PENDING'}).encode()), \
             patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': directory})), \
             patch.object(api.commercial_store, 'list_connections', return_value=[]):
            await api._recover_previsit(case, session, provider)
        return case, client, events

    async def test_recovery_rechecks_matching_policy_and_preserves_original_error(self):
        _, _, _, item = setup()
        case, client, events = await self.exercise({'status': 'COMPLETE', 'discoveryId': 'discovery-1', 'coveragesFound': 1, 'items': [item]})
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert case['previsit']['request_state'] == 'complete'
        assert case['previsit_original_error_object_key'] == 'original-error'
        assert client.post.await_count == 2
        assert client.post.call_args.kwargs['json']['subscriber']['memberId'] == 'new-id'
        assert 'stedi-recovered-member-pending' in events
        result = api._previsit_result(case)
        assert 'eligibility_error' not in result and result['benefits']['active'] is True
        assert 'new-id' not in json.dumps(result)
        context = case['previsit_context']
        raw = {'subscriber': {'memberId': 'new-id'}}
        assert not any('identity' in reason for reason in api._case_benefits(raw, context['inputs'], context['source'], case)['review_reasons'])
        planned = {**context['inputs'], 'reason': 'Updated visit reason', 'lines': [{'code': '95907', 'units': 1}]}
        assert not any('identity' in reason for reason in api._case_benefits(raw, planned, context['source'], case)['review_reasons'])
        changed = {**context['inputs'], 'service_date': '2099-01-01'}
        assert any('identity' in reason for reason in api._case_benefits(raw, changed, context['source'], case)['review_reasons'])

    async def test_no_match_ambiguous_patient_or_other_payer_never_becomes_inactive_or_quoted(self):
        _, _, _, item = setup()
        for items, directory, outcome in [([], 'payer', 'no_match'), ([item, item], 'payer', 'review_needed'), ([item], 'other', 'review_needed')]:
            case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': len(items), 'items': items}, directory=directory)
            assert case['previsit_discovery']['outcome'] == outcome
            assert client.post.await_count == 1
            assert case['previsit']['request_state'] == 'rejected'
            assert case['previsit_context']['result']['benefits'] is None

    async def test_pending_result_is_polled_without_a_second_discovery_post(self):
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 0, 'items': []}, saved=True)
        assert client.post.await_count == 0 and client.get.await_count == 1
        assert case['previsit_discovery']['outcome'] == 'no_match'

    async def test_timeout_is_not_retried_and_pending_cannot_be_deleted(self):
        from app.core.staff_deletions import _pending
        case, client, _ = await self.exercise({}, timeout=True)
        assert client.post.await_count == 1 and _pending(case)
        assert case['previsit_discovery']['request_state'] == 'indeterminate'
        _, session, provider, _ = setup()
        tasks = BackgroundTasks()
        result = await api._start_previsit_recovery(case, session, provider, tasks)
        assert not tasks.tasks and result['recovery']['state'] == 'indeterminate'
        stalled = {'request_state': 'pending', 'started_at': (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()}
        assert discovery.public_status(stalled)['state'] == 'indeterminate'
        assert _pending(stalled)

    async def test_failed_check_can_plan_services_but_cannot_verify_benefits_or_payments(self):
        case, session, _, _ = setup(); case.pop('previsit_discovery')
        context = case['previsit_context']; inputs, source = context['inputs'], context['source']
        with patch.object(api, '_session', AsyncMock(return_value=session)), patch.object(api.store, 'get_case', return_value=case), \
             patch.object(api.ecw_backend, 'fetch_inputs', AsyncMock(return_value=(inputs, [], source))), \
             patch.object(api, '_ecw_practice_config', return_value={}), \
             patch.object(api.object_store, 'get_object_bytes', return_value=b'{"errors":[{"code":"72"}]}'), \
             patch.object(api.commercial_store, 'list_connections', return_value=[]), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            updated = await api.update_visit_review('case', api.VisitReviewInput(context_token=context['context_token'], revision=1, selected_ids=['99203:1'], confirmed_units={'99203:1': 1}), MagicMock())
            assert len(updated['codes']) == 1 and updated['codes'][0]['code'] == '99203'
            assert updated['benefits']['active'] is None and 'patient_max_cents' not in updated['price_range']
            with self.assertRaises(HTTPException):
                await api.update_visit_review('case', api.VisitReviewInput(context_token=context['context_token'], revision=1, payments_received_cents=0, payment_reference='Ledger'), MagicMock())
