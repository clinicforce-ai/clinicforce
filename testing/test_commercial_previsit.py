import json
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.api.routers.commercial_estimator import PrevisitInput, VisitReviewInput, _compare_card_hints, _previsit_benefits, _previsit_result, previsit_estimate, update_visit_review
from app.api.routers.commercial_flow.commercial_previsit_pricing import _previsit_service_types, _reviewed_service_types
from app.api.routers.commercial_flow.commercial_previsit_submit import same_member_and_medical_plan


class PrevisitFlowTest(unittest.IsolatedAsyncioTestCase):
    def test_separate_responses_require_the_same_member_and_medical_plan(self):
        from app.api.routers import commercial_estimator as api
        first = {'subscriber': {'memberId': 'M123', 'name': {'person': {'firstName': 'Pat', 'lastName': 'Example'}}},
                 'plans': [{'name': 'PPO MEDICAL', 'benefits': {'statuses': [{'status': 'Active'}]}}]}
        self.assertTrue(same_member_and_medical_plan(api, first, first))
        changed = json.loads(json.dumps(first))
        changed['subscriber']['memberId'] = 'M999'
        self.assertFalse(same_member_and_medical_plan(api, first, changed))
        changed = json.loads(json.dumps(first))
        changed['plans'][0]['name'] = 'OTHER MEDICAL'
        self.assertFalse(same_member_and_medical_plan(api, first, changed))

    async def test_bcbstx_combined_visit_uses_one_service_type_per_inquiry(self):
        await self._flow(date.today().isoformat(), combined_emg=True)

    async def test_bcbstx_combined_visit_rejects_different_member_on_second_inquiry(self):
        await self._flow(date.today().isoformat(), combined_emg=True, secondary_mismatch=True)

    def test_refresh_requests_selected_diagnostic_and_office_categories(self):
        case = {'previsit_context': {'context_token': 'same-visit',
            'source': {'appointment_id': 'appointment-1'},
            'inputs': {'service_date': '2026-10-22'},
            'result': {'codes': [
                {'id': 'nerve', 'code': '95908'},
                {'id': 'muscle', 'code': '95886'},
                {'id': 'office', 'code': '99204'}]}},
            'visit_review': {'context_token': 'same-visit',
                'selected_ids': ['nerve', 'muscle', 'office']}}
        categories = _reviewed_service_types([case], 'appointment-1', '2026-10-22', 'same-visit')
        self.assertEqual(categories, ['73', '98'])
        self.assertEqual(_previsit_service_types({'lines': [], 'reason': '',
            '_selected_service_types': categories}), ['98', '73', '30'])
        case['previsit_context']['result']['selected_ids'] = ['nerve', 'muscle', 'office']
        case['visit_review'].pop('selected_ids')
        self.assertEqual(_reviewed_service_types([case], 'appointment-1', '2026-10-22', 'same-visit'), ['73', '98'])
        case['previsit_context']['result']['selected_ids'] = []
        case['visit_review']['separate_em_planned'] = True
        self.assertEqual(_reviewed_service_types([case], 'appointment-1', '2026-10-22', 'same-visit'), ['73', '98'])
        self.assertEqual(_reviewed_service_types([case], 'another-appointment', '2026-10-22', 'same-visit'), [])

    def setUp(self):
        self.cpt_rules_patch = patch('app.api.routers.commercial_estimator.commercial_store.list_cpt_requirements', return_value=[])
        self.cpt_rules_patch.start()
        self.addCleanup(self.cpt_rules_patch.stop)
        self.library_patch = patch('app.api.routers.commercial_estimator.library_pricing.prepare', return_value=[])
        self.library_patch.start()
        self.addCleanup(self.library_patch.stop)
        self.library_list_patch = patch('app.api.routers.commercial_estimator.staff_store.list_library', return_value=[])
        self.library_list_patch.start()
        self.addCleanup(self.library_list_patch.stop)
        self.model_patch = patch('app.insurance.cpt_suggester.boto3.client', side_effect=RuntimeError('offline synthetic test'))
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.cash_patch = patch('app.api.routers.commercial_estimator.practice_prices.current', return_value=None)
        self.cash_patch.start()
        self.addCleanup(self.cash_patch.stop)

    async def test_complete_ecw_benefits_skip_paid_check_and_remain_editable(self):
        await self._flow(date.today().isoformat(), ecw_saved=True)

    async def test_incomplete_ecw_benefits_fall_through_to_stedi(self):
        await self._flow(date.today().isoformat(), ecw_saved=True, ecw_incomplete=True, pos='11')

    async def test_card_ocr_fills_missing_member_before_first_stedi_request(self):
        await self._flow(date.today().isoformat(), member_id_missing=True,
            card_fields={'payer': 'Blue Cross Blue Shield Tx', 'member_name': 'Pat Example',
                         'member_id': 'CARD-123', 'group_number': 'GROUP-9'})

    async def test_card_member_conflict_holds_first_stedi_request(self):
        await self._flow(date.today().isoformat(), card_conflict=True,
            card_fields={'payer': 'Blue Cross Blue Shield Tx', 'member_name': 'Pat Example',
                         'member_id': 'CARD-999'})

    async def test_multiple_card_sets_hold_first_stedi_request(self):
        await self._flow(date.today().isoformat(), card_status='multiple_cards')

    def test_card_id_conflict_is_held_even_when_printed_name_is_unmatched(self):
        inputs = {'member_id': 'ECW-123', 'patient': {'patient_first_name': 'Pat', 'patient_last_name': 'Example'},
                  'subscriber': {}, 'relationship': '18'}
        check = _compare_card_hints({'status': 'read', 'fields': {'member_id': 'CARD-999',
            'member_name': 'Another Person', 'payer': 'Example Payer'}}, inputs, {'payer': 'Example Payer'})
        self.assertEqual(check['status'], 'conflict')
        self.assertEqual(check['conflicts'], ['member_id'])
        self.assertEqual(inputs['member_id'], 'ECW-123')

    def test_hpi_card_copay_requires_matching_patient_and_member(self):
        from app.insurance.v1_sources import card_fields
        fields, _ = card_fields([(1, 'Health Plans, Inc. (HPI)\nEXAMPLE: PAT\nID# ABC12345\nSpecialist:\n$45')])
        self.assertEqual(fields['payer'], 'Health Plans Incorporated')
        self.assertEqual(fields['member_name'], 'PAT EXAMPLE')
        self.assertEqual(fields['specialist_copay'], '$45')
        inputs = {'member_id': 'ABC12345', 'patient': {
            'patient_first_name': 'Pat', 'patient_last_name': 'Example'}, 'subscriber': {}}
        source = {'payer': 'phcs', '_patient_id': 'patient-1', 'coverage_id': 'coverage-1', 'active_coverages': 1}
        card = {'status': 'read', 'fields': fields, 'count': 1, 'source': 'clinicforce_card'}
        self.assertEqual(_compare_card_hints(card, inputs.copy(), source.copy())['office_copay_cents'], 4500)
        self.assertNotIn('office_copay_cents', _compare_card_hints(card, {**inputs, 'member_id': 'OTHER'}, source.copy()))

    async def test_explicit_self_pay_skips_stedi_and_defaults_to_cash(self):
        await self._flow(date.today().isoformat(), self_pay=True)

    async def test_missing_provider_enrollment_prevents_paid_eligibility(self):
        await self._flow(date.today().isoformat(), enrollment_missing=True)

    async def test_medicare_secondary_without_eligibility_does_not_invent_zero(self):
        await self._flow(date.today().isoformat(), enrollment_missing=True, medicare_secondary=True)

    async def test_medicare_enrollment_failure_uses_returned_billing_instruction(self):
        await self._flow(date.today().isoformat(), enrollment_missing=True, medicare_secondary=True,
            billing_note='No need to collect money from the patient. Secondary will take care.')

    async def test_self_pay_billing_note_skips_paid_eligibility(self):
        await self._flow(date.today().isoformat(), billing_note='Self Pay')

    async def test_saved_insurance_hold_prevents_paid_refresh(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), refresh=True, too_early=True, saved_hold=True)

    async def test_future_coverage_start_prevents_paid_calls_even_on_refresh(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), refresh=True, too_early=True, coverage_start=(date.today() + timedelta(days=1)).isoformat())

    async def test_uncoded_new_visit_requests_real_office_benefits_before_pricing(self):
        await self._flow(date.today().isoformat(), expect_preliminary=True)

    async def test_uncoded_established_visit_selects_provisional_followup_cpt(self):
        await self._flow(date.today().isoformat(), visit_type='established')

    async def test_documented_95908_is_the_same_provisional_code_in_ui_and_pricing(self):
        await self._flow(date.today().isoformat(), scheduled_code='95908')

    async def test_emg_review_creates_one_ncs_range_without_muscle_work(self):
        await self._flow(date.today().isoformat(), emg_review=True)

    async def test_far_future_visit_does_not_query_stedi_even_on_refresh(self):
        for offset in (8, 56):
            await self._flow((date.today() + timedelta(days=offset)).isoformat(), refresh=True, too_early=True)

    async def test_future_visit_uses_documented_service_date_object(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat())

    async def test_refresh_rechecks_same_visit_and_keeps_pending_requests_single(self):
        await self._flow(date.today().isoformat(), refresh=True)

    async def test_refresh_can_recheck_a_previously_rejected_policy(self):
        await self._flow(date.today().isoformat(), reject={'errors': [{'code': '72'}]}, refresh=True)

    async def test_request_validation_failure_preserves_partial_facts(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), reject=True)

    async def test_request_validation_retries_the_documented_basic_request(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), retry_basic=True)

    async def test_member_id_rejection_is_actionable_and_does_not_return_a_price(self):
        await self._flow(date.today().isoformat(), reject={'errors': [{'code': '72', 'description': 'private response text'}]})

    async def test_subscriber_aaa_error_on_http_200_is_not_saved_as_success(self):
        await self._flow(date.today().isoformat(), reject={'subscriber': {'aaaErrors': [{'code': '72'}]},
            'benefitsInformation': [{'code': '1'}]})

    async def test_unresolved_payer_still_returns_patient_policy_and_editable_new_visit(self):
        await self._flow(date.today().isoformat(), payer_missing=True)

    async def test_newer_conflicting_ecw_check_refreshes_cached_stedi_evidence(self):
        await self._flow(date.today().isoformat(), newer_ecw=True)

    async def _flow(self, service_date, reject=False, retry_basic=False, payer_missing=False, refresh=False, newer_ecw=False, too_early=False, self_pay=False, coverage_start=None, saved_hold=False, enrollment_missing=False, ecw_saved=False, ecw_incomplete=False, medicare_secondary=False, billing_note=None, member_id_missing=False, card_fields=None, card_conflict=False, card_status=None, pos=None, scheduled_code=None, emg_review=False, expect_preliminary=False, visit_type='new', combined_emg=False, secondary_mismatch=False):
        session = {"practice_id": "clinic", "intake_id": "intake", "patient_id": "patient",
                   "share_manifest_id": "", "staff": {"id": "staff", "display_name": "Staff", "practice_name": "Clinic"},
                   "intake": {"updated_at": "version", "patient": {"given_name": "Pat", "family_name": "Example",
                                                                   "birth_date": "1980-01-01"}, "fields": {}}}
        inputs = {"service_date": service_date, "visit_type": visit_type,
                  "reason": "Follow up consultation" if visit_type == 'established' else "New patient consultation",
                  "lines": [], "member_id": None if member_id_missing else "M123", "relationship": "18",
                  "subscriber": {"firstName": "Pat", "lastName": "Example", "birthDate": "1980-01-01"},
                  "patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                  "rendering_provider": {"npi": "1234567890"}}
        if pos:
            inputs['place_of_service'] = pos
        if scheduled_code:
            inputs['lines'] = [{'id': 'documented-service', 'code': scheduled_code,
                                'service_status': 'scheduled', 'units': 1,
                                'source': 'eCW ServiceRequest'}]
        if combined_emg:
            inputs.update(reason='EMG for right cervical radiculopathy', visit_type='established',
                          place_of_service='11')
        source = {"appointment_id": "visit-1", "_patient_id": "p", "coverage_id": "coverage-1", "active_coverages": 1,
                  "payer": "Blue Cross Blue Shield Tx", "plan": "CI", "rendering_provider": {"npi": "1234567890", "first_name": "Ada", "last_name": "Clinician"},
                  "billing_provider": {"name": "Clinic", "npi": "1811853260"}, "location_id": "office"}
        saved_ecw = {'status': 'check_with_stedi'}
        if medicare_secondary:
            source.update(payer='Medicare of Texas', active_coverages=2)
        if ecw_saved:
            from test_ecw_benefits import saved_response
            from app.insurance import ecw_backend
            response = saved_response(service_date)
            if ecw_incomplete:
                response['insurance'][0]['item'][0]['benefit'] = []
            saved_ecw = ecw_backend.saved_eligibility_summary([response], 'p', 'coverage-1', service_date)
        if retry_basic:
            inputs.update(group_number='GROUP-9', plan_number='PLAN-2')
            inputs['patient'].update(gender='F', address={'address1': '1 Main St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'})
        scoped_ecw_contract = ecw_saved and not ecw_incomplete
        connection = {'id': 'payer-a', 'stedi_payer_id': 'QDTRP', 'status': 'staff_guidance_enabled',
                      'category': 'commercial_medical', 'display_name': 'Blue Cross Blue Shield Tx', 'aliases': [],
                      'rules': {'collection_policy': {'mode': 'collect_verified_copay',
                          'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01T12:00:00Z',
                          'reference': 'Synthetic fixed copay policy'}}}
        network = {'id': 'network-a', 'network_name': 'Blue Choice PPO', 'plan_matcher': 'CI',
                   'evidence_reference': 'library:synthetic-contract'}
        profile = {'rules': {}, 'plan_matcher': 'CI'}
        case = {"id": "case-1", "source_mode": "commercial_previsit", "previsit": {"request_state": "not_requested"}}
        source['self_pay_recorded'] = self_pay
        if billing_note:
            source['appointment_fields'] = [{'label': 'Billing Notes', 'text': billing_note, 'source': 'Encounter/visit-1.extension[0].valueString',
                'updated_at': (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}]
        if coverage_start:
            source['coverage_period'] = {'start': coverage_start}
        if emg_review:
            from app.api.routers.commercial_flow.commercial_previsit_finish import _visit_context_token
            case['visit_review'] = {'emg_required': True,
                                    'context_token': _visit_context_token(inputs, source)}
        if saved_hold:
            session['intake']['workflows'] = {'saved': 'held-case'}
        held_cases = [{'previsit_context': {'source': {'appointment_id': 'visit-1'}},
                       'visit_review': {'insurance_check_from': (date.today() + timedelta(days=1)).isoformat()}}]
        if combined_emg:
            from app.api.routers.commercial_flow.commercial_previsit_finish import _visit_context_token
            token = _visit_context_token(inputs, source)
            case['visit_review'] = {'context_token': token, 'separate_em_planned': True}
            case['previsit_context'] = {'context_token': token,
                'source': {'appointment_id': 'visit-1'}, 'inputs': {'service_date': service_date},
                'result': {'codes': [{'id': code, 'code': code, 'units': 1}
                                     for code in ('95909', '95886', '99213')], 'selected_ids': []}}
            session['intake']['workflows'] = {'saved': 'case-1'}
            held_cases = [case]
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        success_response = httpx.Response(200, json={
            "id": "eligibility-1", "plans": [{"benefits": {"statuses": [{"status": "Active"}]}}],
            "benefitsInformation": [{"code": "1"}]})
        client.post = AsyncMock(return_value=success_response)
        if combined_emg:
            response = {'subscriber': {'memberId': 'M123',
                'name': {'person': {'firstName': 'Pat', 'lastName': 'Example'}}},
                'plans': [{'name': 'PPO MEDICAL', 'benefits': {
                    'statuses': [{'status': 'Active', 'service': {'system': 'STC', 'value': '30'},
                        'messages': ['Provider is in-network based on NPI ID provided in request.']}],
                    'outOfPocket': [{'amount': '0', 'coverageLevel': 'FAMILY',
                        'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'REMAINING',
                        'service': {'system': 'STC', 'value': '30'},
                        'messages': ['OFFICE VISIT - PROFESSIONAL']}]}}]}
            second = json.loads(json.dumps(response))
            second['plans'][0].pop('name')
            second['plans'][0]['benefits']['statuses'] = [
                {'status': 'Active', 'service': {'system': 'STC', 'value': '30'},
                 'planCoverageDescription': 'PPO DENTAL'},
                {'status': 'Active', 'service': {'system': 'STC', 'value': '30'},
                 'planCoverageDescription': 'PPO MEDICAL'}]
            second['plans'][0]['benefits']['outOfPocket'][0]['messages'] = ['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL']
            if secondary_mismatch:
                second['subscriber']['memberId'] = 'M999'
            client.post.side_effect = [httpx.Response(200, json={**response, 'id': 'diagnostic-check'}),
                                       httpx.Response(200, json={**second, 'id': 'office-check'})]
        if reject:
            client.post.return_value = httpx.Response(200 if isinstance(reject, dict) else 400,
                json=reject if isinstance(reject, dict) else {'errors': [{'path': '/encounter/dates/service', 'message': 'Must be an object'}]})
        elif retry_basic:
            client.post.side_effect = [httpx.Response(400, json={'errors': [{'path': '/subscriber/address', 'message': 'Invalid'}]}), success_response]
        with patch("app.api.routers.commercial_estimator._session", new=AsyncMock(return_value=session)), \
             patch("app.api.routers.commercial_estimator.ecw_backend.configured", return_value=True), \
             patch("app.api.routers.commercial_estimator.ecw_backend.fetch_inputs", new=AsyncMock(return_value=(inputs, [], source))), \
             patch("app.api.routers.commercial_estimator._ecw_practice_config", return_value={}), \
             patch("app.api.routers.commercial_estimator.ecw_backend.fetch_saved_eligibility", AsyncMock(return_value=saved_ecw)), \
             patch("app.api.routers.commercial_estimator._read_card_hints", new=AsyncMock(return_value={
                 'status': card_status or ('read' if card_fields else 'not_found'), 'fields': card_fields or {}}), create=True), \
             patch("app.api.routers.commercial_estimator.cpt_suggester.suggest_preliminary_cpt_codes", return_value={"groups": [], "notes": []}) as preliminary_suggestions, \
             patch("app.api.routers.commercial_estimator.stedi_payers.search_payer", new=AsyncMock(return_value=None if payer_missing else {"stediId": "QDTRP", "displayName": "Blue Cross and Blue Shield of Texas", 'transactionSupport': {'eligibilityCheck': 'ENROLLMENT_REQUIRED' if enrollment_missing else 'SUPPORTED'}})), \
             patch("app.api.routers.commercial_estimator.stedi_payers.eligibility_enrollment", new=AsyncMock(return_value={'status': 'MISSING', 'reason_code': 'enrollment_missing'})), \
             patch("app.api.routers.commercial_estimator.store.get_cases", return_value=held_cases), \
             patch("app.api.routers.commercial_estimator.store.create_case", return_value=case), \
             patch("app.api.routers.commercial_estimator._save", side_effect=lambda value, *_: value), \
             patch("app.api.routers.commercial_estimator.object_store.enabled", return_value=True), \
             patch("app.api.routers.commercial_estimator.object_store.put_staff_document", return_value="protected-key") as raw_put, \
             ExitStack() as scoped_patches, \
             patch("app.api.routers.commercial_estimator.httpx.AsyncClient", return_value=client), \
             patch.dict("app.api.routers.commercial_estimator.os.environ", {
                 "STEDI_API_KEY": "production-key", "STEDI_EXECUTION_MODE": "commercial_pilot",
                 "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
            scoped_patches.enter_context(patch("app.api.routers.commercial_estimator.commercial_store.list_connections",
                                               return_value=[connection] if scoped_ecw_contract or combined_emg else []))
            scoped_patches.enter_context(patch("app.api.routers.commercial_estimator._previsit_practice_path",
                                               return_value=(connection, network, profile)))
            scoped_patches.enter_context(patch("app.api.routers.commercial_estimator._network_contract_evidence",
                                               return_value={'document_id': 'synthetic-contract'}))
            scoped_patches.enter_context(patch("app.api.routers.commercial_estimator.commercial_store.matching_rates",
                                               return_value=[{'code': code, 'allowed_cents': amount,
                                                   'source_reference': 'Synthetic contract'}
                                                   for code, amount in [('95909', 12644), ('95886', 8918), ('99213', 7615)]]
                                               if combined_emg else []))
            result = await previsit_estimate(PrevisitInput(appointment_id="visit-1", refresh_benefits=refresh if too_early else False), MagicMock())
            if combined_emg:
                self.assertEqual(client.post.await_count, 2)
                self.assertEqual([[row['value'] for row in call.kwargs['json']['encounter']['services']]
                                  for call in client.post.await_args_list], [['98'], ['73']])
                self.assertEqual(case['previsit']['request_state'], 'partial' if secondary_mismatch else 'complete')
                self.assertEqual(result['requested_service_types'], ['98', '73'])
                saved_raw = [json.loads(call.args[3]) for call in raw_put.call_args_list
                             if call.args[2] == 'previsit-stedi-271.json']
                self.assertEqual(len(saved_raw), 1)
                if secondary_mismatch:
                    self.assertNotIn('_service_payloads', saved_raw[0])
                    self.assertIsNone(result['collection_guidance']['amount_cents'])
                    cached = await previsit_estimate(PrevisitInput(appointment_id='visit-1'), MagicMock())
                    self.assertEqual(client.post.await_count, 2)
                    self.assertIsNone(cached['collection_guidance']['amount_cents'])
                    with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                         patch('app.api.routers.commercial_estimator.object_store.get_object_bytes',
                               return_value=json.dumps(response).encode()):
                        edited = await update_visit_review('case-1', VisitReviewInput(
                            context_token=result['context_token'], revision=0,
                            separate_em_planned=True), MagicMock())
                    self.assertIsNone(edited['collection_guidance']['amount_cents'])
                else:
                    self.assertIn('73', saved_raw[0]['_service_payloads'])
                    self.assertEqual([row['code'] for row in result['codes']], ['95909', '95886', '99213'])
                    self.assertEqual(result['collection_guidance']['amount_cents'], 0)
                    case['visit_review']['selected_ids'] = [row['id'] for row in result['codes'] if row['code'] != '99213']
                    with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                         patch('app.api.routers.commercial_estimator.object_store.get_object_bytes',
                               return_value=json.dumps(saved_raw[0]).encode()), \
                         patch('app.api.routers.commercial_flow.commercial_review_routes.check_cpt_authorization',
                               new=AsyncMock(return_value=case)):
                        edited = await update_visit_review('case-1', VisitReviewInput(
                            context_token=result['context_token'], revision=0,
                            separate_em_planned=True), MagicMock())
                    self.assertEqual([row['code'] for row in edited['codes']], ['95909', '95886', '99213'])
                    self.assertEqual(len(edited['selected_ids']), 3)
                    case['previsit_context']['result'].pop('established_for_planning', None)
                    self.assertTrue(_previsit_result(case, 'clinic')['established_for_planning'])
                return
            if visit_type == 'established':
                preliminary_suggestions.assert_called_once()
                self.assertIn('Patient visit status from eCW: established', preliminary_suggestions.call_args.args[0])
                self.assertEqual([row['code'] for row in result['codes']], ['99213'])
                self.assertEqual(result['selected_ids'], [next(row['id'] for row in result['available_codes'] if row['code'] == '99213')])
                self.assertEqual(result['service_selection_status'], 'preview')
                self.assertEqual([row['code'] for row in result['price_range']['lines']], ['99213'])
                self.assertEqual(client.post.await_count, 1)
                return
            if expect_preliminary:
                preliminary_suggestions.assert_called_once()
                self.assertIn('Patient visit status from eCW: new', preliminary_suggestions.call_args.args[0])
            if emg_review:
                self.assertEqual(result['service_selection_status'], 'preview')
                self.assertEqual(result['selected_service_count'], 1)
                self.assertEqual(result['provisional_scenario']['kind'], 'ncs_study_count')
                self.assertEqual(result['price_range']['mode'], 'alternatives')
                self.assertEqual([row['code'] for row in result['codes']],
                                 [str(code) for code in range(95907, 95914)])
                self.assertEqual([row['code'] for row in result['price_range']['lines']],
                                 [str(code) for code in range(95907, 95914)])
                self.assertFalse(any(row['code'] in {'95885', '95886'} for row in result['codes']))
                self.assertIsNone(result['collection_guidance']['amount_cents'])
                with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                     patch('app.api.routers.commercial_estimator.object_store.get_object_bytes', return_value=json.dumps(success_response.json()).encode()):
                    cleared = await update_visit_review('case-1', VisitReviewInput(
                        context_token=result['context_token'], revision=0, selected_ids=[]), MagicMock())
                self.assertEqual(cleared['codes'], [])
                self.assertEqual(cleared['selected_service_count'], 0)
                self.assertNotIn('provisional_scenario', cleared)
                return
            if scheduled_code:
                self.assertEqual(result['service_selection_status'], 'preview')
                self.assertEqual(result['selected_ids'], ['documented-service'])
                self.assertEqual([row['code'] for row in result['codes']], [scheduled_code])
                self.assertEqual([row['id'] for row in result['codes']], result['selected_ids'])
                self.assertEqual([row['code'] for row in result['price_range']['lines']], [scheduled_code])
                self.assertIsNone(result['collection_guidance']['amount_cents'])
                with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                     patch('app.api.routers.commercial_estimator.object_store.get_object_bytes', return_value=json.dumps(success_response.json()).encode()):
                    cleared = await update_visit_review('case-1', VisitReviewInput(
                        context_token=result['context_token'], revision=0, selected_ids=[]), MagicMock())
                self.assertEqual(cleared['selected_ids'], [])
                self.assertEqual(cleared['codes'], [])
                self.assertEqual(cleared['price_range']['lines'], [])
                self.assertIsNone(cleared['collection_guidance']['amount_cents'])
                return
            if card_conflict:
                self.assertEqual(result['card_check']['status'], 'conflict')
                self.assertEqual(result['eligibility_failure']['kind'], 'card_conflict')
                client.post.assert_not_awaited()
                return
            if card_status == 'multiple_cards':
                self.assertEqual(result['card_check']['status'], 'multiple_cards')
                self.assertEqual(result['eligibility_failure']['kind'], 'multiple_cards')
                client.post.assert_not_awaited()
                return
            if card_fields:
                self.assertEqual(result['card_check']['status'], 'filled_missing')
                sent = client.post.await_args.kwargs['json']
                self.assertEqual(sent['subscriber']['memberId'], 'CARD-123')
                self.assertEqual(sent['subscriber']['groupNumber'], 'GROUP-9')
                self.assertEqual(result['insurance_details']['member_source'], 'card_ocr')
                return
            if ecw_saved and not ecw_incomplete:
                self.assertEqual(result['collection_guidance']['amount_cents'], 4000)
                self.assertEqual(result['collection_guidance']['basis'], 'office_copay')
                self.assertEqual(result['service_selection_status'], 'preview')
                self.assertEqual(result['selected_ids'], [])
                self.assertEqual(result['benefit_source'], 'eCW saved eligibility')
                self.assertNotIn('_response', result['ecw_saved_eligibility'])
                self.assertIn('no Stedi check', result['eligibility_reconciliation']['message'])
                from app.insurance import ecw_benefits
                raw = json.dumps(ecw_benefits.payload(saved_ecw, source, inputs)).encode()
                with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                     patch('app.api.routers.commercial_estimator.object_store.get_object_bytes', return_value=raw):
                    edited = await update_visit_review('case-1', VisitReviewInput(
                        context_token=result['context_token'], revision=0, selected_ids=[next(row['id'] for row in result['available_codes'] if row['code'] == '99204')]), MagicMock())
                self.assertEqual(edited['collection_guidance']['amount_cents'], 4000)
                self.assertEqual(edited['service_selection_status'], 'approved')
                self.assertEqual(edited['benefit_source'], 'eCW saved eligibility')
                client.post.assert_not_awaited()
                return
            if enrollment_missing:
                self.assertEqual(result['eligibility_failure']['kind'], 'provider_enrollment')
                self.assertEqual(result['collection_guidance']['amount_cents'], 0 if billing_note else None)
                if billing_note:
                    self.assertEqual(result['collection_guidance']['basis'], 'ecw_billing_note')
                self.assertEqual(case['previsit']['request_state'], 'not_requested')
                client.post.assert_not_awaited()
                return
            if too_early:
                self.assertEqual(result['eligibility_timing']['status'], 'too_early')
                self.assertEqual(result['collection_guidance']['status'], 'too_early')
                self.assertIsNone(result['collection_guidance']['amount_cents'])
                self.assertEqual(result['eligibility_timing']['appointment_date'], service_date)
                client.post.assert_not_awaited()
                return
            if newer_ecw:
                saved = {'status': 'verified_inactive', 'active': False, 'checked_at': datetime.now(timezone.utc).isoformat()}
                with patch('app.api.routers.commercial_estimator.ecw_backend.fetch_saved_eligibility', AsyncMock(return_value=saved)):
                    refreshed = await previsit_estimate(PrevisitInput(appointment_id='visit-1'), MagicMock())
                self.assertEqual(client.post.await_count, 2)
                self.assertFalse(refreshed['eligibility_reconciliation']['requires_review'])
                self.assertIn('newer Stedi', refreshed['eligibility_reconciliation']['message'])
                self.assertEqual(refreshed['price_range']['mode'], 'alternatives')
                self.assertEqual(refreshed['selected_ids'], [])
                return
            if self_pay or billing_note == 'Self Pay':
                self.assertEqual(result['payment_mode'], 'cash')
                client.post.assert_not_awaited()
                return
            if payer_missing:
                self.assertEqual(result['insurance_details']['member_id'], 'M123')
                self.assertTrue(result['context_token'])
                self.assertEqual([row['code'] for row in result['codes']], ['99202', '99203', '99204', '99205'])
                self.assertTrue(result['available_codes'])
                selected = next(row['id'] for row in result['available_codes'] if row['code'] == '99204')
                with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                     patch('app.api.routers.commercial_estimator.object_store.get_object_bytes') as raw:
                    edited = await update_visit_review('case-1', VisitReviewInput(
                        context_token=result['context_token'], revision=0, selected_ids=[selected]), MagicMock())
                    self.assertEqual([row['code'] for row in edited['codes']], ['99204'])
                    self.assertTrue(edited['issues'])
                    self.assertIsNone(edited['benefits']['active'])
                    raw.assert_not_called()
                client.post.assert_not_awaited()
                return
            if reject:
                self.assertEqual(result['eligibility_error'], 'eligibility_rejected' if isinstance(reject, dict) else 'request_validation')
                self.assertIsNone(result['benefits']['active'])
                self.assertEqual(result['plan'], 'CI')
                self.assertEqual(len(result['price_range']['lines']), 4)
                self.assertIn('context_token', result)  # anticipated services remain editable
                self.assertEqual(case['previsit']['request_state'], 'rejected')
                self.assertEqual(client.post.await_count, 1)
                self.assertNotIn('patient_max_cents', result['price_range'])
                self.assertNotIn('private response text', json.dumps(result))
                if isinstance(reject, dict):
                    self.assertEqual(result['eligibility_failure']['codes'], ['72'])
                    self.assertIn('Correct the policy in eCW', result['eligibility_failure']['action'])
                    self.assertTrue(result['eligibility_failure']['attempted_at'])
                    cached = await previsit_estimate(PrevisitInput(appointment_id='visit-1'), MagicMock())
                    self.assertEqual(client.post.await_count, 1)
                    self.assertEqual(cached['eligibility_failure'], result['eligibility_failure'])
                    if refresh:
                        client.post.return_value = success_response
                        checked = await previsit_estimate(PrevisitInput(appointment_id='visit-1', refresh_benefits=True), MagicMock())
                        self.assertEqual(client.post.await_count, 2)
                        self.assertTrue(checked['benefits']['active'])
                        self.assertNotIn('eligibility_failure', checked)
                else:
                    self.assertIn('not an inactive-coverage result', ' '.join(result['issues']))
                if case['previsit']['request_state'] == 'rejected':
                    case.pop('previsit_raw_object_key', None)
                    hold_date = date.today() + timedelta(days=1)
                    with patch('app.api.routers.commercial_estimator.store.get_case', return_value=case), \
                         patch('app.api.routers.commercial_estimator.object_store.get_object_bytes') as raw:
                        held = await update_visit_review('case-1', VisitReviewInput(
                            context_token=result['context_token'], revision=0,
                            insurance_check_from=hold_date), MagicMock())
                        self.assertEqual(held['eligibility_timing']['check_from'], hold_date.isoformat())
                        self.assertIsNone(held['benefits']['active'])
                        raw.assert_not_called()
                        self.assertEqual(client.post.await_count, 1)
                return
            case["previsit"]["benefits"] = {"requires_review": False, "network_status": "in_network"}
            with patch("app.api.routers.commercial_estimator.object_store.get_object_bytes",
                       return_value=json.dumps(success_response.json()).encode()):
                cached = await previsit_estimate(PrevisitInput(appointment_id="visit-1"), MagicMock())
            self.assertTrue(cached["benefits"]["review_reasons"])
            self.assertTrue(cached["benefits"]["requires_review"])
            self.assertEqual(cached["benefits"]["network_status"], "unknown")
            with patch("app.api.routers.commercial_estimator.object_store.get_object_bytes", side_effect=RuntimeError("unavailable")):
                unavailable = await previsit_estimate(PrevisitInput(appointment_id="visit-1"), MagicMock())
            self.assertTrue(unavailable["benefits"]["requires_review"])
            self.assertTrue(any('protected saved payer response could not be reopened' in item
                                for item in unavailable['issues']))
            self.assertTrue(unavailable['benefits']['historical_only'])
            self.assertTrue(any(error['kind'] == 'saved_raw_unavailable'
                                for error in unavailable['benefits']['source_errors']))
            self.assertIsNone(unavailable['collection_guidance']['amount_cents'])
            self.assertEqual(client.post.await_count, 2 if retry_basic else 1)
            if refresh:
                case['previsit_discovery'] = {'request_state': 'complete', 'outcome': 'no_match'}
                client.post.return_value = httpx.Response(200, json={**success_response.json(), 'id': 'eligibility-2'})
                with patch('app.api.routers.commercial_estimator.object_store.get_object_bytes') as raw:
                    checked = await previsit_estimate(PrevisitInput(appointment_id='visit-1', refresh_benefits=True), MagicMock())
                raw.assert_not_called()
                self.assertEqual(client.post.await_count, 2)
                self.assertEqual(checked['check_id'], result['check_id'])
                self.assertEqual(checked['response_reference'], 'eligibility-2')
                self.assertNotIn('recovery', checked)
                for state in ('pending', 'indeterminate'):
                    case['previsit']['request_state'] = state
                    await previsit_estimate(PrevisitInput(appointment_id='visit-1', refresh_benefits=True), MagicMock())
                    self.assertEqual(client.post.await_count, 2)
                case['previsit']['request_state'] = 'complete'
        self.assertEqual([row["code"] for row in result["codes"]], ["99202", "99203", "99204", "99205"])
        self.assertTrue(result["benefits"]["active"])
        self.assertNotIn("patient_min_cents", result["price_range"])
        self.assertEqual(result['encounter_summary']['collect_today_cents'], result['collection_guidance']['amount_cents'])
        self.assertIsNone(result['encounter_summary']['estimated_total_patient_min_cents'])
        self.assertEqual([row['code'] for row in result['requirement_evidence']['procedures']],
                         ['99202', '99203', '99204', '99205'])
        sent = client.post.await_args.kwargs["json"]
        self.assertEqual(sent["encounter"]["services"], [{"system": "STC", "value": "98"}])
        if pos:
            self.assertEqual(sent['encounter']['placeOfService'], {'code': pos})
        if service_date == date.today().isoformat():
            self.assertNotIn("dates", sent["encounter"])
        else:
            self.assertEqual(sent["encounter"]["dates"], {"service": {"start": service_date}})
        self.assertNotIn("procedure", str(sent["encounter"]).lower())
        self.assertEqual(case["previsit"]["request_state"], "complete")
        if retry_basic:
            self.assertEqual(client.post.await_count, 2)
            enriched, basic = [call.kwargs['json'] for call in client.post.await_args_list]
            self.assertEqual(enriched['subscriber']['address']['addressLine1'], '1 Main St')
            self.assertEqual(enriched['subscriber']['gender'], 'FEMALE')
            self.assertNotIn('groupNumber', basic['subscriber'])
            self.assertNotIn('planNumber', basic['subscriber'])
            self.assertNotIn('address', basic['subscriber'])

    def test_member_identity_check_is_shared_by_fresh_and_cached_responses(self):
        benefits = _previsit_benefits({"subscriber": {"memberId": "different"}},
                                     {"member_id": "expected", "active_coverages": 1})
        self.assertTrue(benefits["requires_review"])
        self.assertIn("Confirm the member identity: the payer response differs from eCW.", benefits["review_reasons"])

    async def test_missing_policy_enters_discovery_with_the_ecw_provider(self):
        session = {'practice_id': 'clinic', 'staff': {'practice_name': 'Clinic'}, 'intake': {'patient': {'given_name': 'Pat', 'family_name': 'Example', 'birth_date': '1980-01-01'}, 'fields': {}}}
        inputs = {'service_date': date.today().isoformat(), 'lines': [], 'patient': {}, 'subscriber': {}, 'rendering_provider': {'npi': '1234567890'}}
        source = {'appointment_id': 'visit', 'active_coverages': 0, 'rendering_provider': {'npi': '1234567890', 'first_name': 'Ada', 'last_name': 'Clinician'}}
        with patch('app.api.routers.commercial_estimator._session', AsyncMock(return_value=session)), \
             patch('app.api.routers.commercial_estimator.ecw_backend.configured', return_value=True), \
             patch('app.api.routers.commercial_estimator.ecw_backend.fetch_inputs', AsyncMock(return_value=(inputs, [], source))), \
             patch('app.api.routers.commercial_estimator._ecw_practice_config', return_value={}), \
             patch('app.api.routers.commercial_estimator._read_card_hints', AsyncMock(return_value={'status': 'not_found', 'fields': {}})), \
             patch('app.api.routers.commercial_estimator.object_store.enabled', return_value=True), \
             patch('app.api.routers.commercial_estimator._begin_coverage_discovery', AsyncMock(return_value={'recovery': 'started'})) as discovery, \
             patch.dict('app.api.routers.commercial_estimator.os.environ', {'STEDI_API_KEY': 'test', 'STEDI_EXECUTION_MODE': 'commercial_pilot', 'STEDI_LIVE_ELIGIBILITY_ENABLED': 'true'}):
            result = await previsit_estimate(PrevisitInput(appointment_id='visit'), MagicMock())
        self.assertEqual(result, {'recovery': 'started'})
        self.assertEqual(discovery.await_args.args[4]['rendering_npi'], '1234567890')
        self.assertEqual(discovery.await_args.args[4]['rendering_first'], 'Ada')
