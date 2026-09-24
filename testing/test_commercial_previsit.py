import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.api.routers.commercial_estimator import EcwStartInput, previsit_estimate


class PrevisitFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_uncoded_new_visit_requests_real_office_benefits_before_pricing(self):
        session = {"practice_id": "clinic", "intake_id": "intake", "patient_id": "patient",
                   "share_manifest_id": "", "staff": {"id": "staff", "display_name": "Staff", "practice_name": "Clinic"},
                   "intake": {"updated_at": "version", "patient": {"given_name": "Pat", "family_name": "Example",
                                                                   "birth_date": "1980-01-01"}, "fields": {}}}
        inputs = {"service_date": date.today().isoformat(), "visit_type": "new", "reason": "New patient consultation",
                  "lines": [], "member_id": "M123", "relationship": "18",
                  "subscriber": {"firstName": "Pat", "lastName": "Example", "birthDate": "1980-01-01"},
                  "patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                  "rendering_provider": {"npi": "1234567890"}}
        source = {"appointment_id": "visit-1", "coverage_id": "coverage-1", "active_coverages": 1,
                  "payer": "Blue Cross Blue Shield Tx", "plan": "CI", "rendering_provider": {"npi": "1234567890", "first_name": "Ada", "last_name": "Clinician"},
                  "billing_provider": {"name": "Clinic", "npi": "1811853260"}, "location_id": "office"}
        case = {"id": "case-1", "source_mode": "commercial_previsit", "previsit": {"request_state": "not_requested"}}
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=httpx.Response(200, json={
            "id": "eligibility-1", "plans": [{"benefits": {"statuses": [{"status": "Active"}]}}],
            "benefitsInformation": [{"code": "1"}]}))
        with patch("app.api.routers.commercial_estimator._session", new=AsyncMock(return_value=session)), \
             patch("app.api.routers.commercial_estimator.ecw_backend.configured", return_value=True), \
             patch("app.api.routers.commercial_estimator.ecw_backend.fetch_inputs", new=AsyncMock(return_value=(inputs, [], source))), \
             patch("app.api.routers.commercial_estimator._ecw_practice_for_nmct", return_value={}), \
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
        self.assertEqual([row["code"] for row in result["codes"]], ["99202", "99203", "99204", "99205"])
        self.assertTrue(result["benefits"]["active"])
        self.assertIsNone(result["price_range"])
        sent = client.post.await_args.kwargs["json"]
        self.assertEqual(sent["encounter"]["services"], [{"system": "STC", "value": "98"}])
        self.assertNotIn("dates", sent["encounter"])
        self.assertNotIn("procedure", str(sent["encounter"]).lower())
        self.assertEqual(case["previsit"]["request_state"], "complete")
