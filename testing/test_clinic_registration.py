"""Real PostgreSQL checks for clinic isolation and staff invitation capabilities."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from fastapi import FastAPI
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api.routers import staff, staff_registration as registration
from app.core import clinic_email, clinic_store, patient_store, staff_store


@unittest.skipUnless(os.getenv("STAFF_TEST_DATABASE_URL"), "Set STAFF_TEST_DATABASE_URL to a disposable PostgreSQL database")
class ClinicRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = os.environ["STAFF_TEST_DATABASE_URL"]
        self.schema = "clinic_registration_" + uuid4().hex
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.patches = [
            patch.object(patient_store, "DATABASE_URL", make_conninfo(self.database, options=f"-csearch_path={self.schema}")),
            patch.object(patient_store, "_schema_initialized", False), patch.object(staff_store, "_schema_initialized", False),
            patch.object(staff_store, "_bootstrap_initialized", False), patch.object(clinic_store, "_schema_initialized", False),
            patch.object(staff, "PUBLIC_BASE_URL", "http://localhost"), patch.object(staff, "ALLOWED_ORIGINS", ["http://localhost"]),
            patch.object(registration, "PUBLIC_BASE_URL", "http://localhost"),
            patch.dict(os.environ, {"PUBLIC_BASE_URL": "http://localhost", "CLINIC_EMAIL_SENDER": "", "STAFF_BOOTSTRAP_CREDENTIALS": json.dumps({
                "username": "legacy-admin", "password": "synthetic-bootstrap-password", "practice_id": "primary",
                "practice_name": "Existing clinic", "display_name": "Existing administrator"})}),
        ]
        for item in self.patches:
            item.start()
        self.app = FastAPI(); self.app.include_router(staff.router); self.app.include_router(registration.router)
        self.client = self.new_client()

    def new_client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost", headers={"Origin": "http://localhost"})

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def account(self, **changes):
        return {"clinic_name": "Synthetic Clinic", "clinic_code": "synthetic-clinic", "display_name": "Synthetic Admin",
                "email": "admin@example.test", "password": " synthetic-admin-password ", **changes}

    async def register(self):
        response = await self.client.post("/api/staff/register", json=self.account())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["staff"]

    async def invite(self, role="staff"):
        response = await self.client.post("/api/staff/invitations", json={"role": role})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        return body, urlsplit(body["url"]).path.split("/")[-1]

    async def test_registration_creates_only_new_clinic_preserves_password_and_reserves_existing_code(self):
        member = await self.register()
        self.assertNotEqual(member["practice_id"], "primary")
        self.assertEqual(member["role"], "admin")
        clinic = (await self.client.get("/api/staff/clinic")).json()
        self.assertEqual(clinic["code"], "synthetic-clinic")
        self.assertEqual(clinic["upload_url"], "http://localhost/synthetic-clinic")
        self.assertEqual(clinic_store.get_clinic("primary")["id"], "primary")
        with staff_store._connection() as connection:
            row = connection.execute("SELECT password_hash FROM staff_members WHERE id=%s", (member["id"],)).fetchone()
            self.assertTrue(staff_store.verify_password(self.account()["password"], row["password_hash"]))
            self.assertFalse(staff_store.verify_password(self.account()["password"].strip(), row["password_hash"]))
        await self.client.post("/api/staff/logout")
        response = await self.client.post("/api/staff/login", json={"username": self.account()["email"], "password": self.account()["password"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        collision = await self.client.post("/api/staff/register", json=self.account(email="new@example.test", clinic_code="primary"))
        self.assertEqual(collision.status_code, 409)
        with staff_store._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS count FROM staff_members WHERE username='new@example.test'").fetchone()["count"], 0)

    async def test_registration_rejects_forged_membership_reserved_code_and_cross_origin(self):
        for changes in ({"practice_id": "primary"}, {"role": "admin"}, {"clinic_code": "api"}, {"clinic_code": "../primary"}, {"email": "invalid"}, {"clinic_name": "\n\n"}):
            self.assertEqual((await self.client.post("/api/staff/register", json=self.account(**changes))).status_code, 422)
        self.assertEqual((await self.client.post("/api/staff/register", json=self.account(), headers={"Origin": "https://unrelated.invalid"})).status_code, 403)
        with patch.object(clinic_store, "allow_action", return_value=False):
            self.assertEqual((await self.client.post("/api/staff/register", json=self.account())).status_code, 429)

    async def test_staff_invitation_is_single_use_and_cannot_self_promote_or_cross_practice(self):
        admin = await self.register(); invite, token = await self.invite()
        async with self.new_client() as guest:
            response = await guest.get(f"/api/staff/invitations/{token}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"clinic_name", "expires_at", "role"})
            details = {"display_name": "Invited Staff", "email": "staff@example.test", "password": "synthetic-invited-password"}
            self.assertEqual((await guest.post(f"/api/staff/invitations/{token}", json={**details, "role": "admin"})).status_code, 422)
            response = await guest.post(f"/api/staff/invitations/{token}", json=details)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["staff"]["practice_id"], admin["practice_id"])
            self.assertEqual(response.json()["staff"]["role"], "staff")
            self.assertEqual((await guest.post("/api/staff/invitations", json={})).status_code, 403)
            self.assertEqual((await guest.get(f"/api/staff/invitations/{token}")).status_code, 410)
            self.assertEqual((await guest.post(f"/api/staff/invitations/{token}", json={**details, "email": "second@example.test"})).status_code, 409)
        rows = (await self.client.get("/api/staff/invitations")).json()["invitations"]
        self.assertEqual(rows[0]["status"], "accepted")
        self.assertNotIn(token, json.dumps(rows))
        async with self.new_client() as other:
            self.assertEqual((await other.post("/api/staff/register", json=self.account(email="other@example.test", clinic_code="other-clinic"))).status_code, 201)
            self.assertEqual((await other.get("/api/staff/invitations")).json(), {"invitations": []})
            self.assertEqual((await other.post(f"/api/staff/invitations/{invite['id']}/revoke")).status_code, 404)

    async def test_revoked_expired_and_racing_invitation_acceptance(self):
        await self.register(); invitation, token = await self.invite()
        self.assertEqual((await self.client.post(f"/api/staff/invitations/{invitation['id']}/revoke")).status_code, 204)
        self.assertEqual((await self.client.get(f"/api/staff/invitations/{token}")).status_code, 410)
        expired, expired_token = await self.invite()
        with clinic_store._connection() as connection:
            connection.execute("UPDATE staff_invitations SET expires_at='2000-01-01' WHERE id=%s", (expired["id"],))
        self.assertEqual((await self.client.get(f"/api/staff/invitations/{expired_token}")).status_code, 410)
        _, token = await self.invite()
        async with self.new_client() as one, self.new_client() as two:
            requests = [client.post(f"/api/staff/invitations/{token}", json={"display_name": "Synthetic staff", "email": f"race{index}@example.test", "password": "synthetic-racing-password"}) for index, client in enumerate((one, two))]
            responses = await asyncio.gather(*requests)
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])

    async def test_email_requires_owned_live_link_and_reports_provider_acceptance_only(self):
        await self.register(); invitation, token = await self.invite()
        with patch.object(clinic_email, "send_link", return_value={"status": "sent", "detail": "Email accepted for sending."}) as send:
            response = await self.client.post(f"/api/staff/invitations/{invitation['id']}/email", json={"email": "staff@example.test", "url": invitation["url"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "sent")
            self.assertEqual(send.call_count, 1)
            for wrong in ("https://[", invitation["url"].replace("localhost", "unrelated.invalid"), invitation["url"] + "?redirect=bad", invitation["url"].replace(token, "x" * 43)):
                self.assertEqual((await self.client.post(f"/api/staff/invitations/{invitation['id']}/email", json={"email": "staff@example.test", "url": wrong})).status_code, 404)
            self.assertEqual(send.call_count, 1)
        self.assertFalse((await self.client.get("/api/staff/email/configuration")).json()["available"])
        response = await self.client.post(f"/api/staff/invitations/{invitation['id']}/email", json={"email": "staff@example.test", "url": invitation["url"]})
        self.assertEqual(response.json()["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
