"""One end-to-end mapping check for the eCW backend insurance source."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.insurance import ecw_backend
from app.ingestion import ecw


class EcwBackendSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_cached_patient_read_uses_the_verified_link(self):
        profile = {"patient_first_name": "Pat", "patient_last_name": "Example",
                   "patient_birth_date": "1980-01-01", "gender": "F"}
        with patch.object(ecw_backend, "_read", new=AsyncMock(side_effect=asyncio.TimeoutError)), \
                patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            patient, timed_out = await ecw_backend._cached_patient(None, "", "", "patient-1", profile)
        self.assertEqual((patient["id"], patient["birthDate"], patient["gender"]),
                         ("patient-1", "1980-01-01", "female"))
        self.assertEqual(timed_out, "Patient")

    async def test_slow_supporting_reference_becomes_an_explicit_gap(self):
        with patch.object(ecw_backend, "_read", new=AsyncMock(side_effect=asyncio.TimeoutError)), \
                patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            resource, timed_out = await ecw_backend._bounded_read(None, "", "", "Organization/payer-1")
        self.assertEqual(resource, {})
        self.assertEqual(timed_out, "Organization")

    async def test_slow_direct_read_retries_with_supported_id_search(self):
        practitioner = {"resourceType": "Practitioner", "id": "provider-1"}
        with patch.object(ecw_backend, "_read", new=AsyncMock(side_effect=asyncio.TimeoutError)), \
                patch.object(ecw_backend, "_search", new=AsyncMock(return_value=[practitioner])) as search:
            resource, timed_out = await ecw_backend._bounded_read(None, "issuer", "token", "Practitioner/provider-1")
        self.assertEqual((resource, timed_out), (practitioner, None))
        search.assert_awaited_once_with(None, "issuer", "token", "Practitioner", {"_id": "provider-1"})

    async def test_optional_resource_timeout_does_not_discard_the_appointment(self):
        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            rows, timed_out, unavailable = await ecw_backend._optional_search(
                None, "issuer", "token", "Procedure", {"patient": "patient-1"},
            )
        self.assertEqual((rows, timed_out, unavailable), ([], "Procedure", None))

        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=ecw_backend.EcwBackendError("rejected"))):
            rows, timed_out, unavailable = await ecw_backend._optional_search(
                None, "issuer", "token", "ServiceRequest", {"patient": "patient-1"},
            )
        self.assertEqual((rows, timed_out, unavailable), ([], None, "ServiceRequest"))

        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=ecw_backend.EcwResourceUnavailable("ChargeItem"))):
            rows, timed_out, unavailable = await ecw_backend._optional_search(
                None, "issuer", "token", "ChargeItem", {"subject": "Patient/patient-1"},
            )
        self.assertEqual((rows, timed_out, unavailable), ([], None, "ChargeItem"))

    async def test_rejected_charge_scope_is_not_reported_as_an_empty_result(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"resourceType": "OperationOutcome"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows, timed_out, unavailable = await ecw_backend._optional_search(
                client, "https://ehr.example.test/fhir", "token", "ChargeItem", {"subject": "Patient/patient-1"},
            )
        self.assertEqual((rows, timed_out, unavailable), ([], None, "ChargeItem"))

    async def test_backend_token_and_fhir_resources_become_availity_inputs(self):
        today_date = datetime.now(ZoneInfo("America/New_York")).date()
        today = today_date.isoformat()
        yesterday = (today_date - timedelta(days=1)).isoformat()
        metadata = {"resourceType": "CapabilityStatement", "rest": [{"security": {"extension": [{
            "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
            "extension": [{"url": "token", "valueUri": "https://auth.example.test/token"}],
        }]}}]}
        resources = {
            "Patient": [{"resourceType": "Patient", "id": "patient-1", "name": [{"use": "official", "given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01", "gender": "female", "telecom": [{"system": "phone", "value": "555-234-7700"}]}],
            "Coverage": [{"resourceType": "Coverage", "id": "coverage-1", "status": "active", "subscriberId": "MEMBER123", "beneficiary": {"reference": "Patient/patient-1"}, "subscriber": {"reference": "Patient/patient-1"}, "relationship": {"coding": [{"code": "self"}]}, "payor": [{"reference": "Organization/payer-1"}], "period": {"start": "2026-01-01"}, "class": [{"type": {"coding": [{"code": "group"}]}, "value": "GROUP123"}]}],
            "Encounter": [{"resourceType": "Encounter", "id": "encounter-1", "status": "planned", "period": {"start": f"{today}T14:00:00Z"}, "reasonCode": [{"text": "Neuropathy consultation"}], "participant": [{"individual": {"reference": "Practitioner/provider-1"}}], "location": [{"location": {"reference": "Location/location-1"}}]}],
            "ServiceRequest": [{"resourceType": "ServiceRequest", "id": "order-1", "encounter": {"reference": "Encounter/encounter-1"}, "occurrenceDateTime": f"{today}T14:00:00Z", "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204"}]}, "quantityQuantity": {"value": 1}, "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "G62.9"}]}]}],
            "Procedure": [{"resourceType": "Procedure", "id": "procedure-1", "status": "preparation",
                           "encounter": {"reference": "Encounter/encounter-1"},
                           "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204", "display": "New patient consultation"}]},
                           "quantity": {"value": 1},
                           "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "G62.9"}]}]},
                          {"resourceType": "Procedure", "id": "unrelated-history", "status": "completed", "encounter": {"reference": "Encounter/history-1"},
                           "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "93000"}]}}],
            "PastEncounter": [{"resourceType": "Encounter", "id": "history-1", "status": "finished", "period": {"start": f"{yesterday}T14:00:00Z"}, "reasonCode": [{"text": "Prior follow-up"}]}],
            "ChargeItem": [],
            "Condition": [{"resourceType": "Condition", "id": "unrelated-condition",
                           "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.9"}]}}],
        }
        reads = {
            "/fhir/Organization/payer-1": {"resourceType": "Organization", "id": "payer-1", "name": "Test Health Plan"},
            "/fhir/Practitioner/provider-1": {"resourceType": "Practitioner", "id": "provider-1", "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567893"}], "name": [{"family": "Clinician"}]},
            "/fhir/Location/location-1": {"resourceType": "Location", "id": "location-1", "name": "Main Office"},
            "/fhir/Patient/patient-1": resources["Patient"][0],
        }
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/fhir/metadata":
                return httpx.Response(200, json=metadata)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "private-token"})
            if request.url.path in reads:
                return httpx.Response(200, json=reads[request.url.path])
            resource = request.url.path.rsplit("/", 1)[-1]
            if resource == "Encounter" and f"le{yesterday}" in request.url.params.get_list("date"):
                resource = "PastEncounter"
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": row} for row in resources[resource]]})

        practice = {
            "submitter_id": "SUBMITTER", "payer_aliases": {"Test Health Plan": "TESTPAYER"},
            "providers": {"1234567893": {"lastName": "TEST CLINIC", "ein": "111222333", "addressLine1": "1 MAIN ST", "city": "TEST", "stateCode": "TX", "zipCode": "77001"}},
            "locations": {"location-1": {"billing_provider_npi": "1234567893", "place_of_service": "11", "service_type": "1"}},
        }
        environment = {"ECW_BACKEND_CLIENT_ID": "backend-client", "ECW_BACKEND_ISSUER": "https://ehr.example.test/fhir"}
        with patch.dict(os.environ, environment), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            inputs, missing, source = await ecw_backend.fetch_inputs(
                {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                practice, transport=httpx.MockTransport(handler),
            )

        self.assertEqual(missing, ["eClinicalWorks FHIR did not return each billed line amount required by Availity PCE. Enable the practice-management/billing API field for this app; Availity does not supply this request input."])
        self.assertEqual((inputs["member_id"], inputs["payer_id"], inputs["relationship"]), ("MEMBER123", "TESTPAYER", "18"))
        self.assertEqual(inputs["lines"], [{"code": "99204", "units": "1", "charge": None, "modifiers": [],
                                            "diagnosis": "G629", "service_status": "scheduled",
                                            "description": "New patient consultation"}])
        self.assertEqual(inputs["reason"], "Reason: Neuropathy consultation; Planned service: 99204; Order reason: G62.9; Procedure: New patient consultation; Procedure reason: G62.9")
        self.assertIsNone(inputs["visit_type"])
        self.assertEqual(inputs["provider"]["npi"], "1234567893")
        self.assertEqual(source["appointment_date"], today)
        self.assertEqual(source["_patient_id"], "patient-1")
        self.assertEqual(source["timed_out_resources"], [])
        self.assertEqual(source["visit_type"], "not_returned")
        self.assertEqual(source["missing"], [])
        self.assertEqual(source["resources"]["Procedure"], 1)
        self.assertEqual(source["resources"]["PastEncounter"], 1)
        self.assertEqual(source["history_status"], "loaded")
        self.assertEqual(source["recent_completed_appointments"], [{"date": yesterday, "status": "finished", "visit_type": "not_returned", "reason": "Prior follow-up", "coded_services": ["93000"]}])
        self.assertEqual(source["resources"]["Condition"], 0)
        self.assertIn("system/Procedure.read", ecw_backend.SCOPES)
        self.assertIn("system/PractitionerRole.read", ecw_backend.SCOPES)
        self.assertNotIn("system/Appointment.read", ecw_backend.SCOPES)
        self.assertIn("system/ChargeItem.read", ecw_backend.SCOPES)
        token_request = next(request for request in requests if request.url.path == "/token")
        self.assertIn("grant_type=client_credentials", token_request.content.decode())
        self.assertNotIn("private-token", str(source))
        self.assertTrue(all("_count" not in request.url.params for request in requests))
        patient_request = next(request for request in requests if request.url.path == "/fhir/Patient" and request.method == "GET")
        self.assertEqual(dict(patient_request.url.params),
                         {"given": "Pat", "family": "Example", "birthdate": "1980-01-01"})
        encounter_request = next(request for request in requests if request.url.path == "/fhir/Encounter")
        self.assertEqual(encounter_request.url.params.get_list("date"),
                         [f"ge{today}", f"le{today_date + timedelta(days=365)}"])
        history_request = next(request for request in requests if request.url.path == "/fhir/Encounter" and f"le{yesterday}" in request.url.params.get_list("date"))
        self.assertEqual(history_request.url.params.get_list("date"),
                         [f"ge{today_date - timedelta(days=365)}", f"le{yesterday}"])
        charge_request = next(request for request in requests if request.url.path == "/fhir/ChargeItem")
        self.assertEqual(charge_request.url.params["context"], "Encounter/encounter-1")
        self.assertEqual(charge_request.url.params["subject"], "Patient/patient-1")
        procedure_request = next(request for request in requests if request.url.path == "/fhir/Procedure")
        self.assertEqual(dict(procedure_request.url.params), {"patient": "patient-1"})
        self.assertLess(requests.index(procedure_request), next(
            index for index, request in enumerate(requests) if request.url.path == "/fhir/Coverage"
        ))
        service_request = next(request for request in requests if request.url.path == "/fhir/ServiceRequest")
        self.assertEqual(dict(service_request.url.params), {"patient": "patient-1"})
        self.assertFalse(any(request.url.path == "/fhir/Condition" for request in requests))

        requests.clear()
        with patch.dict(os.environ, environment), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            await ecw_backend.fetch_inputs(
                {"patient_first_name": "Pat", "patient_last_name": "Example",
                 "patient_birth_date": "1980-01-01", "ecw_patient_id": "patient-1",
                 "ecw_appointment_date": today},
                practice, transport=httpx.MockTransport(handler),
            )
        self.assertTrue(any(request.url.path == "/fhir/Patient/patient-1" for request in requests))
        self.assertFalse(any(request.url.path == "/fhir/Patient" for request in requests))
        encounter_request = next(request for request in requests if request.url.path == "/fhir/Encounter")
        self.assertEqual(dict(encounter_request.url.params), {"patient": "patient-1", "date": today})

    def test_uses_only_explicit_ecw_visit_status(self):
        self.assertEqual(ecw_backend._visit_type({"type": [{"text": "New patient visit"}]}), "new")
        self.assertEqual(ecw_backend._visit_type({"serviceType": [{"text": "Established patient visit"}]}), "established")
        self.assertIsNone(ecw_backend._visit_type({"reasonCode": [{"text": "Initial consultation"}]}))


if __name__ == "__main__":
    unittest.main()
