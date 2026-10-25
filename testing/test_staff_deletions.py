"""Deletion uses synthetic records in a disposable PostgreSQL schema; no payer calls."""
import json
import os
import unittest
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
from fastapi import FastAPI
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api.routers import staff
from app.core import intake_links_store, object_store, patient_store, staff_store
from app.insurance import store


@unittest.skipUnless(os.getenv("STAFF_TEST_DATABASE_URL"), "Requires a disposable PostgreSQL database")
class StaffDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = os.environ["STAFF_TEST_DATABASE_URL"]
        self.schema = "delete_" + uuid4().hex
        with psycopg.connect(self.database, autocommit=True) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.actor = {"id": "staff-a", "practice_id": "clinic-a", "display_name": "Clinician", "role": "staff"}
        self.patches = [patch.object(patient_store, "DATABASE_URL", make_conninfo(self.database, options=f"-csearch_path={self.schema}")),
            patch.object(patient_store, "_schema_initialized", False), patch.object(staff_store, "_schema_initialized", False),
            patch.object(intake_links_store, "_schema_initialized", False), patch.object(staff, "PUBLIC_BASE_URL", "http://test"),
            patch.object(staff, "ALLOWED_ORIGINS", []), patch.object(staff_store, "get_session", return_value=self.actor),
            patch.object(object_store, "enabled", return_value=True)]
        for item in self.patches:
            item.start()
        with intake_links_store._connection() as db:
            db.execute("INSERT INTO staff_members(id,username,display_name,practice_id,practice_name,role,password_hash,created_at) VALUES ('staff-a','tester','Clinician','clinic-a','Clinic','staff','unused','2026-09-24')")
            db.execute("INSERT INTO patients(patient_id,password_hash,created_at) VALUES ('portal-owner','unused','2026-09-24')")
            for record_id, practice in (("patient-a", "clinic-a"), ("patient-b", "clinic-a"), ("foreign-patient", "clinic-b")):
                db.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES (%s,%s,'Sample','Person','1980-01-01','2026-09-24')", (record_id, practice))
            self.intake_key = self.key("intake-a", "card.pdf")
            db.execute("""INSERT INTO staff_intakes(id,practice_id,created_by,patient_id,status,documents,created_at,updated_at,source_portal_patient_id)
                VALUES ('intake-a','clinic-a','staff-a','patient-a','ready',%s::jsonb,'2026-09-24','2026-09-24','portal-owner')""",
                (json.dumps([{"object_key": self.intake_key}]),))
            db.execute("""INSERT INTO staff_intakes(id,practice_id,created_by,patient_id,expected_patient_id,status,created_at,updated_at)
                VALUES ('other-intake','clinic-a','staff-a','patient-b','patient-a','ready','2026-09-24','2026-09-24')""")
            db.execute("""INSERT INTO insurance_upload_links(id,token_hash,clinic_code,practice_id,clinic_name,expected_patient_id,status,created_at,expires_at,intake_id)
                VALUES ('link','token','clinic','clinic-a','Clinic','patient-a','received','2026-09-24','2027-09-24','intake-a')""")
        self.case = store.create_case("patient-a", "", "binding", {"source_mode": "commercial_previsit", "previsit": {"request_state": "complete"}},
            practice_id="clinic-a", intake_id="intake-a", workflow_slot="commercial-previsit:visit", staff_actor=self.actor)
        self.check_key = self.key(self.case["id"], "response.json")
        self.library_key = self.key("shared-library", "contract.pdf")
        self.case = store.save_case({**self.case, "previsit_raw_object_key": self.check_key,
                                   "documents": {"contract": {"object_key": self.library_key}}}, "patient-a", "", "test",
                                   practice_id="clinic-a", intake_id="intake-a", staff_actor=self.actor)
        self.check_url = f"/api/staff/intakes/intake-a/checks/{self.case['id']}"
        with staff_store._connection() as db:
            db.execute("""INSERT INTO commercial_eligibility_evidence
                (id,practice_id,case_id,context_hash,source,status,response_reference,requested_dos,payer_checked_at,normalized_json,raw_object_key,created_at)
                VALUES ('evidence','clinic-a',%s,'context','stedi','complete','reference','2026-09-24','2026-09-24','{}',%s,'2026-09-24')""",
                (self.case['id'], self.check_key))
        app = FastAPI(); app.include_router(staff.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://test"})
        self.deleted = []
        item = patch.object(object_store, "delete_object", side_effect=lambda key: self.deleted.append(key))
        item.start(); self.patches.append(item)

    def key(self, record_id, filename):
        return f"{object_store.S3_PREFIX}/staff-insurance/{sha256(b'clinic-a').hexdigest()}/{record_id}/file/{filename}"

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        with psycopg.connect(self.database, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    async def test_unconfirmed_upload_can_be_deleted_without_creating_a_patient(self):
        intake = staff_store.create_intake('draft', self.actor, [{'object_key': self.key('draft', 'card.pdf')}])
        self.assertIsNone(intake['patient'])
        response = await self.client.delete('/api/staff/intakes/draft')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(staff_store.get_intake('draft', 'clinic-a'))
        self.assertIn(self.key('draft', 'card.pdf'), self.deleted)
        self.assertEqual((await self.client.delete('/api/staff/intakes/draft')).status_code, 200)
        self.assertEqual((await self.client.delete('/api/staff/intakes/intake-a')).status_code, 409)
        self.assertEqual((await self.client.delete('/api/staff/intakes/not-owned')).status_code, 404)

    async def test_check_delete_is_scoped_audited_and_keeps_patient_and_library(self):
        listing = await self.client.get('/api/staff/intakes/intake-a/checks')
        self.assertEqual(listing.json()['checks'][0]['id'], self.case['id'])
        response = await self.client.delete(self.check_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.deleted, [self.check_key])

        self.assertIsNotNone(staff_store.get_patient('patient-a', 'clinic-a'))
        intake = staff_store.get_intake('intake-a', 'clinic-a')
        self.assertEqual(intake['workflows'], {})
        self.assertEqual(intake['events'][-1]['action'], 'Insurance check deleted')
        self.assertIsNone(store.get_case(self.case['id'], 'patient-a', '', practice_id='clinic-a', intake_id='intake-a'))
        self.assertIsNone(store.save_case(self.case, 'patient-a', '', 'late-write', practice_id='clinic-a', intake_id='intake-a'))
        self.assertEqual((await self.client.delete(self.check_url)).status_code, 200)
        self.assertEqual(self.deleted, [self.check_key])

    async def test_patient_delete_removes_dependents_but_preserves_portal_and_other_patients(self):
        response = await self.client.delete('/api/staff/patients/patient-a')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(self.deleted), {self.check_key, self.intake_key})
        self.assertIsNone(staff_store.get_patient('patient-a', 'clinic-a'))
        self.assertIsNone(staff_store.get_intake('intake-a', 'clinic-a'))
        self.assertIsNotNone(staff_store.get_patient('patient-b', 'clinic-a'))
        self.assertIsNotNone(staff_store.get_patient('foreign-patient', 'clinic-b'))
        self.assertIsNone(staff_store.get_intake('other-intake', 'clinic-a')['expected_patient_id'])
        with staff_store._connection() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM patients WHERE patient_id='portal-owner'").fetchone())
            link = db.execute("SELECT * FROM insurance_upload_links WHERE id='link'").fetchone()
            self.assertEqual(link['status'], 'revoked')
            self.assertIsNone(link['expected_patient_id']); self.assertIsNone(link['intake_id'])
            receipt = db.execute('SELECT * FROM staff_deletions').fetchone()
            self.assertEqual(receipt['actor_id'], 'staff-a')
            self.assertEqual(receipt['object_keys'], [])
        self.assertEqual((await self.client.delete('/api/staff/patients/patient-a')).status_code, 200)

    async def test_auth_origin_and_cross_clinic_boundaries(self):
        self.assertEqual((await self.client.delete(self.check_url, headers={'Origin': 'https://unrelated.invalid'})).status_code, 403)
        with patch.object(staff_store, 'get_session', return_value=None):
            self.assertEqual((await self.client.delete(self.check_url)).status_code, 401)
        with patch.object(staff_store, 'get_session', return_value={**self.actor, 'practice_id': 'clinic-b'}):
            self.assertEqual((await self.client.delete(self.check_url)).status_code, 404)
            self.assertEqual((await self.client.delete('/api/staff/patients/patient-a')).status_code, 404)
        self.assertEqual((await self.client.delete(self.check_url.replace('intake-a', 'other-intake'))).status_code, 404)
        self.assertEqual(self.deleted, [])

    async def test_saved_history_preserves_results_and_scopes_patient_intakes(self):
        from app.api.routers import commercial_estimator
        patient = staff_store.get_intake('intake-a', 'clinic-a')['patient']
        session = {'patient_id': 'patient-a', 'share_manifest_id': '', 'practice_id': 'clinic-a',
                   'intake_id': 'intake-a', 'staff': self.actor, 'intake': {'patient': patient}}
        original = {'service_date': '2025-01-02', 'visit_type': 'established', 'codes': [],
                    'collection_guidance': {'amount_cents': 4000}, 'appointment_id': 'past-visit'}
        self.case['previsit_context'] = {'result': original}
        self.case = store.save_case(self.case, 'patient-a', '', 'legacy-result',
            practice_id='clinic-a', intake_id='intake-a')
        changed = {**original, 'collection_guidance': {'amount_cents': 7500}}
        self.case['previsit_context']['result'] = changed
        with patch.object(commercial_estimator, '_previsit_result', return_value={**changed, 'revision': self.case['revision'] + 1}):
            self.case = commercial_estimator._save(self.case, session, 'visit-review')
        with patch.object(commercial_estimator, '_previsit_result', side_effect=AssertionError('History must never recalculate')):
            response = await self.client.get(self.check_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        snapshots = response.json()['snapshots']
        self.assertEqual([row['result']['collection_guidance']['amount_cents'] for row in snapshots], [7500, 4000])
        self.assertTrue(snapshots[1]['legacy'])
        self.assertIsNone(snapshots[1]['saved_at'])
        self.assertNotIn('previsit_raw_object_key', response.text)
        with self.assertRaises(store.StaffCaseConflict):
            store.save_case({**self.case, 'revision': 0}, 'patient-a', '', 'stale', snapshot=snapshots[0], practice_id='clinic-a', intake_id='intake-a')
        self.assertEqual(len(store.check_snapshots(self.case['id'], 'clinic-a', 'patient-a', 'intake-a')), 2)
        self.assertEqual((await self.client.get(self.check_url.replace('intake-a', 'other-intake'))).status_code, 404)
        with patch.object(staff_store, 'get_session', return_value={**self.actor, 'practice_id': 'clinic-b'}):
            self.assertEqual((await self.client.get(self.check_url)).status_code, 404)
        with patch.object(staff_store, 'get_session', return_value=None):
            self.assertEqual((await self.client.get(self.check_url)).status_code, 401)
        with staff_store._connection() as db:
            db.execute("UPDATE staff_intakes SET patient_id='patient-a' WHERE id='other-intake'")
        followup = store.create_case('patient-a', '', 'followup', {'source_mode': 'commercial_previsit',
            'previsit_context': {'result': {**original, 'service_date': '2025-02-03'}}},
            practice_id='clinic-a', intake_id='other-intake', workflow_slot='commercial-previsit:followup')
        listing = (await self.client.get('/api/staff/intakes/intake-a/checks')).json()['checks']
        self.assertEqual({row['id'] for row in listing}, {self.case['id'], followup['id']})
        self.assertEqual({row['intake_id'] for row in listing}, {'intake-a', 'other-intake'})
        with staff_store._connection() as db:
            db.execute("UPDATE staff_intakes SET patient_id='patient-b' WHERE id='other-intake'")
        listing = (await self.client.get('/api/staff/intakes/intake-a/checks')).json()['checks']
        self.assertEqual([row['id'] for row in listing], [self.case['id']])

    async def test_saved_claim_comparison_uses_the_owned_case(self):
        from unittest.mock import AsyncMock
        from app.api.routers import commercial_estimator
        self.case['previsit_context'] = {'result': {'service_date': '2025-01-02'}}
        self.case = store.save_case(self.case, 'patient-a', '', 'saved', practice_id='clinic-a', intake_id='intake-a')
        with patch.object(commercial_estimator, 'saved_visit_financials', AsyncMock(return_value={'lines': []})) as read:
            response = await self.client.get(self.check_url + '/financials')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(read.call_args.args, (self.case, 'clinic-a'))
            response = await self.client.get(self.check_url.replace('intake-a', 'other-intake') + '/financials')
            self.assertEqual(response.status_code, 404)
            self.assertEqual(read.call_count, 1)

    async def test_patient_appointment_history_groups_exact_visits_and_keeps_saved_results(self):
        from unittest.mock import AsyncMock
        from app.insurance import ecw_backend
        from app.api.routers import commercial_estimator
        with staff_store._connection() as db:
            db.execute("UPDATE staff_patients SET ecw_patient_id='ecw-a',identity_status='confirmed' WHERE id='patient-a'")
            db.execute("UPDATE staff_intakes SET patient_id='patient-a' WHERE id='other-intake'")
        dashboard = staff_store.patient_dashboard('clinic-a')
        self.assertTrue(next(row for row in dashboard['patients'] if row['clinic_patient_id'] == 'patient-a')['ecw_linked'])
        self.assertFalse(next(row for row in dashboard['patients'] if row['clinic_patient_id'] == 'patient-b')['ecw_linked'])
        def create(visit, intake_id, slot):
            return store.create_case('patient-a', '', slot, {'source_mode': 'commercial_previsit', 'previsit_context': {
                'source': {'_patient_id': 'ecw-a', 'appointment_id': visit},
                'result': {'appointment_id': visit, 'service_date': '2020-01-02', 'reason': 'Saved reason',
                           'patient_details': {'ecw_account_number': '001234'},
                           'collection_guidance': {'amount_cents': 4000}}}},
                practice_id='clinic-a', intake_id=intake_id, workflow_slot=slot)
        first = create('morning', 'intake-a', 'first')
        followup = create('morning', 'other-intake', 'followup')
        create('afternoon', 'intake-a', 'afternoon')
        url = '/api/staff/patients/patient-a/appointments'
        with patch.object(ecw_backend, 'patient_appointment_history', AsyncMock(side_effect=AssertionError('No external read'))), \
             patch.object(commercial_estimator, '_previsit_result', side_effect=AssertionError('No recalculation')):
            response = await self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        visits = response.json()['appointments']
        self.assertEqual(len(visits), 2)
        self.assertEqual(response.json()['ecw_account_number'], '001234')
        morning = next(row for row in visits if row['appointment_id'] == 'morning')
        self.assertEqual({row['id'] for row in morning['checks']}, {first['id'], followup['id']})
        self.assertEqual({row['id'] for row in visits if row['appointment_id'] == 'afternoon'}, {'ecw-a:afternoon'})
        remote = {'patient_id': 'ecw-a', 'ecw_account_number': '001234', 'appointments': [
            {'id': 'morning', 'start': '2020-01-02T09:00:00-05:00', 'status': 'finished', 'date': '2020-01-02', 'reason': 'eCW reason'},
            {'id': 'older', 'start': '2010-01-02T09:00:00-05:00', 'status': 'finished', 'date': '2010-01-02', 'reason': ''}]}
        with patch.object(ecw_backend, 'configured', return_value=True), \
             patch.object(ecw_backend, 'patient_appointment_history', AsyncMock(return_value=remote)) as read:
            merged = (await self.client.get(url + '?include_ecw=true')).json()
            self.assertEqual(read.call_args.args[0]['ecw_patient_id'], 'ecw-a')
        self.assertEqual(merged['ecw_status'], 'complete')
        self.assertEqual(len(merged['appointments']), 3)
        self.assertEqual(next(row for row in merged['appointments'] if row['appointment_id'] == 'older')['checks'], [])
        with patch.object(ecw_backend, 'configured', return_value=True), \
             patch.object(ecw_backend, 'patient_appointment_history', AsyncMock(side_effect=ecw_backend.EcwBackendError('timeout'))):
            fallback = (await self.client.get(url + '?include_ecw=true')).json()
        self.assertEqual(fallback['ecw_status'], 'incomplete')
        self.assertEqual(fallback['appointments'], visits)
        self.assertEqual((await self.client.get('/api/staff/patients/patient-b/appointments')).json()['appointments'], [])
        with patch.object(staff_store, 'get_session', return_value={**self.actor, 'practice_id': 'clinic-b'}):
            self.assertEqual((await self.client.get(url)).status_code, 404)
        with patch.object(staff_store, 'get_session', return_value=None):
            self.assertEqual((await self.client.get(url)).status_code, 401)
        staff_store.save_ecw_account_number('patient-a', 'clinic-b', 'ecw-a', 'WRONG')
        staff_store.save_ecw_account_number('patient-a', 'clinic-a', 'ecw-b', 'WRONG')
        self.assertIsNone(staff_store.get_patient('patient-a', 'clinic-a')['ecw_account_number'])
        staff_store.save_ecw_account_number('patient-a', 'clinic-a', 'ecw-a', '001234')
        self.assertEqual(staff_store.get_patient('patient-a', 'clinic-a')['ecw_account_number'], '001234')
        with staff_store._connection() as db:
            db.execute("UPDATE staff_intakes SET source='ecw_lookup' WHERE id='intake-a'")
        self.assertNotIn('intake-a', {row['id'] for row in staff_store.list_intakes('clinic-a')})
        self.assertIsNotNone(staff_store.get_intake('intake-a', 'clinic-a'))

    async def test_unresolved_payer_request_cannot_be_erased_then_resubmitted(self):
        for state in ('pending', 'indeterminate'):
            self.case = store.save_case({**self.case, 'previsit': {'request_state': state}}, 'patient-a', '', 'pending',
                                       practice_id='clinic-a', intake_id='intake-a')
            for url in (self.check_url, '/api/staff/patients/patient-a'):
                response = await self.client.delete(url)
                self.assertEqual(response.status_code, 409, response.text)
            self.assertIsNotNone(staff_store.get_patient('patient-a', 'clinic-a'))
        self.assertEqual(self.deleted, [])

    async def test_storage_failure_keeps_cleanup_receipt_and_retry_finishes(self):
        with patch.object(object_store, 'delete_object', side_effect=RuntimeError('S3 unavailable')):
            response = await self.client.delete(self.check_url)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()['cleanup_pending'])
        with staff_store._connection() as db:
            self.assertEqual(db.execute('SELECT object_keys FROM staff_deletions').fetchone()['object_keys'], [self.check_key])
        retry = await self.client.delete(self.check_url)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertFalse(retry.json()['cleanup_pending'])
        self.assertEqual(self.deleted, [self.check_key])

    async def test_cleanup_is_available_after_reload_and_stays_in_clinic(self):
        with patch.object(object_store, 'delete_object', side_effect=RuntimeError('S3 unavailable')):
            self.assertEqual((await self.client.delete('/api/staff/patients/patient-a')).status_code, 202)
        self.assertEqual(staff_store.patient_dashboard('clinic-a')['pending_file_cleanup'], 1)
        with patch.object(staff_store, 'get_session', return_value={**self.actor, 'practice_id': 'clinic-b'}):
            foreign = await self.client.post('/api/staff/deletions/retry-cleanup')
        self.assertEqual(foreign.json()['pending_file_cleanup'], 0)
        self.assertEqual(self.deleted, [])
        retry = await self.client.post('/api/staff/deletions/retry-cleanup')
        self.assertEqual(retry.json()['pending_file_cleanup'], 0)
        self.assertEqual(set(self.deleted), {self.check_key, self.intake_key})
        self.assertEqual(staff_store.patient_dashboard('clinic-a')['pending_file_cleanup'], 0)
