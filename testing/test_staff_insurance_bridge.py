"""Real SQL scope/replay checks; each test uses its own disposable database schema.

STAFF_TEST_DATABASE_URL must point at a disposable PostgreSQL database.
No external integrations or patient objects are read.
"""
import asyncio
import copy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi import FastAPI
from fastapi import HTTPException

from app.api.routers import insurance, insurance_v2, staff
from app.core import object_store, patient_store, staff_store
from app.insurance import documented, golden, store, v1_sources


class FeeReplacementTests(unittest.TestCase):
    def test_replacement_updates_only_fee_charges_and_removes_absent_prices(self):
        case = {"inputs": {"lines": [{"code": "99213", "charge": None, "units": "1"}, {"code": "92523", "charge": "0.00", "units": "1"}],
                           "diagnoses": [], "coverage_missing": [], "review_missing": []}, "sources": {}}
        fees = lambda csv: insurance_v2.intake.read_fee_schedule(csv, "fees.csv")
        insurance_v2.apply_fee_schedule(case, fees(b"CPT,Cash Price,Billed Charge\n99213,80,100\n92523,60,110\n"))
        self.assertEqual([(line["charge"], line["charge_source"]) for line in case["inputs"]["lines"]], [("100.00", "fee_schedule"), ("0.00", "visit")])
        insurance_v2.apply_fee_schedule(case, fees(b"CPT,Cash Price,Billed Charge\n99213,90,150\n92523,70,130\n"))
        self.assertEqual([(line["charge"], line["cash_cents"]) for line in case["inputs"]["lines"]], [("150.00", 9000), ("0.00", 7000)])
        insurance_v2.apply_fee_schedule(case, fees(b"CPT,Cash Price,Billed Charge\n92523,30,40\n"))
        self.assertEqual([(line["charge"], line["cash_cents"]) for line in case["inputs"]["lines"]], [(None, None), ("0.00", 3000)], "A removed CSV row cannot retain its former price")
        insurance_v2.apply_fee_schedule(case, fees(b"CPT,Cash Price\n99213,100\n"))
        self.assertIsNone(case["inputs"]["lines"][0]["charge"], "Cash price must never become a billed charge")

    def test_legacy_ambiguous_charge_source_cannot_be_guessed_from_equal_amount(self):
        old = insurance_v2.intake.read_fee_schedule(b"CPT,Billed Charge\n99213,100\n", "old.csv")
        case = {"inputs": {"lines": [{"code": "99213", "charge": "100.00", "units": "1"}]}, "sources": {"fees": old}}
        before = copy.deepcopy(case)
        with self.assertRaises(HTTPException) as error:
            insurance_v2.apply_fee_schedule(case, insurance_v2.intake.read_fee_schedule(b"CPT,Billed Charge\n99213,200\n", "new.csv"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(case, before)


@unittest.skipUnless(os.getenv("STAFF_TEST_DATABASE_URL"), "Set STAFF_TEST_DATABASE_URL to a disposable PostgreSQL database")
class StaffInsuranceBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = os.environ["STAFF_TEST_DATABASE_URL"]
        self.schema = "bridge_" + uuid4().hex
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.patches = [
            patch.object(patient_store, "DATABASE_URL", make_conninfo(self.database, options=f"-csearch_path={self.schema}")),
            patch.object(patient_store, "_schema_initialized", False), patch.object(staff_store, "_schema_initialized", False),
            patch.object(staff_store, "_bootstrap_initialized", False), patch.object(staff, "PUBLIC_BASE_URL", "http://test"),
            patch.object(staff, "ALLOWED_ORIGINS", ["http://test"]), patch.object(insurance, "_enabled"),
            patch.dict(os.environ, {"INSURANCE_SESSION_SECRET": "x" * 40, "STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({
                "username": "bridge-admin", "password": "synthetic-bridge-password", "practice_id": "bridge-practice",
                "practice_name": "Synthetic clinic", "display_name": "Bridge administrator",
            })}),
        ]
        for item in self.patches:
            item.start()
        staff_store.ensure_bootstrap()
        self.actor, self.token = staff_store.authenticate("bridge-admin", "synthetic-bridge-password", "synthetic-address")
        app = FastAPI()
        app.include_router(staff.router); app.include_router(insurance.router); app.include_router(insurance_v2.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://test"})
        self.client.cookies.set(staff.STAFF_COOKIE, self.token)

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def intake(self, actor=None, member_id="MEM123"):
        actor = actor or self.actor
        value = staff_store.create_intake(str(uuid4()), actor, [{"id": "original-card", "filename": "synthetic.pdf", "object_key": "staff-insurance/synthetic-original", "sha256": "synthetic-hash", "content_type": "application/pdf"}])
        return staff_store.confirm_patient(value["id"], actor, {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "member_id": member_id,
                                      "group_number": "GROUP", "payer": "Synthetic payer", "relationship": "self", "identity_confirmed": True})

    def scope(self, intake):
        return {"practice_id": intake["practice_id"], "intake_id": intake["id"], "staff_actor": self.actor}

    def workflow(self):
        return {**documented.pending_case({"member_name": "PAT EXAMPLE", "member_id": "MEM123", "group_number": "GROUP"}),
                "extraction_version": v1_sources.EXTRACTION_VERSION, "evidence_manifest_id": golden.evidence_binding()}

    async def test_ecw_lookup_verifies_before_intake_and_links_verified_patient(self):
        details = {"given_name": "Pat", "middle_initial": "Q", "family_name": "Example", "birth_date": "1990-01-01"}
        path = "/api/staff/intakes/ecw-lookup"
        with patch.object(staff.ecw_backend, "configured", return_value=True), patch.object(
                staff.ecw_backend, "lookup_patient_and_appointments", new=AsyncMock(
                    side_effect=staff.ecw_backend.EcwBackendError("No eClinicalWorks patient matched the entered name and date of birth."))):
            failed = await self.client.post(path, json=details)
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(staff_store.list_intakes(self.actor["practice_id"]), [])
        match = {"patient": {"id": "ecw-patient-1", "given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01"},
                 "appointments": [{"id": "visit-1", "start": "2026-10-01T09:00:00Z", "status": "planned", "reason": "Follow-up"}]}
        with patch.object(staff.ecw_backend, "configured", return_value=True), patch.object(
                staff.ecw_backend, "lookup_patient_and_appointments", new=AsyncMock(return_value=match)) as lookup:
            found = await self.client.post(path, json=details)
        self.assertEqual(found.status_code, 201, found.text)
        self.assertEqual(found.json()["ecw_match"]["appointments"][0]["id"], "visit-1")
        self.assertEqual(found.json()["fields"]["ecw_patient_id"], "ecw-patient-1")
        self.assertEqual(lookup.await_args.args[0]["patient_middle_initial"], "Q")
        intake_id = found.json()["id"]
        confirmation = {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "relationship": "unknown", "identity_confirmed": True}
        changed = await self.client.put(f"/api/staff/intakes/{intake_id}/patient", json={**confirmation, "family_name": "Changed"})
        self.assertEqual(changed.status_code, 409, changed.text)
        saved = await self.client.put(f"/api/staff/intakes/{intake_id}/patient", json=confirmation)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["patient"]["ecw_patient_id"], "ecw-patient-1")
        legacy = staff_store.create_intake(str(uuid4()), self.actor, [], "ecw_lookup", None, details)
        with self.assertRaisesRegex(ValueError, "not verified in eCW"):
            staff_store.confirm_patient(legacy["id"], self.actor, confirmation)
        with patch.object(staff.ecw_backend, "configured", return_value=True), patch.object(
                staff.ecw_backend, "lookup_patient_and_appointments", new=AsyncMock(return_value=match)):
            duplicate = await self.client.post(path, json=details)
        conflicting = await self.client.put(f"/api/staff/intakes/{duplicate.json()['id']}/patient", json=confirmation)
        self.assertEqual(conflicting.status_code, 409, conflicting.text)
        self.assertIn("already linked", conflicting.text)

    async def test_real_header_scope_never_falls_back_to_qr_or_another_patient(self):
        own, other_intake = self.intake(), self.intake(member_id="OTHER")
        response = await self.client.post("/api/doctor/insurance/staff-case", headers={"X-ClinicForce-Intake": own["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        case_id = response.json()["id"]
        path = f"/api/doctor/insurance/cases/{case_id}"
        self.assertEqual((await self.client.get(path)).status_code, 403)
        self.assertEqual((await self.client.get(path, headers={"X-ClinicForce-Intake": other_intake["id"]})).status_code, 404)
        other_actor = staff_store.create_member({**self.actor, "practice_id": "other-clinic"}, "other-staff", "Other staff", "synthetic-other-password", "staff")
        other = self.intake(other_actor)
        self.assertEqual((await self.client.get(path, headers={"X-ClinicForce-Intake": other["id"]})).status_code, 404)
        self.assertEqual((await self.client.post("/api/doctor/insurance/staff-case", headers={"X-ClinicForce-Intake": own["id"], "Origin": "https://unrelated.invalid"})).status_code, 403)
        with patient_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) AS count FROM patients").fetchone()["count"], 0)
            connection.execute("INSERT INTO patients (patient_id,password_hash,created_at) VALUES ('portal-patient','unused',%s)", (staff_store.now(),))
        legacy = store.create_case("portal-patient", "portal-share", golden.evidence_binding(), self.workflow())
        self.client.cookies.set(insurance.CLINICIAN_SHARE_COOKIE, golden.create_share_token("portal-patient", "portal-share", True))
        legacy_path = f"/api/doctor/insurance/cases/{legacy['id']}"
        self.assertEqual((await self.client.get(legacy_path)).status_code, 200)
        self.assertEqual((await self.client.get(path)).status_code, 404, "QR credentials must not grant durable staff-case access")
        self.assertEqual((await self.client.get(legacy_path, headers={"X-ClinicForce-Intake": own["id"]})).status_code, 404)
        self.client.cookies.delete(staff.STAFF_COOKIE)
        self.assertEqual((await self.client.get(legacy_path, headers={"X-ClinicForce-Intake": own["id"]})).status_code, 401, "Invalid staff scope must not fall back to a valid QR")

    async def test_duplicate_start_is_atomic_and_stale_confirmation_is_rejected(self):
        intake = self.intake()
        responses = await asyncio.gather(*(self.client.post("/api/doctor/insurance/staff-case", headers={"X-ClinicForce-Intake": intake["id"]}) for _ in range(4)))
        self.assertTrue(all(response.status_code == 200 for response in responses), [response.text for response in responses])
        self.assertEqual(len({response.json()["id"] for response in responses}), 1)
        saved = staff_store.get_intake(intake["id"], intake["practice_id"])
        self.assertEqual(saved["status"], "in_progress")
        self.assertEqual(saved["events"][-1]["actor_id"], self.actor["id"])
        with patient_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) AS count FROM insurance_cases WHERE intake_id = %s", (intake["id"],)).fetchone()["count"], 1)
        with self.assertRaises(ValueError):
            staff_store.confirm_patient(intake["id"], self.actor, {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "member_id": "CHANGED"})
        draft = self.intake()
        staff_store.confirm_patient(draft["id"], self.actor, {"given_name": "Pat", "family_name": "Example", "birth_date": "1990-01-01", "member_id": "CORRECTED"})
        with self.assertRaises(store.StaffCaseConflict):
            store.create_case(draft["patient"]["id"], "", golden.evidence_binding(), self.workflow(), expected_intake_updated_at=draft["updated_at"], **self.scope(draft))
        self.assertEqual(staff_store.get_intake(draft["id"], draft["practice_id"])["workflows"], {})
        with self.assertRaises(store.StaffCaseConflict):
            store.create_case("wrong-patient", "", golden.evidence_binding(), self.workflow(), **self.scope(draft))

    async def test_revision_history_activity_and_purge_protect_durable_evidence(self):
        intake = self.intake()
        scope, patient_id = self.scope(intake), intake["patient"]["id"]
        case = store.create_case(patient_id, "", golden.evidence_binding(), self.workflow(), **scope)
        fee_update, coverage_update = copy.deepcopy(case), copy.deepcopy(case)
        fee_update["documents"]["fee_schedule"] = {"object_key": "staff-insurance/synthetic-fees"}
        saved = store.save_case(fee_update, patient_id, "", "fee-schedule-uploaded", **scope)
        coverage_update["documents"]["coverage"] = {"object_key": "staff-insurance/synthetic-coverage"}
        with self.assertRaises(store.StaffCaseConflict):
            store.save_case(coverage_update, patient_id, "", "coverage-uploaded", **scope)
        self.assertEqual(store.get_case(case["id"], patient_id, "", **scope)["documents"], fee_update["documents"])
        self.assertIsNone(store.get_case(case["id"], "wrong-patient", "", **scope))
        self.assertIsNone(store.save_case(saved, patient_id, "", "wrong-clinic", **{**scope, "practice_id": "wrong-clinic"}))
        saved["status"] = "complete"
        saved = store.save_case(saved, patient_id, "", "authorization", **scope)
        self.assertEqual(staff_store.get_intake(intake["id"], intake["practice_id"])["status"], "reviewed")
        connected = store.create_case(patient_id, "", "synthetic-binding", {"source_mode": "availity", "revision": 0}, **scope)
        with ThreadPoolExecutor(max_workers=2) as executor:
            updates = list(executor.map(lambda _: store.save_v2(connected, patient_id, "", "coverage-submitting", 0, **scope), range(2)))
        self.assertEqual(sum(update is not None for update in updates), 1, "One revision claim may submit")
        self.assertEqual(staff_store.get_intake(intake["id"], intake["practice_id"])["status"], "in_progress")
        with patient_store._connection() as connection:
            connection.execute("INSERT INTO patients (patient_id,password_hash,created_at) VALUES ('legacy','unused',%s)", (staff_store.now(),))
        legacy = store.create_case("legacy", "legacy-share", "legacy-binding", {"source_mode": "documents", "documents": {"coverage": {"object_key": "legacy-evidence"}}})
        with patient_store._connection() as connection:
            connection.execute("UPDATE insurance_cases SET expires_at = '2000-01-01T00:00:00+00:00'")
        with patch.object(store, "delete_object") as delete:
            self.assertIsNotNone(store.get_case(case["id"], patient_id, "", **scope))
        delete.assert_called_once_with("legacy-evidence")
        self.assertIsNone(store.get_case(legacy["id"], "legacy", "legacy-share"))
        self.assertEqual(staff_store.get_intake(intake["id"], intake["practice_id"])["documents"][0]["object_key"], "staff-insurance/synthetic-original")

    async def test_v2_practice_binding_and_demo_ecw_preview(self):
        intake = self.intake()
        headers = {"X-ClinicForce-Intake": intake["id"]}
        paths = [("GET", "/configuration"), ("GET", "/cases/latest"), ("POST", "/cases"), ("GET", "/cases/unknown"), ("POST", "/cases/unknown/transactions/coverage")]
        for configured in ("", "another-clinic"):
            with patch.dict(os.environ, {"STAFF_INTEGRATION_PRACTICE_ID": configured}), patch.object(insurance_v2, "configuration") as config:
                for method, path in paths:
                    response = await self.client.request(method, "/api/doctor/insurance/v2" + path, headers=headers, **({"json": {}} if path == "/cases" else {}))
                    self.assertEqual(response.status_code, 403, response.text)
                config.assert_not_called()
        inputs = insurance_v2.demo_inputs()
        inputs.update(member_id="MEM123", group_number="GROUP", payer_id="SYNTHETIC", relationship="18")
        inputs["patient"] = {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1990-01-01", "gender": "U"}
        inputs["subscriber"] = {"firstName": "Pat", "lastName": "Example", "birthDate": "1990-01-01", "memberId": "MEM123"}
        practice = {"payer_aliases": {"Synthetic payer": "SYNTHETIC"}}
        ecw_result = (inputs, [], {"system": "eClinicalWorks", "payer": "Synthetic payer", "appointment_date": "2026-09-17"})
        refreshed_inputs = copy.deepcopy(inputs)
        refreshed_inputs["reason"] = "Updated eCW visit reason"
        refreshed_result = (refreshed_inputs, [], {"system": "eClinicalWorks", "payer": "Synthetic payer",
                                                    "appointment_date": "2026-09-18"})
        with patch.dict(os.environ, {"STAFF_INTEGRATION_PRACTICE_ID": "", "STAFF_INTEGRATION_PRACTICE_IDS": f"primary, {intake['practice_id']}"}), \
                patch.object(insurance_v2, "configuration", return_value=(SimpleNamespace(environment="demo", client_id="", client_secret=""), practice, "synthetic")), \
                patch.object(insurance_v2.ecw_backend, "configured", return_value=True), \
                patch.object(insurance_v2.ecw_backend, "fetch_inputs", new=AsyncMock(side_effect=[ecw_result, refreshed_result])) as fetch:
            configuration = await self.client.get("/api/doctor/insurance/v2/configuration", headers=headers)
            self.assertTrue(configuration.json()["ecw_configured"])
            response = await self.client.post("/api/doctor/insurance/v2/cases", headers=headers, json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["inputs"]["patient"]["patient_first_name"], "Pat")
            fetch.assert_awaited_once_with({"patient_first_name": "Pat", "patient_last_name": "Example",
                                            "patient_birth_date": "1990-01-01", "gender": "U",
                                            "ecw_patient_id": None}, practice)
            refreshed = await self.client.post("/api/doctor/insurance/v2/cases", headers=headers, json={})
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(refreshed.json()["id"], response.json()["id"])
            self.assertEqual(refreshed.json()["revision"], 1)
            self.assertEqual(refreshed.json()["inputs"]["reason"], "Updated eCW visit reason")
            self.assertEqual(refreshed.json()["sources"]["ecw"]["appointment_date"], "2026-09-18")
            self.assertEqual(fetch.await_count, 2)
            self.assertEqual(fetch.await_args_list[1].args[0]["ecw_appointment_date"], "2026-09-17")
            blocked = await self.client.post(f"/api/doctor/insurance/v2/cases/{response.json()['id']}/transactions/coverage", headers=headers)
            self.assertEqual(blocked.status_code, 409, "Real eCW data must never be sent to Availity demo")
            self.assertIn("No patient data was submitted", blocked.text)
            async def slow_fetch(*_args):
                await asyncio.sleep(1)
            with patch.object(insurance_v2, "ECW_FETCH_TIMEOUT_SECONDS", 0.01), \
                    patch.object(insurance_v2.ecw_backend, "fetch_inputs", side_effect=slow_fetch):
                timed_out = await self.client.post("/api/doctor/insurance/v2/cases", headers=headers, json={})
            self.assertEqual(timed_out.status_code, 502, timed_out.text)
            self.assertIn("did not respond in time", timed_out.text)

    async def test_v2_fee_original_persists_and_conflict_cleans_only_new_object(self):
        intake = self.intake()
        inputs = insurance_v2.demo_inputs()
        case = store.create_case(intake["patient"]["id"], "", "synthetic-binding", {"source_mode": "availity", "binding": "synthetic-binding", "environment": "production",
            "input_mode": "ecw", "revision": 0, "inputs": inputs, "profile": inputs["patient"], "missing": [], "sources": {}, "transactions": {}, "discovery": {}}, **self.scope(intake))
        path = f"/api/doctor/insurance/v2/cases/{case['id']}/documents/fees"
        files = {"file": ("fees.csv", b"CPT,Cash Price,Billed Charge\n92523,125.00,250.00\n95913,600.00,900.00\n", "text/csv")}
        with patch.dict(os.environ, {"STAFF_INTEGRATION_PRACTICE_ID": intake["practice_id"]}), patch.object(insurance_v2, "configuration", return_value=(SimpleNamespace(environment="production"), {}, "synthetic-binding")), \
                patch.object(object_store, "enabled", return_value=True), patch.object(object_store, "put_staff_document", side_effect=["staff-insurance/saved-fees", "staff-insurance/conflicting-fees"]) as put, patch.object(object_store, "delete_object") as delete:
            response = await self.client.post(path, headers={"X-ClinicForce-Intake": intake["id"]}, files=files)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("object_key", response.json()["sources"]["fees"])
            stored = store.get_case(case["id"], intake["patient"]["id"], "", **self.scope(intake))
            self.assertEqual(stored["sources"]["fees"]["object_key"], "staff-insurance/saved-fees")
            self.assertEqual(stored["sources"]["fees"]["content_type"], "text/csv")
            with patch.object(store, "save_v2", return_value=None):
                response = await self.client.post(path, headers={"X-ClinicForce-Intake": intake["id"]}, files=files)
            self.assertEqual(response.status_code, 409, response.text)
            delete.assert_called_once_with("staff-insurance/conflicting-fees")
            self.assertEqual(put.call_count, 2)
            self.assertEqual(store.get_case(case["id"], intake["patient"]["id"], "", **self.scope(intake))["sources"]["fees"]["object_key"], "staff-insurance/saved-fees")

    async def test_v2_stated_card_conflicts_keep_draft_and_block_case_creation(self):
        intake = self.intake()
        inputs = insurance_v2.demo_inputs()
        inputs.update(member_id="MEM123", group_number="GROUP", payer_id="SYNTHETIC", relationship="18")
        inputs["subscriber"] = {"firstName": "PAT", "lastName": "EXAMPLE", "birthDate": "1990-01-01", "memberId": "MEM123"}
        inputs["patient"] = {"patient_first_name": "Pat", "patient_last_name": "Example", "patient_birth_date": "1990-01-01", "gender": "U"}
        practice = {"production_enabled": True, "payer_aliases": {"Synthetic payer": "SYNTHETIC"}}
        with patch.dict(os.environ, {"STAFF_INTEGRATION_PRACTICE_ID": intake["practice_id"]}), patch.object(insurance_v2, "configuration", return_value=(SimpleNamespace(environment="production"), practice, "synthetic-binding")), patch.object(insurance_v2.ecw_backend, "configured", return_value=True):
            for changes, source in (({"group_number": "OTHER-GROUP"}, {"payer": "Synthetic payer"}), ({"member_id": "OTHER-MEMBER"}, {"payer": "Synthetic payer"}), ({"payer_id": "OTHER"}, {"payer": "Other payer"})):
                with patch.object(insurance_v2.ecw_backend, "fetch_inputs", new=AsyncMock(return_value=({**inputs, **changes}, [], source))):
                    response = await self.client.post("/api/doctor/insurance/v2/cases", headers={"X-ClinicForce-Intake": intake["id"]}, json={})
                    self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(staff_store.get_intake(intake["id"], intake["practice_id"])["workflows"], {})
            with patch.object(insurance_v2.ecw_backend, "fetch_inputs", new=AsyncMock(return_value=(inputs, [], {"payer": "Synthetic payer"}))):
                response = await self.client.post("/api/doctor/insurance/v2/cases", headers={"X-ClinicForce-Intake": intake["id"]}, json={})
                self.assertEqual(response.status_code, 200, response.text)
        for confirmed in ({"subscriber_name": "Wrong Subscriber"}, {"subscriber_birth_date": "1970-01-01"}, {"payer": "Unknown payer"}):
            with self.assertRaises(Exception):
                insurance_v2.validate_staff_coverage(confirmed, inputs, {"payer": "Synthetic payer"}, practice)
        for confirmed in ({"payer": "SYNTHETIC"}, {"payer": "Synthetic payer"}, {"payer": "Approved alias"}):
            insurance_v2.validate_staff_coverage(confirmed, inputs, {"payer": "Synthetic payer"}, {"payer_aliases": {"Approved alias": "SYNTHETIC"}})
        insurance_v2.validate_staff_coverage({"relationship": "self"}, {**inputs, "relationship": "19"}, {"payer": "Synthetic payer"}, practice)
        insurance_v2.validate_staff_coverage({"payer": "Card payer"}, {**inputs, "payer_id": None}, {"payer": None}, practice)
        insurance_v2.validate_staff_coverage({"payer": "Card payer"}, {**inputs, "payer_id": None}, {"payer": "Unmapped eCW payer"}, practice)


if __name__ == "__main__":
    unittest.main()
