"""Stedi mock-client and 271-normalization checks; never use patient data."""
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routers import insurance_v2 as v2
from app.api.routers.v2_flow.insurance_v2_eligibility import stedi_patient_binding
from app.insurance.availity import DEMO_SCOPE, Settings
from app.insurance import stedi, stedi_results


class StediTests(unittest.IsolatedAsyncioTestCase):
    async def test_availity_fallback_retains_benefits_without_using_demo_network_matrix(self):
        case = {"id": "synthetic", "inputs": {"payer_id": "TEST", "provider": {"npi": "1234567893"},
                 "place_of_service": "11", "service_date": "2026-10-15", "member_id": "MEM-1",
                 "relationship": "18", "patient": {"patient_first_name": "Pat", "patient_last_name": "Example",
                                                  "patient_birth_date": "1980-01-01"},
                 "subscriber": {"firstName": "Pat", "lastName": "Example"}},
                "sources": {}, "transactions": {}, "events": []}
        practice = {"stedi_provider_networks": [{"payer_id": "TEST", "provider_npi": "1234567893",
                    "place_of_service": "11", "plan_name": "Example PPO", "status": "in_network"}]}
        payload = {"subscriber": {"firstName": "Pat", "lastName": "Example", "dateOfBirth": "1980-01-01",
                                  "memberId": "MEM-1"},
                   "plans": [{"benefits": {"statuses": [{"status": "Active Coverage",
                    "planCoverageDescription": "Example PPO"}]}}]}
        with patch.object(v2.stedi, "request_body", return_value=({}, None)), \
             patch.object(v2.stedi.Settings, "read", return_value=stedi.Settings("test", "synthetic-key")), \
             patch.object(v2.stedi, "Client") as client, \
             patch.object(v2, "save", new=AsyncMock(side_effect=lambda value, *_: value)), \
             patch.object(v2, "public", side_effect=lambda value: value):
            client.return_value.check = AsyncMock(return_value={"outcome": "complete", "payload": payload})
            await v2.stedi_coverage(case, {}, practice, [], "Availity unavailable.")
        result = case["transactions"]["coverage"]["result"]
        self.assertTrue(result["active"])
        self.assertEqual(result["identity"]["status"], "matched")
        self.assertEqual(result["provider_network_status"], "unconfirmed")
        self.assertIn("location and tax-ID contract match", result["provider_network"])

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
    def test_v2_patient_binding_names_mismatched_fields_without_exposing_names(self):
        case = {"inputs": {"member_id": "MEM-1", "relationship": "18", "payer_id": "TEST",
                           "patient": {"patient_first_name": "Pat", "patient_last_name": "Example",
                                       "patient_birth_date": "1980-01-01"},
                           "subscriber": {"firstName": "Pat", "lastName": "Example"}}}
        payload = {"subscriber": {"firstName": "Other", "lastName": "Person",
                                   "dateOfBirth": "1980-01-01", "memberId": "MEM-1"}}
        decision = stedi_patient_binding(case, payload)
        self.assertEqual(decision["status"], "review")
        self.assertIsNone(decision["binding"])
        self.assertIn("first_name", decision["differences"])
        self.assertIn("subscriber_last", decision["policy_differences"])
        self.assertNotIn("Other", str(decision))

    def test_network_matrix_requires_one_plan_and_nonconflicting_rows(self):
        case = {"inputs": {"payer_id": "TEST", "provider": {"npi": "1234567893"},
                           "place_of_service": "11", "service_date": "2026-10-15"}}
        coverage = {"plans": [{"name": "Example PPO"}]}
        entry = {"payer_id": "TEST", "provider_npi": "1234567893", "place_of_service": "11",
                 "plan_name": "Example PPO", "status": "in_network"}
        other = {**entry, "status": "out_of_network"}
        self.assertEqual(v2.stedi_network(case, {"stedi_provider_networks": [entry]}, coverage)["provider_network_status"], "in_network")
        for rows in ([entry, other], [other, entry]):
            result = v2.stedi_network(case, {"stedi_provider_networks": rows}, coverage)
            self.assertEqual(result["provider_network_status"], "unconfirmed")
            self.assertIn("rows conflict", result["provider_network"])
        multiple = {"plans": [{"name": "Example PPO"}, {"name": "Another PPO"}]}
        result = v2.stedi_network(case, {"stedi_provider_networks": [entry]}, multiple)
        self.assertEqual(result["provider_network_status"], "unconfirmed")
        self.assertIn("different plan names", result["provider_network"])
        result = v2.stedi_network(case, {"stedi_provider_networks": []}, coverage)
        self.assertIn("No clinic network row matches", result["provider_network"])

    def test_nmct_v2_bridge_uses_policy_except_explicit_payer_nonparticipation(self):
        case = {"inputs": {"payer_id": "TEST", "provider": {"npi": "1234567893"},
                           "place_of_service": "11", "service_date": "2026-10-15"}}
        coverage = {"plans": [{"name": "Example HMO"}]}
        matrix = {"stedi_provider_networks": [{"payer_id": "TEST", "provider_npi": "1234567893",
                   "place_of_service": "11", "plan_name": "Example HMO", "status": "out_of_network"}]}
        result = v2.stedi_network(case, matrix, coverage, practice_id="primary")
        self.assertEqual(result["provider_network_status"], "in_network")
        payer_message = {"subscriber": {"dates": {"service": {"start": "2026-10-15", "end": "2026-10-15"}}},
                         "plans": [{"benefits": {"statuses": [{"status": "Active Coverage",
                            "service": {"system": "STC", "value": "30"}}], "limitations": [
                            {"service": {"system": "STC", "value": "30"},
                             "messages": ["Provider is not in network."]}]}}]}
        result = v2.stedi_network(case, matrix, coverage, practice_id="primary", payload=payer_message)
        self.assertEqual(result["provider_network_status"], "out_of_network")
        payer_message["plans"][0]["benefits"]["limitations"][0]["messages"] = [
            "Provider is in network based on NPI ID provided in request"]
        result = v2.stedi_network(case, matrix, coverage, practice_id="primary", payload=payer_message)
        self.assertEqual(result["provider_network_status"], "in_network")
        self.assertIn("payer specifically reports", result["provider_network"])

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
