"""Synthetic API contract, patient isolation, duplicate-submission and money checks."""
import asyncio
from datetime import datetime
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import fitz
import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.insurance.availity import Gateway, Settings, DEMO_SCOPE, LIVE_SCOPE, PATHS, DEMO_IDS
from app.insurance import availity_intake as intake, availity_results as results
from app.insurance import golden
from app.api.routers import insurance_v2 as v2
from app.core.patient_models import PatientProfile
from app.main import app


def settings(demo=True):
    return Settings('demo' if demo else 'production', 'synthetic-client', 'synthetic-secret', DEMO_SCOPE if demo else LIVE_SCOPE,
                    'https://api.availity.com/availity', 'https://api.availity.com/v1/token')


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_accepted_response_and_safe_polling(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path == '/v1/token':
                return httpx.Response(200, json={'access_token': 'secret-token', 'expires_in': 300})
            if request.method == 'POST':
                self.assertEqual(json.loads(request.content), {})
                return httpx.Response(202, headers={'Location': 'https://api.availity.com/availity/v2/patient-cost-estimates/prof/123', 'X-Api-Mock-Response': 'true'})
            self.assertEqual(request.headers['X-Api-Mock-Scenario-ID'], 'PCEP-Success')
            return httpx.Response(200, json={'id': DEMO_IDS['estimate'], 'result': {}}, headers={'X-Api-Mock-Response': 'true'})
        client = Gateway(settings(), transport=httpx.MockTransport(handler))
        accepted = await client.request('POST', PATHS['estimate'], body={}, resource='estimate', scenario='PCEP-Accepted')
        self.assertEqual(accepted['outcome'], 'pending')
        self.assertEqual(accepted['resource_id'], DEMO_IDS['estimate'])
        await client.request('GET', PATHS['estimate'] + '/' + accepted['resource_id'], resource='estimate', scenario='PCEP-Success')
        self.assertEqual(sum(r.url.path == '/v1/token' for r in calls), 1)
        self.assertIsNone(client.resource_id('https://evil.invalid/availity/v2/patient-cost-estimates/prof/123', 'estimate'))
        self.assertIsNone(client.resource_id('/availity/v2/patient-cost-estimates/prof/../other', 'estimate'))
        self.assertIsNone(client.resource_id('/availity/v2/patient-cost-estimates/prof/123?token=anything', 'estimate'))
        self.assertIsNone(client.resource_id('/availity/v1/coverages/123', 'estimate'))

    async def test_environment_and_timeout_fail_closed(self):
        for mode in ('mock-in-production', 'post-timeout', 'token-failure', 'unsafe-location'):
            with self.subTest(mode=mode):
                def handler(request):
                    if request.url.path == '/v1/token':
                        return httpx.Response(403 if mode == 'token-failure' else 200, json={'access_token': 'token'})
                    if mode == 'post-timeout':
                        raise httpx.ReadTimeout('secret-token must not escape', request=request)
                    if mode == 'unsafe-location':
                        return httpx.Response(202, headers={'Location': 'https://evil.invalid/steal'})
                    return httpx.Response(200, json={'id': '123', 'statusCode': '4'}, headers={'X-Api-Mock-Response': 'true'})
                client = Gateway(settings(False), transport=httpx.MockTransport(handler))
                value = await client.request('POST', PATHS['coverage'], data={'memberId': 'TEST'}, resource='coverage')
                self.assertIn(value['outcome'], {'indeterminate', 'unavailable'})
                self.assertNotIn('secret-token', json.dumps(value))
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'production', 'AVAILITY_SCOPE': DEMO_SCOPE}):
            with self.assertRaises(ValueError):
                Settings.read()


class ResultsAndIntakeTests(unittest.TestCase):
    def test_demo_does_not_require_a_practice_configuration(self):
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'demo', 'AVAILITY_SCOPE': DEMO_SCOPE,
                                     'AVAILITY_PRACTICE_CONFIG': 'not-json'}):
            configured, practice, _ = v2.configuration()
        self.assertEqual(configured.environment, 'demo')
        self.assertEqual(practice, {})
        expected = {'timezone': 'America/Chicago', 'payer_aliases': {'Test plan': 'TEST'}}
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'demo', 'AVAILITY_SCOPE': DEMO_SCOPE,
                                     'AVAILITY_PRACTICE_CONFIG': json.dumps(expected)}):
            _, practice, _ = v2.configuration()
        self.assertEqual(practice, expected)

        placeholder = {**expected, 'providers': {'BILLING OR RENDERING NPI': {'lastName': 'placeholder'},
                                                  '1234567893': {'lastName': 'valid'}}}
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'demo', 'AVAILITY_SCOPE': DEMO_SCOPE,
                                     'AVAILITY_PRACTICE_CONFIG': json.dumps(placeholder)}):
            _, practice, _ = v2.configuration()
        self.assertEqual(practice['providers'], {'1234567893': {'lastName': 'valid'}})
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'production', 'AVAILITY_SCOPE': LIVE_SCOPE,
                                     'AVAILITY_PRACTICE_CONFIG': json.dumps(placeholder)}):
            with self.assertRaises(HTTPException):
                v2.configuration()

    def test_only_complete_matching_payer_estimates_become_a_total(self):
        expected = [{'code': '99204', 'charge': '300.00', 'units': '1', 'modifiers': []}]
        payload = {'result': {'claimInformation': {'serviceLines': [{'procedureCode': '99204', 'quantity': '1', 'amount': '300.00', 'estimatedPatientResponsibility': '0.00', 'allowed': {'amount': '150.00'}}]}}}
        value = results.estimate(payload, expected, False)
        self.assertTrue(value['estimate_available'])
        self.assertEqual(value['patient_cents'], 0)
        self.assertEqual(value['allowed_cents'], 15000)
        for change in ({'procedureCode': '99205'}, {'quantity': '2'}, {'quantity': 'invalid'}, {'amount': '299.00'}, {'estimatedPatientResponsibility': None}, {'estimatedPatientResponsibility': 'NaN'}):
            copy = deepcopy(payload)
            copy['result']['claimInformation']['serviceLines'][0].update(change)
            self.assertFalse(results.estimate(copy, expected, False)['estimate_available'])
        for bad in ('NaN', 'Infinity', '-0.01', '0.001', True, {}, None):
            self.assertIsNone(results.cents(bad))

    def test_coverage_keeps_network_and_accumulator_dimensions(self):
        value = results.coverage({'statusCode': '4', 'plans': [{'name': 'TEST', 'benefits': [{'name': 'Medical', 'amounts': {'deductibles': {'inNetwork': [{'remaining': '10.25', 'level': 'Individual'}], 'outOfNetwork': [{'remaining': '500.00', 'level': 'Family'}]}}}]}]})
        benefits = value['plans'][0]['benefits']
        self.assertEqual([(x['network'], x['remaining'], x['level']) for x in benefits], [('inNetwork', '10.25', 'Individual'), ('outOfNetwork', '500.00', 'Family')])
        self.assertIn('Not established', value['provider_network'])
        self.assertIsNone(value['pce_available'])
        self.assertIn('Partially', results.review({'statusCode': 'A2'})['status'])
        self.assertIn('check again', results.review({'statusCode': 'A6'})['status'])

    def test_document_inputs_do_not_import_old_benefits_or_credentials(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 40), 'New Patient: Neuropathy and EMG\nPassword: should-never-be-stored\nDED and OOP met; collect $241.51')
            source = intake.read_source('appointment', doc.tobytes(), 'email.pdf', {})
        self.assertNotIn('should-never-be-stored', json.dumps(source))
        self.assertNotIn('241.51', json.dumps(source))
        self.assertEqual(source['lines'], [])
        self.assertEqual(list(source['fields']), ['reason'])
        with self.assertRaises(golden.GoldenCaseError):
            intake.read_source('charges', b'Procedure Code,Network,Amount\n99204,Blue Choice PPO,$150.00\n', 'rates.csv', {})
        source = intake.read_source('order', b'Patient First Name,Patient Last Name,Patient DOB,CPT,Units,Billed Charge,ICD-10\nPat,Example,1980-01-01,99204,1,300.00,G62.9\n', 'order.csv', {})
        self.assertEqual(source['lines'][0]['charge'], '300.00')
        with self.assertRaises(golden.GoldenCaseError):
            intake.read_source('order', b'Patient First Name,CPT\nPat,99204\nOther,99205\n', 'mixed.csv', {})

    def test_fee_csv_and_front_desk_gate_use_explicit_prices_and_payer_evidence(self):
        source = intake.read_fee_schedule(
            b'CPT,Description,Cash Price,Billed Charge,Service Status\n99204,New visit,225.00,300.00,Scheduled\n95913,Possible test,500.00,900.00,Possible\n',
            'clinic-fees.csv',
        )
        self.assertEqual((source['rate_count'], source['lines'][0]['cash_cents']), (2, 22500))
        case = {'inputs': {'service_date': '2026-09-11', 'lines': [
                    {'code': '99204', 'units': '1', 'cash_cents': 22500, 'service_status': 'scheduled'},
                    {'code': '95913', 'units': '1', 'cash_cents': 50000, 'service_status': 'possible'},
                ]}, 'transactions': {'coverage': {'result': {'active': True, 'plans': [{'insuranceType': 'HMO', 'benefits': []}]}},
                'estimate': {'result': {'estimate_available': True, 'patient_cents': 0, 'allowed_cents': 15000}}}}
        summary = v2.front_desk_summary(case)
        self.assertEqual((summary['action'], summary['scheduled']['cash_cents']), ('do_not_schedule', 22500))
        case['transactions']['referral'] = {'result': {'status_code': 'A1', 'status': 'Certified in total', 'certificationNumber': 'REF-1',
                                                       'certificationEffectiveDate': '2026-09-01', 'certificationExpirationDate': '2026-10-01'}}
        self.assertEqual(v2.front_desk_summary(case)['heading'], 'Collect $0.00 for the scheduled visit')

    def test_fee_csv_rejects_ambiguous_amount_column(self):
        with self.assertRaisesRegex(golden.GoldenCaseError, 'Cash Price and/or Billed Charge'):
            intake.read_fee_schedule(
                b'Procedure Code,Description,Network,Amount\n99204,New visit,Blue Choice PPO,151.60\n',
                'payer-rates.csv',
            )


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}
        self.posts = 0
        self.patches = []
        self.env = patch.dict(os.environ, {'INSURANCE_BETA_ENABLED': 'true', 'SOURCE_LINK_SECRET': 's' * 40,
                                           'AVAILITY_ENVIRONMENT': 'demo', 'AVAILITY_SCOPE': DEMO_SCOPE,
                                           'AVAILITY_PRACTICE_CONFIG': '{}', 'AVAILITY_CLIENT_ID': 'synthetic-client', 'AVAILITY_CLIENT_SECRET': 'synthetic-secret'})
        self.env.start()
        def create(patient, share, binding, case):
            value = {**deepcopy(case), 'id': 'case-v2', 'created_at': '2026-09-09T10:00:00Z', 'expires_at': '2099-01-01T10:00:00Z'}
            self.saved['case-v2'] = (patient, share, value)
            return deepcopy(value)
        def get(id, patient, share):
            value = self.saved.get(id)
            return deepcopy(value[2]) if value and value[:2] == (patient, share) else None
        def save(case, patient, share, event, revision):
            before = get(case['id'], patient, share)
            if not before or before['revision'] != revision:
                return None
            value = {**deepcopy(case), 'revision': revision + 1}
            self.saved[case['id']] = (patient, share, value)
            return deepcopy(value)
        def latest(patient, share, binding):
            candidates = [deepcopy(value) for owner, shared, value in self.saved.values()
                          if (owner, shared, value.get('binding')) == (patient, share, binding)]
            return candidates[-1] if candidates else None
        for name, replacement in [('create_case', create), ('get_case', get), ('save_v2', save), ('latest_v2', latest)]:
            p = patch.object(v2.store, name, side_effect=replacement); p.start(); self.patches.append(p)
        p = patch.object(v2, 'get_patient_profile', return_value=PatientProfile('patient-1', 'Pat', 'Example', '1980-01-01', 'unknown'))
        p.start(); self.patches.append(p)
        def handler(request):
            if request.url.path == '/v1/token':
                return httpx.Response(200, json={'access_token': 'private-token', 'expires_in': 300})
            if request.method == 'POST':
                self.posts += 1
            if '/coverages' in request.url.path:
                return httpx.Response(200, json={'id': 'coverage-123', 'statusCode': '4', 'supplementalInformation': {'professionalPatientCostEstimator': True}, 'plans': []}, headers={'X-Api-Mock-Response': 'true'})
            if request.method == 'POST':
                self.assertEqual(json.loads(request.content), {})
                return httpx.Response(202, headers={'X-Api-Mock-Response': 'true', 'Location': 'https://api.availity.com/availity/v2/patient-cost-estimates/prof/' + DEMO_IDS['estimate']})
            return httpx.Response(200, json={'result': {'claimInformation': {'serviceLines': [{'procedureCode': '95165', 'estimatedPatientResponsibility': '10.00', 'allowed': {'amount': '1807.50'}}]}}}, headers={'X-Api-Mock-Response': 'true'})
        client = Gateway(settings(), transport=httpx.MockTransport(handler))
        p = patch.object(v2, 'gateway', return_value=client); p.start(); self.patches.append(p)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')
        self.client.cookies.set('clinicforce_doctor_share', golden.create_share_token('patient-1', 'share-1', True), path='/api/doctor')

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in self.patches:
            p.stop()
        self.env.stop()

    async def test_saved_workflow_polls_without_resubmitting_and_cannot_cross_patients(self):
        base = '/api/doctor/insurance/v2/cases'
        created = await self.client.post(base, json={})
        self.assertEqual(created.status_code, 200, created.text)
        path = base + '/case-v2'
        response = await self.client.post(path + '/transactions/coverage')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['transactions']['coverage']['outcome'], 'complete')
        for _ in range(2):
            response = await self.client.post(path + '/transactions/estimate')
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.posts, 2)
        self.saved['case-v2'][2]['transactions']['estimate']['next_poll_at'] = 0
        response = await self.client.post(path + '/transactions/estimate/refresh')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['transactions']['estimate']['result']['patient_cents'], 1000)
        self.assertNotIn('payload', response.text)
        self.assertNotIn('request_body', response.text)
        self.assertNotIn('private-token', response.text)
        self.assertEqual(self.posts, 2)
        v1 = await self.client.get('/api/doctor/insurance/cases/case-v2')
        self.assertEqual(v1.status_code, 409)
        self.client.cookies.set('clinicforce_doctor_share', golden.create_share_token('patient-other', 'share-1', True), path='/api/doctor')
        denied = await self.client.get(path)
        self.assertEqual(denied.status_code, 404)

    async def test_demo_rejects_uploads_and_parallel_posts_claim_once(self):
        base = '/api/doctor/insurance/v2/cases'
        await self.client.post(base, json={})
        blocked = await self.client.post(base + '/case-v2/documents/card', files={'file': ('card.pdf', b'patient-data')})
        self.assertEqual(blocked.status_code, 409)
        fees = await self.client.post(base + '/case-v2/documents/fees', files={'file': ('fees.csv', b'CPT,Cash Price,Billed Charge\n92523,125.00,250.00\n95913,600.00,900.00\n')})
        self.assertEqual(fees.status_code, 200, fees.text)
        self.assertEqual(fees.json()['summary']['scheduled']['cash_cents'], 1250000)
        suggestion = {'groups': [{'id': 'service', 'label': 'Speech service', 'category': 'procedure', 'selection_mode': 'multiple',
                                  'requires_groups': [], 'codes': [{'code': '92523', 'reason': 'Matches the stated synthetic service'}]}], 'notes': []}
        with patch.object(v2.cpt_suggester, 'suggest_cpt_codes', return_value=suggestion):
            suggested = await self.client.post(base + '/case-v2/suggestions')
        self.assertEqual(suggested.status_code, 200, suggested.text)
        confirmed = await self.client.post(base + '/case-v2/services', json={'lines': [{'code': '92523', 'quantity': 2, 'service_status': 'scheduled'}]})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()['inputs']['lines'][0]['cash_cents'], 12500)
        responses = await asyncio.gather(*(self.client.post(base + '/case-v2/transactions/coverage') for _ in range(2)))
        self.assertTrue(all(r.status_code in {200, 409} for r in responses))
        self.assertEqual(self.posts, 1)
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'production', 'AVAILITY_SCOPE': LIVE_SCOPE}):
            changed = await self.client.get(base + '/case-v2')
            self.assertEqual(changed.status_code, 409)

    async def test_only_confirmed_access_rejections_can_be_retried(self):
        base = '/api/doctor/insurance/v2/cases'
        await self.client.post(base, json={})
        saved = self.saved['case-v2'][2]
        saved['transactions']['coverage'] = {'outcome': 'indeterminate', 'http_status': 504, 'next_poll_at': 0}
        rejected = await self.client.post(base + '/case-v2/transactions/coverage/retry')
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.posts, 0)
        saved['transactions']['coverage'] = {'outcome': 'unavailable', 'http_status': 403, 'next_poll_at': 0}
        accepted = await self.client.post(base + '/case-v2/transactions/coverage/retry')
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['transactions']['coverage']['outcome'], 'complete')
        self.assertEqual(self.posts, 1)
        self.assertEqual(len(self.saved['case-v2'][2]['previous_attempts']), 1)

    async def test_production_ecw_to_payer_workflow(self):
        provider = {'lastName': 'TEST CLINIC', 'ein': '111222333', 'addressLine1': '123 TEST ST', 'city': 'TEST', 'stateCode': 'TX', 'zipCode': '77001'}
        practice = {'production_enabled': True, 'timezone': 'America/Chicago', 'submitter_id': 'TESTSUBMITTER', 'providers': {'1234567893': provider}}
        sent = []
        def handler(request):
            if request.url.path == '/v1/token':
                return httpx.Response(200, json={'access_token': 'private-production-token', 'expires_in': 300})
            self.assertNotIn('x-api-mock-scenario-id', request.headers)
            if '/configurations' in request.url.path:
                type_name = request.url.params['type']
                elements = {'memberId': {'required': True}} if type_name == '270' else {'payerId': {'required': True}} if type_name == 'professional-claims' else {'patient': {'required': True}}
                return httpx.Response(200, json={'configurations': [{'payerId': 'TESTPAYER', 'type': type_name, 'elements': elements}]})
            if '/availity-payer-list' in request.url.path:
                return httpx.Response(200, json={'payers': [{'payerId': 'TESTPAYER', 'processingRoutes': [{'transactionDescription': '270 Eligibility', 'submissionMode': 'API', 'availability': True, 'enrollmentRequired': False}]}]})
            sent.append(request)
            if '/coverages' in request.url.path:
                self.assertIn(b'patientFirstName=Pat', request.content)
                return httpx.Response(200, json={'id': 'real-coverage-id', 'statusCode': '4', 'subscriber': {'memberId': 'MEMBER123'}, 'payer': {'payerId': 'TESTPAYER'}, 'patient': {'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'}, 'plans': [{'status': 'Active', 'benefits': []}], 'supplementalInformation': {'professionalPatientCostEstimator': True}})
            if '/patient-cost-estimates' in request.url.path:
                body = json.loads(request.content)
                self.assertEqual(body['coverageId'], 'real-coverage-id')
                self.assertEqual(body['billingProvider']['ein'], '111222333')
                line = body['claimInformation']['serviceLines'][0]
                expected = {'99204': ('300.00', '75.00', '150.00'), '95913': ('900.00', '250.00', '600.00')}[line['procedureCode']]
                self.assertEqual(line['amount'], expected[0])
                return httpx.Response(200, json={'id': 'estimate-live', 'result': {'claimInformation': {'serviceLines': [{'procedureCode': line['procedureCode'], 'amount': expected[0], 'quantity': '1', 'estimatedPatientResponsibility': expected[1], 'allowed': {'amount': expected[2]}}]}}})
            body = json.loads(request.content)
            self.assertEqual(body['renderingProviders'][0]['npi'], '1234567893')
            self.assertEqual(body['procedures'][0]['code'], '99204')
            return httpx.Response(200, json={'id': 'review-live', 'statusCode': 'A2', 'certificationNumber': 'PARTIAL-123', 'procedures': [{'code': '99204', 'statusCode': 'A1', 'quantity': '1'}]})
        day = datetime.now(ZoneInfo('America/Chicago')).date().isoformat()
        inputs = {"patient": {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1980-01-01", "gender": "U"},
                  "subscriber": {"firstName": "Pat", "lastName": "Example", "birthDate": "1980-01-01", "memberId": "MEMBER123"},
                  "relationship": "18", "member_id": "MEMBER123", "payer_id": "TESTPAYER", "group_number": "GROUP123",
                  "provider": {**provider, "npi": "1234567893"}, "submitter_id": "TESTSUBMITTER",
                  "rendering_provider": {**provider, "npi": "1234567893"}, "service_type": "1", "service_date": day,
                  "place_of_service": "11", "diagnoses": ["G629"], "patient_phone": "5552347700", "reason": "Neuropathy",
                  "coverage_missing": [], "review_missing": [],
                  "lines": [{"code": "99204", "units": "1", "charge": "300.00", "cash_cents": 22500, "service_status": "scheduled", "modifiers": [], "diagnosis": "G629"},
                            {"code": "95913", "units": "1", "charge": "900.00", "cash_cents": 50000, "service_status": "possible", "modifiers": [], "diagnosis": "G629"}]}
        ecw_result = (inputs, [], {"system": "eClinicalWorks", "retrieved_at": "2026-09-10T10:00:00Z", "appointment_date": day})
        with patch.dict(os.environ, {'AVAILITY_ENVIRONMENT': 'production', 'AVAILITY_SCOPE': LIVE_SCOPE, 'AVAILITY_PRACTICE_CONFIG': json.dumps(practice)}), \
             patch.object(v2, 'gateway', return_value=Gateway(settings(False), transport=httpx.MockTransport(handler))), \
             patch.object(v2.ecw_backend, 'configured', return_value=True), \
             patch.object(v2.ecw_backend, 'fetch_inputs', new=AsyncMock(return_value=ecw_result)):
            base = '/api/doctor/insurance/v2/cases'
            response = await self.client.post(base, json={})
            self.assertEqual(response.status_code, 200, response.text)
            path = base + '/case-v2'
            self.assertEqual(response.json()['missing'], [])
            self.assertEqual(response.json()['sources']['ecw']['system'], 'eClinicalWorks')
            response = await self.client.post(path + '/requirements')
            self.assertEqual(response.status_code, 200, response.text)
            for resource in ('coverage', 'estimate', 'procedure_estimate', 'authorization', 'referral'):
                response = await self.client.post(path + '/transactions/' + resource)
                self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['transactions']['estimate']['result']['patient_cents'], 7500)
            self.assertEqual(result['transactions']['procedure_estimate']['result']['patient_cents'], 25000)
            self.assertIn('Partially', result['transactions']['authorization']['result']['status'])
            self.assertNotIn('111222333', response.text)
            self.assertEqual(len(sent), 5)


if __name__ == '__main__':
    unittest.main()
