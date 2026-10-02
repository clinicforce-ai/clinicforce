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
    source = {'appointment_id': 'visit', 'coverage_id': 'coverage', 'active_coverages': 1,
              'payer': 'Example Payer', 'plan': 'Choice'}
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
    assert discovery.request_body({**inputs, 'relationship': '19'}, provider)
    assert not discovery.patient_address({'address': [{'state': 'TX', 'postalCode': '78701'}, {'state': 'NY', 'postalCode': '10001'}]})


def test_discovery_returns_safe_dependent_candidate_for_staff_selection():
    case, _, _, item = setup(); inputs = case['previsit_context']['inputs']
    dependent = deepcopy(item)
    dependent['subscriber'] = {'firstName': 'Parent', 'lastName': 'Example', 'dateOfBirth': '19500101', 'memberId': 'family-id'}
    dependent['dependent'] = {'firstName': 'Pat', 'lastName': 'Example', 'dateOfBirth': '19800101', 'address': inputs['patient']['address']}
    rows = discovery.candidates({'items': [dependent]}, inputs)
    assert len(rows) == 1
    assert rows[0]['relationship'] == 'dependent'
    assert rows[0]['address_matches'] is True
    assert 'family-id' not in json.dumps(rows)


def test_automatic_payer_recovery_requires_same_active_ecw_policy():
    case, _, _, item = setup()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['member_id'] = 'new-id'
    assert discovery.matches_ecw_policy(item, inputs, source)
    no_address = deepcopy(item)
    no_address['subscriber'].pop('address')
    assert discovery.matches_ecw_policy(no_address, inputs, source)
    assert not discovery.matches_ecw_policy(item, {**inputs, 'member_id': 'different'}, source)
    assert not discovery.matches_ecw_policy(item, inputs, {**source, 'active_coverages': 2})
    assert discovery.matches_ecw_policy(item, inputs, {**source, 'payer': 'UHC Medicare'})
    assert not discovery.matches_ecw_policy(item, inputs, {**source, 'payer_identifiers': ['different-payer']})
    assert discovery.matches_ecw_policy(item, inputs, {**source, 'payer_identifiers': ['example']})
    inactive = deepcopy(item)
    inactive['benefitsInformation'] = [{'code': '6', 'serviceTypeCodes': ['30']}]
    inactive.pop('plans', None)
    assert not discovery.matches_ecw_policy(inactive, inputs, source)
    expired = deepcopy(item)
    expired['planDateInformation'] = {'planEnd': '20200101'}
    assert not discovery.matches_ecw_policy(expired, inputs, source)


def test_failed_automatic_routes_open_cash_toggle_when_quote_is_complete():
    case, _, _, _ = setup()
    case['previsit_context']['result'].update(eligibility_error='payer_routing_unresolved',
        payment_mode='insurance', cash_quote={'complete': True})
    case['previsit_discovery'].update(request_state='complete', outcome='no_match')
    assert api._previsit_result(case)['payment_mode'] == 'cash'
    case['visit_review'] = {'payment_mode': 'insurance'}
    assert api._previsit_result(case)['payment_mode'] == 'insurance'
    case.pop('visit_review')
    case['previsit_discovery']['request_state'] = 'pending'
    assert api._previsit_result(case)['payment_mode'] == 'insurance'
    case['previsit_discovery']['request_state'] = 'complete'
    case['previsit_context']['result']['cash_quote']['complete'] = False
    assert api._previsit_result(case)['payment_mode'] == 'insurance'


class RecoveryTest(IsolatedAsyncioTestCase):
    async def test_unroutable_ecw_payer_starts_discovery_automatically(self):
        case, session, provider, _ = setup()
        case.pop('previsit_discovery')
        session['intake']['updated_at'] = 'version'
        inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
        source['payer'] = 'Humana Health Plan'
        inputs['patient']['address'] = {'address1': '100 Test St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'}
        tasks = BackgroundTasks()
        with patch.object(api.store, 'create_case', return_value=case), \
             patch.object(api, '_finish_previsit', side_effect=lambda result, saved, *_: saved['previsit_context'].update(result=result)), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            result = await api._begin_coverage_discovery({'service_date': inputs['service_date'], 'issues': [], 'codes': []}, session, inputs, source, provider, tasks,
                'The eCW payer name and ID did not identify one Stedi route.')
        assert result['eligibility_error'] == 'payer_routing_unresolved'
        assert result['recovery']['state'] == 'pending'
        assert len(tasks.tasks) == 1

    async def test_unroutable_ecw_payer_is_recovered_without_staff_selection(self):
        case, session, provider, item = setup()
        case['previsit_context']['payer_id'] = None
        case['previsit_context']['inputs']['member_id'] = 'new-id'
        case['previsit_context']['result']['eligibility_failure']['detail'] = 'Payer route unresolved'
        response = {'status': 'COMPLETE', 'discoveryId': 'discovery-1', 'coveragesFound': 1, 'items': [item]}
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=[httpx.Response(200, json=response),
                                              httpx.Response(200, json={**payload(), 'subscriber': {'memberId': 'new-id'}})])
        with patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'HUMAN'})), \
             patch.object(api.commercial_store, 'list_connections', return_value=[]):
            await api._recover_previsit(case, session, provider)
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert case['previsit']['request_state'] == 'complete'
        assert client.post.await_count == 2

    async def test_recent_no_match_and_ambiguous_results_are_reused_without_paid_search(self):
        for outcome, refresh, expired, changed in [
                ('no_match', False, False, False), ('review_needed', False, False, False),
                ('no_match', True, False, False), ('no_match', False, True, False),
                ('no_match', False, False, True)]:
            case, session, provider, _ = setup()
            session['intake']['updated_at'] = 'version'
            context = case['previsit_context']
            recovery = case['previsit_discovery']
            recovery.update(request_state='complete', outcome=outcome, discovery_id='saved-search',
                            checked_at=(datetime.now(timezone.utc) - timedelta(hours=25 if expired else 1)).isoformat())
            if changed:
                recovery['identity_binding'] = 'different-patient-details'
            tasks = BackgroundTasks()
            with patch.object(api.store, 'create_case', return_value=case), \
                 patch.object(api, '_finish_previsit'), \
                 patch.object(api, '_save', side_effect=lambda value, *_: value):
                await api._begin_coverage_discovery({}, session, context['inputs'], context['source'],
                    provider, tasks, 'Missing policy', refresh_benefits=refresh)
            if refresh or expired or changed:
                assert len(tasks.tasks) == 1
                assert case['previsit_discovery']['request_state'] == 'pending'
            else:
                assert not tasks.tasks
                assert case['previsit_discovery']['discovery_id'] == 'saved-search'
                assert case['previsit_discovery']['outcome'] == outcome

    async def test_explicit_refresh_restarts_completed_discovery_but_not_pending_work(self):
        case, session, provider, _ = setup()
        session['intake']['updated_at'] = 'version'
        context = case['previsit_context']
        case['previsit']['request_state'] = 'complete'
        case['previsit_discovery'].update(request_state='complete', outcome='recovered')
        tasks = BackgroundTasks()
        with patch.object(api.store, 'create_case', return_value=case), \
             patch.object(api, '_finish_previsit'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            await api._begin_coverage_discovery({}, session, context['inputs'], context['source'], provider, tasks, 'Missing policy')
            assert not tasks.tasks
            await api._begin_coverage_discovery({}, session, context['inputs'], context['source'], provider, tasks, 'Missing policy', refresh_benefits=True)
            assert case['previsit_discovery']['request_state'] == 'pending'
            assert len(tasks.tasks) == 1
            await api._begin_coverage_discovery({}, session, context['inputs'], context['source'], provider, tasks, 'Missing policy', refresh_benefits=True)
            assert len(tasks.tasks) == 1

    def setUp(self):
        self.cash_patch = patch.object(api.practice_prices, 'current', return_value=None)
        self.cash_patch.start()
        self.addCleanup(self.cash_patch.stop)

    async def exercise(self, response, *, directory='payer', timeout=False, saved=False, unroutable=False,
                       matching_ecw=False):
        case, session, provider, _ = setup()
        if unroutable:
            case['previsit_context']['payer_id'] = None
            case['previsit_context']['result'].update(eligibility_error='payer_routing_unresolved',
                eligibility_failure={'kind': 'payer_routing_unresolved', 'detail': 'Payer route unresolved', 'codes': []})
        if matching_ecw:
            case['previsit_context']['inputs']['member_id'] = 'new-id'
            case['previsit_context']['source']['payer'] = 'UHC Medicare'
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
        assert result['insurance_details']['payer_subscriber']['memberId'] == 'new-id'
        assert 'eligibility_object_key' not in json.dumps(result)
        context = case['previsit_context']
        raw = {'subscriber': {'memberId': 'new-id'}}
        assert not any('identity' in reason for reason in api._case_benefits(raw, context['inputs'], context['source'], case)['review_reasons'])
        planned = {**context['inputs'], 'reason': 'Updated visit reason', 'lines': [{'code': '95907', 'units': 1}]}
        assert not any('identity' in reason for reason in api._case_benefits(raw, planned, context['source'], case)['review_reasons'])
        changed = {**context['inputs'], 'service_date': '2099-01-01'}
        assert any('identity' in reason for reason in api._case_benefits(raw, changed, context['source'], case)['review_reasons'])

    async def test_unroutable_label_selects_one_active_ecw_member_among_discovery_results(self):
        _, _, _, item = setup()
        item['payer']['name'] = 'UnitedHealthcare Group Medicare Advantage PPO'
        other = deepcopy(item)
        other['subscriber']['memberId'] = 'another-member'
        case, client, _ = await self.exercise(
            {'status': 'COMPLETE', 'coveragesFound': 2, 'items': [other, item]},
            unroutable=True, matching_ecw=True)
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert client.post.await_count == 2
        assert case['previsit_context']['result']['benefits']['active'] is True

    async def test_unresolved_route_only_offers_matching_ecw_policy_for_selection(self):
        _, _, _, item = setup()
        other = deepcopy(item)
        other['subscriber']['memberId'] = 'different-member'
        case, client, _ = await self.exercise(
            {'status': 'COMPLETE', 'coveragesFound': 3, 'items': [other, item, item]},
            unroutable=True, matching_ecw=True)
        case['previsit_context']['payer_id'] = ''
        assert case['previsit_discovery']['outcome'] == 'review_needed'
        assert [row['matches_ecw_policy'] for row in case['previsit_discovery']['candidates']] == [False, True, True]
        assert client.post.await_count == 1

        session = setup()[1]
        candidate = case['previsit_discovery']['candidates'][0]
        with patch.object(api, '_session', AsyncMock(return_value=session)), \
             patch.object(api.store, 'get_case', return_value=case):
            with self.assertRaises(HTTPException) as rejected:
                await api.select_coverage_candidate('case', api.CoverageCandidateInput(
                    context_token=case['previsit_context']['context_token'],
                    revision=case['revision'], candidate_id=candidate['id']), MagicMock(), BackgroundTasks())
        assert rejected.exception.status_code == 409

    async def test_no_match_ambiguous_patient_or_other_payer_never_becomes_inactive_or_quoted(self):
        _, _, _, item = setup()
        for items, directory, outcome in [([], 'payer', 'no_match'), ([item, item], 'payer', 'review_needed'), ([item], 'other', 'review_needed')]:
            case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': len(items), 'items': items}, directory=directory)
            assert case['previsit_discovery']['outcome'] == outcome
            assert client.post.await_count == 1
            assert case['previsit']['request_state'] == 'rejected'
            assert case['previsit_context']['result']['benefits'] is None

    async def test_failed_directory_and_discovery_report_unverified_not_inactive(self):
        _, _, _, item = setup()
        for items in ([], [item]):
            case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': len(items), 'items': items},
                                                   unroutable=True)
            detail = case['previsit_context']['result']['eligibility_failure']['detail']
            assert 'payer name and ID' in detail and 'discover' in detail.lower()
            assert 'unverified, not proven inactive' in detail
            assert case['previsit_discovery']['reason_code'] == 'no_matching_ecw_policy'
            assert client.post.await_count == 1

    def test_public_recovery_status_includes_safe_stop_reason_and_reference(self):
        status = discovery.public_status({'request_state': 'complete', 'outcome': 'review_needed',
                                          'stage': 'candidate_match_review',
                                          'reason_code': 'multiple_matching_ecw_policies',
                                          'discovery_id': 'discovery-1'})
        assert status['stage'] == 'candidate_match_review'
        assert status['reason_code'] == 'multiple_matching_ecw_policies'
        assert status['reference'] == 'discovery-1'

    async def test_other_payer_is_selectable_without_exposing_member_id(self):
        _, _, _, item = setup()
        case, _, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]}, directory='other')
        public = api._previsit_result(case)['recovery']
        assert public['outcome'] == 'review_needed'
        assert public['candidates'][0]['payer'] == 'Example Payer'
        assert 'new-id' not in json.dumps(public)

    async def test_staff_selection_is_bound_to_saved_case_and_candidate(self):
        case, session, _, item = setup()
        candidate = discovery.candidates({'items': [item]}, case['previsit_context']['inputs'])[0]
        case['previsit_discovery'].update(request_state='complete', outcome='review_needed', candidates=[candidate], raw_object_key='protected')
        tasks = BackgroundTasks()
        with patch.object(api, '_session', AsyncMock(return_value=session)), \
             patch.object(api.store, 'get_case', return_value=case), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            result = await api.select_coverage_candidate('case', api.CoverageCandidateInput(
                context_token=case['previsit_context']['context_token'], revision=1, candidate_id=candidate['id']), MagicMock(), tasks)
        assert result['recovery']['state'] == 'pending'
        assert case['previsit_discovery']['selected_candidate_id'] == candidate['id']
        assert len(tasks.tasks) == 1

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
