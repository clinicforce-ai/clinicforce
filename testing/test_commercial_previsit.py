import json
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.api.routers.commercial_estimator import EcwStartInput, _previsit_benefits, previsit_estimate


class PrevisitFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_uncoded_new_visit_requests_real_office_benefits_before_pricing(self):
        await self._flow(date.today().isoformat())

    async def test_future_visit_uses_documented_service_date_object(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat())

    async def test_request_validation_failure_preserves_partial_facts(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), reject=True)

    async def test_request_validation_retries_the_documented_basic_request(self):
        await self._flow((date.today() + timedelta(days=2)).isoformat(), retry_basic=True)

    async def test_member_id_rejection_is_actionable_and_does_not_return_a_price(self):
        await self._flow(date.today().isoformat(), reject={'errors': [{'code': '72', 'description': 'private response text'}]})

    async def test_subscriber_aaa_error_on_http_200_is_not_saved_as_success(self):
        await self._flow(date.today().isoformat(), reject={'subscriber': {'aaaErrors': [{'code': '72'}]},
            'benefitsInformation': [{'code': '1'}]})

    async def _flow(self, service_date, reject=False, retry_basic=False):
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
             patch("app.api.routers.commercial_estimator.stedi_payers.search_payer", new=AsyncMock(return_value={"stediId": "QDTRP", "displayName": "Blue Cross and Blue Shield of Texas"})), \
             patch("app.api.routers.commercial_estimator.store.create_case", return_value=case), \
             patch("app.api.routers.commercial_estimator._save", side_effect=lambda value, *_: value), \
             patch("app.api.routers.commercial_estimator.object_store.enabled", return_value=True), \
             patch("app.api.routers.commercial_estimator.object_store.put_staff_document", return_value="protected-key"), \
             patch("app.api.routers.commercial_estimator.commercial_store.list_connections", return_value=[]), \
             patch("app.api.routers.commercial_estimator.httpx.AsyncClient", return_value=client), \
             patch.dict("app.api.routers.commercial_estimator.os.environ", {
                 "STEDI_API_KEY": "production-key", "STEDI_EXECUTION_MODE": "commercial_pilot",
                 "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
            result = await previsit_estimate(EcwStartInput(appointment_id="visit-1"), MagicMock())
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
                    cached = await previsit_estimate(EcwStartInput(appointment_id='visit-1'), MagicMock())
                    self.assertEqual(client.post.await_count, 1)
                    self.assertEqual(cached['eligibility_failure'], result['eligibility_failure'])
                else:
                    self.assertIn('not an inactive-coverage result', ' '.join(result['issues']))
                return
            case["previsit"]["benefits"] = {"requires_review": False, "network_status": "in_network"}
            with patch("app.api.routers.commercial_estimator.object_store.get_object_bytes",
                       return_value=json.dumps(success_response.json()).encode()):
                cached = await previsit_estimate(EcwStartInput(appointment_id="visit-1"), MagicMock())
            self.assertTrue(cached["benefits"]["review_reasons"])
            self.assertTrue(cached["benefits"]["requires_review"])
            self.assertEqual(cached["benefits"]["network_status"], "unknown")
            with patch("app.api.routers.commercial_estimator.object_store.get_object_bytes", side_effect=RuntimeError("unavailable")):
                unavailable = await previsit_estimate(EcwStartInput(appointment_id="visit-1"), MagicMock())
            self.assertTrue(unavailable["benefits"]["requires_review"])
            self.assertTrue(any("Saved benefit evidence" in item for item in unavailable["issues"]))
            self.assertEqual(client.post.await_count, 2 if retry_basic else 1)
        self.assertEqual([row["code"] for row in result["codes"]], ["99202", "99203", "99204", "99205"])
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
            result = await previsit_estimate(EcwStartInput(appointment_id='visit'), MagicMock())
        self.assertEqual(result, {'recovery': 'started'})
        self.assertEqual(discovery.await_args.args[4]['rendering_npi'], '1234567890')
        self.assertEqual(discovery.await_args.args[4]['rendering_first'], 'Ada')
