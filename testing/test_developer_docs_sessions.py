"""Swagger's extra gate must never replace resource-level authorization."""
import unittest
from unittest.mock import patch

import httpx
import psycopg

from app.main import app
from app.api import developer_docs as docs
from app.api import application
from app.api.settings import PATIENT_SESSION_COOKIE, PUBLIC_BASE_URL


class SwaggerSessions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')
        self.patient = patch.object(docs.patient_store, 'get_patient_session', side_effect=lambda token: 'synthetic-patient' if token == 'patient-valid' else None).start()
        patch.object(application, 'get_patient_session', self.patient).start()
        self.staff = patch.object(docs.staff_store, 'get_session', side_effect=lambda token: {'id': 'synthetic-staff', 'practice_id': 'synthetic-practice', 'role': 'staff'} if token == 'staff-valid' else None).start()

    async def asyncTearDown(self):
        patch.stopall()
        await self.client.aclose()

    async def test_public_docs_and_schema_remain_visible_without_database_access(self):
        for path in ['/api/docs', '/api/redoc', '/api/openapi.json', '/api/docs/session']:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
        self.patient.assert_not_called()
        self.staff.assert_not_called()
        schema = (await self.client.get('/api/openapi.json')).json()
        self.assertEqual(schema['paths']['/api/patient/profile']['get']['x-clinicforce-session'], 'patient')
        self.assertEqual(schema['paths']['/api/staff/session']['get']['x-clinicforce-session'], 'clinician')

    async def test_session_status_is_boolean_only_and_not_cached(self):
        self.client.cookies.set(PATIENT_SESSION_COOKIE, 'patient-valid')
        self.client.cookies.set('clinicforce_staff_session', 'staff-valid')
        response = await self.client.get('/api/docs/session')
        self.assertEqual(response.json(), {'patient': True, 'clinician': True})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('synthetic', response.text)

    async def test_anonymous_cannot_use_swagger_even_on_public_or_login_routes(self):
        for path in ['/api/staff/session', '/api/patient/profile', '/healthz']:
            response = await self.client.get(path, params={'__clinicforce_docs': '1'})
            self.assertEqual(response.status_code, 401)
        response = await self.client.post('/api/patient/login?__clinicforce_docs=1', json={}, headers={'origin': PUBLIC_BASE_URL})
        self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.client.get('/healthz')).status_code, 200)

    async def test_wrong_role_and_expired_cookie_do_not_reach_the_route(self):
        self.client.cookies.set(PATIENT_SESSION_COOKIE, 'patient-valid')
        response = await self.client.get('/api/staff/session?__clinicforce_docs=1')
        self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.client.cookies.set('clinicforce_staff_session', 'staff-valid')
        response = await self.client.get('/api/patient/profile?__clinicforce_docs=1')
        self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.client.cookies.set(PATIENT_SESSION_COOKIE, 'expired')
        self.assertEqual((await self.client.get('/api/patient/profile?__clinicforce_docs=1')).status_code, 401)

    async def test_clinician_session_can_execute_matching_get(self):
        self.client.cookies.set('clinicforce_staff_session', 'staff-valid')
        response = await self.client.get('/api/staff/session?__clinicforce_docs=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['staff']['id'], 'synthetic-staff')

    async def test_patient_passes_gate_but_existing_route_ownership_still_applies(self):
        self.client.cookies.set(PATIENT_SESSION_COOKIE, 'patient-valid')
        # The actual patient auth rejects a different identity before loading data.
        response = await self.client.put('/api/patient/profile?__clinicforce_docs=1', headers={'origin': PUBLIC_BASE_URL}, json={
            'patient_id': 'different-patient', 'given_name': 'Alex', 'family_name': 'Example',
            'birth_date': '1990-01-01', 'administrative_gender': 'unknown',
        })
        self.assertEqual(response.status_code, 403)

    async def test_cross_origin_mutation_and_unavailable_session_store_fail_closed(self):
        self.client.cookies.set('clinicforce_staff_session', 'staff-valid')
        response = await self.client.post('/api/staff/logout?__clinicforce_docs=1', headers={'origin': 'https://unrelated.invalid'})
        self.assertEqual(response.status_code, 403)
        self.staff.side_effect = psycopg.OperationalError('synthetic database failure')
        response = await self.client.get('/api/staff/session?__clinicforce_docs=1')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('synthetic database', response.text)
