"""Focused security and route-contract tests for ID.me OIDC."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
from jwcrypto import jwk, jwt
from psycopg import sql
from psycopg.conninfo import make_conninfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routers import idme as idme_routes  # noqa: E402
from app.api.routers.public_entry import _redacted_target  # noqa: E402
from app.core import idme, patient_store  # noqa: E402
from app.core.patient_models import (  # noqa: E402
    IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS,
    StoredIdmeTransaction,
)
from app.main import app  # noqa: E402


CONFIG = {
    "IDME_ENABLED": "true",
    "IDME_ENVIRONMENT": "sandbox",
    "IDME_CLIENT_ID": "clinicforce-client",
    "IDME_CLIENT_SECRET": "test-secret",
    "IDME_OIDC_DISCOVERY_URL": "https://api.idmelabs.com/oidc/.well-known/openid-configuration",
    "PUBLIC_BASE_URL": "https://app.clinicforce.ai",
    "IDME_REDIRECT_URI": "https://app.clinicforce.ai/api/patient/idme/callback",
    "IDME_SCOPE": "openid clinicforce-test-policy",
}
DISCOVERY = {
    "issuer": "https://api.idmelabs.com/oidc",
    "authorization_endpoint": "https://api.idmelabs.com/oauth/authorize",
    "token_endpoint": "https://api.idmelabs.com/oauth/token",
    "jwks_uri": "https://api.idmelabs.com/oidc/.well-known/jwks",
    "id_token_signing_alg_values_supported": ["RS256", "ES256"],
}


class IdmeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        idme._discovery_cache.clear()

    async def test_configuration_is_allowlisted_and_requires_https_callback(self) -> None:
        with patch.dict(os.environ, CONFIG, clear=False):
            self.assertTrue(idme.configured())
            self.assertTrue(idme.enabled())
        with patch.dict(os.environ, {**CONFIG, "IDME_OIDC_DISCOVERY_URL": "https://attacker.example/oidc"}, clear=False):
            self.assertFalse(idme.configured())
        with patch.dict(os.environ, {**CONFIG, "IDME_REDIRECT_URI": "http://app.clinicforce.ai/callback"}, clear=False):
            self.assertFalse(idme.configured())
        with patch.dict(os.environ, {**CONFIG, "IDME_SCOPE": "openid"}, clear=False):
            self.assertFalse(idme.configured())
        with patch.dict(os.environ, {**CONFIG, "IDME_SCOPE": "openid openid"}, clear=False):
            self.assertFalse(idme.configured())
        with patch.dict(
            os.environ,
            {**CONFIG, "IDME_SCOPE": "openid clinicforce-test-policy clinicforce-test-policy"},
            clear=False,
        ):
            self.assertFalse(idme.configured())
        with patch.dict(os.environ, {**CONFIG, "IDME_ENVIRONMENT": "production"}, clear=False):
            self.assertFalse(idme.configured())
        with patch.dict(
            os.environ,
            {**CONFIG, "IDME_REDIRECT_URI": "https://app.clinicforce.ai/a/different/callback"},
            clear=False,
        ):
            self.assertFalse(idme.configured())

    async def test_authorization_uses_nonce_state_and_s256_pkce(self) -> None:
        get_json = AsyncMock(return_value=DISCOVERY)
        with patch.dict(os.environ, CONFIG, clear=False), patch(
            "app.core.idme._get_json", new=get_json
        ):
            started = await idme.begin_authorization()
            await idme.begin_authorization()
        parsed = urlsplit(started.authorization_url)
        query = parse_qs(parsed.query)
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", DISCOVERY["authorization_endpoint"])
        self.assertEqual(query["state"], [started.state])
        self.assertEqual(query["nonce"], [started.nonce])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotEqual(query["code_challenge"], [started.pkce_verifier])
        self.assertNotEqual(started.browser_token, started.state)
        self.assertEqual(get_json.await_count, 1)

    async def test_discovery_cannot_redirect_tokens_or_select_an_unsupported_client_auth_method(self) -> None:
        with patch.dict(os.environ, CONFIG, clear=False):
            for metadata in (
                {**DISCOVERY, "token_endpoint": "https://attacker.example/token"},
                {**DISCOVERY, "token_endpoint_auth_methods_supported": ["private_key_jwt"]},
            ):
                with self.subTest(metadata=metadata), patch(
                    "app.core.idme._get_json", new=AsyncMock(return_value=metadata)
                ), self.assertRaises(idme.IdmeError):
                    await idme.begin_authorization()

    async def test_token_exchange_rejects_a_downgraded_or_missing_policy_scope(self) -> None:
        metadata = {
            "authorization_endpoint": DISCOVERY["authorization_endpoint"],
            "token_endpoint": DISCOVERY["token_endpoint"],
            "jwks_uri": DISCOVERY["jwks_uri"],
        }
        with patch.dict(os.environ, CONFIG, clear=False), patch.object(
            idme, "_discovery", new=AsyncMock(return_value=metadata)
        ), patch.object(idme, "_get_json", new=AsyncMock()) as get_json:
            for token_response in (
                {"id_token": "header.payload.signature", "scope": "openid"},
                {"id_token": "header.payload.signature"},
            ):
                with self.subTest(token_response=token_response), patch.object(
                    idme, "_post_form", new=AsyncMock(return_value=token_response)
                ), self.assertRaises(idme.IdmeError):
                    await idme.complete_authorization("code", "verifier", "nonce")
        get_json.assert_not_awaited()

    def test_documented_token_scope_shape_accepts_the_policy_without_openid(self) -> None:
        config = idme.IdmeConfig(
            "clinicforce-client",
            "secret",
            CONFIG["IDME_OIDC_DISCOVERY_URL"],
            CONFIG["IDME_REDIRECT_URI"],
            CONFIG["IDME_SCOPE"],
            DISCOVERY["issuer"],
        )
        idme._validate_granted_policy_scope(
            {"scope": "clinicforce-test-policy"},
            config,
        )

    def test_id_token_requires_rs256_signature_issuer_audience_time_and_nonce(self) -> None:
        private_key = jwk.JWK.generate(kty="RSA", size=2048)
        private_key.update(kid="idme-test-key", use="sig", alg="RS256")
        public_key = json.loads(private_key.export_public())
        config = idme.IdmeConfig(
            "clinicforce-client",
            "secret",
            CONFIG["IDME_OIDC_DISCOVERY_URL"],
            CONFIG["IDME_REDIRECT_URI"],
            CONFIG["IDME_SCOPE"],
            DISCOVERY["issuer"],
        )

        def signed(**overrides: object) -> str:
            claims = {
                "iss": DISCOVERY["issuer"],
                "sub": "stable-idme-subject",
                "aud": "clinicforce-client",
                "iat": 950,
                "exp": 1_050,
                "nonce": "expected-nonce",
                **overrides,
            }
            token = jwt.JWT(
                header={"alg": "RS256", "kid": "idme-test-key", "typ": "JWT"},
                claims=claims,
            )
            token.make_signed_token(private_key)
            return token.serialize()

        with patch("app.core.idme.time.time", return_value=1_000):
            identity = idme._verify_id_token(signed(), {"keys": [public_key]}, config, "expected-nonce")
            self.assertEqual(identity, idme.VerifiedIdentity(DISCOVERY["issuer"], "stable-idme-subject"))
            for overrides in (
                {"iss": "https://api.id.me/oidc"},
                {"aud": "another-client"},
                {"azp": "another-client"},
                {"iat": 1_100},
                {"exp": 999},
                {"nonce": "replayed-nonce"},
                {"nbf": 1_100},
            ):
                with self.subTest(overrides=overrides), self.assertRaises(idme.IdmeError):
                    idme._verify_id_token(signed(**overrides), {"keys": [public_key]}, config, "expected-nonce")
            wrong_key = jwk.JWK.generate(kty="RSA", size=2048)
            wrong_key.update(kid="idme-test-key", use="sig", alg="RS256")
            with self.assertRaises(idme.IdmeError):
                idme._verify_id_token(
                    signed(), {"keys": [json.loads(wrong_key.export_public())]}, config, "expected-nonce"
                )

    def test_callback_query_is_completely_removed_from_access_log_target(self) -> None:
        target = "/api/patient/idme/callback?code=secret-code&state=secret-state&error_description=private"
        self.assertEqual(_redacted_target(target), "/api/patient/idme/callback")
        self.assertEqual(
            _redacted_target("/api/patient/idme/callback/?code=secret-code"),
            "/api/patient/idme/callback/",
        )


class IdmeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", follow_redirects=False
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_status_is_public_minimal_and_not_cached(self) -> None:
        with (
            patch.object(idme_routes.idme_client, "enabled", return_value=True),
            patch.object(idme_routes.idme_client, "configured", return_value=True),
        ):
            response = await self.client.get("/api/patient/idme/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"enabled": True, "configured": True, "linked": False, "auth_recent": False},
        )
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_status_reports_link_and_recent_auth_without_exposing_identity(self) -> None:
        with (
            patch.object(idme_routes.idme_client, "enabled", return_value=True),
            patch.object(idme_routes.idme_client, "configured", return_value=True),
            patch.object(idme_routes, "get_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "get_recent_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "patient_has_idme_identity", return_value=True),
        ):
            self.client.cookies.set("clinicforce_patient_session", "current-session")
            response = await self.client.get("/api/patient/idme/status")
        self.assertEqual(
            response.json(),
            {"enabled": True, "configured": True, "linked": True, "auth_recent": True},
        )

    async def test_start_persists_server_transaction_and_sets_only_browser_binding_cookie(self) -> None:
        started = idme.StartedAuthorization(
            "https://api.idmelabs.com/oauth/authorize?state=provider-state",
            "provider-state",
            "browser-binding",
            "provider-nonce",
            "pkce-verifier",
        )
        with (
            patch.object(idme_routes.idme_client, "enabled", return_value=True),
            patch.object(idme_routes.idme_client, "begin_authorization", new=AsyncMock(return_value=started)),
            patch.object(idme_routes.clinic_store, "allow_action", return_value=True),
            patch.object(idme_routes, "save_idme_oidc_transaction") as save,
        ):
            response = await self.client.post("/api/patient/idme/start", json={"intent": "login"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"authorization_url": started.authorization_url})
        save.assert_called_once_with(
            "login", None, "provider-state", "browser-binding", "provider-nonce", "pkce-verifier"
        )
        cookie = response.headers["set-cookie"]
        self.assertIn("clinicforce_idme_oauth=browser-binding", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Path=/api/patient/idme/callback", cookie)
        self.assertIn(f"Max-Age={IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS}", cookie)
        self.assertNotIn("provider-state", cookie)
        self.assertNotIn("pkce-verifier", response.text)

    async def test_start_is_rate_limited_before_contacting_idme(self) -> None:
        with (
            patch.object(idme_routes.idme_client, "enabled", return_value=True),
            patch.object(idme_routes.clinic_store, "allow_action", return_value=False),
            patch.object(idme_routes.idme_client, "begin_authorization", new=AsyncMock()) as begin,
        ):
            response = await self.client.post("/api/patient/idme/start", json={"intent": "login"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "900")
        begin.assert_not_awaited()

    async def test_link_start_requires_the_existing_patient_session(self) -> None:
        with patch.object(idme_routes.idme_client, "enabled", return_value=True):
            response = await self.client.post("/api/patient/idme/start", json={"intent": "link"})
        self.assertEqual(response.status_code, 401)

    async def test_link_start_requires_recent_authentication(self) -> None:
        with (
            patch.object(idme_routes.idme_client, "enabled", return_value=True),
            patch.object(idme_routes, "get_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "get_recent_patient_session", return_value=None),
        ):
            self.client.cookies.set("clinicforce_patient_session", "old-session")
            response = await self.client.post(
                "/api/patient/idme/start",
                json={"intent": "link"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Sign in again before linking ID.me"})

    async def test_login_callback_creates_session_for_an_explicitly_linked_identity(self) -> None:
        transaction = StoredIdmeTransaction("login", None, "provider-nonce", "pkce-verifier")
        identity = idme.VerifiedIdentity(DISCOVERY["issuer"], "stable-subject")
        with (
            patch.object(idme_routes, "consume_idme_oidc_transaction", return_value=transaction),
            patch.object(idme_routes.idme_client, "complete_authorization", new=AsyncMock(return_value=identity)),
            patch.object(
                idme_routes,
                "create_idme_patient_session",
                return_value=("linked-local-id", "patient-session"),
            ) as authenticate,
        ):
            self.client.cookies.set("clinicforce_idme_oauth", "browser-binding")
            response = await self.client.get("/api/patient/idme/callback?state=provider-state&code=authorization-code")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:8000/?patient&idme=connected")
        self.assertIn("clinicforce_patient_session=patient-session", response.headers.get_list("set-cookie")[-1])
        authenticate.assert_called_once_with(DISCOVERY["issuer"], "stable-subject")
        self.assertNotIn("authorization-code", response.text)
        self.assertNotIn("stable-subject", response.text)

    async def test_login_callback_does_not_create_an_unlinked_patient_account(self) -> None:
        transaction = StoredIdmeTransaction("login", None, "provider-nonce", "pkce-verifier")
        identity = idme.VerifiedIdentity(DISCOVERY["issuer"], "unlinked-subject")
        with (
            patch.object(idme_routes, "consume_idme_oidc_transaction", return_value=transaction),
            patch.object(idme_routes.idme_client, "complete_authorization", new=AsyncMock(return_value=identity)),
            patch.object(idme_routes, "create_idme_patient_session", return_value=None),
            patch.object(idme_routes, "record_idme_verification_event") as audit,
        ):
            self.client.cookies.set("clinicforce_idme_oauth", "browser-binding")
            response = await self.client.get(
                "/api/patient/idme/callback?state=provider-state&code=authorization-code"
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:8000/?patient&idme=not-linked")
        audit.assert_called_once_with(None, "login", "failure", "identity_not_linked")

    async def test_link_callback_never_merges_an_identity_owned_by_another_account(self) -> None:
        transaction = StoredIdmeTransaction("link", "patient-1", "provider-nonce", "pkce-verifier")
        identity = idme.VerifiedIdentity(DISCOVERY["issuer"], "already-owned-subject")
        with (
            patch.object(idme_routes, "consume_idme_oidc_transaction", return_value=transaction),
            patch.object(idme_routes.idme_client, "complete_authorization", new=AsyncMock(return_value=identity)),
            patch.object(idme_routes, "link_idme_identity", return_value=False) as link,
        ):
            self.client.cookies.set("clinicforce_idme_oauth", "browser-binding")
            self.client.cookies.set("clinicforce_patient_session", "patient-session")
            response = await self.client.get("/api/patient/idme/callback?state=provider-state&code=authorization-code")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:8000/?patient&idme=error")
        link.assert_called_once_with(
            "patient-1", "patient-session", DISCOVERY["issuer"], "already-owned-subject"
        )

    async def test_unlink_refuses_to_remove_the_only_login(self) -> None:
        with (
            patch.object(idme_routes, "get_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "get_recent_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "unlink_idme_identity", return_value="last_login"),
        ):
            self.client.cookies.set("clinicforce_patient_session", "patient-session")
            response = await self.client.delete("/api/patient/idme/link")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"detail": "Add another sign-in method before disconnecting ID.me"})

    async def test_unlink_requires_recent_authentication(self) -> None:
        with (
            patch.object(idme_routes, "get_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "get_recent_patient_session", return_value=None),
            patch.object(idme_routes, "unlink_idme_identity") as unlink,
        ):
            self.client.cookies.set("clinicforce_patient_session", "old-session")
            response = await self.client.delete("/api/patient/idme/link")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Sign in again before disconnecting ID.me"})
        unlink.assert_not_called()

    async def test_unlink_revokes_sessions_and_clears_the_current_cookie(self) -> None:
        with (
            patch.object(idme_routes, "get_patient_session", return_value="patient-1"),
            patch.object(idme_routes, "get_recent_patient_session", return_value="patient-1"),
            patch.object(idme_routes.google_drive, "enabled", return_value=True),
            patch.object(idme_routes, "unlink_idme_identity", return_value="unlinked") as unlink,
        ):
            self.client.cookies.set("clinicforce_patient_session", "current-session")
            response = await self.client.delete("/api/patient/idme/link")
        self.assertEqual(response.status_code, 204)
        unlink.assert_called_once_with("patient-1", True)
        self.assertIn("clinicforce_patient_session=", response.headers["set-cookie"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])


@unittest.skipUnless(
    os.getenv("STAFF_TEST_DATABASE_URL"),
    "Set STAFF_TEST_DATABASE_URL to a disposable PostgreSQL database",
)
class IdmeStoreTests(unittest.TestCase):
    """Exercise the one-time transaction and exact-identity invariants in PostgreSQL."""

    def setUp(self) -> None:
        database = os.environ["STAFF_TEST_DATABASE_URL"]
        self.database = database
        self.schema = "idme_oidc_" + os.urandom(12).hex()
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.patches = [
            patch.object(
                patient_store,
                "DATABASE_URL",
                make_conninfo(database, options=f"-csearch_path={self.schema}"),
            ),
            patch.object(patient_store, "_schema_initialized", False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        with psycopg.connect(self.database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_migration_marks_legacy_google_placeholder_password_unusable(self) -> None:
        legacy_url = make_conninfo(self.database, options=f"-csearch_path={self.schema}")
        google_subject = "legacy-google-subject"
        google_patient_id = "google-" + hashlib.sha256(google_subject.encode()).hexdigest()[:20]
        with psycopg.connect(legacy_url) as connection:
            connection.execute(
                """CREATE TABLE patients (
                    patient_id TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, storage_preference TEXT NOT NULL DEFAULT 's3'
                )"""
            )
            connection.execute(
                """CREATE TABLE patient_drive_connections (
                    patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id),
                    google_subject TEXT NOT NULL, refresh_token_ciphertext TEXT NOT NULL,
                    folder_id TEXT NOT NULL, summary_file_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            stamp = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO patients(patient_id,password_hash,created_at) VALUES (%s,%s,%s)",
                (google_patient_id, "unknown-random-placeholder", stamp),
            )
            connection.execute(
                """INSERT INTO patient_drive_connections
                   (patient_id,google_subject,refresh_token_ciphertext,folder_id,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (google_patient_id, google_subject, "ciphertext", "folder", stamp, stamp),
            )
            connection.execute(
                "INSERT INTO patients(patient_id,password_hash,created_at) VALUES (%s,%s,%s)",
                ("google-alice", "real-local-password-hash", stamp),
            )
            connection.execute(
                """INSERT INTO patient_drive_connections
                   (patient_id,google_subject,refresh_token_ciphertext,folder_id,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                ("google-alice", "different-subject", "ciphertext", "folder-2", stamp, stamp),
            )

        self.assertIsNone(patient_store.get_password_hash(google_patient_id))
        self.assertEqual(patient_store.get_password_hash("google-alice"), "real-local-password-hash")
        with patient_store._connection() as connection:
            enabled = connection.execute(
                "SELECT password_login_enabled FROM patients WHERE patient_id = %s",
                (google_patient_id,),
            ).fetchone()["password_login_enabled"]
        self.assertFalse(enabled)

    def test_unlink_derives_google_only_account_even_if_an_old_task_set_the_default_true(self) -> None:
        google_subject = "rolling-deploy-google-subject"
        patient_id = patient_store.create_google_patient(google_subject, "unknown-random-placeholder")
        patient_store.upsert_drive_connection(
            patient_id, google_subject, "encrypted-refresh-token", "folder-id"
        )
        with patient_store._connection() as connection:
            connection.execute(
                "UPDATE patients SET password_login_enabled = TRUE WHERE patient_id = %s",
                (patient_id,),
            )
        session = patient_store.create_patient_session(patient_id)
        self.assertTrue(
            patient_store.link_idme_identity(
                patient_id, session, DISCOVERY["issuer"], "idme-google-subject"
            )
        )
        self.assertEqual(patient_store.unlink_idme_identity(patient_id, False), "last_login")
        self.assertEqual(patient_store.get_patient_session(session), patient_id)
        self.assertEqual(patient_store.unlink_idme_identity(patient_id, True), "unlinked")
        self.assertIsNone(patient_store.get_patient_session(session))

    def test_transaction_identity_link_and_session_invariants(self) -> None:
        self.assertTrue(patient_store.create_patient("local-patient", "local-password-hash"))
        self.assertTrue(patient_store.create_patient("second-local-patient", "second-password-hash"))
        patient_store.save_idme_oidc_transaction(
            "login", None, "provider-state", "browser-binding", "nonce", "pkce-verifier"
        )
        with patient_store._connection() as connection:
            expires_at = datetime.fromisoformat(
                connection.execute(
                    "SELECT expires_at FROM idme_oidc_transactions WHERE state_hash = %s",
                    (patient_store._hash_session_token("provider-state"),),
                ).fetchone()["expires_at"]
            )
        remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining, IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS - 10)
        self.assertLessEqual(remaining, IDME_OIDC_TRANSACTION_MAX_AGE_SECONDS)
        self.assertIsNone(
            patient_store.consume_idme_oidc_transaction("provider-state", "wrong-browser")
        )
        transaction = patient_store.consume_idme_oidc_transaction(
            "provider-state", "browser-binding"
        )
        self.assertEqual(
            transaction,
            StoredIdmeTransaction("login", None, "nonce", "pkce-verifier"),
        )
        self.assertIsNone(
            patient_store.consume_idme_oidc_transaction("provider-state", "browser-binding")
        )
        patient_store.save_idme_oidc_transaction(
            "login", None, "expired-state", "expired-browser", "nonce", "pkce-verifier"
        )
        with patient_store._connection() as connection:
            connection.execute(
                "UPDATE idme_oidc_transactions SET expires_at = %s WHERE state_hash = %s",
                (
                    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    patient_store._hash_session_token("expired-state"),
                ),
            )
        self.assertIsNone(
            patient_store.consume_idme_oidc_transaction("expired-state", "expired-browser")
        )

        self.assertIsNone(
            patient_store.create_idme_patient_session(DISCOVERY["issuer"], "idme-subject")
        )
        link_session = patient_store.create_patient_session("local-patient")
        self.assertTrue(
            patient_store.link_idme_identity(
                "local-patient", link_session, DISCOVERY["issuer"], "idme-subject"
            )
        )
        authenticated = patient_store.create_idme_patient_session(
            DISCOVERY["issuer"], "idme-subject"
        )
        self.assertIsNotNone(authenticated)
        idme_patient, idme_session = authenticated or ("", "")
        self.assertEqual(idme_patient, "local-patient")
        self.assertEqual(patient_store.get_patient_session(idme_session), "local-patient")
        self.assertFalse(
            patient_store.link_idme_identity(
                "second-local-patient",
                patient_store.create_patient_session("second-local-patient"),
                DISCOVERY["issuer"],
                "idme-subject",
            )
        )

        second_session = patient_store.create_patient_session("local-patient")
        self.assertEqual(patient_store.get_patient_session(second_session), "local-patient")
        self.assertEqual(patient_store.unlink_idme_identity("local-patient"), "unlinked")
        self.assertIsNone(
            patient_store.create_idme_patient_session(DISCOVERY["issuer"], "idme-subject")
        )
        self.assertIsNone(patient_store.get_patient_session(idme_session))
        self.assertIsNone(patient_store.get_patient_session(second_session))
        self.assertFalse(
            patient_store.link_idme_identity(
                "local-patient", link_session, DISCOVERY["issuer"], "idme-subject"
            )
        )

        session = patient_store.create_patient_session("local-patient")
        self.assertEqual(patient_store.get_recent_patient_session(session), "local-patient")
        with patient_store._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM patient_external_identities"
                ).fetchone()["count"],
                0,
            )
            self.assertGreaterEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM identity_verification_events"
                ).fetchone()["count"],
                4,
            )


if __name__ == "__main__":
    unittest.main()
