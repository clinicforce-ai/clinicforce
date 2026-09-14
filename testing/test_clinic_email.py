"""No network calls: exercise acceptance, failures, and private-link validation."""

import os
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError, EndpointConnectionError

from app.core import clinic_email


class ClinicEmailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CLINIC_EMAIL_SENDER": "clinic@example.com", "PUBLIC_BASE_URL": "https://app.example.com"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = patch.object(clinic_email.boto3, "client")
        self.factory = self.client.start()
        self.addCleanup(self.client.stop)
        self.arguments = {"clinic_name": "Example Clinic", "link_url": "https://app.example.com/example/private-test-token"}

    def test_unconfigured_returns_fallback_without_aws(self):
        with patch.dict(os.environ, {"CLINIC_EMAIL_SENDER": ""}):
            self.assertFalse(clinic_email.configured())
            self.assertEqual(clinic_email.configuration()["provider"], None)
            self.assertFalse(clinic_email.configuration()["available"])
            result = clinic_email.send_link("patient@example.net", **self.arguments)
        self.assertEqual(result["status"], "unavailable")
        self.factory.assert_not_called()

    def test_provider_acceptance_is_not_delivery_confirmation(self):
        self.factory.return_value.send_email.return_value = {"MessageId": "provider-id"}
        result = clinic_email.send_link("patient@example.net", **self.arguments)
        self.assertEqual(result, {"status": "sent", "message_id": "provider-id", "detail": "Email accepted for sending."})
        arguments = self.factory.return_value.send_email.call_args.kwargs
        self.assertEqual(arguments["Destination"], {"ToAddresses": ["patient@example.net"]})
        self.assertEqual(arguments["Content"]["Simple"]["Body"]["Text"]["Data"], clinic_email.link_message(**self.arguments)["body"])
        self.assertNotIn("Html", arguments["Content"]["Simple"]["Body"])
        self.assertEqual(self.factory.call_args.kwargs["config"].retries, {"total_max_attempts": 1})

    def test_provider_errors_and_ambiguous_responses_are_generic(self):
        for error in (ClientError({"Error": {"Code": "MessageRejected", "Message": "private patient@example.net and token"}}, "SendEmail"),
                      EndpointConnectionError(endpoint_url="https://private-token.example.net")):
            with self.subTest(error=type(error).__name__):
                self.factory.return_value.send_email.side_effect = error
                result = clinic_email.send_link("patient@example.net", **self.arguments)
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("private", str(result))
                self.assertNotIn("patient@example.net", str(result))
        self.factory.return_value.send_email.side_effect = None
        self.factory.return_value.send_email.return_value = {}
        self.assertEqual(clinic_email.send_link("patient@example.net", **self.arguments)["status"], "failed")

    def test_invalid_recipient_or_foreign_link_never_reaches_provider(self):
        for recipient in ("", "a@example.net,b@example.net", "Person <a@example.net>", "a@example.net\r\nBcc:x@example.net", "a\x00@example.net", "a"):
            with self.subTest(recipient=recipient), self.assertRaises(ValueError):
                clinic_email.send_link(recipient, **self.arguments)
        for link in ("https://outside.example.net/token", "https://user@app.example.com/token", "http://app.example.com/token", "https://app.example.com/a\nb"):
            with self.subTest(link=link), self.assertRaises(ValueError):
                clinic_email.send_link("a@example.net", clinic_name="Clinic", link_url=link)
        self.factory.assert_not_called()

    def test_staff_invitation_is_a_separate_plain_text_message(self):
        message = clinic_email.link_message(**self.arguments, purpose="staff_invite")
        self.assertIn("Create your staff account", message["body"])
        self.assertNotIn("insurance card", message["body"])
        with self.assertRaises(ValueError):
            clinic_email.link_message(**self.arguments, purpose="arbitrary_message")


if __name__ == "__main__":
    unittest.main()
