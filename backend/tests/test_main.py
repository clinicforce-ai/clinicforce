"""Route and storage-boundary tests with external services mocked."""

from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.shl import encrypt_bundle, generate_symmetric_key, hash_pin  # noqa: E402
from app.core.source_links import create_document_download_token, verify_document_download_token  # noqa: E402
from app.core import patient_store  # noqa: E402
from app.ingestion.bedrock_extractor import ExtractedFact, facts_to_fhir_bundle  # noqa: E402
from app.ingestion.pdf_parser import parse_pdf_text_with_llm, pdf_extraction_to_fhir_bundle  # noqa: E402
from app.main import app  # noqa: E402
from app.reports.pdf_builder import generate_doctor_pdf  # noqa: E402
from app.reports.summarizer import summarize_fhir_bundle  # noqa: E402


class MainRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_healthcheck_is_public_and_has_no_data(self) -> None:
        response = await self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_patient_store_schema_initializes_only_once_per_process(self) -> None:
        """Concurrent request connections must not each issue schema-altering DDL."""
        previous_state = patient_store._schema_initialized
        patient_store._schema_initialized = False
        first_connection, second_connection = Mock(), Mock()
        try:
            with (
                patch("app.core.patient_store._database_url", return_value="postgresql://test"),
                patch("app.core.patient_store.psycopg.connect", side_effect=[first_connection, second_connection]),
                patch("app.core.patient_store._initialize_schema") as initialize,
            ):
                self.assertIs(patient_store._connection(), first_connection)
                self.assertIs(patient_store._connection(), second_connection)
            initialize.assert_called_once_with(first_connection)
        finally:
            patient_store._schema_initialized = previous_state

    async def test_integration_route_creates_an_encrypted_manifest(self) -> None:
        patient = {"resourceType": "Patient", "id": "patient-1"}
        observation = {"resourceType": "Observation", "status": "final"}
        fhir_bundle = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": patient}]}
        with (
            patch("app.main._build_ingestion_resources", new=AsyncMock(return_value=[patient, observation])),
            patch("app.main.post_to_fhir", new=AsyncMock(side_effect=[{"id": "patient-1"}, {"id": "observation-1"}])),
            patch("app.main.get_patient_records", new=AsyncMock(return_value=fhir_bundle)),
            patch("app.main.create_manifest", return_value=str(uuid4())),
        ):
            response = await self.client.post(
                "/api/test/integration", json={"patient_id": "patient-1", "passcode": "1234"}
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["fhir_post_status"]), 2)
        self.assertTrue(payload["shl_uri"].startswith("shlink:/"))
        self.assertTrue(payload["qr_code_base64"])

    async def test_manifest_rejects_an_incorrect_passcode(self) -> None:
        manifest_id = uuid4()
        with patch(
            "app.main.get_manifest",
            return_value={"encrypted_jwe": "encrypted", "hashed_pin": hash_pin("1234")},
        ):
            response = await self.client.post(f"/manifest/{manifest_id}", json={"passcode": "0000"})
        self.assertEqual(response.status_code, 401)

    async def test_upload_requires_s3_instead_of_database_fallback(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.s3_enabled", return_value=False),
            patch("app.main.add_document") as add_document,
        ):
            response = await self.client.post(
                "/api/patient/upload",
                data={"patient_id": "patient-1", "password": "long-password"},
                files={"file": ("record.pdf", b"%PDF-1.4", "application/pdf")},
            )
        self.assertEqual(response.status_code, 503)
        add_document.assert_not_called()

    async def test_upload_accepts_the_standard_plural_formdata_key(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.s3_enabled", return_value=True),
            patch(
                "app.main.extract_pdf_to_fhir_bundle",
                new=AsyncMock(return_value={
                    "resourceType": "Bundle",
                    "type": "collection",
                    "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}],
                }),
            ),
            patch("app.main.post_to_fhir", new=AsyncMock()),
            patch("app.main.put_patient_pdf", return_value="clinicforce/patient-1/source/report.pdf"),
            patch("app.main.add_document", return_value=1),
            patch("app.main.list_documents", return_value=[]),
            patch("app.main.list_records", return_value=[]),
        ):
            response = await self.client.post(
                "/api/patient/upload",
                data={"patient_id": "patient-1", "password": "long-password"},
                files={"files": ("record.pdf", b"%PDF-1.4 valid", "application/pdf")},
            )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_patient_record_contract_matches_the_browser_form(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.post_to_fhir", new=AsyncMock()),
            patch("app.main.add_record"),
            patch("app.main.list_documents", return_value=[]),
            patch("app.main.list_records", return_value=[]),
        ):
            response = await self.client.post(
                "/api/patient/records",
                json={
                    "patient_id": "patient-1",
                    "password": "long-password",
                    "category": "lab",
                    "name": "Hemoglobin A1c",
                    "value": "5.8",
                    "unit": "%",
                    "date": "2026-08-09",
                    "details": "Annual lab",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)

    async def test_invalid_browser_payload_is_rejected_before_persistence(self) -> None:
        with patch("app.main.post_to_fhir", new=AsyncMock()) as post_to_fhir:
            response = await self.client.post(
                "/api/patient/records",
                json={"patient_id": "patient-1", "password": "long-password", "category": "not-a-record"},
            )
        self.assertEqual(response.status_code, 422)
        post_to_fhir.assert_not_awaited()

    async def test_download_reads_the_document_from_s3(self) -> None:
        document = Mock(s3_key="clinicforce/patients/hash/source/id/record.pdf", filename="record.pdf")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_document", return_value=document),
            patch("app.main.get_pdf", return_value=b"%PDF-1.4") as get_pdf,
        ):
            response = await self.client.post(
                "/api/patient/documents/7/download",
                json={"patient_id": "patient-1", "password": "long-password"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4")
        get_pdf.assert_called_once_with(document.s3_key)

    async def test_doctor_source_download_uses_a_verified_single_document_capability(self) -> None:
        document = Mock(s3_key="clinicforce/patients/hash/source/id/record.pdf", filename="record.pdf")
        with (
            patch("app.main.verify_document_download_token") as verify,
            patch("app.main.get_document_by_id", return_value=document),
            patch("app.main.get_pdf", return_value=b"%PDF-1.4") as get_pdf,
        ):
            response = await self.client.get("/api/doctor/documents/7/download?token=signed-capability")
        self.assertEqual(response.status_code, 200)
        verify.assert_called_once_with("signed-capability", 7)
        get_pdf.assert_called_once_with(document.s3_key)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store, private")

    async def test_doctor_source_download_rejects_an_invalid_capability(self) -> None:
        with patch("app.main.verify_document_download_token", side_effect=ValueError("Invalid source download link")):
            response = await self.client.get("/api/doctor/documents/7/download?token=invalid")
        self.assertEqual(response.status_code, 403)

    async def test_doctor_report_is_returned_inline_not_left_on_task_storage(self) -> None:
        key = generate_symmetric_key()
        key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        encrypted = encrypt_bundle({"resourceType": "Bundle", "type": "collection", "entry": []}, key_bytes)
        summary = {"narrative": "Clinical history reviewed.", "vitals": [], "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": []}

        def render_pdf(_summary: dict, filename: str) -> str:
            Path(filename).write_bytes(b"%PDF-1.4 test")
            return filename

        with (
            patch("app.main.summarize_fhir_bundle", new=AsyncMock(return_value=summary)),
            patch("app.main.generate_doctor_pdf", side_effect=render_pdf),
        ):
            response = await self.client.post(
                "/api/doctor/decrypt-and-summarize", json={"jwe_payload": encrypted, "key": key}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"], summary)
        self.assertEqual(response.json()["pdf_url"], "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode())

    async def test_local_pdf_extraction_preserves_citations_without_bedrock(self) -> None:
        extraction = await parse_pdf_text_with_llm("[PAGE 2]\nConditions: Asthma\nMedications: Albuterol", "report.pdf")
        bundle = pdf_extraction_to_fhir_bundle(extraction, "patient-1")
        citation = bundle["entry"][0]["resource"]["extension"][0]["extension"]
        self.assertIn({"url": "page_number", "valuePositiveInt": 2}, citation)

    async def test_summarizer_is_local_and_preserves_structured_data(self) -> None:
        bundle = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}]}
        summary = await summarize_fhir_bundle(bundle)
        self.assertIn("Asthma", summary["narrative"])
        self.assertEqual(summary["conditions"][0]["name"], "Asthma")

    def test_pdf_builder_uses_weasyprint_with_rendered_structured_values(self) -> None:
        summary = {"narrative": "Clinical history reviewed.", "vitals": [{"name": "Heart rate", "value": 72, "unit": "beats/min", "date": "2026-08-02", "citation": {"source_filename": "cardiology.pdf", "page_number": 3}}], "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": []}
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock()
            result = generate_doctor_pdf(summary, "/tmp/mock-summary.pdf")
        self.assertEqual(result, "/tmp/mock-summary.pdf")
        html.return_value.write_pdf.assert_called_once_with("/tmp/mock-summary.pdf")
        self.assertIn("72", html.call_args.kwargs["string"])
        self.assertIn("cardiology.pdf · p. 3", html.call_args.kwargs["string"])

    def test_pdf_builder_only_renders_populated_sections_and_fact_level_sources(self) -> None:
        summary = {
            "narrative": "Clinical history reviewed.",
            "vitals": [
                {"name": "Heart rate", "value": value, "unit": "bpm", "date": f"2026-08-0{index}", "citation": {"source_filename": "vitals.pdf", "page_number": index, "download_url": "https://app.example/api/doctor/documents/9/download?token=signed"}}
                for index, value in enumerate((70, 72, 74, 73), start=1)
            ],
            "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": [],
        }
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock()
            generate_doctor_pdf(summary, "/tmp/mock-summary.pdf")
        rendered = html.call_args.kwargs["string"]
        self.assertNotIn("Original Source Register", rendered)
        self.assertNotIn("Clinical overview sources", rendered)
        self.assertIn('href="https://app.example/api/doctor/documents/9/download?token=signed"', rendered)
        self.assertNotIn("Care visits", rendered)
        self.assertNotIn("Historical Value", rendered)
        self.assertIn("table-layout: auto", rendered)
        self.assertIn('aria-label="Heart rate (bpm) trend with 4 values"', rendered)

    def test_source_capability_is_document_scoped_and_expiring(self) -> None:
        with patch.dict(os.environ, {"SOURCE_LINK_SECRET": "a" * 32}, clear=False):
            token = create_document_download_token(9, now=1_000, ttl_seconds=120)
            verify_document_download_token(token, 9, now=1_120)
            with self.assertRaisesRegex(ValueError, "Invalid source download link"):
                verify_document_download_token(token, 10, now=1_120)
            with self.assertRaisesRegex(ValueError, "expired"):
                verify_document_download_token(token, 9, now=1_121)

    def test_bedrock_facts_become_citation_backed_fhir_without_model_fhir_output(self) -> None:
        bundle = facts_to_fhir_bundle(
            [ExtractedFact(type="lab", page=4, name="Hemoglobin A1c", value="5.8", unit="%")],
            "patient-1",
            "annual-labs.pdf",
        )
        resource = bundle["entry"][0]["resource"]
        self.assertEqual(resource["resourceType"], "Observation")
        self.assertEqual(resource["valueQuantity"]["value"], 5.8)
        self.assertIn({"url": "source_filename", "valueString": "annual-labs.pdf"}, resource["extension"][0]["extension"])
