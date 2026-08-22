"""Tests for eClinicalWorks OEM endpoint construction and SMART PKCE launch."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from jwcrypto import jwk
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import ecw  # noqa: E402


class EcwConnectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.key = jwk.JWK.generate(kty="RSA", size=2048, kid="clinicforce-ecw-test-key")
        self.config = patch.multiple(
            ecw,
            ECW_CLIENT_ID="ecw-client-id-for-test",
            ECW_CLIENT_JWK=self.key.export_private(),
            ECW_OAUTH_STATE_SECRET="test-state-secret",
        )
        self.config.start()

    def tearDown(self) -> None:
        self.config.stop()

    def test_bedbaa_constructs_the_published_practice_specific_fhir_base(self) -> None:
        # BEDBAA intentionally contains letters: an OEM code is six characters,
        # not a six-digit numeric field.
        self.assertEqual(
            ecw.fhir_base_for_practice("bedbaa"),
            "https://fhir4.healow.com/fhir/r4/BEDBAA",
        )
        with self.assertRaises(ecw.EcwError):
            ecw.fhir_base_for_practice("https://untrusted.example")

    async def test_bedbaa_launch_uses_discovered_endpoints_and_s256_pkce(self) -> None:
        configuration = {
            "authorization_endpoint": "https://auth.eclinicalworks.example/authorize",
            "token_endpoint": "https://auth.eclinicalworks.example/token",
        }
        with patch("app.ingestion.ecw._smart_configuration", new=AsyncMock(return_value=configuration)) as discover:
            started = await ecw.begin_authorization(
                "BEDBAA", "clinicforce-patient", "https://app.clinicforce.ai/api/patient/ecw/callback", popup=True,
            )

        expected_base = "https://fhir4.healow.com/fhir/r4/BEDBAA"
        discover.assert_awaited_once_with(expected_base)
        parsed = urlsplit(started.authorization_url)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", configuration["authorization_endpoint"])
        self.assertEqual(query["aud"], [expected_base])
        self.assertEqual(query["redirect_uri"], ["https://app.clinicforce.ai/api/patient/ecw/callback"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertIn("openid", query["scope"][0].split())
        self.assertIn("launch/patient", query["scope"][0].split())
        self.assertIn("offline_access", query["scope"][0].split())
        self.assertNotIn("online_access", query["scope"][0].split())
        self.assertIn("patient/CareTeam.read", query["scope"][0].split())

        sealed = ecw._open_state(started.state_cookie)
        self.assertEqual(sealed["practice_code"], "BEDBAA")
        self.assertTrue(sealed["popup"])
        self.assertEqual(query["state"], [sealed["state"]])
        self.assertEqual(query["code_challenge"], [ecw.pkce_challenge(sealed["verifier"])])
        self.assertGreaterEqual(len(sealed["verifier"]), 64)

    async def test_confidential_token_exchange_uses_private_key_jwt_not_client_id(self) -> None:
        configuration = {
            "authorization_endpoint": "https://auth.eclinicalworks.example/authorize",
            "token_endpoint": "https://auth.eclinicalworks.example/token",
        }
        with patch("app.ingestion.ecw._smart_configuration", new=AsyncMock(return_value=configuration)):
            started = await ecw.begin_authorization(
                "BEDBAA", "clinicforce-patient", "https://app.clinicforce.ai/api/patient/ecw/callback",
            )
        state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]

        class TokenResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"access_token": "access-token", "patient": "remote-patient", "expires_in": 300}

        class TokenClient:
            request_data: dict[str, str]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args) -> None:
                return None

            async def post(self, _url: str, *, data: dict[str, str], headers: dict[str, str]) -> TokenResponse:
                self.request_data = data
                self.request_headers = headers
                return TokenResponse()

        client = TokenClient()
        with (
            patch("app.ingestion.ecw.httpx.AsyncClient", return_value=client),
            patch("app.ingestion.ecw._client_assertion", return_value="signed-client-assertion"),
        ):
            session_id, popup = await ecw.complete_authorization(
                started.state_cookie, state, "authorization-code",
                "https://app.clinicforce.ai/api/patient/ecw/callback", "clinicforce-patient",
            )

        self.assertFalse(popup)
        self.assertNotIn("client_id", client.request_data)
        self.assertEqual(client.request_data["client_assertion"], "signed-client-assertion")
        self.assertEqual(client.request_data["client_assertion_type"], "urn:ietf:params:oauth:client-assertion-type:jwt-bearer")
        ecw.clear_live_session(session_id)

    def test_public_jwks_never_contains_private_key_material(self) -> None:
        payload = ecw.public_jwks()
        self.assertEqual(payload["keys"][0]["kid"], "clinicforce-ecw-test-key")
        self.assertNotIn("d", payload["keys"][0])
        self.assertNotIn("p", payload["keys"][0])
        self.assertNotIn("q", payload["keys"][0])

    def test_kms_backed_jwks_and_client_assertion_use_the_non_exportable_key(self) -> None:
        public_der = self.key.export_to_pem(private_key=False, password=None)
        # jwcrypto emits PEM; cryptography gives the exact DER format KMS uses.
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        der = load_pem_public_key(public_der).public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)

        class Kms:
            def get_public_key(self, **kwargs):
                self.get_public_key_kwargs = kwargs
                return {"PublicKey": der}

            def sign(self, **kwargs):
                self.sign_kwargs = kwargs
                return {"Signature": b"kms-signature"}

        kms = Kms()
        with (
            patch.object(ecw, "ECW_KMS_KEY_ID", "12345678-1234-1234-1234-123456789012"),
            patch("app.ingestion.ecw.boto3.client", return_value=kms),
        ):
            jwks = ecw.public_jwks()
            assertion = ecw._client_assertion("https://auth.eclinicalworks.example/token")
        self.assertEqual(jwks["keys"][0]["kid"], "12345678-1234-1234-1234-123456789012")
        self.assertEqual(jwks["keys"][0]["alg"], "RS384")
        self.assertEqual(kms.get_public_key_kwargs["KeyId"], "12345678-1234-1234-1234-123456789012")
        self.assertEqual(kms.sign_kwargs["SigningAlgorithm"], "RSASSA_PKCS1_V1_5_SHA_384")
        self.assertTrue(assertion.endswith(".a21zLXNpZ25hdHVyZQ"))

    async def test_fetch_record_marks_denied_scopes_without_treating_them_as_empty(self) -> None:
        session = ecw.LiveEcwSession(
            patient_id="patient-1", practice_code="BEDBAA", fhir_base="https://fhir.example/FHIRServer/fhir/BEDBAA",
            remote_patient_id="remote-1", access_token="test-token", expires_at=time.time() + 300,
        )

        async def fake_fetch(_session, resource_type):
            if resource_type == "Patient":
                return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {"resourceType": "Patient", "id": "remote-1"}}]}
            if resource_type == "Condition":
                return {"resourceType": "Bundle", "type": "collection", "entry": [], "extension": [{"url": "urn:clinicforce:ecw-status", "valueString": "forbidden"}]}
            return {"resourceType": "Bundle", "type": "collection", "entry": []}

        with patch("app.ingestion.ecw.fetch_resource_type", side_effect=fake_fetch):
            bundle, availability = await ecw.fetch_record(session)
        self.assertEqual(len(bundle["entry"]), 1)
        self.assertEqual(availability["Condition"], "forbidden")
        self.assertEqual(availability["Observation"], "empty")
        self.assertEqual(session.review_bundle, bundle)

    def test_expired_live_session_is_removed_and_requires_reconnect(self) -> None:
        ecw._live_sessions["expired"] = ecw.LiveEcwSession(
            patient_id="patient-1", practice_code="BEDBAA", fhir_base="https://fhir.example/FHIRServer/fhir/BEDBAA",
            remote_patient_id="remote-1", access_token="test-token", expires_at=time.time() - 1,
        )
        with self.assertRaisesRegex(ecw.EcwError, "session has ended"):
            ecw.get_live_session("expired", "patient-1")
        self.assertNotIn("expired", ecw._live_sessions)
