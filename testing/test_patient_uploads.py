"""Synthetic public upload/clinic isolation tests in a disposable PostgreSQL schema."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import fitz
import httpx
import psycopg
from fastapi import FastAPI
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api import settings
from app.api.routers import patient_uploads, staff
from app.core import clinic_store, intake_links_store as links, object_store, patient_store, staff_store


def card_pdf(text="Member Name: POLICY HOLDER\nMember ID: CARD123\nGroup Number: GROUP123"):
    with fitz.open() as document:
        document.new_page().insert_text((25, 25), text)
        return document.tobytes()


@unittest.skipUnless(os.getenv("STAFF_TEST_DATABASE_URL"), "Set STAFF_TEST_DATABASE_URL to a disposable PostgreSQL database")
class PatientUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = os.environ["STAFF_TEST_DATABASE_URL"]
        self.schema = "patient_upload_" + uuid4().hex
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.patches = [patch.object(patient_store, "DATABASE_URL", make_conninfo(self.database, options=f"-csearch_path={self.schema}")),
            patch.object(patient_store, "_schema_initialized", False), patch.object(staff_store, "_schema_initialized", False),
            patch.object(staff_store, "_bootstrap_initialized", False), patch.object(clinic_store, "_schema_initialized", False), patch.object(links, "_schema_initialized", False),
            patch.object(staff, "PUBLIC_BASE_URL", "http://test"), patch.object(staff, "ALLOWED_ORIGINS", ["http://test"]),
            patch.object(settings, "PUBLIC_BASE_URL", "http://test"), patch.object(patient_uploads, "PUBLIC_BASE_URL", "http://test"),
            patch.dict(os.environ, {"STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({"username": "upload-admin", "password": "synthetic-upload-password", "practice_id": "upload-clinic",
                "practice_name": "Synthetic Upload Clinic", "display_name": "Upload administrator"})})]
        for item in self.patches:
            item.start()
        staff_store.ensure_bootstrap()
        self.actor, self.token = staff_store.authenticate("upload-admin", "synthetic-upload-password", "synthetic-address")
        self.clinic = clinic_store.get_for_practice(self.actor["practice_id"], self.actor["practice_name"])
        with self.assertRaises(links.UploadLinkError):
            links.create_link(self.clinic, self.actor)
        clinic_store.set_npi(self.actor["practice_id"], "1234567893")
        self.clinic = clinic_store.get_for_practice(self.actor["practice_id"], self.actor["practice_name"])
        self.other = clinic_store.register_clinic(clinic_name="Other Synthetic Clinic", npi="2345678900", display_name="Other Tester", email="other@example.test", password="synthetic-other-password")
        _, self.other_token = staff_store.authenticate("other@example.test", "synthetic-other-password", "other-address")
        app = FastAPI(); app.include_router(staff.router); app.include_router(patient_uploads.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://test"})
        self.public = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://test"})
        self.client.cookies.set(staff.STAFF_COOKIE, self.token)
        self.objects = {}
        def put(practice_id, record_id, filename, content, content_type):
            key = f"staff-insurance/{practice_id}/{record_id}/{uuid4()}"
            self.objects[key] = content
            return key
        self.patches += [patch.object(object_store, "enabled", return_value=True), patch.object(object_store, "put_staff_document", side_effect=put),
            patch.object(object_store, "get_object_bytes", side_effect=lambda key: self.objects[key]),
            patch.object(object_store, "delete_object", side_effect=lambda key: self.objects.pop(key, None))]
        for item in self.patches[-4:]:
            item.start()

    async def asyncTearDown(self):
        await self.client.aclose(); await self.public.aclose()
        for item in reversed(self.patches):
            item.stop()
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    async def new_link(self, patient_id=None):
        response = await self.client.post("/api/staff/upload-links", json={"patient_id": patient_id} if patient_id else {})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def route(self, link):
        return f"/api/insurance-upload/{link['clinic']['npi']}/{link['upload_code']}"

    async def upload(self, link, *, data=None, content=None):
        return await self.public.post(self.route(link), data={"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "consent": "true", "submission_id": "synthetic-submission", **(data or {})},
            files=[("files", ("original-card.pdf", content if content is not None else card_pdf(), "application/pdf"))])

    async def test_public_receipt_preserves_original_and_pending_identity_without_account(self):
        with staff_store._connection() as connection:
            connection.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES ('expected-patient',%s,'Other','Person','1980-01-01',%s)", (self.actor["practice_id"], staff_store.now()))
        link = await self.new_link("expected-patient")
        self.assertEqual(link["url"], f"http://test/{self.clinic['npi']}/{link['upload_code']}")
        self.assertEqual(set((await self.public.get(self.route(link))).json()), {"clinic_name", "expires_at", "status"})
        content = card_pdf()
        with patch.object(patient_uploads.v1_sources, "read_pages", side_effect=AssertionError("Public upload must not call OCR")):
            response = await self.upload(link, content=content)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(set(response.json()), {"receipt_id", "status", "received_at", "clinic_name"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        row = links.owned_request(link["id"], self.actor["practice_id"])
        self.assertNotIn(link["upload_code"], json.dumps(row))
        intake = staff_store.get_intake(row["intake_id"], self.actor["practice_id"])
        self.assertIsNone(intake["created_by"]); self.assertIsNone(intake["patient_id"])
        self.assertEqual(intake["expected_patient_id"], "expected-patient")
        self.assertEqual(intake["source"], "patient_upload"); self.assertEqual(intake["status"], "needs_confirmation")
        self.assertEqual(intake["field_sources"]["given_name"], "patient_provided")
        self.assertEqual(intake["events"][0]["actor"], "Patient upload"); self.assertIsNone(intake["events"][0]["actor_id"])
        self.assertEqual(self.objects[intake["documents"][0]["object_key"]], content)
        self.assertEqual((await self.public.get(f"/api/staff/intakes/{row['intake_id']}")).status_code, 401)
        self.client.cookies.set(staff.STAFF_COOKIE, self.other_token)
        self.assertEqual((await self.client.get(f"/api/staff/intakes/{row['intake_id']}")).status_code, 404)
        self.assertEqual((await self.client.get("/api/staff/upload-links")).json()["upload_links"], [])
        self.assertEqual((await self.client.post(f"/api/staff/upload-links/{link['id']}/revoke")).status_code, 404)

    async def test_patient_specific_pending_card_is_only_a_reference_for_that_patient(self):
        with staff_store._connection() as connection:
            connection.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES ('matching-patient',%s,'Pat','Example','1990-01-01',%s)",
                               (self.actor['practice_id'], staff_store.now()))
        link = await self.new_link('matching-patient')
        response = await self.upload(link)
        self.assertEqual(response.status_code, 201, response.text)
        matching = staff_store.patient_pending_card_references(self.actor['practice_id'], 'matching-patient')
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['fields']['given_name'], 'Pat')
        self.assertEqual(staff_store.patient_pending_card_references(self.other['id'], 'matching-patient'), [])
        generic = await self.new_link()
        self.assertEqual((await self.upload(generic)).status_code, 201)
        self.assertEqual(len(staff_store.patient_pending_card_references(self.actor['practice_id'], 'matching-patient')), 1)

    async def test_retry_and_concurrent_different_submission_cannot_replace_saved_original(self):
        link = await self.new_link(); content = card_pdf()
        first = await self.upload(link, content=content)
        second = await self.upload(link, content=content)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual((await self.upload(link, data={"given_name": "Changed"}, content=content)).status_code, 409)
        self.assertEqual(len(self.objects), 1)
        next_link = await self.new_link()
        fields = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01"}
        prepared = [("card.pdf", content, "application/pdf")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(links.submit, self.clinic["npi"], next_link["upload_code"], {**fields, "given_name": name}, prepared) for name in ("One", "Two")]
            outcomes = []
            for future in futures:
                try: outcomes.append(future.result())
                except links.UploadLinkError as exc: outcomes.append(exc.status)
        self.assertEqual(sum(isinstance(item, tuple) for item in outcomes), 1)
        self.assertIn(409, outcomes); self.assertEqual(len(self.objects), 2)

    async def test_failed_database_save_cleans_only_new_objects_and_link_can_retry(self):
        link = await self.new_link(); content = card_pdf()
        with links._connection() as connection:
            connection.execute("ALTER TABLE staff_intakes ADD CONSTRAINT synthetic_failure CHECK (source <> 'patient_upload')")
        response = await self.upload(link, content=content)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.objects, {})
        self.assertEqual(links.metadata(self.clinic["npi"], link["upload_code"])["status"], "pending")
        with links._connection() as connection:
            connection.execute("ALTER TABLE staff_intakes DROP CONSTRAINT synthetic_failure")
        self.assertEqual((await self.upload(link, content=content)).status_code, 201)
        self.assertEqual(len(self.objects), 1)

    async def test_expiry_revoke_wrong_clinic_and_url_validation_are_scoped(self):
        link = await self.new_link()
        self.assertIsNotNone(links.validate_owned_url(link["id"], self.actor["practice_id"], link["url"]))
        self.assertIsNone(links.validate_owned_url(link["id"], self.other["practice_id"], link["url"]))
        self.assertIsNone(links.validate_owned_url(link["id"], self.actor["practice_id"], link["url"].replace("http://test", "https://evil.example")))
        self.assertIsNone(links.validate_owned_url(link["id"], self.actor["practice_id"], link["url"] + "?extra=1"))
        wrong = {**link, "clinic": {"npi": "2345678900"}}
        self.assertEqual((await self.upload(wrong)).status_code, 404)
        self.assertEqual((await self.public.post(f"/api/staff/upload-links/{link['id']}/revoke")).status_code, 401)
        self.assertEqual((await self.client.post(f"/api/staff/upload-links/{link['id']}/revoke")).status_code, 200)
        self.assertEqual((await self.upload(link)).status_code, 410)
        expired = await self.new_link()
        with links._connection() as connection:
            connection.execute("UPDATE insurance_upload_links SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=%s", (expired["id"],))
        self.assertEqual((await self.public.get(self.route(expired))).status_code, 410)
        self.assertEqual((await self.upload(expired)).status_code, 410)
        self.assertEqual(self.objects, {})

    async def test_lost_commit_acknowledgment_never_deletes_committed_originals(self):
        link = await self.new_link()
        real_connection = links._connection
        class LostCommit:
            def __enter__(self):
                self.connection = real_connection()
                return self.connection.__enter__()
            def __exit__(self, kind, value, traceback):
                result = self.connection.__exit__(kind, value, traceback)
                if kind is None:
                    raise OSError("Synthetic connection lost after successful commit")
                return result
        calls = 0
        def connect():
            nonlocal calls
            calls += 1
            return LostCommit() if calls == 1 else real_connection()
        content = card_pdf()
        with patch.object(links, "_connection", side_effect=connect):
            result, is_new = links.submit(self.clinic["npi"], link["upload_code"], {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01"}, [("card.pdf", content, "application/pdf")])
        self.assertTrue(is_new); self.assertEqual(result["status"], "received")
        self.assertEqual(len(self.objects), 1)
        self.assertEqual(next(iter(self.objects.values())), content)
        self.assertEqual(links.owned_request(link["id"], self.actor["practice_id"])["receipt_id"], result["receipt_id"])

    async def test_direct_clinic_start_and_upload_limits_persist_across_store_restart(self):
        metadata = await self.public.get(f"/api/insurance-upload/clinics/{self.clinic['npi']}")
        self.assertEqual(metadata.json(), {"clinic": {"npi": self.clinic["npi"], "name": self.clinic["name"]}})
        self.assertEqual((await self.public.get("/api/insurance-upload/clinics/3456789015")).status_code, 404)
        for _ in range(10):
            response = await self.public.post("/api/insurance-upload/start", json={"npi": self.clinic["npi"]})
            self.assertEqual(response.status_code, 201, response.text)
        links._schema_initialized = False
        self.assertEqual((await self.public.post("/api/insurance-upload/start", json={"npi": self.clinic["npi"]})).status_code, 429)
        link = response.json()
        for _ in range(10):
            self.assertEqual((await self.upload(link, content=b"not-a-card")).status_code, 422)
        self.assertEqual((await self.upload(link)).status_code, 429)
        self.assertEqual(self.objects, {})
        with links._connection() as connection:
            stored = json.dumps(connection.execute("SELECT * FROM insurance_upload_limits").fetchall())
        self.assertNotIn("127.0.0.1", stored)
        for number in range(100):
            links.check_rate(f"different-{number}", "volume-clinic", "submit")
        with self.assertRaises(links.UploadLinkError) as error:
            links.check_rate("another-address", "volume-clinic", "submit")
        self.assertEqual(error.exception.status, 429)

    async def test_validation_and_staff_card_reading_preserve_patient_identity_and_provenance(self):
        link = await self.new_link()
        preview = await self.public.post(f"{self.route(link)}/read-card", files=[("files", ("original-card.pdf", card_pdf("Member Name: PAT EXAMPLE\nDOB: 01/02/1990\nMember ID: CARD123"), "application/pdf"))])
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json(), {"fields": {"member_name": "PAT EXAMPLE", "birth_date": "1990-01-02", "member_id": "CARD123"}})
        self.assertEqual(self.objects, {}, "Reading a card for autofill must not save it")
        self.assertEqual((await self.upload(link, data={"consent": "false"})).status_code, 422)
        self.assertEqual((await self.upload(link, data={"birth_date": "2999-01-01"})).status_code, 422)
        response = await self.upload(link, data={"member_id": "PATIENT-ENTERED"})
        self.assertEqual(response.status_code, 201, response.text)
        record = links.owned_request(link["id"], self.actor["practice_id"])
        read_route = f"/api/staff/intakes/{record['intake_id']}/read-card"
        self.assertEqual((await self.public.post(read_route)).status_code, 401)
        self.client.cookies.set(staff.STAFF_COOKIE, self.other_token)
        self.assertEqual((await self.client.post(read_route)).status_code, 404)
        self.client.cookies.set(staff.STAFF_COOKIE, self.token)
        result = await self.client.post(read_route)
        self.assertEqual(result.status_code, 200, result.text)
        intake = result.json()
        self.assertEqual(intake["fields"]["given_name"], "Pat")
        self.assertEqual(intake["fields"]["birth_date"], "1990-01-01")
        self.assertEqual(intake["fields"]["member_id"], "PATIENT-ENTERED")
        self.assertEqual(intake["ocr_fields"]["member_id"], "CARD123")
        self.assertEqual(intake["fields"]["group_number"], "GROUP123")
        self.assertEqual(intake["field_sources"]["group_number"], "card_ocr")
        self.assertEqual(intake["field_sources"]["member_id"], "patient_provided")
        source = intake["ocr_evidence"]["member_id"]
        self.assertEqual((source["source_filename"], source["source_page"], source["document_id"]), ("original-card.pdf", 1, intake["documents"][0]["id"]))
        with self.assertRaises(links.UploadLinkError):
            links.save_card_reading(record["intake_id"], self.actor, {"member_id": "CHANGED"}, {}, "stale-revision")
        self.assertEqual(staff_store.get_intake(record["intake_id"], self.actor["practice_id"])["fields"]["member_id"], "PATIENT-ENTERED")

    async def test_file_limits_and_two_originals_keep_separate_source_pages(self):
        link = await self.new_link()
        data = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "consent": "true"}
        empty = await self.upload(link, content=b"")
        self.assertEqual(empty.status_code, 422)
        oversized = await self.upload(link, content=b"x" * (patient_uploads.MAX_INSURANCE_CARD_BYTES + 1))
        self.assertEqual(oversized.status_code, 413)
        with fitz.open() as document:
            document.new_page(); document.new_page()
            two_pages = document.tobytes()
        too_many = await self.public.post(self.route(link), data=data, files=[("files", ("two.pdf", two_pages, "application/pdf")), ("files", ("extra.pdf", card_pdf(), "application/pdf"))])
        self.assertEqual(too_many.status_code, 422)
        self.assertEqual(self.objects, {})
        response = await self.public.post(self.route(link), data=data, files=[
            ("files", ("front.pdf", card_pdf("Member Name: POLICY HOLDER"), "application/pdf")),
            ("files", ("back.pdf", card_pdf("Member ID: BACK123"), "application/pdf"))])
        self.assertEqual(response.status_code, 201, response.text)
        row = links.owned_request(link["id"], self.actor["practice_id"])
        response = await self.client.post(f"/api/staff/intakes/{row['intake_id']}/read-card")
        self.assertEqual(response.status_code, 200, response.text)
        intake = response.json(); evidence = intake["ocr_evidence"]["member_id"]
        self.assertEqual((evidence["source_filename"], evidence["source_page"], evidence["document_id"]), ("back.pdf", 1, intake["documents"][1]["id"]))
        self.assertEqual(len(self.objects), 2)

    async def test_targeted_link_requires_explicit_matching_staff_confirmation(self):
        with staff_store._connection() as connection:
            for patient_id in ("expected-patient", "another-patient"):
                connection.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES (%s,%s,'Correct','Patient','1980-01-01',%s)",
                                   (patient_id, self.actor["practice_id"], staff_store.now()))
        link = await self.new_link("expected-patient")
        self.assertEqual((await self.upload(link)).status_code, 201)
        row = links.owned_request(link["id"], self.actor["practice_id"])
        route = f"/api/staff/intakes/{row['intake_id']}/patient"
        data = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "member_id": "CARD123", "payer": "Synthetic payer", "relationship": "self", "identity_confirmed": True}
        mismatch = await self.client.put(route, json=data)
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        pending = staff_store.get_intake(row["intake_id"], self.actor["practice_id"])
        self.assertEqual(pending["status"], "needs_confirmation"); self.assertIsNone(pending["patient_id"])
        self.assertEqual(pending["fields"]["given_name"], "Pat")
        corrected = {**data, "given_name": "Correct", "family_name": "Patient", "birth_date": "1980-01-01"}
        wrong_selection = await self.client.put(route, json={**corrected, "patient_id": "another-patient"})
        self.assertEqual(wrong_selection.status_code, 409, wrong_selection.text)
        self.assertEqual((await self.public.put(route, json=corrected)).status_code, 401)
        self.assertEqual((await self.client.put(route, json={**corrected, "identity_confirmed": False})).status_code, 422)
        confirmed = await self.client.put(route, json=corrected)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["patient"]["id"], "expected-patient")
        self.assertEqual(confirmed.json()["status"], "ready")
        with staff_store._connection() as connection:
            count = connection.execute("SELECT count(*) AS total FROM staff_patients WHERE practice_id=%s", (self.actor["practice_id"],)).fetchone()["total"]
        self.assertEqual(count, 2, "Confirming a targeted link must not create a duplicate clinic record")
        self.assertEqual(set((await self.public.get(self.route(link))).json()), {"clinic_name", "expires_at", "status"})

    async def test_dashboard_cohorts_history_and_portal_identifiers_are_clinic_scoped(self):
        fields = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "email": "patient@example.test"}
        with staff_store._connection() as connection:
            connection.execute("INSERT INTO patients(patient_id,password_hash,created_at) VALUES ('never-shared-portal','unused',%s)", (staff_store.now(),))
            connection.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES ('clinic-without-card',%s,'Clinic','Only','1985-01-01',%s)", (self.actor["practice_id"], staff_store.now()))
        shared_missing = staff_store.create_intake(str(uuid4()), self.actor, [], "clinicforce_qr", "private-portal-missing", {**fields, "given_name": "Missing"})
        shared_history = staff_store.create_intake(str(uuid4()), self.actor, [], "clinicforce_qr", "private-portal-with-card", fields)
        staff_store.create_intake(str(uuid4()), self.other, [], "clinicforce_qr", "other-private-account", fields)
        link = await self.new_link()
        self.assertEqual((await self.upload(link, data={"email": fields["email"]})).status_code, 201)
        associated = await self.new_link()
        links.submit(self.clinic["npi"], associated["upload_code"], fields, [("card.pdf", card_pdf(), "application/pdf")], source_portal_patient_id="private-portal-with-card")
        self.assertEqual((await self.public.get("/api/staff/patients/dashboard")).status_code, 401)
        result = (await self.client.get("/api/staff/patients/dashboard")).json()
        self.assertEqual(result["counts"], {"all": 4, "accounts_with_cards": 1, "accounts_without_cards": 1, "card_only": 1, "other": 1})
        self.assertNotIn("private-portal", json.dumps(result)); self.assertNotIn("never-shared", json.dumps(result)); self.assertNotIn("other-private", json.dumps(result))
        portal = next(row for row in result["patients"] if row["account_status"] == "portal" and row["card_status"] == "received")
        self.assertEqual(portal["intake_count"], 2); self.assertIn(shared_history["id"], portal["intake_ids"])
        self.assertEqual(len(portal["intakes"]), 2)
        automatic = next(row for row in result["patients"] if row["account_status"] == "automatic")
        self.assertEqual(automatic["identity_status"], "unverified"); self.assertEqual(automatic["email"], fields["email"])
        requested = await self.client.post("/api/staff/upload-links", json={"intake_id": shared_missing["id"]})
        self.assertEqual(requested.status_code, 201, requested.text)
        pending = staff_store.get_intake(shared_missing["id"], self.actor["practice_id"])
        self.assertIsNone(pending["patient_id"]); self.assertIsNotNone(pending["auto_patient_id"])
        self.client.cookies.set(staff.STAFF_COOKIE, self.other_token)
        self.assertEqual((await self.client.post("/api/staff/upload-links", json={"intake_id": shared_missing["id"]})).status_code, 404)
        other_result = (await self.client.get("/api/staff/patients/dashboard")).json()
        self.assertEqual(other_result["counts"]["all"], 1)

    async def test_automatic_record_confirmation_reuses_or_merges_without_duplicate(self):
        link = await self.new_link(); self.assertEqual((await self.upload(link)).status_code, 201)
        row = links.owned_request(link["id"], self.actor["practice_id"])
        pending = staff_store.get_intake(row["intake_id"], self.actor["practice_id"])
        auto_id = pending["auto_patient_id"]
        self.assertIsNone(pending["patient_id"]); self.assertEqual(pending["auto_patient"]["identity_status"], "unverified")
        data = {"given_name": "Corrected", "family_name": "Example", "birth_date": "1990-01-01", "member_id": "CARD123", "relationship": "self", "identity_confirmed": True}
        result = await self.client.put(f"/api/staff/intakes/{row['intake_id']}/patient", json=data)
        self.assertEqual(result.status_code, 200, result.text); self.assertEqual(result.json()["patient_id"], auto_id)
        self.assertEqual(result.json()["patient"]["given_name"], "Corrected")
        dashboard = (await self.client.get("/api/staff/patients/dashboard")).json()
        self.assertEqual(dashboard["counts"]["all"], 1)
        self.assertEqual((dashboard["patients"][0]["account_status"], dashboard["patients"][0]["identity_status"]), ("automatic", "confirmed"))
        second = await self.new_link(); self.assertEqual((await self.upload(second, data={"given_name": "Corrected"})).status_code, 201)
        new_row = links.owned_request(second["id"], self.actor["practice_id"])
        new_auto = staff_store.get_intake(new_row["intake_id"], self.actor["practice_id"])["auto_patient_id"]
        followup = await self.new_link(new_auto)
        result = await self.client.put(f"/api/staff/intakes/{new_row['intake_id']}/patient", json={**data, "patient_id": auto_id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(staff_store.get_patient(new_auto, self.actor["practice_id"]))
        self.assertEqual(links.owned_request(followup["id"], self.actor["practice_id"])["expected_patient_id"], auto_id)
        dashboard = (await self.client.get("/api/staff/patients/dashboard")).json()
        self.assertEqual(dashboard["counts"]["all"], 1); self.assertEqual(dashboard["patients"][0]["intake_count"], 2)

    async def test_mismatched_target_and_changed_identity_cannot_reassign_portal_cohort(self):
        with staff_store._connection() as connection:
            connection.execute("INSERT INTO staff_patients(id,practice_id,given_name,family_name,birth_date,created_at) VALUES ('intended-patient',%s,'Intended','Patient','1980-01-01',%s)", (self.actor["practice_id"], staff_store.now()))
        link = await self.new_link("intended-patient")
        fields = {"given_name": "Different", "family_name": "Patient", "birth_date": "1990-01-01"}
        prepared = [("card.pdf", card_pdf(), "application/pdf")]
        receipt, _ = links.submit(self.clinic["npi"], link["upload_code"], fields, prepared, source_portal_patient_id="private-different-account")
        row = links.owned_request(link["id"], self.actor["practice_id"])
        record = staff_store.get_intake(row["intake_id"], self.actor["practice_id"])
        self.assertNotEqual(record["auto_patient_id"], "intended-patient")
        dashboard = staff_store.patient_dashboard(self.actor["practice_id"])
        target = next(item for item in dashboard["patients"] if item["id"] == "intended-patient")
        self.assertEqual(target["card_status"], "missing")
        self.assertEqual(dashboard["counts"]["accounts_with_cards"], 1)
        with self.assertRaises(links.UploadLinkError):
            links.submit(self.clinic["npi"], link["upload_code"], fields, prepared, source_portal_patient_id="another-account")
        self.assertEqual(links.owned_request(link["id"], self.actor["practice_id"])["receipt_id"], receipt["receipt_id"])
        data = {"given_name": "Intended", "family_name": "Patient", "birth_date": "1980-01-01", "member_id": "CARD123", "relationship": "self", "identity_confirmed": True}
        result = await self.client.put(f"/api/staff/intakes/{row['intake_id']}/patient", json=data)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(staff_store.get_intake(row["intake_id"], self.actor["practice_id"])["source_portal_patient_id"])
        dashboard = staff_store.patient_dashboard(self.actor["practice_id"])
        self.assertEqual(dashboard["counts"]["accounts_with_cards"], 0); self.assertEqual(dashboard["counts"]["all"], 1)

    async def test_shared_auto_correction_keeps_old_unconfirmed_card_history_separate(self):
        original_fields = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01"}
        original_share = staff_store.create_intake(str(uuid4()), self.actor, [], "clinicforce_qr", "private-original-portal", original_fields)
        link = (await self.client.post("/api/staff/upload-links", json={"intake_id": original_share["id"]})).json()
        self.assertEqual((await self.upload(link)).status_code, 201)
        pending_id = links.owned_request(link["id"], self.actor["practice_id"])["intake_id"]
        corrected_fields = {**original_fields, "given_name": "Corrected"}
        confirmation = {**corrected_fields, "relationship": "self", "identity_confirmed": True}
        corrected = await self.client.put(f"/api/staff/intakes/{original_share['id']}/patient", json=confirmation)
        self.assertEqual(corrected.status_code, 200, corrected.text)
        corrected_patient_id = corrected.json()["patient_id"]
        current_share = staff_store.create_intake(str(uuid4()), self.actor, [], "clinicforce_qr", "private-corrected-portal", corrected_fields)
        result = await self.client.put(f"/api/staff/intakes/{current_share['id']}/patient", json={**confirmation, "patient_id": corrected_patient_id})
        self.assertEqual(result.status_code, 200, result.text)
        dashboard = staff_store.patient_dashboard(self.actor["practice_id"])
        portal = next(item for item in dashboard["patients"] if item["account_status"] == "portal")
        self.assertEqual(portal["given_name"], "Corrected"); self.assertEqual(portal["card_status"], "missing")
        self.assertNotIn(pending_id, portal["intake_ids"])
        pending = next(item for item in dashboard["patients"] if pending_id in item["intake_ids"])
        self.assertEqual(pending["given_name"], "Pat"); self.assertEqual(pending["card_status"], "received")
        self.assertEqual(dashboard["counts"], {"all": 2, "accounts_with_cards": 0, "accounts_without_cards": 1, "card_only": 1, "other": 0})
        followup = await self.client.post("/api/staff/upload-links", json={"intake_id": pending_id})
        self.assertEqual(followup.status_code, 201, followup.text)
        self.assertNotEqual(links.owned_request(followup.json()["id"], self.actor["practice_id"])["expected_patient_id"], corrected_patient_id)


if __name__ == "__main__":
    unittest.main()
