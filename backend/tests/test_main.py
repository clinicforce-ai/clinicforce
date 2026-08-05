"""Self-contained integration-route tests with all external services mocked."""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.shl import encrypt_bundle, generate_symmetric_key  # noqa: E402
from app.main import app  # noqa: E402
from app.ingestion.pdf_parser import parse_pdf_text_with_llm, pdf_extraction_to_fhir_bundle  # noqa: E402
from app.reports.pdf_builder import generate_doctor_pdf  # noqa: E402
from app.reports.summarizer import summarize_fhir_bundle  # noqa: E402


class MainRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_integration_route_creates_an_encrypted_manifest_and_qr(self) -> None:
        patient = {"resourceType": "Patient", "id": "patient-1"}
        observation = {"resourceType": "Observation", "status": "final"}
        fhir_bundle = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": patient}]}
        with (
            patch("app.main._build_ingestion_resources", new=AsyncMock(return_value=[patient, observation])),
            patch("app.main.post_to_fhir", new=AsyncMock(side_effect=[{"id": "patient-1"}, {"id": "observation-1"}])),
            patch("app.main.get_patient_records", new=AsyncMock(return_value=fhir_bundle)),
        ):
            response = await self.client.post(
                "/api/test/integration",
                json={"patient_id": "patient-1", "passcode": "1234"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["fhir_post_status"]), 2)
        self.assertTrue(payload["shl_uri"].startswith("shlink:/"))
        self.assertTrue(payload["qr_code_base64"])

        manifest = await self.client.post(
            f"/manifest/{payload['manifest_id']}", json={"passcode": "1234"}
        )
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["files"][0]["contentType"], "application/fhir+json")
        self.assertEqual(len(manifest.json()["files"][0]["embedded"].split(".")), 5)

    async def test_manifest_rejects_an_incorrect_passcode(self) -> None:
        patient = {"resourceType": "Patient", "id": "patient-2"}
        fhir_bundle = {"resourceType": "Bundle", "type": "searchset", "entry": []}
        with (
            patch("app.main._build_ingestion_resources", new=AsyncMock(return_value=[patient])),
            patch("app.main.post_to_fhir", new=AsyncMock(return_value={"id": "patient-2"})),
            patch("app.main.get_patient_records", new=AsyncMock(return_value=fhir_bundle)),
        ):
            integration_response = await self.client.post(
                "/api/test/integration",
                json={"patient_id": "patient-2", "passcode": "1234"},
            )
        manifest = await self.client.post(
            f"/manifest/{integration_response.json()['manifest_id']}", json={"passcode": "0000"}
        )
        self.assertEqual(manifest.status_code, 401)

    async def test_integration_error_identifies_the_failing_component(self) -> None:
        with patch(
            "app.main._build_ingestion_resources",
            new=AsyncMock(side_effect=RuntimeError("Bedrock unavailable")),
        ):
            response = await self.client.post(
                "/api/test/integration",
                json={"patient_id": "patient-3", "passcode": "1234"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("Ingestion", response.json()["detail"])

    async def test_doctor_route_decrypts_summarizes_and_renders_without_services(self) -> None:
        key = generate_symmetric_key()
        key_bytes = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": []}
        encrypted = encrypt_bundle(bundle, key_bytes)
        summary = {"narrative": "Clinical history reviewed.", "vitals": [], "labs": [], "medications": [], "conditions": [], "allergies": []}
        with (
            patch("app.main.summarize_fhir_bundle", new=AsyncMock(return_value=summary)),
            patch("app.main.generate_doctor_pdf", return_value="/tmp/test_summary.pdf") as pdf,
        ):
            response = await self.client.post(
                "/api/doctor/decrypt-and-summarize", json={"jwe_payload": encrypted, "key": key}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"], summary)
        self.assertEqual(response.json()["pdf_url"], "http://localhost:8000/api/doctor/download-summary")
        pdf.assert_called_once_with(summary,'/tmp/clinical_summary.pdf')

    async def test_summarizer_uses_bedrock_and_filters_numeric_narrative(self) -> None:
        bundle = {"resourceType": "Bundle", "entry": []}
        mock_client = Mock()
        mock_client.invoke_model.return_value = {"body": Mock(read=Mock(return_value=b'{"content":[{"type":"text","text":"{\\"narrative\\": \\"Patient has 10 mg medication. History reviewed.\\"}"}]}'))}
        with patch("app.reports.summarizer.boto3.client", return_value=mock_client):
            summary = await summarize_fhir_bundle(bundle)
        self.assertEqual(summary["narrative"], "History reviewed.")
        self.assertEqual(mock_client.invoke_model.call_args.kwargs["modelId"], "anthropic.claude-3-haiku-20240307-v1:0")

    async def test_pdf_extraction_requires_and_preserves_citations(self) -> None:
        mock_client = Mock()
        mock_client.invoke_model.return_value = {"body": Mock(read=Mock(return_value=(
            b'{"content":[{"type":"text","text":"{\\"conditions\\":[{\\"name\\":\\"Asthma\\",\\"clinical_status\\":null,\\"citation\\":{\\"source_filename\\":\\"report.pdf\\",\\"page_number\\":2}}],\\"allergies\\":[],\\"medications\\":[]}"}]}'
        )))}
        with patch("app.ingestion.pdf_parser.boto3.client", return_value=mock_client):
            extraction = await parse_pdf_text_with_llm("[PAGE 2] Asthma", "report.pdf")
        bundle = pdf_extraction_to_fhir_bundle(extraction, "patient-1")
        citation = bundle["entry"][0]["resource"]["extension"][0]["extension"]
        self.assertIn({"url": "page_number", "valuePositiveInt": 2}, citation)
        request = mock_client.invoke_model.call_args.kwargs
        self.assertEqual(request["modelId"], "anthropic.claude-3-haiku-20240307-v1:0")
        self.assertIn("You MUST NOT invent", request["body"])

    async def test_pdf_builder_uses_weasyprint_with_rendered_structured_values(self) -> None:
        summary = {
            "narrative": "Clinical history reviewed.",
            "vitals": [{"name": "Heart rate", "value": 72, "unit": "beats/min", "date": "2026-08-02"}],
            "labs": [], "medications": [], "conditions": [], "allergies": [],
        }
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock()
            result = generate_doctor_pdf(summary, "/tmp/mock-summary.pdf")
        self.assertEqual(result, "/tmp/mock-summary.pdf")
        html.return_value.write_pdf.assert_called_once_with("/tmp/mock-summary.pdf")
        self.assertIn("72", html.call_args.kwargs["string"])

    def test_pdf_builder_renders_citation_source_link(self) -> None:
        summary = {"narrative": "Clinical history reviewed.", "vitals": [{"name": "Heart rate", "value": 72, "unit": "beats/min", "citation": {"document_id": 7, "page_number": 2}}], "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": []}
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock()
            generate_doctor_pdf(summary, "/tmp/mock-summary.pdf")
        self.assertIn('/api/documents/7#page=2', html.call_args.kwargs["string"])
