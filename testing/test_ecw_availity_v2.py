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
    async def test_slow_cached_patient_read_does_not_invent_a_patient(self):
        profile = {"patient_first_name": "Pat", "patient_last_name": "Example",
                   "patient_birth_date": "1980-01-01", "gender": "F"}
        with patch.object(ecw_backend, "_read", new=AsyncMock(side_effect=asyncio.TimeoutError)), \
                patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=asyncio.TimeoutError)):
            with self.assertRaisesRegex(ecw_backend.EcwBackendError, "timed out"):
                await ecw_backend._cached_patient(None, "", "", "patient-1", profile)

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
        self.assertEqual((rows, timed_out, unavailable), ([], None, "ServiceRequest (read failed)"))

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

    async def test_saved_eligibility_probe_preserves_forbidden_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fhir/metadata":
                return httpx.Response(200, json={"resourceType": "CapabilityStatement", "rest": [{"resource": [
                    {"type": "CoverageEligibilityResponse"}, {"type": "DocumentReference"},
                ]}]})
            if request.url.path == "/fhir/Patient":
                return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": {
                    "resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Pat"], "family": "Example"}],
                    "birthDate": "1980-01-01",
                }}]})
            if request.url.path == "/fhir/CoverageEligibilityResponse":
                return httpx.Response(403, json={"resourceType": "OperationOutcome"})
            if request.url.path == "/fhir/DocumentReference":
                return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": {
                    "resourceType": "DocumentReference", "id": "document-1",
                }}, {"resource": {"resourceType": "OperationOutcome"}}]})
            return httpx.Response(404)

        with patch.object(ecw_backend, "settings", return_value=("client", "https://ehr.example.test/fhir")), \
                patch.object(ecw_backend, "_access_token", new=AsyncMock(return_value="token")) as access_token:
            result = await ecw_backend.probe_saved_eligibility(
                {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(result["resources"], {
            "CoverageEligibilityResponse": {"status": "forbidden", "count": None},
            "DocumentReference": {"status": "available", "count": 1},
        })
        self.assertEqual(access_token.await_args.args[3], ecw_backend.SCOPES + ('system/DocumentReference.read',))
        self.assertEqual(access_token.await_count, 3)

        async def scoped_token(client, client_id, issuer, scopes=ecw_backend.SCOPES):
            if 'system/CoverageEligibilityResponse.read' in scopes:
                raise ecw_backend.EcwBackendError('Optional scope denied')
            return 'document-token'

        with patch.object(ecw_backend, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
                patch.object(ecw_backend, '_access_token', side_effect=scoped_token):
            result = await ecw_backend.probe_saved_eligibility(
                {'patient_first_name': 'Pat', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01'},
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(result['resources']['CoverageEligibilityResponse']['status'], 'scope_denied_or_unavailable')
        self.assertEqual(result['resources']['DocumentReference'], {'status': 'available', 'count': 1})

    async def test_backend_ignores_foreign_policy_and_reads_contained_subscriber(self):
        await self._backend_inputs(contained=True)

    async def test_backend_token_and_fhir_resources_become_availity_inputs(self):
        await self._backend_inputs()

    async def _backend_inputs(self, contained=False):
        today_date = datetime.now(ZoneInfo("America/New_York")).date()
        today = today_date.isoformat()
        tomorrow = (today_date + timedelta(days=1)).isoformat()
        yesterday = (today_date - timedelta(days=1)).isoformat()
        metadata = {"resourceType": "CapabilityStatement", "rest": [{"security": {"extension": [{
            "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
            "extension": [{"url": "token", "valueUri": "https://auth.example.test/token"}],
        }]}}]}
        resources = {
            "Patient": [{"resourceType": "Patient", "id": "patient-1", "name": [{"use": "official", "given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01", "gender": "female", "telecom": [{"system": "phone", "value": "555-234-7700"}]}],
            "Coverage": [{"resourceType": "Coverage", "id": "coverage-1", "status": "active", "subscriberId": "MEMBER123", "beneficiary": {"reference": "Patient/patient-1"}, "subscriber": {"reference": "Patient/patient-1"}, "relationship": {"coding": [{"code": "self"}]}, "payor": [{"reference": "Organization/payer-1"}], "period": {"start": "2026-01-01"}, "class": [{"type": {"coding": [{"code": "group"}]}, "value": "GROUP123"}]}],
            "Encounter": [{"resourceType": "Encounter", "id": "encounter-1", "status": "planned", "subject": {"reference": "Patient/patient-1"}, "period": {"start": f"{tomorrow}T14:00:00Z"}, "reasonCode": [{"text": "Neuropathy consultation"}], "participant": [{"individual": {"reference": "Practitioner/provider-1"}}], "location": [{"location": {"reference": "Location/location-1"}}]}],
            "ServiceRequest": [{"resourceType": "ServiceRequest", "id": "order-1", "encounter": {"reference": "Encounter/encounter-1"}, "occurrenceDateTime": f"{tomorrow}T14:00:00Z", "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204"}]}, "quantityQuantity": {"value": 1}, "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "G62.9"}]}]}],
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
        if contained:
            policy = resources['Coverage'][0]
            policy['subscriber'] = {'reference': '#sub'}
            policy['contained'] = [{**resources['Patient'][0], 'id': 'sub', 'resourceType': 'RelatedPerson'}]
            policy['subscriberId'] = ' '
            policy['identifier'] = [{'type': {'coding': [{'code': 'MB'}]}, 'value': 'MEMBER123'}]
            resources['Coverage'].append({**policy, 'id': 'foreign', 'beneficiary': {'reference': 'Patient/other'}})
        reads = {
            "/fhir/Organization/payer-1": {"resourceType": "Organization", "id": "payer-1", "name": "Test Health Plan"},
            "/fhir/Practitioner/provider-1": {"resourceType": "Practitioner", "id": "provider-1", "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567893"}], "name": [{"family": "Clinician"}]},
            "/fhir/Location/location-1": {"resourceType": "Location", "id": "location-1", "name": "Main Office"},
            "/fhir/Patient/patient-1": resources["Patient"][0],
        }
        resources['Encounter'][0]['appointment'] = [{'reference': 'Appointment/notes'}, {'reference': 'Appointment/other-patient'}]
        reads['/fhir/Appointment/notes'] = {'resourceType': 'Appointment', 'id': 'notes', 'status': 'booked',
            'participant': [{'actor': {'reference': 'Patient/patient-1'}}], 'comment': 'Patient: please explain my treatment plan.'}
        reads['/fhir/Appointment/other-patient'] = {'resourceType': 'Appointment', 'id': 'other-patient', 'status': 'booked',
            'participant': [{'actor': {'reference': 'Patient/another'}}], 'comment': 'Must not import this.'}
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/fhir/metadata":
                return httpx.Response(200, json=metadata)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "private-token"})
            if request.url.path in reads:
                return httpx.Response(200, json=reads[request.url.path])
            if request.url.path == "/fhir/Encounter/encounter-1":
                return httpx.Response(200, json=resources["Encounter"][0])
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
        self.assertEqual([row['id'] for row in inputs['lines']], ['Procedure:procedure-1:99204', 'ServiceRequest:order-1:99204'])
        self.assertTrue(all(row['needs_confirmation'] for row in inputs['lines']))
        self.assertTrue(all(row['code'] == '99204' and row['units'] == '1' and row['diagnosis'] == 'G629' for row in inputs['lines']))
        self.assertEqual(inputs['lines'][0]['description'], 'New patient consultation')
        self.assertEqual(inputs["reason"], "Reason: Neuropathy consultation; Planned service: 99204; Order reason: G62.9; Procedure: New patient consultation; Procedure reason: G62.9")
        self.assertIsNone(inputs["visit_type"])
        self.assertEqual(inputs["provider"]["npi"], "1234567893")
        self.assertEqual(source['appointment_notes_status'], 'returned')
        self.assertEqual([field['text'] for field in source['appointment_fields']], ['Patient: please explain my treatment plan.'])
        self.assertEqual(source['coverage_records'][0]['member_id'], 'MEMBER123')
        self.assertNotIn('treatment plan', inputs['reason'])
        self.assertEqual(source["appointment_date"], tomorrow)
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
        # Live eCW accepts bare IDs; qualified references return HTTP 400.
        self.assertEqual(charge_request.url.params["context"], "encounter-1")
        self.assertEqual(charge_request.url.params["subject"], "patient-1")
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
                 "ecw_appointment_date": tomorrow},
                practice, transport=httpx.MockTransport(handler),
            )
        self.assertTrue(any(request.url.path == "/fhir/Patient/patient-1" for request in requests))
        self.assertFalse(any(request.url.path == "/fhir/Patient" for request in requests))
        encounter_request = next(request for request in requests if request.url.path == "/fhir/Encounter")
        self.assertEqual(dict(encounter_request.url.params), {"patient": "patient-1", "date": tomorrow})

        requests.clear()
        with patch.dict(os.environ, environment), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            selected_inputs, _, _ = await ecw_backend.fetch_inputs(
                {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                practice, "encounter-1", transport=httpx.MockTransport(handler),
            )
        self.assertEqual(selected_inputs["service_date"], tomorrow)
        self.assertTrue(any(request.url.path == "/fhir/Encounter/encounter-1" for request in requests))

        async def optional_denied(client, client_id, issuer, scopes=ecw_backend.SCOPES):
            if 'system/Appointment.read' in scopes:
                raise ecw_backend.EcwBackendError('Scope unavailable')
            return 'token'
        with patch.dict(os.environ, environment), patch.object(ecw, 'ECW_KMS_KEY_ID', 'kms-key'), patch.object(ecw_backend, '_access_token', side_effect=optional_denied):
            _, _, partial = await ecw_backend.fetch_inputs(
                {'patient_first_name': 'Pat', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01'},
                practice, 'encounter-1', transport=httpx.MockTransport(handler))
        self.assertEqual(partial['appointment_notes_status'], 'unavailable')
        self.assertEqual(partial['appointment_id'], 'encounter-1')

    async def test_lists_only_current_selectable_appointments(self):
        now = datetime.now(ZoneInfo("America/New_York"))
        today = now.date()
        tomorrow = (today + timedelta(days=1)).isoformat()
        metadata = {"resourceType": "CapabilityStatement", "rest": [{"security": {"extension": [{
            "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
            "extension": [{"url": "token", "valueUri": "https://auth.example.test/token"}],
        }]}}]}
        patient = {"resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}
        encounters = [
            {"resourceType": "Encounter", "id": "already-started", "status": "planned", "subject": {"reference": "Patient/patient-1"}, "period": {"start": (now - timedelta(minutes=5)).isoformat()}},
            {"resourceType": "Encounter", "id": "later-today", "status": "planned", "subject": {"reference": "Patient/patient-1"}, "period": {"start": (now + timedelta(hours=2)).isoformat()}, "reasonCode": [{"text": "Later today"}]},
            {"resourceType": "Encounter", "id": "scheduled", "status": "planned", "subject": {"reference": "Patient/patient-1"}, "period": {"start": f"{tomorrow}T09:00:00Z"}, "reasonCode": [{"text": "Follow-up"}]},
            {"resourceType": "Encounter", "id": "cancelled", "status": "cancelled", "subject": {"reference": "Patient/patient-1"}, "period": {"start": f"{tomorrow}T10:00:00Z"}},
            {"resourceType": "Encounter", "id": "other-patient", "status": "planned", "subject": {"reference": "Patient/patient-2"}, "period": {"start": f"{tomorrow}T11:00:00Z"}},
        ]
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fhir/metadata":
                return httpx.Response(200, json=metadata)
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "token"})
            if request.url.path == "/fhir/Patient":
                return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": patient}]})
            if request.url.path == "/fhir/Encounter":
                return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": row} for row in encounters]})
            return httpx.Response(404)
        with patch.dict(os.environ, {"ECW_BACKEND_CLIENT_ID": "backend-client", "ECW_BACKEND_ISSUER": "https://ehr.example.test/fhir"}), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            result = await ecw_backend.list_appointments(
                {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                {"timezone": "America/New_York"}, transport=httpx.MockTransport(handler),
            )
        self.assertEqual(result, [
            {"id": "later-today", "start": (now + timedelta(hours=2)).isoformat(), "status": "planned", "reason": "Reason: Later today"},
            {"id": "scheduled", "start": f"{tomorrow}T09:00:00Z", "status": "planned", "reason": "Reason: Follow-up"},
        ])

    async def test_patient_lookup_retries_family_and_birthdate_then_requires_unique_match(self):
        profile = {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"}
        patient = {"resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}
        async def search(_, __, ___, resource, params):
            self.assertEqual(resource, "Patient")
            return [] if "given" in params else [patient]
        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=search)) as query:
            matched, patient_id, _ = await ecw_backend._patient_for_profile(None, "", "", profile)
        self.assertEqual((matched, patient_id), (patient, "patient-1"))
        self.assertEqual(query.await_count, 2)
        with patch.object(ecw_backend, "_search", new=AsyncMock(return_value=[])):
            with self.assertRaisesRegex(ecw_backend.EcwBackendError, "No eClinicalWorks patient matched"):
                await ecw_backend._patient_for_profile(None, "", "", profile)

    async def test_patient_lookup_finds_unique_multiword_ecw_surname(self):
        profile = {"patient_first_name": "Sample", "patient_middle_initial": "T",
                   "patient_last_name": "Vale", "patient_birth_date": "1980-01-01"}
        patient = {"resourceType": "Patient", "id": "patient-1", "name": [
            {"given": ["Sample", "Taylor"], "family": "North Vale"}], "birthDate": "1980-01-01"}
        other = {**patient, "id": "patient-2", "name": [{"given": ["Sample", "Taylor"], "family": "Other Vale"}]}
        async def search(_, __, ___, resource, params):
            self.assertEqual(resource, "Patient")
            return [patient] if params.get("name") == "Sample" else []
        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=search)) as query:
            matched, patient_id, _ = await ecw_backend._patient_for_profile(None, "", "", profile)
        self.assertEqual((matched, patient_id), (patient, "patient-1"))
        self.assertEqual(query.await_count, 4)
        self.assertEqual(query.await_args.args[-1], {"name": "Sample", "birthdate": "1980-01-01"})
        with patch.object(ecw_backend, "_search", new=AsyncMock(
                side_effect=lambda *args: [patient] if args[-1] == {"given": "Sample", "birthdate": "1980-01-01"} else [])) as query:
            matched, patient_id, _ = await ecw_backend._patient_for_profile(None, "", "", profile)
        self.assertEqual((matched, patient_id), (patient, "patient-1"))
        self.assertEqual(query.await_count, 5)
        with patch.object(ecw_backend, "_search", new=AsyncMock(side_effect=lambda *args: [patient, other] if "name" in args[-1] else [])):
            with self.assertRaisesRegex(ecw_backend.EcwBackendError, "Multiple eClinicalWorks patients matched"):
                await ecw_backend._patient_for_profile(None, "", "", profile)

    async def test_lists_completed_history_without_an_eligibility_request(self):
        today = datetime.now(ZoneInfo("America/New_York")).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        metadata = {"resourceType": "CapabilityStatement", "rest": [{"security": {"extension": [{
            "url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
            "extension": [{"url": "token", "valueUri": "https://auth.example.test/token"}],
        }]}}]}
        patient = {"resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}
        encounters = [
            {"resourceType": "Encounter", "id": "completed", "status": "finished", "period": {"start": f"{yesterday}T09:00:00Z"}, "reasonCode": [{"text": "Follow-up"}]},
            {"resourceType": "Encounter", "id": "cancelled", "status": "cancelled", "period": {"start": f"{yesterday}T10:00:00Z"}},
        ]
        procedures = [{"resourceType": "Procedure", "id": "procedure-1", "encounter": {"reference": "Encounter/completed"},
                       "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99214"}]}}]
        requests = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/fhir/metadata": return httpx.Response(200, json=metadata)
            if request.url.path == "/token": return httpx.Response(200, json={"access_token": "token"})
            if request.url.path == "/fhir/Patient": return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": patient}]})
            if request.url.path == "/fhir/Encounter": return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": row} for row in encounters]})
            if request.url.path == "/fhir/Procedure": return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": row} for row in procedures]})
            if request.url.path == "/fhir/ServiceRequest": return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
            return httpx.Response(404)
        with patch.dict(os.environ, {"ECW_BACKEND_CLIENT_ID": "backend-client", "ECW_BACKEND_ISSUER": "https://ehr.example.test/fhir"}), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            result = await ecw_backend.list_completed_appointments(
                {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01"},
                {"timezone": "America/New_York"}, transport=httpx.MockTransport(handler),
            )
        self.assertEqual(result, [{"id": "completed", "start": f"{yesterday}T09:00:00Z", "date": yesterday,
                                   "status": "finished", "visit_type": "not_returned", "reason": "Follow-up", "coded_services": ["99214"]}])
        self.assertTrue(all(request.url.host != "healthcare.us.stedi.com" for request in requests))

    def test_uses_only_explicit_ecw_visit_status(self):
        self.assertEqual(ecw_backend._visit_type({"type": [{"text": "New patient visit"}]}), "new")
        self.assertEqual(ecw_backend._visit_type({"serviceType": [{"text": "Established patient visit"}]}), "established")
        self.assertIsNone(ecw_backend._visit_type({"reasonCode": [{"text": "Initial consultation"}]}))

        requests.clear()
        with patch.dict(os.environ, environment), patch.object(ecw, "ECW_KMS_KEY_ID", "kms-key"), patch.object(ecw, "_client_assertion", return_value="signed-assertion"):
            await ecw_backend.fetch_inputs(
                {"patient_first_name": "Pat", "patient_last_name": "Example",
                 "patient_birth_date": "1980-01-01", "ecw_patient_id": "patient-1"},
                practice, transport=httpx.MockTransport(handler),
            )
        self.assertTrue(any(request.url.path == "/fhir/Patient/patient-1" for request in requests))
        self.assertFalse(any(request.url.path == "/fhir/Patient" for request in requests))


if __name__ == "__main__":
    unittest.main()
