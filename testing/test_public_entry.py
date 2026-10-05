"""Private redirects preserve status codes and never consume an invitation."""

import logging
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import public_entry


TOKEN = "s" * 43
NPI = "1234567893"


class PublicEntryTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(public_entry.router)
        self.client = TestClient(app, follow_redirects=False)

    def test_existing_clinic_redirects_without_cache_or_referrer(self):
        with patch.object(public_entry.clinic_store, "get_public_clinic", return_value={"name": "Clinic"}):
            result = self.client.get("/" + NPI)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/?clinic=" + NPI)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["referrer-policy"], "no-referrer")

    def test_upload_and_invitation_redirects_are_read_only(self):
        with patch.object(public_entry.intake_links_store, "metadata", return_value={"status": "pending"}) as metadata:
            result = self.client.get("/" + NPI + "/" + TOKEN)
            metadata.assert_called_once_with(NPI, TOKEN)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers["location"], "/?clinic=" + NPI + "&upload=" + TOKEN)
        with patch.object(public_entry.clinic_store, "get_invitation", return_value={"accepted_at": "earlier"}):
            result = self.client.get("/join/" + TOKEN)
        self.assertEqual(result.headers["location"], "/?join=" + TOKEN)

    def test_unknown_reserved_and_malformed_links_remain_404(self):
        with patch.object(public_entry.clinic_store, "get_public_clinic", return_value=None):
            self.assertEqual(self.client.get("/" + NPI).status_code, 404)
        with patch.object(public_entry.clinic_store, "get_public_clinic") as clinic:
            for path in ("/api", "/assets/file.js", "/manifest/" + TOKEN, "/join/short", "/" + NPI + "/short"):
                self.assertEqual(self.client.get(path).status_code, 404)
            clinic.assert_not_called()
        with patch.object(public_entry.intake_links_store, "metadata", side_effect=public_entry.intake_links_store.UploadLinkError("missing", 404)):
            self.assertEqual(self.client.get("/" + NPI + "/" + TOKEN).status_code, 404)

    def test_expired_upload_redirects_to_form_error(self):
        with patch.object(public_entry.intake_links_store, "metadata", side_effect=public_entry.intake_links_store.UploadLinkError("expired", 410)):
            result = self.client.get("/" + NPI + "/" + TOKEN)
        self.assertEqual(result.status_code, 303)

    def test_access_filter_redacts_only_private_link_paths_and_query_values(self):
        filter_ = public_entry.PrivateLinkAccessFilter()
        for target in ("/join/" + TOKEN, "/" + NPI + "/" + TOKEN, "/?clinic=" + NPI + "&upload=" + TOKEN, "/?join=" + TOKEN,
                       "/api/insurance-upload/" + NPI + "/" + TOKEN, "/api/staff/invitations/" + TOKEN,
                       "/manifest/" + TOKEN, "/manifest/short?key=" + TOKEN):
            record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("client", "GET", target, "1.1", 303), None)
            self.assertTrue(filter_.filter(record))
            self.assertNotIn(TOKEN, record.getMessage())
            self.assertIn("303", record.getMessage())
        for target in ("/api/staff/session", "/assets/main.js?v=1", "/api/insurance/cases/abc?version=2", "/?ordinary=value"):
            self.assertEqual(public_entry._redacted_target(target), target)


if __name__ == "__main__":
    unittest.main()
