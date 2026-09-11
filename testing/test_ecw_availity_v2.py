"""One end-to-end mapping check for the eCW backend insurance source."""

from datetime import date
from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.insurance import ecw_backend
from app.ingestion import ecw


class EcwBackendSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_token_and_fhir_resources_become_availity_inputs(self):
        today = date.today().isoformat()
        metadata = {"resourceType": "CapabilityStatement", "rest": [{"security": {"extension": [{
            "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
            "extension": [{"url": "token", "valueUri": "https://auth.example.test/token"}],
        }]}}]}
        resources = {
            "Patient": [{"resourceType": "Patient", "id": "patient-1", "name": [{"use": "official", "given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01", "gender": "female", "telecom": [{"system": "phone", "value": "555-234-7700"}]}],
            "Coverage": [{"resourceType": "Coverage", "id": "coverage-1", "status": "active", "subscriberId": "MEMBER123", "beneficiary": {"reference": "Patient/patient-1"}, "subscriber": {"reference": "Patient/patient-1"}, "relationship": {"coding": [{"code": "self"}]}, "payor": [{"reference": "Organization/payer-1"}], "period": {"start": "2026-01-01"}, "class": [{"type": {"coding": [{"code": "group"}]}, "value": "GROUP123"}]}],
            "Encounter": [{"resourceType": "Encounter", "id": "encounter-1", "status": "planned", "period": {"start": f"{today}T14:00:00Z"}, "reasonCode": [{"text": "Neuropathy consultation"}], "participant": [{"individual": {"reference": "Practitioner/provider-1"}}], "location": [{"location": {"reference": "Location/location-1"}}]}],
            "ServiceRequest": [{"resourceType": "ServiceRequest", "id": "order-1", "encounter": {"reference": "Encounter/encounter-1"}, "occurrenceDateTime": f"{today}T14:00:00Z", "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204"}]}, "quantityQuantity": {"value": 1}, "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "G62.9"}]}]}],
            "ChargeItem": [],
            "Condition": [],
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
        self.assertEqual(inputs["lines"], [{"code": "99204", "units": "1", "charge": None, "modifiers": [], "diagnosis": "G629", "service_status": "scheduled"}])
        self.assertEqual(inputs["provider"]["npi"], "1234567893")
        self.assertEqual(source["appointment_date"], today)
        self.assertNotIn("system/Appointment.read", ecw_backend.SCOPES)
        token_request = next(request for request in requests if request.url.path == "/token")
        self.assertIn("grant_type=client_credentials", token_request.content.decode())
        self.assertNotIn("private-token", str(source))
        self.assertTrue(all("_count" not in request.url.params for request in requests))
        encounter_request = next(request for request in requests if request.url.path == "/fhir/Encounter")
        self.assertEqual(encounter_request.url.params["date"], f"ge{today}")
        charge_request = next(request for request in requests if request.url.path == "/fhir/ChargeItem")
        self.assertEqual(charge_request.url.params["context"], "Encounter/encounter-1")
        self.assertEqual(charge_request.url.params["subject"], "Patient/patient-1")


if __name__ == "__main__":
    unittest.main()
