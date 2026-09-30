import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.api.routers.commercial_estimator import PrevisitInput, VisitReviewInput, _previsit_benefits, previsit_estimate, update_visit_review


class PrevisitFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model_patch = patch('app.insurance.cpt_suggester.boto3.client', side_effect=RuntimeError('offline synthetic test'))
        self.model_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.cash_patch = patch('app.api.routers.commercial_estimator.practice_prices.current', return_value=None)
        self.cash_patch.start()
        self.addCleanup(self.cash_patch.stop)

    async def test_explicit_self_pay_skips_stedi_and_defaults_to_cash(self):
        await self._flow(date.today().isoformat(), self_pay=True)

    async def test_missing_provider_enrollment_prevents_paid_eligibility(self):
        await self._flow(date.today().isoformat(), enrollment_missing=True)

    async def test_saved_insurance_hold_prevents_paid_refresh(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), refresh=True, too_early=True, saved_hold=True)

    async def test_future_coverage_start_prevents_paid_calls_even_on_refresh(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), refresh=True, too_early=True, coverage_start=(date.today() + timedelta(days=1)).isoformat())

    async def test_uncoded_new_visit_requests_real_office_benefits_before_pricing(self):
        await self._flow(date.today().isoformat())

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

    async def _flow(self, service_date, reject=False, retry_basic=False, payer_missing=False, refresh=False, newer_ecw=False, too_early=False, self_pay=False, coverage_start=None, saved_hold=False, enrollment_missing=False):
        session = {"practice_id": "clinic", "intake_id": "intake", "patient_id": "patient",
                   "share_manifest_id": "", "staff": {"id": "staff", "display_name": "Staff", "practice_name": "Clinic"},
                   "intake": {"updated_at": "version", "patient": {"given_name": "Pat", "family_name": "Example",
                                                                   "birth_date": "1980-01-01"}, "fields": {}}}
        inputs = {"service_date": service_date, "visit_type": "new", "reason": "New patient consultation",
                  "lines": [], "member_id": "M123", "relationship": "18",
                  "subscriber": {"firstName": "Pat", "lastName": "Example", "birthDate": "1980-01-01"},
                  "patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                  "rendering_provider": {"npi": "1234567890"}}
        source = {"appointment_id": "visit-1", "coverage_id": "coverage-1", "active_coverages": 1,
                  "payer": "Blue Cross Blue Shield Tx", "plan": "CI", "rendering_provider": {"npi": "1234567890", "first_name": "Ada", "last_name": "Clinician"},
                  "billing_provider": {"name": "Clinic", "npi": "1811853260"}, "location_id": "office"}
        if retry_basic:
            inputs.update(group_number='GROUP-9', plan_number='PLAN-2')
            inputs['patient'].update(gender='F', address={'address1': '1 Main St', 'city': 'Austin', 'state': 'TX', 'postalCode': '78701'})
        case = {"id": "case-1", "source_mode": "commercial_previsit", "previsit": {"request_state": "not_requested"}}
        source['self_pay_recorded'] = self_pay
        if coverage_start:
            source['coverage_period'] = {'start': coverage_start}
        if saved_hold:
            session['intake']['workflows'] = {'saved': 'held-case'}
        held_cases = [{'previsit_context': {'source': {'appointment_id': 'visit-1'}},
                       'visit_review': {'insurance_check_from': (date.today() + timedelta(days=1)).isoformat()}}]
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        success_response = httpx.Response(200, json={
            "id": "eligibility-1", "plans": [{"benefits": {"statuses": [{"status": "Active"}]}}],
            "benefitsInformation": [{"code": "1"}]})
        client.post = AsyncMock(return_value=success_response)
        if reject:
            client.post.return_value = httpx.Response(200 if isinstance(reject, dict) else 400,
                json=reject if isinstance(reject, dict) else {'errors': [{'path': '/encounter/dates/service', 'message': 'Must be an object'}]})
        elif retry_basic:
            client.post.side_effect = [httpx.Response(400, json={'errors': [{'path': '/subscriber/address', 'message': 'Invalid'}]}), success_response]
        with patch("app.api.routers.commercial_estimator._session", new=AsyncMock(return_value=session)), \
             patch("app.api.routers.commercial_estimator.ecw_backend.configured", return_value=True), \
             patch("app.api.routers.commercial_estimator.ecw_backend.fetch_inputs", new=AsyncMock(return_value=(inputs, [], source))), \
             patch("app.api.routers.commercial_estimator._ecw_practice_config", return_value={}), \
             patch("app.api.routers.commercial_estimator.cpt_suggester.suggest_preliminary_cpt_codes", return_value={"groups": [], "notes": []}), \
             patch("app.api.routers.commercial_estimator.stedi_payers.search_payer", new=AsyncMock(return_value=None if payer_missing else {"stediId": "QDTRP", "displayName": "Blue Cross and Blue Shield of Texas", 'transactionSupport': {'eligibilityCheck': 'ENROLLMENT_REQUIRED' if enrollment_missing else 'SUPPORTED'}})), \
             patch("app.api.routers.commercial_estimator.stedi_payers.eligibility_enrolled", new=AsyncMock(return_value=False)), \
             patch("app.api.routers.commercial_estimator.store.get_cases", return_value=held_cases), \
             patch("app.api.routers.commercial_estimator.store.create_case", return_value=case), \
             patch("app.api.routers.commercial_estimator._save", side_effect=lambda value, *_: value), \
             patch("app.api.routers.commercial_estimator.object_store.enabled", return_value=True), \
             patch("app.api.routers.commercial_estimator.object_store.put_staff_document", return_value="protected-key"), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_connections", return_value=[]), \
             patch("app.api.routers.commercial_estimator.httpx.AsyncClient", return_value=client), \
             patch.dict("app.api.routers.commercial_estimator.os.environ", {
                 "STEDI_API_KEY": "production-key", "STEDI_EXECUTION_MODE": "commercial_pilot",
                 "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
            result = await previsit_estimate(PrevisitInput(appointment_id="visit-1", refresh_benefits=refresh if too_early else False), MagicMock())
            if enrollment_missing:
                self.assertEqual(result['eligibility_failure']['kind'], 'provider_enrollment')
                self.assertEqual(result['collection_guidance']['amount_cents'], 0)
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
                self.assertEqual(refreshed['price_range']['mode'], 'services')
                self.assertEqual(refreshed['selected_ids'], ['99203:1'])
                return
            if self_pay:
                self.assertEqual(result['payment_mode'], 'cash')
                client.post.assert_not_awaited()
                return
            if payer_missing:
                self.assertEqual(result['insurance_details']['member_id'], 'M123')
                self.assertTrue(result['context_token'])
                self.assertEqual([row['code'] for row in result['codes']], ['99203'])
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
                self.assertEqual(len(result['price_range']['lines']), 1)
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
            self.assertTrue(any("Saved benefit evidence" in item for item in unavailable["issues"]))
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
        self.assertEqual([row["code"] for row in result["codes"]], ["99203"])
        self.assertTrue(result["benefits"]["active"])
        self.assertNotIn("patient_min_cents", result["price_range"])
        sent = client.post.await_args.kwargs["json"]
        self.assertEqual(sent["encounter"]["services"], [{"system": "STC", "value": "98"}])
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
             patch('app.api.routers.commercial_estimator.object_store.enabled', return_value=True), \
             patch('app.api.routers.commercial_estimator._begin_coverage_discovery', AsyncMock(return_value={'recovery': 'started'})) as discovery, \
             patch.dict('app.api.routers.commercial_estimator.os.environ', {'STEDI_API_KEY': 'test', 'STEDI_EXECUTION_MODE': 'commercial_pilot', 'STEDI_LIVE_ELIGIBILITY_ENABLED': 'true'}):
            result = await previsit_estimate(PrevisitInput(appointment_id='visit'), MagicMock())
        self.assertEqual(result, {'recovery': 'started'})
        self.assertEqual(discovery.await_args.args[4]['rendering_npi'], '1234567890')
        self.assertEqual(discovery.await_args.args[4]['rendering_first'], 'Ada')
