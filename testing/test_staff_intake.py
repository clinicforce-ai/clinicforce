"""Staff intake boundaries; optional real PostgreSQL checks never call external services.

Run the complete suite with STAFF_TEST_DATABASE_URL pointing at a disposable database.
"""

import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import fitz
import httpx
from fastapi import FastAPI
from PIL import Image, ImageFile

from app.api.routers import staff
from app.core import patient_store, staff_store, object_store
from app.core.patient_models import PatientProfile
from app.insurance import golden, store as insurance_store


ADMIN_PASSWORD = "synthetic-admin-password-123"


def card_pdf(text="Member Name: PAT EXAMPLE\nMember ID: MEM123\nGroup Number: GROUP123"):
    with fitz.open() as document:
        document.new_page().insert_text((25, 25), text)
        return document.tobytes()


class FileBoundaryTests(unittest.TestCase):
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
            connection.execute("TRUNCATE staff_library,staff_intakes,staff_patients,staff_sessions,staff_members,staff_login_attempts CASCADE")
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
