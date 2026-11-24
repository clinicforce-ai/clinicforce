"""Staff intake boundaries; optional real PostgreSQL checks never call external services.

Run the complete suite with STAFF_TEST_DATABASE_URL pointing at a disposable database.
"""

import json
import os
import asyncio
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import fitz
import httpx
from fastapi import FastAPI
from PIL import Image, ImageFile
from starlette.requests import Request

from app.api.routers import staff
from app.core import patient_store, staff_store, object_store, ecw_card_refresh
from app.core.patient_models import PatientProfile
from app.insurance import golden, store as insurance_store, commercial_store


ADMIN_PASSWORD = "synthetic-admin-password-123"


def card_pdf(text="Member Name: PAT EXAMPLE\nMember ID: MEM123\nGroup Number: GROUP123"):
    with fitz.open() as document:
        document.new_page().insert_text((25, 25), text)
        return document.tobytes()


class FileBoundaryTests(unittest.TestCase):
    def test_saved_only_card_read_does_not_wait_for_ecw(self):
        intake = {'id': 'synthetic-intake', 'patient': {'id': 'synthetic-patient',
            'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}, 'documents': []}
        uploaded = {'id': 'saved-intake', 'documents': [{'id': 'saved-card',
            'filename': 'card.pdf', 'content_type': 'application/pdf'}]}
        with patch.object(staff, 'require_staff', AsyncMock(return_value={'practice_id': 'synthetic-practice'})), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(staff.staff_store, 'patient_card_intakes', return_value=[uploaded]), \
             patch.object(staff.ecw_backend, 'insurance_cards', AsyncMock()) as ecw:
            response = asyncio.run(staff.patient_cards('synthetic-intake', None, saved_only=True))
        self.assertEqual(json.loads(response.body)['cards'][0]['title'], 'card.pdf')
        ecw.assert_not_awaited()

    def test_status_poll_reads_queue_without_repeating_ecw(self):
        intake = {'id': 'synthetic-intake', 'patient': {'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}}
        job = {'status': 'running', 'checked_at': None, 'cards': [], 'lease_until': None}
        with patch.object(staff, 'require_staff', AsyncMock(return_value={'practice_id': 'synthetic-practice'})), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(staff.ecw_backend, 'configured', return_value=True), \
             patch.object(ecw_card_refresh, 'current_or_enqueue', return_value=job) as queue, \
             patch.object(staff.ecw_backend, 'insurance_cards', AsyncMock()) as ecw:
            for _ in range(3):
                response = asyncio.run(staff.ecw_cards_status('synthetic-intake', None))
                self.assertEqual(json.loads(response.body)['status'], 'running')
        self.assertEqual(queue.call_count, 3)
        ecw.assert_not_awaited()

    def test_shared_ecw_card_preview_reads_verified_object(self):
        from datetime import datetime, timezone
        intake = {'patient': {'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}}
        job = {'checked_at': datetime.now(timezone.utc), 'cards': [{'id': 'card:0',
            'content_type': 'application/pdf', 'object_key': 'private-object'}]}
        with patch.object(staff, 'require_staff', AsyncMock(return_value={'practice_id': 'synthetic-practice'})), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(ecw_card_refresh, 'status', return_value=job), \
             patch.object(object_store, 'get_object_bytes', return_value=b'%PDF synthetic') as store_read, \
             patch.object(staff.ecw_backend, 'insurance_cards', AsyncMock()) as ecw:
            response = asyncio.run(staff.ecw_card('synthetic-intake', 'card:0', None))
        self.assertEqual(response.body, b'%PDF synthetic')
        store_read.assert_called_once_with('private-object')
        ecw.assert_not_awaited()

    def test_login_immediately_reports_nmct_no_upfront_policy(self):
        actor = {'practice_id': 'primary', 'id': 'synthetic-staff', 'role': 'staff'}
        request = Request({'type': 'http', 'method': 'POST', 'headers': []})
        with patch.object(staff, 'check_origin'), \
             patch.object(staff, 'request_address', return_value='127.0.0.1'), \
             patch.object(staff.staff_store, 'authenticate', return_value=(actor, 'synthetic-token')):
            response = asyncio.run(staff.login(staff.Login(username='synthetic', password='synthetic-password'), request))
        self.assertEqual(json.loads(response.body)['staff']['arrival_collection_default_cents'], 0)

    def test_session_reports_nmct_no_upfront_policy_without_assigning_it_to_other_practices(self):
        for practice_id, expected in [('primary', 0), ('synthetic-practice', None)]:
            with patch.object(staff, 'require_staff', AsyncMock(return_value={
                    'practice_id': practice_id, 'id': 'synthetic-staff'})):
                response = asyncio.run(staff.session(None))
            self.assertEqual(json.loads(response.body)['staff']['arrival_collection_default_cents'], expected)

    def test_saved_card_and_current_ecw_card_are_labeled_separately(self):
        from datetime import datetime, timezone
        intake = {'id': 'synthetic-intake', 'patient': {'id': 'synthetic-patient',
            'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}, 'documents': []}
        uploaded = {'id': 'saved-intake', 'documents': [{'id': 'saved-card',
            'filename': 'card.png', 'content_type': 'image/png'}]}
        staff_user = {'practice_id': 'synthetic-practice'}
        ready = {'status': 'ready', 'checked_at': datetime.now(timezone.utc),
                 'cards': [{'id': 'ecw-card:0', 'title': 'Insurance card',
                            'content_type': 'image/png', 'object_key': 'private-object'}]}
        with patch.object(staff, 'require_staff', AsyncMock(return_value=staff_user)), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(staff.staff_store, 'patient_card_intakes', return_value=[uploaded]), \
             patch.object(staff.ecw_backend, 'configured', return_value=True), \
             patch.object(ecw_card_refresh, 'current_or_enqueue', return_value=ready), \
             patch.object(staff.ecw_backend, 'insurance_cards', AsyncMock()) as ecw:
            response = asyncio.run(staff.patient_cards('synthetic-intake', None))
        data = json.loads(response.body)
        self.assertEqual([row['source'] for row in data['cards']], ['clinicforce_upload', 'ecw_current'])
        self.assertIn('/ecw-card?', data['cards'][1]['url'])
        ecw.assert_not_awaited()

    def test_card_list_returns_saved_card_while_ecw_scan_is_running(self):
        intake = {'id': 'synthetic-intake', 'patient': {'id': 'synthetic-patient',
                  'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}, 'documents': []}
        uploaded = {'id': 'saved-intake', 'documents': [{'id': 'saved-card',
            'filename': 'card.pdf', 'content_type': 'application/pdf'}]}
        with patch.object(staff, 'require_staff', AsyncMock(return_value={'practice_id': 'synthetic-practice'})), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(staff.staff_store, 'patient_card_intakes', return_value=[uploaded]), \
             patch.object(staff.ecw_backend, 'configured', return_value=True), \
             patch.object(ecw_card_refresh, 'current_or_enqueue', return_value={
                 'status': 'running', 'checked_at': None, 'cards': []}), \
             patch.object(staff.ecw_backend, 'insurance_cards', AsyncMock()) as ecw:
            response = asyncio.run(staff.patient_cards('synthetic-intake', None))
        data = json.loads(response.body)
        self.assertEqual(data['cards'][0]['title'], 'card.pdf')
        self.assertEqual(data['status'], 'running')
        ecw.assert_not_awaited()

    def test_ecw_card_read_timeout_returns_gateway_timeout(self):
        intake = {'patient': {'ecw_patient_id': 'synthetic-ecw'}, 'fields': {}}

        async def stalled_cards(*args, **kwargs):
            await asyncio.sleep(1)

        with patch.object(staff, 'require_staff', AsyncMock(return_value={'practice_id': 'synthetic-practice'})), \
             patch.object(staff, 'owned_intake', AsyncMock(return_value=intake)), \
             patch.object(ecw_card_refresh, 'status', return_value=None), \
             patch.object(staff.ecw_backend, 'insurance_cards', side_effect=stalled_cards), \
             patch.object(staff, 'CARD_PREVIEW_TIMEOUT_SECONDS', .01):
            with self.assertRaises(staff.HTTPException) as error:
                asyncio.run(staff.ecw_card('synthetic-intake', 'synthetic-document', None))
        self.assertEqual(error.exception.status_code, 504)

    def test_bytes_are_validated_before_ocr_and_object_keys_exclude_names(self):
        self.assertEqual(staff.inspect_file(card_pdf()), ("application/pdf", 1))
        with self.assertRaises(Exception):
            staff.inspect_file(b"%PDF forged")
        photo = BytesIO()
        Image.new("RGB", (40, 40), "white").save(photo, format="JPEG")
        with patch.object(ImageFile, "LOAD_TRUNCATED_IMAGES", True):
            with self.assertRaises(Exception):
                staff.inspect_file(photo.getvalue()[:-30])
        with patch.object(object_store, "S3_BUCKET", "synthetic-bucket"), patch.object(object_store.boto3, "client") as client:
            key = object_store.put_staff_document("practice-1", "case-1", "Patient Full Name.pdf", b"example", "application/pdf")
        self.assertIn("/staff-insurance/", key)
        self.assertNotIn("Patient", key)
        self.assertNotIn("insurance-cases", key)
        self.assertEqual(client.return_value.put_object.call_args.kwargs["ServerSideEncryption"], "aws:kms")

    def test_manual_card_fields_accept_optional_fallback_details(self):
        self.assertEqual(staff.manual_card_fields("Pat Example", "MEM-MANUAL", "1990-01-01"), {"given_name": "Pat", "family_name": "Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"})
        with self.assertRaises(Exception):
            staff.manual_card_fields("Pat Example", "MEM-MANUAL", "2999-01-01")


@unittest.skipUnless(os.getenv("STAFF_TEST_DATABASE_URL"), "Set STAFF_TEST_DATABASE_URL to a disposable PostgreSQL database")
class StaffDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(patient_store, "DATABASE_URL", os.environ["STAFF_TEST_DATABASE_URL"]),
            patch.object(staff, "PUBLIC_BASE_URL", "http://test"),
            patch.object(staff, "ALLOWED_ORIGINS", ["http://test"]),
            patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({
                "username": "clinic-admin", "password": ADMIN_PASSWORD, "practice_id": "test-practice",
                "practice_name": "Synthetic clinic", "display_name": "Test administrator",
            })}),
        ]
        for item in self.patches:
            item.start()
        staff_store._bootstrap_initialized = False
        with staff_store._connection() as connection:
            connection.execute("TRUNCATE staff_ecw_card_refresh,staff_library,staff_intakes,staff_patients,staff_sessions,staff_members,staff_login_attempts CASCADE")
        self.app = FastAPI()
        self.app.include_router(staff.router)
        self.app.add_api_route("/api/doctor/insurance/staff-intake", staff.from_share, methods=["POST"])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers={"Origin": "http://test"})
        self.objects = {}
        def put(practice_id, record_id, filename, content, content_type):
            key = f"staff-insurance/{practice_id}/{record_id}/{uuid4()}"
            self.objects[key] = content
            return key
        self.storage_patches = [
            patch.object(object_store, "enabled", return_value=True),
            patch.object(object_store, "put_staff_document", side_effect=put),
            patch.object(object_store, "get_object_bytes", side_effect=lambda key: self.objects[key]),
            patch.object(object_store, "delete_object", side_effect=lambda key: self.objects.pop(key, None)),
        ]
        for item in self.storage_patches:
            item.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.storage_patches + self.patches):
            item.stop()

    async def sign_in(self, username="clinic-admin", password=ADMIN_PASSWORD):
        response = await self.client.post("/api/staff/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["staff"]

    async def test_card_refresh_queue_deduplicates_and_preserves_last_good_card(self):
        practice, patient = 'test-practice', 'synthetic-ecw'
        self.assertEqual(ecw_card_refresh.enqueue(practice, patient)['status'], 'queued')
        self.assertEqual(ecw_card_refresh.enqueue(practice, patient)['status'], 'queued')
        job = ecw_card_refresh.claim()
        self.assertEqual(job['ecw_patient_id'], patient)
        self.assertIsNone(ecw_card_refresh.claim())
        card = {'id': 'card:0', 'title': 'Synthetic card', 'content_type': 'application/pdf',
                'object_key': 'insurance-cases/synthetic'}
        self.assertEqual(ecw_card_refresh.complete(job, [card]), [])
        self.assertEqual(ecw_card_refresh.enqueue(practice, patient)['status'], 'ready')
        self.assertEqual(ecw_card_refresh.status(practice, patient)['cards'], [card])
        ecw_card_refresh.enqueue(practice, patient, force=True)
        with patch.object(ecw_card_refresh, 'MIN_SCAN_SPACING_SECONDS', 0):
            retry = ecw_card_refresh.claim()
        self.assertEqual(ecw_card_refresh.complete(retry, [], failed=True), [])
        saved = ecw_card_refresh.status(practice, patient)
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['cards'], [card])

    async def test_opened_card_precedes_scheduled_prefetch(self):
        practice = 'test-practice'
        ecw_card_refresh.enqueue_many(practice, {'scheduled-one', 'scheduled-two'})
        self.assertEqual(ecw_card_refresh.enqueue(practice, 'scheduled-two')['priority'], 10)
        self.assertEqual(ecw_card_refresh.claim()['ecw_patient_id'], 'scheduled-two')

    async def upload(self):
        response = await self.client.post("/api/staff/intakes", files=[("files", ("card.pdf", card_pdf(), "application/pdf"))])
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def confirm(self, intake, **changes):
        data = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "member_id": "MEM-CORRECTED", "payer": "Synthetic payer", "group_number": "GROUP123",
                "relationship": "child", "subscriber_name": "OTHER EXAMPLE", "subscriber_birth_date": "1970-01-01", "identity_confirmed": True, **changes}
        response = await self.client.put(f"/api/staff/intakes/{intake['id']}/patient", json=data)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_verified_policy_names_persist_only_for_the_same_clinic_patient(self):
        actor = await self.sign_in()
        record = await self.confirm(await self.upload())
        patient_id = record['patient']['id']
        proof = {'binding': 'a' * 64, 'reference': 'Payer call', 'actor_id': actor['id'], 'verified_at': '2026-09-28'}
        staff_store.save_insurance_identity(patient_id, actor['practice_id'], proof)
        staff_store.save_insurance_identity(patient_id, 'another-clinic', {**proof, 'reference': 'Wrong clinic'})
        saved = staff_store.get_intake(record['id'], actor['practice_id'])
        self.assertEqual(saved['patient']['insurance_identities'], {proof['binding']: proof})
        response = await self.client.get(f"/api/staff/intakes/{record['id']}")
        self.assertNotIn('insurance_identities', response.json()['patient'])
        payer = commercial_store.create_connection(actor['practice_id'], {'display_name': 'Example Payer', 'stedi_payer_id': str(uuid4())})
        network = commercial_store.create_network(actor['practice_id'], {'payer_connection_id': payer['id'],
            'billing_npi': '1234567890', 'rendering_npi': '1234567890', 'organization_name': 'Example Clinic',
            'tin': '123456789', 'location_id': 'office', 'place_of_service': '11', 'network_name': 'PPO',
            'effective_from': '2026-01-01', 'evidence_reference': 'Verified contract'})
        profile = commercial_store.create_profile(actor['practice_id'], {'payer_connection_id': payer['id'],
            'provider_network_id': network['id'], 'service_category': 'office', 'rules': {'cost_share': 'payer_returned'}})
        commercial_store.save_product_mapping(actor['practice_id'], profile['id'], ['Choice Medical'], proof)
        rules = commercial_store.profile(actor['practice_id'], profile['id'])['rules']
        self.assertEqual(rules['cost_share'], 'payer_returned')
        self.assertEqual(rules['product_mappings'], [{'products': ['Choice Medical'], **proof}])
        with self.assertRaises(Exception):
            commercial_store.save_product_mapping('another-clinic', profile['id'], ['Wrong plan'], proof)


    async def test_hash_only_bootstrap_authenticates_without_resetting_existing_staff(self):
        config = json.loads(os.environ["STAFF_BOOTSTRAP_CREDENTIALS"])
        config["password_hash"] = staff_store.hash_password(config.pop("password"))
        with patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps(config)}):
            await self.sign_in()
            with staff_store._connection() as connection:
                stored = connection.execute("SELECT password_hash FROM staff_members WHERE username = 'clinic-admin'").fetchone()
            self.assertEqual(stored["password_hash"], config["password_hash"])
            staff_store._bootstrap_initialized = False
            replacement = {**config, "password_hash": staff_store.hash_password("unused-replacement-password")}
            with patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps(replacement)}):
                await self.sign_in()
        staff_store._bootstrap_initialized = False
        with patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({**config, "password_hash": "invalid"})}):
            with self.assertRaises(RuntimeError):
                staff_store.ensure_bootstrap()

    async def test_bootstrap_hashed_sessions_origin_and_persisted_throttle(self):
        self.assertEqual((await self.client.get("/api/staff/session")).status_code, 401)
        response = await self.client.post("/api/staff/login", json={"username": "clinic-admin", "password": ADMIN_PASSWORD}, headers={"Origin": "https://unrelated.invalid"})
        self.assertEqual(response.status_code, 403)
        actor = await self.sign_in()
        self.assertEqual(actor["role"], "admin")
        token = self.client.cookies.get(staff.STAFF_COOKIE)
        with staff_store._connection() as connection:
            session = connection.execute("SELECT * FROM staff_sessions").fetchone()
            member = connection.execute("SELECT * FROM staff_members").fetchone()
        self.assertNotEqual(session["session_hash"], token)
        self.assertNotEqual(member["password_hash"], ADMIN_PASSWORD)
        self.assertNotIn("password_hash", actor)
        # Deploying the bootstrap secret again must not overwrite this account.
        with patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({"username": "clinic-admin", "password": "different-password-123", "practice_id": "other", "practice_name": "Other", "display_name": "Other"})}):
            staff_store._bootstrap_initialized = False
            staff_store.ensure_bootstrap()
        self.assertEqual(staff_store.get_session(token)["practice_id"], "test-practice")
        self.assertEqual((await self.client.post("/api/staff/logout")).status_code, 204)
        self.assertIsNone(staff_store.get_session(token))
        for _ in range(8):
            response = await self.client.post("/api/staff/login", json={"username": "clinic-admin", "password": "wrong"})
            self.assertEqual(response.status_code, 401)
        response = await self.client.post("/api/staff/login", json={"username": "clinic-admin", "password": ADMIN_PASSWORD})
        self.assertEqual(response.status_code, 401)

    async def test_saved_card_survives_ocr_failure_confirmation_and_cross_practice_access(self):
        actor = await self.sign_in()
        with patient_store._connection() as connection:
            portal_count = connection.execute("SELECT count(*) AS count FROM patients").fetchone()["count"]
        def unavailable(*args):
            self.assertEqual(len(staff_store.list_intakes(actor["practice_id"])), 1, "Intake must exist before OCR")
            raise golden.OCRServiceError("synthetic failure")
        with patch.object(staff.v1_sources, "read_pages", side_effect=unavailable):
            intake = await self.upload()
        self.assertEqual(intake["status"], "needs_confirmation")
        self.assertIn("Card saved", intake["warning"])
        self.assertNotIn("object_key", intake["documents"][0])
        confirmed = await self.confirm(intake)
        self.assertEqual(confirmed["status"], "ready")
        self.assertEqual(confirmed["patient"]["given_name"], "Pat")
        self.assertEqual(confirmed["fields"]["member_id"], "MEM-CORRECTED")
        self.assertEqual(confirmed["field_sources"]["member_id"], "staff_confirmed")
        matches = await self.client.post("/api/staff/patients/search", json={"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01"})
        self.assertEqual(matches.status_code, 200)
        self.assertEqual(matches.json()["patients"][0]["id"], confirmed["patient"]["id"])
        search = await self.client.post("/api/staff/intakes/search", json={"query": "MEM-CORRECTED", "status": "ready"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["intakes"][0]["id"], intake["id"])
        again = await self.confirm(intake)
        self.assertEqual(again["patient"]["id"], confirmed["patient"]["id"])
        with patient_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) AS count FROM patients").fetchone()["count"], portal_count)
        self.assertIsNone(staff_store.get_intake(intake["id"], "other-practice"))
        self.assertIsNone(staff_store.get_patient(confirmed["patient"]["id"], "other-practice"))
        self.assertEqual(staff_store.find_patients("other-practice", "Pat"), [])
        other = staff_store.create_member({**actor, "practice_id": "other-practice"}, "other-staff", "Other staff", ADMIN_PASSWORD, "staff")
        await self.sign_in("other-staff")
        self.assertEqual((await self.client.get("/api/staff/intakes")).json()["intakes"], [])
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{intake['id']}")).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{intake['id']}/documents/{intake['documents'][0]['id']}")).status_code, 404)
        self.assertEqual((await self.client.post("/api/staff/members", json={"username": "attacker", "display_name": "Test", "password": ADMIN_PASSWORD, "role": "admin"})).status_code, 403)
        self.assertEqual(other["practice_id"], "other-practice")

    async def test_actual_pdf_fields_original_download_library_and_validation(self):
        await self.sign_in()
        intake = await self.upload()
        self.assertEqual(intake["ocr_fields"]["member_id"], "MEM123")
        self.assertNotIn("given_name", intake["fields"], "Subscriber name must not silently become patient name")
        confirmed = await self.confirm(intake)
        self.assertEqual(confirmed["ocr_fields"]["member_id"], "MEM123")
        response = await self.client.get(f"/api/staff/intakes/{intake['id']}/documents/{intake['documents'][0]['id']}")
        self.assertEqual(response.content[:4], b"%PDF")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        before = len(self.objects)
        response = await self.client.post("/api/staff/intakes", files={"files": ("forged.pdf", b"%PDF not a file", "application/pdf")})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.objects), before)
        response = await self.client.post("/api/staff/library", data={"kind": "fee_schedule", "label": "Clinic fees"}, files={"file": ("fees.csv", b"CPT,Billed Charge\n99213,200\n", "text/csv")})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("object_key", response.json())
        self.assertEqual(len((await self.client.get("/api/staff/library")).json()["documents"]), 1)
        self.assertEqual((await self.client.get(f"/api/staff/library/{response.json()['id']}/document")).status_code, 200)

    async def test_library_lists_all_clinic_files_and_opens_only_owned_documents(self):
        self.assertEqual((await self.client.get("/api/staff/library")).status_code, 401)
        actor = await self.sign_in()
        documents = []
        for kind, filename, content, content_type in [
            ("contract", "Payer contract.pdf", card_pdf("Clinic contract"), "application/pdf"),
            ("fee_schedule", "fees.csv", b"CPT,Billed Charge\n99213,200\n", "text/csv"),
            ("self_pay", "cash.csv", b"CPT,Cash Price\n99213,100\n", "text/csv"),
        ]:
            response = await self.client.post("/api/staff/library", data={"kind": kind, "label": filename}, files={"file": (filename, content, content_type)})
            self.assertEqual(response.status_code, 201, response.text)
            document = response.json()
            documents.append(document)
            path = f"/api/staff/library/{document['id']}/document"
            preview = await self.client.get(path + "?preview=true")
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.content, content)
            self.assertTrue(preview.headers["Content-Disposition"].startswith("inline;"))
            self.assertEqual(preview.headers["Content-Type"].split(";")[0], "application/pdf" if kind == "contract" else "text/plain")
            self.assertEqual(preview.headers["Cache-Control"], "no-store")
            self.assertIn("sandbox", preview.headers["Content-Security-Policy"])
            self.assertIn("default-src 'none'", preview.headers["Content-Security-Policy"])
            download = await self.client.get(path)
            self.assertTrue(download.headers["Content-Disposition"].startswith("attachment;"))
            self.assertEqual(download.headers["Content-Type"].split(";")[0], content_type)

        text_path = f"/api/staff/library/{documents[0]['id']}/text"
        extracted = await self.client.get(text_path)
        self.assertEqual(extracted.status_code, 200)
        self.assertIn('Clinic contract', extracted.text)
        self.assertIn('default-src', extracted.headers['Content-Security-Policy'])
        self.assertEqual(extracted.headers['Cache-Control'], 'no-store')
        summary = (await self.client.get('/api/staff/library')).json()['documents']
        self.assertTrue(any(doc.get('extraction_summary', {}).get('page_count') == 1 for doc in summary if doc.get('extraction_summary')))
        self.assertTrue(all('extraction' not in doc for doc in summary))

        # Every saved version remains reachable, including beyond the former 200-file cutoff.
        stored = staff_store.get_library(documents[0]["id"], actor["practice_id"])
        for _ in range(198):
            staff_store.create_library(actor, {**stored, "id": str(uuid4())})
        listed = (await self.client.get("/api/staff/library")).json()["documents"]
        self.assertEqual(len(listed), 201)
        self.assertTrue({doc["id"] for doc in documents}.issubset({doc["id"] for doc in listed}))
        self.assertTrue(all("object_key" not in doc and "created_by" not in doc for doc in listed))

        staff_store.create_member(actor, "library-reader", "Clinic reader", ADMIN_PASSWORD, "staff")
        await self.sign_in("library-reader")
        self.assertEqual(len((await self.client.get("/api/staff/library")).json()["documents"]), 201)
        path = f"/api/staff/library/{documents[0]['id']}/document"
        self.assertEqual((await self.client.get(path + "?preview=true")).status_code, 200)
        staff_store.create_member({**actor, "practice_id": "another-clinic"}, "other-reader", "Other clinic", ADMIN_PASSWORD, "staff")
        await self.sign_in("other-reader")
        self.assertEqual((await self.client.get("/api/staff/library")).json()["documents"], [])
        self.assertEqual((await self.client.get(path)).status_code, 404)
        self.assertEqual((await self.client.get(text_path)).status_code, 404)
        self.assertEqual((await self.client.get(path + "?preview=true")).status_code, 404)
        await self.client.post("/api/staff/logout")
        self.assertEqual((await self.client.get(path + "?preview=true")).status_code, 401)

    async def test_failed_contract_extraction_preserves_original_and_can_retry_in_background(self):
        from app.core import library_files
        await self.sign_in()
        original = card_pdf('Contract with a fee schedule')
        with patch.object(library_files, 'extract_document', side_effect=RuntimeError('OCR unavailable')):
            response = await self.client.post('/api/staff/library', data={'kind': 'contract'}, files={'file': ('contract.pdf', original, 'application/pdf')})
        self.assertEqual(response.status_code, 201)
        path = f"/api/staff/library/{response.json()['id']}"
        self.assertEqual((await self.client.get(path + '/document')).content, original)
        self.assertEqual((await self.client.get(path + '/text')).status_code, 503)
        pending = await self.client.get(path + '/text?retry=true')
        self.assertEqual(pending.status_code, 202)
        self.assertIn('refresh', pending.text)
        ready = await self.client.get(path + '/text')
        self.assertEqual(ready.status_code, 200)
        self.assertIn('Contract with a fee schedule', ready.text)

    async def test_office_library_originals_and_estimator_imports_stay_connected(self):
        import hashlib
        from unittest.mock import AsyncMock
        from test_library_files import word_file, workbook_file
        from app.api.routers import commercial_estimator
        from app.insurance import commercial_store
        actor = await self.sign_in()
        for kind, filename, content, expected in [
            ('plan_matrix', 'clinic-plans.xlsx', workbook_file(), 'Aetna'),
            ('self_pay', 'cash.docx', word_file(), '$250'),
        ]:
            response = await self.client.post('/api/staff/library', data={'kind': kind, 'label': filename}, files={'file': (filename, content)})
            self.assertEqual(response.status_code, 201, response.text)
            document = response.json()
            self.assertEqual(document['sha256'], hashlib.sha256(content).hexdigest())
            path = f"/api/staff/library/{document['id']}/document"
            self.assertEqual((await self.client.get(path)).content, content)
            preview = await self.client.get(path + '?preview=true')
            self.assertEqual(preview.status_code, 200)
            self.assertIn(expected, preview.text)
            self.assertNotIn('<script>', preview.text)
        response = await self.client.post('/api/staff/library', data={'kind': 'plan_matrix'}, files={'file': ('bad.xlsx', b'not a workbook')})
        self.assertEqual(response.status_code, 422)

        payer = commercial_store.create_connection(actor['practice_id'], {'display_name': 'Example insurance', 'stedi_payer_id': 'TEST-' + uuid4().hex})
        content = b'CPT,Allowed Amount,Network,POS,Effective From\n99203,125,Example PPO,11,2026-01-01\n'
        self.app.include_router(commercial_estimator.router)
        session = {'practice_id': actor['practice_id'], 'staff': actor}
        with patch.object(commercial_estimator, '_session', new=AsyncMock(return_value=session)):
            response = await self.client.post('/api/doctor/insurance/commercial/rates', params={
                'payer_connection_id': payer['id'], 'source_type': 'contract_export',
                'source_reference': 'Approved original contract', 'effective_from': '2026-01-01',
            }, files={'file': ('contract-rates.csv', content, 'text/csv')})
            self.assertEqual(response.status_code, 200, response.text)
            version = response.json()
            self.assertEqual(version['source_reference'], 'Approved original contract')
            commercial_store.activate_rates(actor['practice_id'], version['id'])
            response = await self.client.post('/api/doctor/insurance/commercial/cash-schedules', params={'effective_from': '2026-01-01'}, files={'file': ('shared-cash.csv', b'CPT,Cash Price\n99203,150\n', 'text/csv')})
            self.assertEqual(response.status_code, 200, response.text)
        listed = (await self.client.get('/api/staff/library')).json()['documents']
        self.assertEqual(len(listed), 4)
        source = next(doc for doc in listed if doc['filename'] == 'contract-rates.csv')
        self.assertEqual(source['estimator_uses'][0]['status'], 'active')
        self.assertEqual(source['estimator_uses'][0]['payer'], 'Example insurance')
        original = await self.client.get(f"/api/staff/library/{source['id']}/document")
        self.assertEqual(original.content, content)
        with commercial_store._connection() as db:
            cash_before = db.execute('SELECT filename,sha256,rows_json,status FROM practice_cash_schedules WHERE practice_id=%s ORDER BY id', (actor['practice_id'],)).fetchall()
        # Uploading another insurer's agreement does not change shared cash prices or active rates.
        response = await self.client.post('/api/staff/library', data={'kind': 'contract', 'payer': 'Another insurer'}, files={'file': ('agreement.pdf', card_pdf())})
        self.assertEqual(response.status_code, 201)
        with commercial_store._connection() as db:
            self.assertEqual(db.execute('SELECT filename,sha256,rows_json,status FROM practice_cash_schedules WHERE practice_id=%s ORDER BY id', (actor['practice_id'],)).fetchall(), cash_before)
        self.assertEqual(next(v for v in commercial_store.list_rate_versions(actor['practice_id']) if v['id'] == version['id'])['status'], 'active')
        staff_store.create_member({**actor, 'practice_id': 'other-library-clinic'}, 'other-library-reader', 'Other reader', ADMIN_PASSWORD, 'staff')
        await self.sign_in('other-library-reader')
        self.assertEqual((await self.client.get('/api/staff/library')).json()['documents'], [])
        self.assertEqual((await self.client.get(f"/api/staff/library/{source['id']}/document?preview=true")).status_code, 404)

    async def test_manual_fallback_survives_ocr_failure_and_ocr_replaces_card_values(self):
        await self.sign_in()
        response = await self.client.post("/api/staff/intakes", data={"full_name": "Pat Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"}, files=[("files", ("card.pdf", card_pdf("Member ID: MEM-CARD\nDOB: 02/03/1991"), "application/pdf"))])
        self.assertEqual(response.status_code, 201, response.text)
        intake = response.json()
        self.assertEqual(intake["fields"]["given_name"], "Pat")
        self.assertEqual(intake["fields"]["family_name"], "Example")
        self.assertEqual(intake["fields"]["member_id"], "MEM-CARD")
        self.assertEqual(intake["fields"]["birth_date"], "1991-02-03")
        self.assertEqual(intake["field_sources"]["given_name"], "staff_entered")
        self.assertEqual(intake["field_sources"]["member_id"], "card_ocr")
        with patch.object(staff.v1_sources, "read_pages", side_effect=golden.OCRServiceError("synthetic failure")):
            response = await self.client.post("/api/staff/intakes", data={"full_name": "Pat Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"}, files=[("files", ("fallback.pdf", card_pdf(), "application/pdf"))])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["fields"], {"given_name": "Pat", "family_name": "Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"})

    async def test_complete_manual_details_create_an_intake_without_a_card_file(self):
        await self.sign_in()
        with patch.object(object_store, "enabled", return_value=False):
            response = await self.client.post("/api/staff/intakes", data={"full_name": "Pat Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"})
        self.assertEqual(response.status_code, 201, response.text)
        intake = response.json()
        self.assertEqual(intake["source"], "manual_entry")
        self.assertEqual(intake["documents"], [])
        self.assertEqual(intake["fields"], {"given_name": "Pat", "family_name": "Example", "member_id": "MEM-MANUAL", "birth_date": "1990-01-01"})
        response = await self.client.post("/api/staff/intakes", data={"full_name": "Pat Example", "birth_date": "1990-01-01"})
        self.assertEqual(response.status_code, 422)

    async def test_qr_intake_requires_both_staff_and_valid_share_without_clinical_copy(self):
        path = "/api/doctor/insurance/staff-intake"
        self.assertEqual((await self.client.post(path)).status_code, 401)
        actor = await self.sign_in()
        self.assertEqual((await self.client.post(path)).status_code, 403)
        with patch.dict(os.environ, {"INSURANCE_SESSION_SECRET": "synthetic-share-test-secret-at-least-32-characters"}):
            token = golden.create_share_token("portal-patient", "synthetic-qr-manifest")
            self.client.cookies.set(staff.CLINICIAN_SHARE_COOKIE, token, path="/api/doctor")
            with patch.object(staff, "get_patient_profile", return_value=PatientProfile("portal-patient", "Portal", "Example", "1991-01-01", "unknown")):
                response = await self.client.post(path)
        self.assertEqual(response.status_code, 201, response.text)
        intake = response.json()
        self.assertEqual(intake["source"], "clinicforce_qr")
        self.assertEqual(intake["documents"], [])
        self.assertIsNone(intake["patient"])
        self.assertNotIn("source_portal_patient_id", intake)
        self.assertEqual(intake["fields"]["given_name"], "Portal")
        self.assertEqual(staff_store.get_intake(intake["id"], actor["practice_id"])["source_portal_patient_id"], "portal-patient")

    async def test_dependent_confirmation_requires_policyholder_and_rejects_other_practice_patient(self):
        actor = await self.sign_in()
        intake = await self.upload()
        response = await self.client.put(f"/api/staff/intakes/{intake['id']}/patient", json={"given_name": "Child", "family_name": "Example", "birth_date": "2015-01-01", "member_id": "MEM123", "relationship": "child", "identity_confirmed": True})
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(staff_store.get_intake(intake["id"], actor["practice_id"])["patient"])
        confirmed = await self.confirm(intake)
        second = await self.upload()
        with self.assertRaises(ValueError):
            staff_store.confirm_patient(second["id"], actor, {"given_name": "Different", "family_name": "Patient", "birth_date": "1990-01-01", "member_id": "MEM123", "patient_id": confirmed["patient"]["id"]})

    async def test_card_preview_reuses_only_confirmed_intakes_for_same_clinic_patient(self):
        actor = await self.sign_in()
        original = await self.confirm(await self.upload())
        linked = staff_store.create_intake(str(uuid4()), actor, [], source='manual_entry')
        linked = await self.confirm(linked, patient_id=original['patient']['id'])
        other = await self.confirm(await self.upload(), given_name='Different')
        draft = await self.upload()
        foreign = staff_store.create_intake(str(uuid4()), {**actor, 'practice_id': 'foreign', 'id': None}, original['documents'])
        self.assertEqual(staff_store.patient_card_intakes('foreign', original['patient']['id']), [])
        response = await self.client.get(f"/api/staff/intakes/{linked['id']}/cards")
        self.assertEqual(response.status_code, 200)
        cards = response.json()['cards']
        self.assertEqual(len(cards), 1)
        self.assertIn(original['id'], cards[0]['url'])
        self.assertNotIn(other['id'], cards[0]['url'])
        self.assertEqual((await self.client.get(cards[0]['url'])).status_code, 200)
        own_draft = (await self.client.get(f"/api/staff/intakes/{draft['id']}/cards")).json()['cards']
        self.assertEqual(len(own_draft), 1)
        self.assertIn(draft['id'], own_draft[0]['url'])
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{foreign['id']}/cards")).status_code, 404)

    async def test_two_files_keep_original_page_and_filename_in_evidence(self):
        await self.sign_in()
        response = await self.client.post("/api/staff/intakes", files=[
            ("files", ("front.pdf", card_pdf("Member Name: PAT EXAMPLE"), "application/pdf")),
            ("files", ("back.pdf", card_pdf("Member ID: MEM123"), "application/pdf")),
        ])
        self.assertEqual(response.status_code, 201, response.text)
        intake = response.json()
        evidence = intake["ocr_evidence"]["member_id"]
        self.assertEqual(evidence["source_filename"], "back.pdf")
        self.assertEqual(evidence["source_page"], 1)
        self.assertEqual(evidence["document_id"], intake["documents"][1]["id"])

    async def test_workflow_documents_reopen_only_through_their_owned_intake(self):
        actor = await self.sign_in()
        intake = await self.confirm(await self.upload())
        scope = {"practice_id": actor["practice_id"], "intake_id": intake["id"]}
        self.objects["synthetic-contract-object"] = card_pdf("Synthetic clinic contract")
        self.objects["synthetic-fees-object"] = b"CPT,Billed Charge\n99213,200\n"
        document_case = insurance_store.create_case(intake["patient"]["id"], "", "documents", {
            "source_mode": "documents", "documents": {"contract": {"filename": "Clinic contract.pdf", "object_key": "synthetic-contract-object", "observed_at": staff_store.now()}},
        }, **scope)
        insurance_store.create_case(intake["patient"]["id"], "", "connected", {
            "source_mode": "availity", "sources": {"fees": {"filename": "Clinic fees.csv", "object_key": "synthetic-fees-object", "content_type": "text/csv"}},
        }, **scope)
        response = await self.client.get(f"/api/staff/intakes/{intake['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        documents = response.json()["workflow_documents"]
        self.assertEqual({item["id"] for item in documents}, {"v1:contract", "v2:fees"})
        self.assertTrue(all("object_key" not in item for item in documents))
        for metadata in documents:
            downloaded = await self.client.get(metadata["download_url"])
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.headers["Cache-Control"], "no-store")
            self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{intake['id']}/workflow-documents/v2/fees")).content, self.objects["synthetic-fees-object"])
        # Even a corrupt workflow pointer cannot reveal another intake's document.
        second = await self.confirm(await self.upload())
        with staff_store._connection() as connection:
            connection.execute("UPDATE staff_intakes SET workflows = %s::jsonb WHERE id = %s", (json.dumps({"v1": document_case["id"]}), second["id"]))
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{second['id']}")).json()["workflow_documents"], [])
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{second['id']}/workflow-documents/v1/contract")).status_code, 404)
        staff_store.create_member({**actor, "practice_id": "other-practice"}, "other-staff", "Other staff", ADMIN_PASSWORD, "staff")
        await self.sign_in("other-staff")
        self.assertEqual((await self.client.get(documents[0]["download_url"])).status_code, 404)

    async def test_inbox_paginates_every_submission_with_stable_order(self):
        actor = await self.sign_in()
        with staff_store._connection() as connection:
            for index in range(105):
                connection.execute("INSERT INTO staff_intakes (id,practice_id,created_by,status,created_at,updated_at) VALUES (%s,%s,%s,'needs_confirmation',%s,%s)",
                    (f"page-{index:03}", actor["practice_id"], actor["id"], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
        first = (await self.client.post("/api/staff/intakes/search", json={"query": "", "status": "", "offset": 0})).json()
        self.assertEqual(len(first["intakes"]), 100)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_offset"], 100)
        second = (await self.client.get("/api/staff/intakes?offset=100")).json()
        self.assertEqual(len(second["intakes"]), 5)
        self.assertFalse(second["has_more"])
        self.assertEqual(len({item["id"] for item in first["intakes"] + second["intakes"]}), 105)
        self.assertEqual((await self.client.post("/api/staff/intakes/search", json={"offset": -1})).status_code, 422)

    async def test_durable_workflow_store_scope_revision_and_legacy_purge(self):
        actor = await self.sign_in()
        intake = await self.confirm(await self.upload())
        patient_id = intake["patient"]["id"]
        scope = {"practice_id": actor["practice_id"], "intake_id": intake["id"]}
        case = insurance_store.create_case(patient_id, "", "binding", {"source_mode": "availity", "revision": 0, "transactions": {}}, **scope)
        self.assertIsNone(case["expires_at"])
        self.assertIsNotNone(insurance_store.get_case(case["id"], patient_id, "", **scope))
        self.assertIsNone(insurance_store.get_case(case["id"], patient_id, "", practice_id="another", intake_id=intake["id"]))
        self.assertIsNone(insurance_store.get_case(case["id"], patient_id, "some-qr"))
        first = insurance_store.save_v2(case, patient_id, "", "coverage-started", 0, **scope)
        self.assertEqual(first["revision"], 1)
        self.assertIsNone(insurance_store.save_v2(case, patient_id, "", "duplicate", 0, **scope))
        self.assertEqual(insurance_store.latest_v2(patient_id, "", "binding", **scope)["id"], case["id"])
        with patient_store._connection() as connection:
            connection.execute("UPDATE insurance_cases SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = %s", (case["id"],))
        self.assertIsNotNone(insurance_store.get_case(case["id"], patient_id, "", **scope))
        staff_store.attach_workflow(intake["id"], actor["practice_id"], "v2", case["id"])
        with self.assertRaises(ValueError):
            staff_store.confirm_patient(intake["id"], actor, {"given_name": "Wrong", "family_name": "Person", "birth_date": "1990-01-01"})
        self.assertEqual(staff_store.get_intake(intake["id"], actor["practice_id"])["workflows"]["v2"], case["id"])


if __name__ == "__main__":
    unittest.main()
