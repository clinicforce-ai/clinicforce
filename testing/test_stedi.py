"""Stedi mock-client and 271-normalization checks; never use patient data."""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routers import insurance_v2 as v2
from app.insurance.availity import DEMO_SCOPE, Settings
from app.insurance import stedi, stedi_results


class StediTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_uses_server_authorization_and_returns_check_id(self):
        def handler(request):
            self.assertEqual(request.url, httpx.URL(stedi.ELIGIBILITY_URL))
            self.assertEqual(request.headers["authorization"], "test-key")
            self.assertNotIn("test-key", request.content.decode())
            return httpx.Response(200, json={"id": "ec_123", "plans": []})

        result = await stedi.Client(stedi.Settings("test", "test-key"), transport=httpx.MockTransport(handler)).check({"payerId": "60054"})
        self.assertEqual((result["outcome"], result["check_id"], result["mock_response"]), ("complete", "ec_123", True))

    async def test_test_key_precedes_production_key(self):
        with patch.dict("os.environ", {"STEDI_TEST_API_KEY": "test-key", "STEDI_API_KEY": "production-key"}):
            self.assertEqual(stedi.Settings.read().mode, "test")

    async def test_fallback_can_be_disabled_after_availity_launch(self):
        with patch.dict("os.environ", {"STEDI_FALLBACK_ENABLED": "false"}):
            self.assertFalse(stedi.fallback_enabled())

    async def test_request_body_maps_subscriber_and_dependent_without_inventing_data(self):
        inputs = {"payer_id": "BCBS", "provider": {"npi": "1234567893"}, "subscriber": {"firstName": "John", "lastName": "Doe", "birthDate": "1970-01-01", "memberId": "MEMBER"},
                  "relationship": "19", "patient": {"patient_first_name": "Jane", "patient_last_name": "Doe", "patient_birth_date": "2000-01-01"}, "service_date": "2026-09-20"}
        body, error = stedi.request_body(inputs, {"stedi_payer_ids": {"BCBS": "84980"}, "providers": {"1234567893": {"stedi_organization_name": "Clinicforce Test Clinic"}}})
        self.assertIsNone(error)
        self.assertEqual((body["payerId"], body["encounter"]["services"][0]["value"], body["dependent"]["name"]["person"]["firstName"]), ("84980", "98", "Jane"))


class StediResultTests(unittest.TestCase):
    def test_normalizes_benefits_but_keeps_network_unconfirmed_and_price_unavailable(self):
        result = stedi_results.coverage({"id": "ec_123", "payer": {"name": {"organization": "Test Payer"}}, "plans": [{"benefits": {
            "statuses": [{"status": "Active Coverage", "network": {"code": "Y", "definition": "In Network"}, "planCoverageDescription": "Test plan"}],
            "coPayment": [{"amount": "25.00", "network": {"code": "Y", "definition": "In Network"}, "service": "Health Benefit Plan Coverage"}],
            "coInsurance": [{"percent": "20", "network": {"code": "Y", "definition": "In Network"}, "service": "Health Benefit Plan Coverage"}],
        }}]})
        self.assertEqual((result["source"], result["active"], result["pce_available"], result["provider_network_status"]), ("stedi", True, False, "unconfirmed"))
        self.assertEqual(result["plans"][0]["benefits"][0]["amount"], "25.00")
        summary = v2.front_desk_summary({"inputs": {"lines": []}, "transactions": {"coverage": {"result": result}}})
        self.assertEqual((summary["action"], summary["heading"]), ("verify", "Insurance estimate pending — verify payer details before scheduling"))

    def test_manual_stedi_review_can_select_cash_without_creating_an_insured_quote(self):
        case = {"inputs": {"lines": [{"code": "99204", "units": "1", "cash_cents": 22500, "service_status": "scheduled"}]},
                "transactions": {"coverage": {"source": "stedi", "result": {"source": "stedi", "active": True, "plans": []}}},
                "manual_review": {"source": "payer_call", "network_status": "in_network", "network_reference": "NET-1",
                                  "referral_status": "not_required", "referral_reference": "REF-1",
                                  "authorization_status": "not_required", "authorization_reference": "AUTH-1", "price_path": "cash"}}
        summary = v2.front_desk_summary(case)
        self.assertEqual((summary["action"], summary["scheduled"]["insurance_cents"], summary["scheduled"]["cash_cents"]), ("self_pay", None, 22500))
        self.assertEqual(summary["scheduled"]["network_status"], "in_network")

    def test_only_empty_or_unavailable_availity_results_are_fallback_candidates(self):
        self.assertIsNotNone(v2.stedi_fallback_reason({"outcome": "payer_error", "payload": []}))
        self.assertIsNotNone(v2.stedi_fallback_reason({"outcome": "unavailable", "payload": {}}))
        self.assertIsNotNone(v2.stedi_fallback_reason({"outcome": "indeterminate", "http_status": 501, "payload": {}}))
        self.assertIsNone(v2.stedi_fallback_reason({"outcome": "payer_error", "payload": {"coverages": [{"statusCode": "6"}]}}))
        self.assertIsNone(v2.stedi_fallback_reason({"outcome": "indeterminate", "payload": {}}))


class StediManualReviewEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_review_requires_references_and_keeps_insured_price_empty(self):
        case = {"id": "case-1", "revision": 0, "environment": "production", "sources": {}, "missing": [], "discovery": {}, "events": [],
                "inputs": {"coverage_missing": [], "review_missing": [], "diagnoses": [], "lines": []},
                "transactions": {"coverage": {"source": "stedi", "outcome": "complete", "result": {"source": "stedi", "active": True, "plans": []}}}}
        settings = Settings("demo", "client", "secret", DEMO_SCOPE, "https://api.availity.com/availity", "https://api.availity.com/v1/token")
        review = v2.ManualReview(source="payer_call", network_status="in_network", network_reference="NET-1",
                                 referral_status="not_required", referral_reference="REF-1",
                                 authorization_status="not_required", authorization_reference="AUTH-1", price_path="cash")
        with patch.object(v2, "_session", new=AsyncMock(return_value={"patient_id": "patient", "share_manifest_id": "share"})), \
             patch.object(v2, "configuration", return_value=(settings, {}, "binding")), \
             patch.object(v2, "load", new=AsyncMock(return_value=case)), \
             patch.object(v2, "save", new=AsyncMock(side_effect=lambda value, *_: value)):
            await v2.save_manual_review("case-1", review, None)
            self.assertEqual(case["manual_review"]["network_status"], "in_network")
            self.assertIsNone(v2.front_desk_summary(case)["scheduled"]["insurance_cents"])
            invalid = review.model_copy(update={"network_reference": ""})
            with self.assertRaises(HTTPException) as raised:
                await v2.save_manual_review("case-1", invalid, None)
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
