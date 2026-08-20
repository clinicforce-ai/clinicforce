"""SHL transport conformance and PIN-brute-force protections."""

from __future__ import annotations

import base64
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import patient_store  # noqa: E402
from app.core.shl import create_shl_uri, encrypt_bundle, generate_symmetric_key, hash_pin  # noqa: E402


class _Result:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class _ManifestConnection:
    """A thread-safe narrow fake for the SQL used by verify_manifest_pin()."""

    def __init__(self, hashed_pin: str) -> None:
        self.lock = threading.Lock()
        self.row: dict[str, object] = {
            "patient_id": "patient-1",
            "encrypted_jwe": "encrypted",
            "hashed_pin": hashed_pin,
            "failed_pin_attempts": 0,
            "revoked_at": None,
        }

    def __enter__(self) -> "_ManifestConnection":
        self.lock.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.lock.release()

    def execute(self, query: str, params: tuple[object, ...]) -> _Result:
        if query.lstrip().startswith("SELECT"):
            if self.row["revoked_at"] is not None:
                return _Result(None)
            return _Result(dict(self.row))
        if query.lstrip().startswith("UPDATE shl_manifests"):
            maximum = int(params[0])
            self.row["failed_pin_attempts"] = int(self.row["failed_pin_attempts"]) + 1
            if int(self.row["failed_pin_attempts"]) >= maximum:
                self.row["revoked_at"] = str(params[1])
            return _Result({"failed_pin_attempts": self.row["failed_pin_attempts"]})
        raise AssertionError(query)


class ShlTransportTests(unittest.TestCase):
    def test_manifest_tokens_are_43_character_base64url_256_bit_values(self) -> None:
        tokens = {patient_store.generate_manifest_token() for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        for token in tokens:
            self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")

    def test_jwe_uses_required_shl_headers(self) -> None:
        key = generate_symmetric_key()
        key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        encrypted = encrypt_bundle({"resourceType": "Bundle", "entry": []}, key_bytes)
        header = json.loads(base64.urlsafe_b64decode(encrypted.split(".", 1)[0] + "=="))
        self.assertEqual(header, {"alg": "dir", "enc": "A256GCM", "cty": "application/fhir+json"})

    def test_shlink_payload_is_versioned_and_carries_expiry(self) -> None:
        uri = create_shl_uri("https://app.example/manifest/" + "A" * 43, generate_symmetric_key(), "ClinicForce records", 1_800_000_000)
        encoded = uri.removeprefix("shlink:/")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["flag"], "P")
        self.assertEqual(payload["exp"], 1_800_000_000)

    def test_lifetime_pin_limit_is_not_reset_by_a_success_and_is_race_safe(self) -> None:
        connection = _ManifestConnection(hash_pin("1234"))
        results: list[int | None] = []
        results_lock = threading.Lock()

        def attempt() -> None:
            try:
                patient_store.verify_manifest_pin("A" * 43, "0000")
                result: int | None = None
            except patient_store.InvalidManifestPin as exc:
                result = exc.remaining_attempts
            with results_lock:
                results.append(result)

        with patch("app.core.patient_store._connection", return_value=connection):
            workers = [threading.Thread(target=attempt) for _ in range(10)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            self.assertEqual(connection.row["failed_pin_attempts"], patient_store.MANIFEST_PIN_MAX_ATTEMPTS)
            self.assertIsNotNone(connection.row["revoked_at"])
            self.assertEqual(sorted(result for result in results if result is not None), [0, 1, 2, 3, 4])
            self.assertEqual(sum(result is None for result in results), 5)
            self.assertIsNone(patient_store.verify_manifest_pin("A" * 43, "1234"))
