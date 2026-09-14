"""A submission can join a portal account only with its session and explicit consent."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.routers import patient_clinic_account as account
from app.api.settings import PATIENT_SESSION_COOKIE
from app.core.patient_models import PatientProfile


class PatientClinicAccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = PatientProfile("private-portal-id", "Sample", "Patient", "1980-01-01", None)
        self.fields = {"given_name": "Sample", "family_name": "Patient", "birth_date": "1980-01-01"}
        self.request = Request({"type": "http", "headers": [(b"cookie", f"{PATIENT_SESSION_COOKIE}=opaque".encode())]})
        self.session = patch.object(account, "get_patient_session", return_value="private-portal-id").start()
        self.get_profile = patch.object(account, "get_patient_profile", return_value=self.profile).start()
        self.addCleanup(patch.stopall)

    async def test_cookie_alone_never_links_account(self):
        self.assertIsNone(await account.portal_patient_for_upload(self.request, self.fields, False))
        self.session.assert_not_called()
        self.get_profile.assert_not_called()

    async def test_explicit_owner_consent_matches_demographics(self):
        self.assertEqual(await account.portal_patient_for_upload(self.request, {**self.fields, "given_name": " sample "}, True), "private-portal-id")
        for key, value in (("given_name", "Other"), ("family_name", "Different"), ("birth_date", "1981-01-01")):
            with self.subTest(key=key), self.assertRaises(HTTPException) as context:
                await account.portal_patient_for_upload(self.request, {**self.fields, key: value}, True)
            self.assertEqual(context.exception.status_code, 409)

    async def test_expired_or_missing_session_cannot_claim_account(self):
        self.session.return_value = None
        with self.assertRaises(HTTPException) as context:
            await account.portal_patient_for_upload(self.request, self.fields, True)
        self.assertEqual(context.exception.status_code, 401)
        self.get_profile.assert_not_called()

    def test_metadata_is_current_user_only_and_never_cached(self):
        app = FastAPI()
        app.include_router(account.router)
        with TestClient(app) as client:
            response = client.get("/api/insurance-upload/account")
            self.assertEqual(response.json(), {"available": False})
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.session.assert_not_called()
            client.cookies.set(PATIENT_SESSION_COOKIE, "opaque")
            response = client.get("/api/insurance-upload/account")
            self.assertEqual(response.json(), {"available": True, **self.fields})
            self.assertNotIn(self.profile.patient_id, response.text)
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")


if __name__ == "__main__":
    unittest.main()
