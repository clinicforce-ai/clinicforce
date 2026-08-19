"""Tests for patient-authorized Epic live retrieval."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion import epic  # noqa: E402


class EpicLiveRetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = epic.LiveEpicSession(
            patient_id="local-patient",
            fhir_base="https://fhir.example.test/R4",
            remote_patient_id="epic-patient",
            access_token="test-token",
            expires_at=9_999_999_999,
            organization_name="Example Health",
        )

    async def test_condition_search_includes_problem_list_health_concerns_and_encounter_diagnoses(self) -> None:
        async def get_json(_session, _url, params=None):
            category = (params or {}).get("category")
            entries = {
                None: [{"resource": {"resourceType": "Condition", "id": "problem", "code": {"text": "Asthma"}}}],
                "problem-list-item": [{"resource": {"resourceType": "Condition", "id": "problem", "code": {"text": "Asthma"}}}],
                "health-concern": [{"resource": {"resourceType": "Condition", "id": "concern", "code": {"text": "Fall risk"}}}],
                "encounter-diagnosis": [{"resource": {"resourceType": "Condition", "id": "diagnosis", "code": {"text": "Pneumonia"}}}],
            }[category]
            return 200, {"resourceType": "Bundle", "entry": entries}, {}

        with patch("app.ingestion.epic._get_json", new=AsyncMock(side_effect=get_json)):
            bundle = await epic.fetch_resource_type(self.session, "Condition")

        resources = [entry["resource"] for entry in bundle["entry"]]
        self.assertEqual([resource["id"] for resource in resources], ["problem", "concern", "diagnosis"])
        self.assertEqual([resource["code"]["text"] for resource in resources], ["Asthma", "Fall risk", "Pneumonia"])

    async def test_fetch_record_marks_successful_empty_searches_as_empty(self) -> None:
        empty_bundle = {"resourceType": "Bundle", "type": "collection", "entry": []}
        with patch("app.ingestion.epic.fetch_resource_type", new=AsyncMock(return_value=empty_bundle)):
            bundle, availability = await epic.fetch_record(self.session)

        self.assertEqual(bundle["entry"], [])
        self.assertEqual(availability["Condition"], "empty")

