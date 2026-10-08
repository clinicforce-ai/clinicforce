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
    item = {**payload(), 'planDateInformation': {'planBegin': inputs['service_date'].replace('-', '')},
            'subscriber': {'firstName': 'PAT', 'lastName': 'EXAMPLE', 'dateOfBirth': '19800101', 'memberId': 'new-id', 'address': patient['address']},
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
    assert rows[0]['member_id'] == 'family-id'  # Authorized case response, never an audit event.


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


def test_discovery_recovers_missing_member_and_dependent_with_prior_address():
    case, _, provider, item = setup()
    inputs = case['previsit_context']['inputs']
    inputs.update(member_id=None, relationship='01')
    source = {'active_coverages': 0}
    inputs['patient']['previous_addresses'] = [inputs['patient']['address']]
    inputs['patient']['address'] = {'state': 'NY'}
    item['dependent'] = {**item['subscriber'], 'relationToSubscriberCode': '01'}
    item['subscriber'] = {'firstName': 'Spouse', 'lastName': 'Different', 'memberId': 'FAMILY-123'}
    inputs['subscriber'] = {'firstName': 'Spouse', 'lastName': 'Different', 'birthDate': '1982-02-02'}
    request = discovery.request_body(inputs, provider)
    assert request['subscriber']['firstName'] == 'Spouse'
    assert request['dependent']['firstName'] == 'Pat'
    assert discovery.automatic_match(item, inputs, source)
    coverage = api._discovered_coverage(inputs, source, item)
    assert coverage['relationship'] == '01'
    assert coverage['subscriber_birth_date'] is None
    coverage.pop('subscriber_birth_date')
    body = api.stedi_request_body({'stedi_payer_id': 'EXAMPLE'}, provider, coverage, inputs['service_date'], '98')
    assert 'dateOfBirth' not in body['subscriber']
    assert body['dependent']['dateOfBirth'] == '1980-01-01'
    item['dependent']['dateOfBirth'] = '19800102'
    assert not discovery.automatic_match(item, inputs, source)


def test_partial_address_and_medical_coverage_are_not_discarded_or_invented():
    assert discovery.patient_address({'address': [{'state': 'TX'}]}) == {'state': 'TX'}
    case, _, _, item = setup()
    inputs = case['previsit_context']['inputs']
    for row in item['benefitsInformation']:
        row['serviceTypeCodes'] = ['35']
    assert not discovery.active_medical(item, inputs)


def test_card_identity_hints_require_named_patient_or_known_subscriber():
    case, _, _, _ = setup()
    inputs = case['previsit_context']['inputs']
    fields = {'member_name': 'Pat Example', 'member_id': 'CARD-123', 'payer': 'Example Payer', 'group_number': 'GROUP'}
    item = discovery.card_candidate(fields, inputs)
    assert item['subscriber']['memberId'] == 'CARD-123'
    assert item['subscriber']['groupNumber'] == 'GROUP'
    assert not discovery.active_medical(item, inputs)
    assert not discovery.card_candidate({**fields, 'member_name': 'Another Person'}, inputs)
    assert not discovery.card_candidate({**fields, 'birth_date': '1981-01-01'}, inputs)


def test_known_route_demographic_request_never_fabricates_member_id():
    case, _, provider, _ = setup()
    inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
    inputs['member_id'] = None
    coverage = api._coverage_snapshot(inputs, source)
    body = api.stedi_request_body({'stedi_payer_id': 'EXAMPLE'}, provider, coverage, inputs['service_date'], '98', allow_demographic=True)
    assert 'memberId' not in body['subscriber']
    assert body['subscriber']['dateOfBirth'] == '1980-01-01'


def test_completed_recovery_falls_back_to_cash_without_inventing_price_or_coverage():
    case, _, _, _ = setup()
    case['previsit_context']['result'].update(eligibility_error='payer_routing_unresolved',
        payment_mode='insurance', cash_quote={'complete': True})
    case['previsit_discovery'].update(request_state='complete', outcome='no_match')
    result = api._previsit_result(case)
    assert result['payment_mode'] == 'cash'
    assert result['coverage_status'] == 'NOT_FOUND'
    assert result['collection_guidance']['amount_cents'] is None
    assert result['collection_guidance']['status'] == 'cash_price_missing'
    case['previsit_context']['result']['price_range'] = {'cash_complete': True, 'cash_min_cents': 16000, 'cash_max_cents': 16000}
    assert api._previsit_result(case)['collection_guidance']['amount_cents'] == 16000
    case['visit_review'] = {'payment_mode': 'insurance'}
    assert api._previsit_result(case)['payment_mode'] == 'insurance'
    case.pop('visit_review')
    case['previsit_discovery']['request_state'] = 'pending'
    assert api._previsit_result(case)['payment_mode'] == 'insurance'
    case['previsit_discovery']['request_state'] = 'complete'
    case['previsit_context']['result']['cash_quote']['complete'] = False
    assert api._previsit_result(case)['payment_mode'] == 'cash'


class RecoveryTest(IsolatedAsyncioTestCase):
    async def test_card_ocr_recovers_missing_policy_but_missing_rates_never_invent_price(self):
        from app.api.routers import staff
        from app.insurance import v1_sources
        case, session, provider, item = setup()
        inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
        inputs.update(member_id=None, relationship=None, subscriber={})
        source.update(_patient_id='synthetic', active_coverages=0, coverage_id='', payer=None)
        case['previsit_context']['payer_id'] = None
        case['previsit_discovery']['identity_binding'] = api._recovery_binding(inputs, source)
        item['subscriber']['memberId'] = 'CARD-123'
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=httpx.Response(200, json=item))
        with patch.object(api.staff_store, 'patient_card_intakes', return_value=[]), \
             patch.object(api.ecw_backend, 'insurance_cards', AsyncMock(side_effect=[[{'id': 'card:0'}], (b'synthetic', 'image/png')])), \
             patch.object(staff, 'inspect_file', return_value=('image/png', 1)), \
             patch.object(v1_sources, 'read_card_fields', return_value=({'member_name': 'Pat Example', 'member_id': 'CARD-123', 'payer': 'Example Payer'}, {}, [1])), \
             patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE'})), \
             patch.object(api.commercial_store, 'list_connections', return_value=[]), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api, '_recovery_event') as audit:
            await api._recover_previsit(case, session, provider)
        assert client.post.await_count == 1  # Eligibility from card; no discovery submission.
        assert client.post.call_args.args[0] != discovery.URL
        result = api._previsit_result(case)
        assert result['coverage_status'] == 'VERIFIED'
        assert result['insurance_details']['member_id'] == 'CARD-123'
        assert result['collection_guidance']['amount_cents'] is None
        assert any(call.args[2:4] == ('card_download', 'complete') for call in audit.call_args_list)
        assert any(call.args[2:4] == ('card_ocr', 'complete') for call in audit.call_args_list)

    async def test_saved_empty_upstream_result_is_terminal_without_repeating_requests(self):
        case, session, provider, _ = setup()
        inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
        inputs.update(member_id=None, subscriber={}, relationship=None)
        source.update(active_coverages=0, coverage_id='', payer=None)
        case['previsit_context']['payer_id'] = None
        case['previsit_discovery'].update(raw_object_key='saved', identity_binding=api._recovery_binding(inputs, source))
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(); client.get = AsyncMock()
        with patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api.object_store, 'get_object_bytes', return_value=json.dumps(
                 {'status': 'COMPLETE', 'coveragesFound': 0, 'items': []}).encode()), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api, '_recovery_event') as audit:
            await api._recover_previsit(case, session, provider)
            result = api._previsit_result(case)
            tasks = BackgroundTasks()
            await api._start_previsit_recovery(case, session, provider, tasks)
        client.post.assert_not_called(); client.get.assert_not_called()
        assert not tasks.tasks
        assert result['recovery']['state'] == 'complete'
        assert result['coverage_status'] == 'NOT_FOUND'
        assert result['benefits'] is None and result['collection_guidance']['amount_cents'] is None
        reasons = [call.kwargs.get('reason') for call in audit.call_args_list]
        assert 'saved_response' in reasons and 'upstream_no_policies' in reasons
        assert 'no_upstream_candidates' in reasons

    async def test_filtered_candidates_are_review_required_and_audited_separately(self):
        _, _, _, item = setup()
        item['subscriber']['dateOfBirth'] = '19990101'
        with patch.object(api, '_recovery_event') as audit:
            case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]})
        assert case['previsit_discovery']['outcome'] == 'review_needed'
        assert discovery.public_status(case['previsit_discovery'])['coverage_status'] == 'REVIEW_REQUIRED'
        assert client.post.await_count == 1
        counts = {call.args[2]: call.kwargs.get('count') for call in audit.call_args_list}
        assert counts['discovery_result'] == 1 and counts['candidate_filter'] == 1
        assert case['previsit_discovery']['candidates'][0]['verification_status'] == 'REVIEW_REQUIRED'
        assert 'PATIENT_DOB_MISMATCH' in case['previsit_discovery']['candidates'][0]['reason_codes']

    def test_terminal_and_legacy_statuses_never_claim_a_search_is_running(self):
        for record in [
            {'request_state': 'complete', 'outcome': 'not_available'},
            {'request_state': 'complete'},
            {'request_state': 'not_requested'},
            {'request_state': 'rejected', 'outcome': 'rejected'},
            {'request_state': 'complete', 'outcome': 'no_match', 'coverage_count': 2},
        ]:
            result = discovery.public_status(record)
            assert 'Searching Stedi' not in result['message']
            assert result['coverage_status'] == 'REVIEW_REQUIRED'
        assert discovery.public_status({'request_state': 'pending', 'outcome': 'no_match'})['coverage_status'] == 'REVIEW_REQUIRED'

    async def test_selected_ecw_route_can_recover_member_id_without_discovery_policy(self):
        case, session, provider, item = setup()
        case['previsit_context']['inputs']['member_id'] = None
        recovery = case['previsit_discovery']
        route = {'stediId': 'route-two', 'displayName': 'Example Payer'}
        recovery.update(route_source='ecw', payer_routes=[route])
        with patch.object(api, '_post_stedi_eligibility', AsyncMock(return_value=(httpx.Response(200), item, None))) as submit, \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api.commercial_store, 'list_connections', return_value=[]):
            await api._recheck_discovery_candidate(case, session, provider, selected_route=route)
        sent = submit.call_args.args[2]
        assert sent['payerId'] == 'route-two' and 'memberId' not in sent['subscriber']
        assert recovery['outcome'] == 'recovered'
        assert recovery['verified_coverage']['member_id'] == 'new-id'
        result = api._previsit_result(case)
        assert result['insurance_details']['member_id'] == 'new-id'
        assert result['insurance_details']['ecw_member_id'] is None

    async def test_payer_route_requires_saved_candidate_and_revision(self):
        case, session, provider, _ = setup()
        recovery = case['previsit_discovery']
        recovery.update(request_state='complete', outcome='no_match', route_source='ecw',
                        payer_routes=[{'stediId': 'route-one', 'displayName': 'Example Payer'}])
        case['previsit_context']['source']['rendering_provider'] = {'npi': provider['rendering_npi'], 'first_name': 'Ada', 'last_name': 'Clinician'}
        value = {'route_id': 'route-one', 'revision': case['revision'], 'context_token': case['previsit_context']['context_token']}
        with patch.object(api, '_session', AsyncMock(return_value=session)), \
             patch.object(api.store, 'get_case', return_value=case), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            for changed, status in [({'route_id': 'arbitrary'}, 422), ({'revision': 0}, 409)]:
                with self.assertRaises(HTTPException) as caught:
                    await api.select_payer_route('case', api.PayerRouteInput(**{**value, **changed}), None, BackgroundTasks())
                assert caught.exception.status_code == status
            tasks = BackgroundTasks()
            result = await api.select_payer_route('case', api.PayerRouteInput(**value), None, tasks)
            assert len(tasks.tasks) == 1 and result['recovery']['state'] == 'pending'
            with self.assertRaises(HTTPException) as caught:
                await api.select_payer_route('case', api.PayerRouteInput(**value), None, BackgroundTasks())
            assert caught.exception.status_code == 409

    async def test_missing_policy_recovers_to_contracted_price(self):
        case, session, provider, item = setup()
        inputs, source = case['previsit_context']['inputs'], case['previsit_context']['source']
        inputs['member_id'] = None
        source.update(active_coverages=0, coverage_id='', payer=None, plan=None)
        case['previsit_context']['payer_id'] = None
        case['previsit_discovery']['identity_binding'] = api._recovery_binding(inputs, source)
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=[httpx.Response(200, json={'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]}),
                                              httpx.Response(200, json=item)])
        connection = {'id': 'connection', 'stedi_payer_id': 'EXAMPLE', 'status': 'staff_guidance_enabled'}
        network = {'plan_matcher': 'Choice', 'network_name': 'Synthetic contracted network'}
        profile = {'rules': {'cost_share': 'coinsurance'}}
        def rates(*args):
            return [{'code': '99203', 'allowed_cents': 10000, 'source_reference': 'Synthetic contract'}]
        with patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE', 'displayName': 'Example Payer'})), \
             patch.object(api.commercial_store, 'list_connections', return_value=[connection]), \
             patch.object(api, '_previsit_practice_path', return_value=(connection, network, profile)), \
             patch.object(api.commercial_store, 'matching_rates', side_effect=rates):
            await api._recover_previsit(case, session, provider)
        result = api._previsit_result(case)
        assert result['coverage_status'] == 'VERIFIED'
        assert result['price_range']['lines'][0]['allowed_cents'] == 10000
        assert result['collection_guidance']['amount_cents'] == 6000
        assert client.post.await_count == 2

    async def test_completed_no_match_retries_only_a_distinct_recorded_address(self):
        case, session, provider, _ = setup()
        patient = case['previsit_context']['inputs']['patient']
        patient['previous_addresses'] = [patient['address'],
            {'address1': '200 Prior St', 'city': 'Dallas', 'state': 'TX', 'postalCode': '75001'}]
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=httpx.Response(200, json={'status': 'COMPLETE', 'coveragesFound': 0, 'items': []}))
        revision = case['revision']
        def save(value, *_):
            nonlocal revision
            assert value['revision'] == revision
            revision += 1
            return {**value, 'revision': revision}
        with patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api, '_save', side_effect=save), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'):
            await api._recover_previsit(case, session, provider)
        assert client.post.await_count == 2
        assert client.post.call_args.kwargs['json']['subscriber']['address']['address1'] == '200 Prior St'
        assert len(case['previsit_discovery']['attempts']) == 2
        assert case['previsit_discovery']['outcome'] == 'no_match'

    async def test_card_ocr_runs_before_discovery_and_rejection_continues_recovery(self):
        from app.api.routers import staff
        from app.insurance import v1_sources
        case, session, provider, _ = setup()
        case['previsit_context']['source']['_patient_id'] = 'synthetic-patient'
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=[httpx.Response(200, json={'errors': [{'code': '75'}]}),
            httpx.Response(200, json={'status': 'COMPLETE', 'items': [], 'coveragesFound': 0})])
        fields = {'member_name': 'Pat Example', 'member_id': 'CARD-123', 'payer': 'Example Payer'}
        revision = case['revision']
        def save(value, *_):
            nonlocal revision
            assert value['revision'] == revision
            revision += 1
            return {**value, 'revision': revision}
        with patch.object(api.staff_store, 'patient_card_intakes', return_value=[]), \
             patch.object(api.ecw_backend, 'insurance_cards', AsyncMock(side_effect=[[{'id': 'card:0'}], (b'synthetic', 'image/png')])), \
             patch.object(staff, 'inspect_file', return_value=('image/png', 1)), \
             patch.object(v1_sources, 'read_card_fields', return_value=(fields, {}, [1])), \
             patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE'})), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api, '_save', side_effect=save):
            await api._recover_previsit(case, session, provider)
        assert client.post.await_count == 2
        assert client.post.call_args_list[0].kwargs['json']['subscriber']['memberId'] == 'CARD-123'
        assert 'memberId' not in client.post.call_args_list[1].kwargs['json']['subscriber']
        assert case['previsit_discovery']['card_status'] == 'read_no_verified_policy'
        assert case['previsit_discovery']['outcome'] == 'no_match'

    async def test_duplicate_final_address_does_not_leave_recovery_pending(self):
        case, session, provider, _ = setup()
        patient = case['previsit_context']['inputs']['patient']
        patient['previous_addresses'] = [patient['address']]
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=httpx.Response(200, json={'status': 'COMPLETE', 'items': []}))
        with patch.object(api.httpx, 'AsyncClient', return_value=client), \
             patch.object(api, '_save', side_effect=lambda value, *_: value), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'):
            await api._recover_previsit(case, session, provider)
        assert client.post.await_count == 1
        assert case['previsit_discovery']['request_state'] == 'complete'
        assert case['previsit_discovery']['outcome'] == 'no_match'

    async def test_cards_from_separate_intakes_are_not_combined(self):
        from app.insurance import v1_sources
        case, session, provider, _ = setup()
        case['previsit_context']['source']['_patient_id'] = 'synthetic-patient'
        intakes = [{'documents': [{'object_key': key}]} for key in ('card-one', 'card-two')]
        with patch.object(api.staff_store, 'patient_card_intakes', return_value=intakes), \
             patch.object(api.ecw_backend, 'insurance_cards', AsyncMock(return_value=[])), \
             patch.object(api.object_store, 'get_object_bytes') as download, \
             patch.object(v1_sources, 'read_card_fields') as ocr, \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            assert not await api._recover_card(case, session, provider)
        download.assert_not_called()
        ocr.assert_not_called()
        assert case['previsit_discovery']['card_status'] == 'multiple_cards'

    async def test_followup_timeout_remains_indeterminate_without_another_post(self):
        case, session, provider, item = setup()
        recovery = case['previsit_discovery']
        recovery.update(candidates=discovery.candidates({'items': [item]}, case['previsit_context']['inputs']))
        recovery['selected_candidate_id'] = recovery['candidates'][0]['id']
        with patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE'})), \
             patch.object(api, '_post_stedi_eligibility', AsyncMock(side_effect=httpx.ReadTimeout('private'))), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected-request'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            await api._recheck_discovery_candidate(case, session, provider, discovery_payload={'items': [item]})
        assert recovery['request_state'] == 'indeterminate'
        tasks = BackgroundTasks()
        await api._start_previsit_recovery(case, session, provider, tasks)
        assert not tasks.tasks

    async def test_recovered_payer_wrong_dob_cannot_become_verified(self):
        case, session, provider, item = setup()
        recovery = case['previsit_discovery']
        recovery.update(candidates=discovery.candidates({'items': [item]}, case['previsit_context']['inputs']))
        recovery['selected_candidate_id'] = recovery['candidates'][0]['id']
        wrong = deepcopy(item); wrong['subscriber']['dateOfBirth'] = '19800102'
        with patch.object(api.stedi_payers, 'search_payer', AsyncMock(return_value={'stediId': 'EXAMPLE'})), \
             patch.object(api, '_post_stedi_eligibility', AsyncMock(return_value=(httpx.Response(200), wrong, False))), \
             patch.object(api.object_store, 'put_staff_document', return_value='protected'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            await api._recheck_discovery_candidate(case, session, provider, discovery_payload={'items': [item]})
        assert recovery['outcome'] == 'review_needed'
        assert discovery.public_status(recovery)['coverage_status'] == 'REVIEW_REQUIRED'
        assert case['previsit']['request_state'] == 'rejected'

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
        case['previsit_context']['result']['eligibility_error'] = 'payer_routing_unresolved'
        case['previsit_context']['inputs']['member_id'] = 'new-id'
        case['previsit_context']['result']['eligibility_failure']['detail'] = 'Payer route unresolved'
        response = {'status': 'COMPLETE', 'discoveryId': 'discovery-1', 'coveragesFound': 1, 'items': [item]}
        client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(side_effect=[httpx.Response(200, json=response),
                                              httpx.Response(200, json=item)])
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

    async def test_reused_legacy_result_schedules_local_reconciliation_only(self):
        case, session, provider, item = setup()
        session['intake']['updated_at'] = 'version'
        context = case['previsit_context']
        context['payer_id'] = None
        context['inputs']['member_id'] = 'new-id'
        case['previsit_discovery'].update(request_state='complete', outcome='review_needed', raw_object_key='saved',
                                          candidates=discovery.candidates({'items': [item]}, context['inputs']),
                                          checked_at=datetime.now(timezone.utc).isoformat(),
                                          identity_binding=api._recovery_binding(context['inputs'], context['source']))
        tasks = BackgroundTasks()
        with patch.object(api.store, 'create_case', return_value=case), \
             patch.object(api, '_finish_previsit'), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            await api._begin_coverage_discovery({}, session, context['inputs'], context['source'], provider,
                                                tasks, 'Missing policy')
        assert case['previsit_discovery']['request_state'] == 'pending'
        assert case['previsit_discovery']['stage'] == 'reconciling_saved_discovery'
        assert len(tasks.tasks) == 1

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
        for target, name, value in ((api.store, 'patient_checks', []),
                                    (api.staff_store, 'patient_card_intakes', [])):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
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
        final = {**setup()[3], 'id': 'verified'}
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

    async def test_multiple_active_policies_require_primacy_even_with_one_ecw_member_match(self):
        _, _, _, item = setup()
        item['payer']['name'] = 'UnitedHealthcare Group Medicare Advantage PPO'
        other = deepcopy(item)
        other['subscriber']['memberId'] = 'another-member'
        case, client, _ = await self.exercise(
            {'status': 'COMPLETE', 'coveragesFound': 2, 'items': [other, item]},
            unroutable=True, matching_ecw=True)
        assert case['previsit_discovery']['outcome'] == 'review_needed'
        assert client.post.await_count == 1
        assert case['previsit_context']['result']['benefits'] is None

    async def test_unresolved_route_only_offers_matching_ecw_policy_for_selection(self):
        _, _, _, item = setup()
        other = deepcopy(item)
        other['subscriber']['memberId'] = 'different-member'
        case, client, _ = await self.exercise(
            {'status': 'COMPLETE', 'coveragesFound': 3, 'items': [other, item, item]},
            unroutable=True, matching_ecw=True)
        case['previsit_context']['payer_id'] = ''
        assert case['previsit_discovery']['outcome'] == 'review_needed'
        assert [row['matches_ecw_policy'] for row in case['previsit_discovery']['candidates']] == [False, True]
        assert client.post.await_count == 1

        session = setup()[1]
        candidate = case['previsit_discovery']['candidates'][0]
        tasks = BackgroundTasks()
        with patch.object(api, '_session', AsyncMock(return_value=session)), \
             patch.object(api.store, 'get_case', return_value=case), \
             patch.object(api, '_save', side_effect=lambda value, *_: value):
            await api.select_coverage_candidate('case', api.CoverageCandidateInput(
                context_token=case['previsit_context']['context_token'],
                revision=case['revision'], candidate_id=candidate['id']), MagicMock(), tasks)
        assert len(tasks.tasks) == 1  # Explicit selection still runs payer identity validation.

    async def test_legacy_discovery_is_reconciled_without_a_new_stedi_request(self):
        case, session, _, item = setup()
        case['previsit_context']['payer_id'] = None
        case['previsit_context']['inputs']['member_id'] = 'new-id'
        other = deepcopy(item)
        other['subscriber']['memberId'] = 'different-member'
        case['previsit_discovery'].update(request_state='complete', outcome='review_needed', raw_object_key='saved',
                                          candidates=discovery.candidates({'items': [other, item]}, case['previsit_context']['inputs']))
        events = []
        with patch.object(api.object_store, 'get_object_bytes', return_value=json.dumps(
                {'status': 'COMPLETE', 'coveragesFound': 2, 'items': [other, item]}).encode()), \
             patch.object(api, '_save', side_effect=lambda value, _, event: events.append(event) or value):
            await api._reconcile_saved_discovery(case, session)
        recovery = case['previsit_discovery']
        assert recovery['request_state'] == 'complete'
        assert recovery['reason_code'] == 'multiple_active_policies'
        assert [row['matches_ecw_policy'] for row in recovery['candidates']] == [False, True]
        assert events == ['stedi-discovery-reconciled']
        assert 'retained for review' in case['previsit_context']['result']['eligibility_failure']['detail']

    async def test_no_match_ambiguous_patient_or_other_payer_never_becomes_inactive_or_quoted(self):
        _, _, _, item = setup()
        other = deepcopy(item); other['subscriber']['memberId'] = 'another-policy'
        for items, directory, outcome in [([], 'payer', 'no_match'), ([item, other], 'payer', 'review_needed')]:
            case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': len(items), 'items': items}, directory=directory)
            assert case['previsit_discovery']['outcome'] == outcome
            assert client.post.await_count == 1
            assert case['previsit']['request_state'] == 'rejected'
            assert case['previsit_context']['result']['benefits'] is None

    async def test_missing_ecw_member_can_recover_and_zero_results_are_not_uninsured(self):
        _, _, _, item = setup()
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 0, 'items': []}, unroutable=True)
        assert case['previsit_discovery']['reason_code'] == 'no_coverage_found'
        assert discovery.public_status(case['previsit_discovery'])['coverage_status'] == 'NOT_FOUND'
        assert 'does not establish' in case['previsit_context']['result']['eligibility_failure']['detail']
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]}, unroutable=True)
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert client.post.await_count == 2

    async def test_missing_address_runs_one_targeted_inquiry_without_staff_selection(self):
        _, _, _, item = setup()
        item['subscriber'].pop('address')
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]})
        assert client.post.await_count == 2  # One discovery, one targeted eligibility.
        assert case['previsit_discovery']['outcome'] == 'recovered'
        assert case['previsit_discovery']['selected_candidate_id']

    async def test_expired_policy_does_not_trigger_a_redundant_targeted_check(self):
        _, _, _, item = setup()
        item['subscriber'].pop('address')
        item['planDateInformation'] = {'planBegin': '20000101', 'planEnd': '20001231'}
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]})
        assert client.post.await_count == 1
        assert case['previsit_discovery']['exhausted'] is True
        assert api._previsit_result(case)['payment_mode'] == 'cash'
        assert case['previsit_discovery']['candidates'][0]['coverage_evidence']['status'] == 'EXPIRED'

    def test_public_recovery_status_includes_safe_stop_reason_and_reference(self):
        status = discovery.public_status({'request_state': 'complete', 'outcome': 'review_needed',
                                          'stage': 'candidate_match_review',
                                          'reason_code': 'multiple_matching_ecw_policies',
                                          'discovery_id': 'discovery-1'})
        assert status['stage'] == 'candidate_match_review'
        assert status['reason_code'] == 'multiple_matching_ecw_policies'
        assert status['reference'] == 'discovery-1'

    async def test_unique_active_returned_payer_recovers_without_using_original_route(self):
        _, _, _, item = setup()
        case, client, _ = await self.exercise({'status': 'COMPLETE', 'coveragesFound': 1, 'items': [item]}, directory='other')
        public = api._previsit_result(case)['recovery']
        assert public['outcome'] == 'recovered'
        assert public['coverage_status'] == 'VERIFIED'
        assert client.post.call_args.kwargs['json']['payerId'] == 'other'
        assert public['candidates'][0]['member_id'] == 'new-id'
        assert 'raw_object_key' not in json.dumps(public)

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
