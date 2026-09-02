"""Route and storage-boundary tests with external services mocked."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unittest
from copy import deepcopy
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4
from zipfile import ZipFile

import httpx
import fitz
import psycopg
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.patient_store import InvalidManifestPin, PatientProfile  # noqa: E402
from app.core.shl import encrypt_bundle, generate_symmetric_key, hash_pin  # noqa: E402
from app.core.source_links import create_document_download_token, verify_document_download_token  # noqa: E402
from app.core import patient_store  # noqa: E402
from app.ingestion.bedrock_extractor import ExtractedFact, facts_to_fhir_bundle  # noqa: E402
from app.ingestion.pdf_parser import parse_pdf_text_with_llm, pdf_extraction_to_fhir_bundle  # noqa: E402
from app.insurance import documented, golden  # noqa: E402
from app.main import _epic_share_snapshot, _normalize_ecw_bundle, app, build_exchange_bundle  # noqa: E402
from app.reports.pdf_builder import generate_doctor_pdf, render_doctor_pdf_bytes  # noqa: E402
from app.reports.summarizer import summarize_fhir_bundle  # noqa: E402


MATCHABLE_PROFILE = PatientProfile(
    patient_id="patient-1", given_name="Pat", family_name="Example",
    birth_date="1980-01-01", administrative_gender="unknown",
)

TEST_CITATION = {"source_filename": "reviewed.pdf", "sha256": "a" * 64, "reference": "page 2", "observed_at": "2026-08-24"}
GOLDEN_MANIFEST = {
    "manifest_id": "evidence-1",
    "identity": {
        "given_name_sha256": hashlib.sha256(b"pat").hexdigest(),
        "family_name_sha256": hashlib.sha256(b"example").hexdigest(),
        "birth_date_sha256": hashlib.sha256(b"19800101").hexdigest(),
        "member_id_sha256": hashlib.sha256(b"member123").hexdigest(),
        "group_number_sha256": hashlib.sha256(b"group123").hexdigest(),
    },
    "appointment": {
        "fhir_r4_resource_type": "Appointment", "status": "booked", "start": None, "end": None,
        "date": "2026-08-24", "service_type": "Neurology office visit", "appointment_type": "New patient",
        "reason": "Neuropathy", "participants": ["Patient", "Neurology clinician"],
        "provider": "NMCT", "location": "Office", "source": TEST_CITATION,
    },
    "coverage": {
        "status": "active", "coinsurance_percent": 20, "individual_oop_remaining_cents": 0,
        "transaction_reference": "Availity 270/271 · DOS 2026-08-24", "source": TEST_CITATION,
    },
    "card_source": TEST_CITATION,
    "network": {"display": "NMCT · Blue Choice PPO · in network", "source": TEST_CITATION, "agreement_source": TEST_CITATION},
    "authorization": {"referral_source": TEST_CITATION, "prior_auth_source": TEST_CITATION},
    "rates": {"99204": 15160, "99205": 20023}, "rates_source": TEST_CITATION,
    "rate_lines": [
        {"code": "99202", "description": "New patient office visit, straightforward", "allowed_cents": 6484},
        {"code": "99203", "description": "New patient office visit, low MDM", "allowed_cents": 10117},
        {"code": "99204", "description": "New patient office visit, moderate MDM", "allowed_cents": 15160},
        {"code": "99205", "description": "New patient office visit, high MDM", "allowed_cents": 20023},
        {"code": "95886", "description": "Needle EMG, complete", "allowed_cents": 8918},
        {"code": "95907", "description": "Nerve conduction studies, 1-2", "allowed_cents": 8440},
    ],
    "rejected_source": TEST_CITATION,
    "sources": [{"id": "reviewed", "filename": "reviewed.pdf", "sha256": "a" * 64, "observed_at": "2026-08-24"}],
}


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

    async def test_insurance_beta_is_off_by_default_and_rejects_session_bypass(self) -> None:
        with patch.dict(os.environ, {"INSURANCE_BETA_ENABLED": "false"}, clear=False):
            disabled = await self.client.post("/api/doctor/insurance/beta")
        self.assertEqual(disabled.status_code, 404)
        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            missing = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"bad", "image/png")})
            token = golden.create_share_token("patient-1", "share-1", beta_enabled=False)
            self.client.cookies.set("clinicforce_doctor_share", token, path="/api/doctor")
            bypass = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"bad", "image/png")})
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(bypass.status_code, 403)

    async def test_right_click_beta_activation_rotates_the_bound_session(self) -> None:
        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            token = golden.create_share_token("patient-1", "share-1")
            self.client.cookies.set("clinicforce_doctor_share", token, path="/api/doctor")
            with patch("app.api.routers.insurance.golden.load_manifest", return_value=GOLDEN_MANIFEST), \
                 patch("app.api.routers.insurance.get_patient_profile", return_value=MATCHABLE_PROFILE):
                response = await self.client.post("/api/doctor/insurance/beta")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["beta_enabled"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Max-Age=900", response.headers["set-cookie"])

    async def test_beta_activation_allows_another_qr_patient_to_start_document_review(self) -> None:
        different_profile = PatientProfile(
            patient_id="patient-1", given_name="Different", family_name="Patient",
            birth_date="1990-01-01", administrative_gender="unknown",
        )
        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            token = golden.create_share_token("patient-1", "share-1")
            self.client.cookies.set("clinicforce_doctor_share", token, path="/api/doctor")
            with patch("app.api.routers.insurance.golden.load_manifest", return_value=GOLDEN_MANIFEST), \
                 patch("app.api.routers.insurance.get_patient_profile", return_value=different_profile):
                response = await self.client.post("/api/doctor/insurance/beta")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["beta_enabled"])

    def test_insurance_session_expiry_malformed_files_and_bundle_hashes_fail_closed(self) -> None:
        with patch.dict(os.environ, {
            "SOURCE_LINK_SECRET": "s" * 40, "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            token = golden.create_share_token("patient-1", "share-1", beta_enabled=True)
            with patch("app.insurance.golden.datetime") as clock:
                clock.now.return_value = SimpleNamespace(timestamp=lambda: 9_999_999_999)
                with self.assertRaisesRegex(golden.GoldenCaseError, "expired"):
                    golden.open_share_token(token, require_beta=True)
        with self.assertRaisesRegex(golden.GoldenCaseError, "PDF, JPG, or PNG"):
            golden.read_card(b"not-an-image")

        broken = BytesIO()
        with ZipFile(broken, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps({**GOLDEN_MANIFEST, "sources": [{
                "id": "reviewed", "filename": "reviewed.pdf", "sha256": "0" * 64, "observed_at": "2026-08-24",
            }]}))
            bundle.writestr("sources/reviewed.pdf", b"reviewed evidence")
        with patch.dict(os.environ, {"INSURANCE_GOLDEN_MANIFEST_PATH": "/private/golden.zip"}, clear=False), \
             patch("app.insurance.golden.Path.read_bytes", return_value=broken.getvalue()):
            with self.assertRaisesRegex(golden.GoldenCaseError, "SHA-256"):
                golden.load_manifest()

    async def test_card_ocr_stays_locked_until_clinician_review(self) -> None:
        def stored(_patient: str, _share: str, _evidence: str, workflow: dict) -> dict:
            return {**workflow, "id": "case-1", "created_at": "2026-08-24T00:00:00+00:00", "expires_at": "2026-08-25T00:00:00+00:00"}

        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            token = golden.create_share_token("patient-1", "share-1", beta_enabled=True)
            self.client.cookies.set("clinicforce_doctor_share", token, path="/api/doctor")
            with patch("app.api.routers.insurance.golden.load_manifest", return_value=GOLDEN_MANIFEST), \
                 patch("app.api.routers.insurance.get_patient_profile", return_value=MATCHABLE_PROFILE), \
                 patch("app.api.routers.insurance.golden.read_card", return_value={"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"}), \
                 patch("app.api.routers.insurance.store.create_case", side_effect=stored):
                response = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"png", "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        case = response.json()
        self.assertEqual(case["next_stage"], "intake")
        self.assertEqual(case["facts"], [])
        self.assertIsNone(case["appointment"])
        self.assertFalse(case["ocr"]["confirmed"])

    async def test_fixture_backed_api_scenario_reaches_the_zero_dollar_estimate(self) -> None:
        cases: dict[str, dict] = {}

        def create(_patient: str, _share: str, _evidence: str, workflow: dict) -> dict:
            case = {**workflow, "id": "case-1", "created_at": "2026-08-24T00:00:00+00:00", "expires_at": "2026-08-25T00:00:00+00:00"}
            cases[case["id"]] = deepcopy(case)
            return case

        def get(case_id: str, _patient: str, _share: str) -> dict | None:
            return deepcopy(cases.get(case_id))

        def save(case: dict, _patient: str, _share: str, _event: str) -> dict:
            cases[case["id"]] = deepcopy(case)
            return case

        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            self.client.cookies.set(
                "clinicforce_doctor_share", golden.create_share_token("patient-1", "share-1", beta_enabled=True),
                path="/api/doctor",
            )
            with patch("app.api.routers.insurance.golden.load_manifest", return_value=GOLDEN_MANIFEST), \
                 patch("app.api.routers.insurance.get_patient_profile", return_value=MATCHABLE_PROFILE), \
                 patch("app.api.routers.insurance.golden.read_card", return_value={"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"}), \
                 patch("app.api.routers.insurance.store.create_case", side_effect=create), \
                 patch("app.api.routers.insurance.store.get_case", side_effect=get), \
                 patch("app.api.routers.insurance.store.save_case", side_effect=save):
                created = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"png", "image/png")})
                self.assertEqual(created.status_code, 200, created.text)
                reviewed = await self.client.put("/api/doctor/insurance/cases/case-1/intake", json={"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"})
                self.assertEqual(reviewed.json()["next_stage"], "eligibility")
                self.assertEqual(reviewed.json()["source_mode"], "documents")
                self.assertEqual(reviewed.json()["documents"], {})
                self.assertIsNone(reviewed.json()["coverage"])

    async def test_document_backed_api_scenario_requires_evidence_and_clinician_context(self) -> None:
        cases: dict[str, dict] = {}
        profile = PatientProfile(
            patient_id="patient-2", given_name="Jordan", family_name="Rivera",
            birth_date="1991-02-03", administrative_gender="unknown",
        )

        def create(_patient: str, _share: str, _evidence: str, workflow: dict) -> dict:
            case = {**workflow, "id": "case-docs", "created_at": "2026-08-24T00:00:00+00:00", "expires_at": "2026-08-25T00:00:00+00:00"}
            cases[case["id"]] = deepcopy(case)
            return case

        def get(case_id: str, _patient: str, _share: str) -> dict | None:
            return deepcopy(cases.get(case_id))

        def save(case: dict, _patient: str, _share: str, _event: str) -> dict:
            cases[case["id"]] = deepcopy(case)
            return case

        def inspect(_content: bytes, filename: str, kind: str, _member_id: str = "") -> dict:
            return {
                "id": kind, "filename": filename, "sha256": kind[0] * 64,
                "observed_at": "2026-08-24", "reference": f"Reviewed {kind} PDF",
            }

        with patch.dict(os.environ, {
            "INSURANCE_BETA_ENABLED": "true", "SOURCE_LINK_SECRET": "s" * 40,
            "INSURANCE_GOLDEN_BUNDLE_KEY": "clinicforce/golden/test.zip",
        }, clear=False):
            self.client.cookies.set(
                "clinicforce_doctor_share",
                golden.create_share_token(
                    "patient-2", "share-2", beta_enabled=True,
                ),
                path="/api/doctor",
            )
            with patch("app.api.routers.insurance.golden.load_manifest", return_value=GOLDEN_MANIFEST), \
                 patch("app.api.routers.insurance.get_patient_profile", return_value=profile), \
                 patch("app.api.routers.insurance.golden.read_card", return_value={"member_name": "JORDAN RIVERA", "member_id": "BCBS-456", "group_number": "GROUP-789"}), \
                 patch("app.api.routers.insurance.documented.inspect_pdf", side_effect=inspect), \
                 patch("app.api.routers.insurance.store.create_case", side_effect=create), \
                 patch("app.api.routers.insurance.store.get_case", side_effect=get), \
                 patch("app.api.routers.insurance.store.save_case", side_effect=save):
                created = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"png", "image/png")})
                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(created.json()["source_mode"], "documents")
                self.assertEqual(created.json()["service_lines"], [])
                reviewed = await self.client.put("/api/doctor/insurance/cases/case-docs/intake", json={
                    "member_name": "JORDAN RIVERA", "member_id": "BCBS-456", "group_number": "GROUP-789",
                })
                self.assertEqual(reviewed.status_code, 200, reviewed.text)
                for kind in ("eligibility", "contract"):
                    uploaded = await self.client.post(
                        f"/api/doctor/insurance/cases/case-docs/documents/{kind}",
                        files={"file": (f"{kind}.pdf", b"%PDF reviewed", "application/pdf")},
                    )
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                evidence = await self.client.put("/api/doctor/insurance/cases/case-docs/evidence", json={
                    "clinic": "NMCT", "payer": "BCBSTX", "plan_name": "Blue Choice PPO Medical",
                    "eligibility_observed_at": "2026-08-24", "coverage_status": "active",
                    "network_status": "in_network", "coinsurance_percent": 20, "copay_cents": 0,
                    "individual_deductible_remaining_cents": 0, "individual_oop_remaining_cents": 0,
                    "referral_status": "not_required", "authorization_status": "not_required",
                })
                self.assertEqual(evidence.status_code, 200, evidence.text)
                network = await self.client.post("/api/doctor/insurance/cases/case-docs/stages/network")
                self.assertEqual(network.status_code, 200, network.text)
                suggested = await self.client.put(
                    "/api/doctor/insurance/cases/case-docs/clinical-context",
                    json={"clinical_context": "Burning, numbness and tingling in both feet"},
                )
                self.assertEqual(suggested.status_code, 200, suggested.text)
                self.assertIn("95886", {line["code"] for line in suggested.json()["service_lines"]})
                selected = await self.client.put(
                    "/api/doctor/insurance/cases/case-docs/service-lines", json={"codes": ["99204", "95886"]},
                )
                self.assertEqual(selected.status_code, 200, selected.text)
                for stage in ("authorization", "benefits", "estimate"):
                    response = await self.client.post(f"/api/doctor/insurance/cases/case-docs/stages/{stage}")
                    self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["estimate"]["allowed_cents"], 24078)
        self.assertEqual(response.json()["estimate"]["patient_cents"], 0)

    def test_golden_card_unlock_requires_uploaded_benefits_and_contract(self) -> None:
        fields = {"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"}
        case = golden.pending_case(fields)
        golden.unlock_case(case, GOLDEN_MANIFEST, fields)
        self.assertEqual(case["source_mode"], "documents")
        self.assertEqual(case["documents"], {})
        self.assertIsNone(case["coverage"])
        self.assertFalse(any(fact["stage"] == "eligibility" for fact in case["facts"]))
        with self.assertRaisesRegex(golden.GoldenCaseError, "only the reviewed"):
            golden.unlock_case(golden.pending_case(fields), GOLDEN_MANIFEST, {**fields, "member_id": "WRONG-123"})

    def test_golden_context_exposes_rate_backed_neuropathy_candidates(self) -> None:
        case = golden.pending_case({"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"})
        golden.unlock_case(case, GOLDEN_MANIFEST, case["ocr"])
        self.assertEqual(case["service_lines"], [])
        case["next_stage"] = "service"
        documented.set_clinical_context(case, "Numbness and tingling; EMG is planned", GOLDEN_MANIFEST["rate_lines"])
        emg = next(line for line in case["service_lines"] if line["code"] == "95886")
        self.assertTrue(emg["available"])
        documented.select_services(case, ["99204", "95886"], GOLDEN_MANIFEST["rates_source"])

    def test_document_parser_reads_all_contract_pages_and_prefills_explicit_benefits(self) -> None:
        contract = fitz.open()
        for page_number in range(60):
            page = contract.new_page()
            if page_number == 59:
                page.insert_textbox(fitz.Rect(72, 72, 520, 760), "Professional Provider Agreement Blue Cross and Blue Shield Neurology and Movement Center Authorized Signature Blue Choice PPO Compensation")
        contract_source = documented.inspect_pdf(contract.tobytes(), "agreement.pdf", "contract")
        self.assertEqual(contract_source["findings"]["pages_scanned"], 60)
        self.assertEqual(contract_source["findings"]["participation_pages"], [60])

        eligibility = fitz.open()
        page = eligibility.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 520, 760), "BCBSTX Active Coverage Member ID BCBS-456 In Network\nPlan / Product: Preferred Provider Option Medical\nDiagnostic Medical 100% Annual Deductible $600 / Calendar Year $0 Remaining Out Of Pocket $3,000 / Calendar Year $0 Remaining\nOut Of Network Not Covered")
        suggestions = documented.inspect_pdf(eligibility.tobytes(), "eligibility.pdf", "eligibility", "BCBS-456")["findings"]["suggested_coverage"]
        self.assertEqual(suggestions, {"coverage_status": "active", "network_status": "in_network", "out_of_network_benefit_status": "not_covered", "plan_name": "Preferred Provider Option Medical", "coinsurance_percent": 0, "deductible_remaining_cents": 0, "oop_remaining_cents": 0})

    def test_anita_contract_calculates_without_staff_entering_benefits(self) -> None:
        fields = {"member_name": "Pat Example", "member_id": "MEMBER-123", "group_number": "GROUP-123"}
        case = golden.pending_case(fields)
        golden.unlock_case(case, GOLDEN_MANIFEST, fields)
        documented.add_document(case, "contract", {
            "id": "contract", "filename": "nmct-bcbs.pdf", "sha256": "a" * 64, "observed_at": "2026-08-24", "reference": "Provider agreement",
        })
        documented.confirm_anita_evidence(case, GOLDEN_MANIFEST)
        self.assertEqual(case["coverage"]["copay_cents"], 0)
        self.assertEqual(case["coverage"]["individual_oop_remaining_cents"], 0)
        documented.set_clinical_context(case, "neuropathy", GOLDEN_MANIFEST["rate_lines"])
        documented.select_services(case, ["99204"], GOLDEN_MANIFEST["rates_source"])
        documented.advance(case, "authorization", GOLDEN_MANIFEST["rates_source"])
        documented.advance(case, "benefits", GOLDEN_MANIFEST["rates_source"])
        self.assertEqual(case["estimate"]["patient_cents"], 0)

    def test_document_fallback_suggests_reviewed_codes_and_calculates_exact_cents(self) -> None:
        profile = PatientProfile(
            patient_id="patient-2", given_name="Jordan", family_name="Rivera",
            birth_date="1991-02-03", administrative_gender="unknown",
        )
        fields = {"member_name": "JORDAN RIVERA", "member_id": "BCBS-456", "group_number": "GROUP-789"}
        case = documented.pending_case(fields)
        case["id"] = "case-docs"
        documented.confirm_intake(case, profile, fields)
        self.assertEqual(case["next_stage"], "eligibility")
        self.assertEqual(case["service_lines"], [])
        documented.add_document(case, "eligibility", {
            "id": "eligibility", "filename": "eligibility.pdf", "sha256": "b" * 64,
            "observed_at": "2026-08-24", "reference": "Patient eligibility response",
        })
        documented.add_document(case, "contract", {
            "id": "contract", "filename": "contract.pdf", "sha256": "c" * 64,
            "observed_at": "2026-08-24", "reference": "NMCT provider agreement",
        })
        documented.confirm_evidence(case, {
            "clinic": "NMCT", "payer": "BCBSTX", "plan_name": "Blue Choice PPO Medical",
            "eligibility_observed_at": date(2026, 8, 24), "coverage_status": "active",
            "network_status": "in_network", "coinsurance_percent": 20, "copay_cents": 2500,
            "individual_deductible_remaining_cents": 5000, "individual_oop_remaining_cents": 10000,
            "referral_status": "not_required", "authorization_status": "not_required",
        })
        documented.advance(case, "network", GOLDEN_MANIFEST["rates_source"])
        documented.set_clinical_context(
            case, "Burning, numbness and tingling in both feet", GOLDEN_MANIFEST["rate_lines"],
        )
        self.assertIn("95886", {line["code"] for line in case["service_lines"]})
        documented.select_services(case, ["99204", "95886"], GOLDEN_MANIFEST["rates_source"])
        documented.advance(case, "authorization", GOLDEN_MANIFEST["rates_source"])
        documented.advance(case, "benefits", GOLDEN_MANIFEST["rates_source"])
        self.assertEqual(case["estimate"]["allowed_cents"], 24078)
        self.assertEqual(case["estimate"]["deductible_applied_cents"], 5000)
        self.assertEqual(case["estimate"]["nominal_coinsurance_cents"], 3816)
        self.assertEqual(case["estimate"]["patient_cents"], 10000)
        self.assertEqual(case["estimate"]["plan_cents"], 14078)
        unpriced = deepcopy(case)
        unpriced["service_lines"].append({"code": "76881", "allowed_cents": None, "selected": True, "confirmed": True})
        self.assertFalse(documented.calculate(unpriced)["estimate_available"])
        out_of_network = deepcopy(case)
        out_of_network["coverage"].update({
            "network_status": "out_of_network", "out_of_network_benefit_status": "not_covered",
            "out_of_network_allowed_cents": None,
        })
        unavailable = documented.calculate(out_of_network)
        self.assertFalse(unavailable["estimate_available"])
        self.assertIsNone(unavailable["patient_cents"])
        out_of_network["coverage"].update({"out_of_network_benefit_status": "covered", "out_of_network_allowed_cents": 25000})
        covered = documented.calculate(out_of_network)
        self.assertTrue(covered["estimate_available"])
        self.assertEqual(covered["allowed_cents"], 25000)
        with self.assertRaisesRegex(golden.GoldenCaseError, "one anticipated"):
            duplicate_office = deepcopy(case)
            duplicate_office["next_stage"] = "service"
            documented.select_services(duplicate_office, ["99203", "99204"], GOLDEN_MANIFEST["rates_source"])

    def test_document_fallback_accepts_free_text_and_validates_patient_evidence(self) -> None:
        generic = documented.service_lines("A concern not present in the reviewed procedure rules", GOLDEN_MANIFEST["rate_lines"])
        self.assertEqual({line["code"] for line in generic}, {"99202", "99203", "99204", "99205"})
        sonography = documented.service_lines("Extremity sonography", GOLDEN_MANIFEST["rate_lines"])
        self.assertEqual({line["code"] for line in sonography if line["allowed_cents"] is None}, {"76881", "76882"})

        def pdf(text: str) -> bytes:
            document = fitz.open()
            page = document.new_page()
            page.insert_textbox(fitz.Rect(72, 72, 520, 760), text)
            return document.tobytes()

        eligibility = pdf(
            "Availity BCBSTX Active Coverage In Network Member ID BCBS-456 "
            "Preferred Provider Option Medical individual benefits response dated 2026-08-24"
        )
        source = documented.inspect_pdf(eligibility, "eligibility.pdf", "eligibility", "BCBS-456")
        self.assertEqual(source["id"], "eligibility")
        with self.assertRaisesRegex(golden.GoldenCaseError, "patient's dated"):
            documented.inspect_pdf(eligibility, "eligibility.pdf", "eligibility", "ANOTHER-ID")

        contract = pdf(
            "Professional Provider Agreement between Blue Cross and Blue Shield and Neurology and Movement Center "
            "of Texas. Authorized Signature. Executed provider participation terms and fee schedule agreement."
        )
        self.assertEqual(documented.inspect_pdf(contract, "agreement.pdf", "contract")["id"], "contract")

    def test_context_suggestions_follow_visit_type_and_condition(self) -> None:
        rates = GOLDEN_MANIFEST["rate_lines"] + [
            {"code": "99214", "description": "Established patient, moderate MDM", "allowed_cents": 11156},
            {"code": "95885", "description": "Needle EMG, limited", "allowed_cents": 5701},
        ]
        neuropathy = documented.service_lines("neuropathy; EMG planned", rates, "established")
        self.assertTrue({"99214", "95885", "95907"}.issubset({line["code"] for line in neuropathy}))
        botox = documented.service_lines("Botox for chronic migraine", rates, "established")
        self.assertTrue({"99214", "64612", "64615"}.issubset({line["code"] for line in botox}))
        heart = documented.service_lines("palpitations and chest pain", rates, "established")
        self.assertTrue({"99214", "93000", "93224", "93015"}.issubset({line["code"] for line in heart}))
        leg = documented.service_lines("leg swelling and calf pain", rates, "established")
        self.assertTrue({"99214", "93970", "93971"}.issubset({line["code"] for line in leg}))
        self.assertFalse({"64642", "64643"}.intersection({line["code"] for line in leg}))
        brain = documented.service_lines("brain symptoms with seizure", rates, "established")
        self.assertTrue({"99214", "70551", "95816"}.issubset({line["code"] for line in brain}))

    def test_card_ocr_accepts_the_reviewed_identification_number_label(self) -> None:
        reviewed_layout = """Member ID Card\nSubscriber Name:\nPAT EXAMPLE\nIdentification Number:\nMEMBER-123\nGroup Number:  GROUP-123\n"""
        with patch("app.insurance.golden._pdf_text", return_value=reviewed_layout):
            fields = golden.read_card(b"%PDF-reviewed-card")
        self.assertEqual(fields, {
            "member_name": "PAT EXAMPLE",
            "member_id": "MEMBER-123",
            "group_number": "GROUP-123",
        })
        self.assertTrue(golden.card_matches({
            **fields, "member_name": "PAT EXAMPLE Choice PPO Plan",
        }, GOLDEN_MANIFEST))

    async def test_registered_ecw_jwks_url_serves_the_public_key_set(self) -> None:
        payload = {"keys": [{"kty": "RSA", "kid": "clinicforce-test-key"}]}
        with patch("app.main.ecw.public_jwks", return_value=payload):
            response = await self.client.get("/.well-known/jwks.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    async def test_readiness_check_requires_a_usable_database_connection(self) -> None:
        with patch("app.api.routers.system.check_database_connection") as check_database_connection:
            response = await self.client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        check_database_connection.assert_called_once_with()

    async def test_database_connection_error_returns_a_safe_service_unavailable_response(self) -> None:
        with patch(
            "app.api.routers.system.check_database_connection",
            side_effect=psycopg.OperationalError("password authentication failed"),
        ):
            response = await self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "detail": "Patient data is temporarily unavailable. Please try again shortly.",
        })

    async def test_login_awaits_persistent_session_creation(self) -> None:
        workspace = {
            "patient_id": "patient-1", "documents": [], "records": [], "storage_provider": "s3",
            "drive_connected": False, "drive_pending_import_count": 0, "drive_sync_message": None,
            "drive_access_upgrade_required": False,
        }
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._load_workspace", new=AsyncMock(return_value=workspace)),
            patch("app.main._set_patient_session", new=AsyncMock()) as set_patient_session,
        ):
            response = await self.client.post(
                "/api/patient/login", json={"patient_id": "patient-1", "password": "long-password"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        set_patient_session.assert_awaited_once()

    async def test_epic_authorization_sets_only_an_http_only_state_cookie(self) -> None:
        endpoint = Mock(id="epic:demo:1", name="Demo Health", fhir_base="https://fhir.demo.example")
        started = Mock(authorization_url="https://mychart.demo.example/authorize", state_cookie="sealed-state")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_epic_endpoint", return_value=endpoint),
            patch("app.main.epic.begin_authorization", new=AsyncMock(return_value=started)),
        ):
            response = await self.client.post("/api/patient/epic/authorize", json={"endpoint_id": endpoint.id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"authorization_url": started.authorization_url})
        self.assertIn("clinicforce_epic_oauth=sealed-state", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertNotIn("access_token", response.text)

    async def test_ecw_authorization_normalizes_bedbaa_and_sets_only_state_cookie(self) -> None:
        started = Mock(authorization_url="https://auth.demo.example/authorize", state_cookie="sealed-ecw-state")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.ecw.begin_authorization", new=AsyncMock(return_value=started)) as begin,
        ):
            response = await self.client.post("/api/patient/ecw/authorize", json={"practice_code": "bedbaa"})
        self.assertEqual(response.status_code, 200, response.text)
        begin.assert_awaited_once_with(
            "BEDBAA", "patient-1", "http://localhost:8000/api/patient/ecw/callback", popup=False,
        )
        self.assertEqual(response.json(), {"authorization_url": started.authorization_url})
        self.assertIn("clinicforce_ecw_oauth=sealed-ecw-state", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertNotIn("access_token", response.text)

    async def test_ecw_record_is_review_only_and_surfaces_partial_scope_access(self) -> None:
        live = SimpleNamespace(practice_code="BEDBAA")
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {"resourceType": "Patient", "id": "remote-1"}}]}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._live_ecw_session", new=AsyncMock(return_value=live)),
            patch("app.main.ecw.fetch_record", new=AsyncMock(return_value=(bundle, {"Patient": "ok", "Condition": "forbidden"}))),
            patch("app.main.ecw.source_metadata", return_value={"organization_name": "eClinicalWorks / healow (practice BEDBAA)", "practice_code": "BEDBAA", "retrieved_at": "2026-01-01T00:00:00+00:00"}),
            patch("app.main.add_record") as add_record,
        ):
            response = await self.client.get("/api/patient/ecw/record")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["availability"]["Condition"], "forbidden")
        add_record.assert_not_called()

    async def test_ecw_expired_session_returns_reconnect_status(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._live_ecw_session", new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Your eClinicalWorks session has ended. Reconnect healow to continue."))),
        ):
            response = await self.client.get("/api/patient/ecw/record")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Reconnect healow", response.json()["detail"])

    async def test_ecw_import_requires_review_then_persists_ecw_provenance(self) -> None:
        review_bundle = {
            "resourceType": "Bundle", "type": "collection", "entry": [
                {"resource": {"resourceType": "Patient", "id": "remote-1"}},
                {"resource": {"resourceType": "Condition", "id": "condition-1", "code": {"text": "Asthma"}}},
                {"resource": {"resourceType": "DocumentReference", "id": "document-1", "content": [{"attachment": {"data": "secret", "url": "https://files.example/pdf", "title": "report.pdf"}}]}},
            ],
        }
        live = SimpleNamespace(practice_code="BEDBAA", review_bundle=review_bundle)
        workspace = {"patient_id": "patient-1", "documents": [], "records": [], "storage_provider": "s3", "drive_connected": False, "drive_pending_import_count": 0, "drive_sync_message": None, "drive_access_upgrade_required": False}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._live_ecw_session", new=AsyncMock(return_value=live)),
            patch("app.main._load_workspace", new=AsyncMock(return_value=workspace)),
            patch("app.main.add_record") as add_record,
            patch("app.main.ecw.clear_live_session") as clear_live_session,
        ):
            response = await self.client.post("/api/patient/ecw/import")
        self.assertEqual(response.status_code, 200, response.text)
        stored = add_record.call_args.args[3]
        self.assertEqual(add_record.call_args.args[:3], ("patient-1", "ecw", "eClinicalWorks / healow import from practice BEDBAA"))
        self.assertEqual(len(stored["entry"]), 2)
        condition = stored["entry"][0]["resource"]
        self.assertIn({"system": "https://clinicforce.example/fhir/source", "code": "ecw-healow", "display": "eClinicalWorks / healow practice BEDBAA"}, condition["meta"]["tag"])
        attachment = stored["entry"][1]["resource"]["content"][0]["attachment"]
        self.assertNotIn("data", attachment)
        self.assertNotIn("url", attachment)
        clear_live_session.assert_called_once()

    async def test_ecw_import_without_a_review_is_rejected(self) -> None:
        live = SimpleNamespace(practice_code="BEDBAA", review_bundle=None)
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._live_ecw_session", new=AsyncMock(return_value=live)),
        ):
            response = await self.client.post("/api/patient/ecw/import")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Review the eClinicalWorks data", response.json()["detail"])

    async def test_epic_popup_callback_notifies_opener_without_redirecting(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.epic.popup_requested", return_value=True),
            patch("app.main.epic.complete_authorization", new=AsyncMock(return_value=("live-session", True))),
        ):
            response = await self.client.get("/api/patient/epic/callback?state=state&code=code")
        self.assertEqual(response.status_code, 200)
        self.assertIn("clinicforce:epic-connected", response.text)
        self.assertIn("window.opener.postMessage", response.text)
        self.assertIn("clinicforce_epic_live=live-session", response.headers["set-cookie"])

    async def test_epic_popup_callback_reports_denied_authorization_to_opener(self) -> None:
        with patch("app.main.epic.popup_requested", return_value=True):
            response = await self.client.get("/api/patient/epic/callback?error=access_denied")
        self.assertEqual(response.status_code, 200)
        self.assertIn("clinicforce:epic-not-connected", response.text)
        self.assertIn("Cache-Control", response.headers)

    async def test_epic_record_is_live_and_does_not_call_persistent_ingestion(self) -> None:
        live_session = Mock()
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {"resourceType": "Condition", "id": "c1"}}]}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._live_epic_session", new=AsyncMock(return_value=live_session)),
            patch("app.main.epic.fetch_record", new=AsyncMock(return_value=(bundle, {"Condition": "ok"}))),
            patch("app.main.epic.source_metadata", return_value={"organization_name": "Demo Health", "retrieved_at": "2026-08-14T00:00:00+00:00"}),
            patch("app.main._backfill_profile_from_live_epic", new=AsyncMock(return_value=MATCHABLE_PROFILE)) as backfill_profile,
            patch("app.main.add_document") as add_document,
            patch("app.main.add_record") as add_record,
            patch("app.main.post_to_fhir", new=AsyncMock()) as post_to_fhir,
        ):
            response = await self.client.get("/api/patient/epic/record")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["bundle"], bundle)
        backfill_profile.assert_awaited_once_with("patient-1", bundle)
        add_document.assert_not_called()
        add_record.assert_not_called()
        post_to_fhir.assert_not_awaited()

    async def test_standard_share_combines_current_epic_data_into_one_qr(self) -> None:
        live_session = Mock()
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [
            {"resource": {"resourceType": "Patient", "id": "epic-patient", "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}},
            {"resource": {"resourceType": "Condition", "id": "c1", "code": {"text": "Asthma"}}},
        ]}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._load_workspace", new=AsyncMock(return_value={})),
            patch("app.main.list_documents", return_value=[]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=None),
            patch("app.main._backfill_profile_from_live_epic", new=AsyncMock(return_value=None)),
            patch("app.main._live_epic_session", new=AsyncMock(return_value=live_session)),
            patch("app.main.epic.fetch_record", new=AsyncMock(return_value=(bundle, {}))),
            patch("app.main.epic.source_metadata", return_value={"organization_name": "Demo Health", "retrieved_at": "2026-08-14T00:00:00+00:00"}),
            patch("app.main._create_upload_shl", return_value=("shlink:/combined", "qr", "manifest-1")) as create_snapshot,
            patch("app.main.get_storage_preference", return_value="s3"),
            patch("app.main.add_document") as add_document,
            patch("app.main.post_to_fhir", new=AsyncMock()) as post_to_fhir,
        ):
            self.client.cookies.set("clinicforce_epic_live", "live-session")
            response = await self.client.post("/api/patient/generate-link", json={"passcode": "1234"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["epic_included"])
        shared_bundle = create_snapshot.call_args.args[1]
        self.assertEqual(shared_bundle["entry"][0]["resource"]["resourceType"], "Patient")
        shared_condition = next(entry["resource"] for entry in shared_bundle["entry"] if entry["resource"]["resourceType"] == "Condition")
        self.assertEqual(shared_condition["code"]["text"], "Asthma")
        self.assertEqual(shared_condition["meta"]["tag"][-1]["display"], "Demo Health")
        add_document.assert_not_called()
        post_to_fhir.assert_not_awaited()

    async def test_ecw_compat_share_uses_a_separate_no_pin_transport_mode(self) -> None:
        exchange_bundle = {"resourceType": "Bundle", "type": "collection", "entry": [
            {"resource": {"resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}},
            {"resource": {"resourceType": "Condition", "id": "condition-1", "code": {"text": "Asthma"}}},
        ]}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._load_workspace", new=AsyncMock(return_value={})),
            patch("app.main.list_documents", return_value=[]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=MATCHABLE_PROFILE),
            patch("app.main.build_exchange_bundle", return_value=exchange_bundle),
            patch("app.main._create_upload_shl", return_value=("shlink:/bare", "qr", "manifest-1")) as create_share,
            patch("app.main.get_storage_preference", return_value="s3"),
        ):
            response = await self.client.post("/api/patient/generate-link", json={"share_mode": "ecw_compat"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["share_mode"], "ecw_compat")
        self.assertEqual(create_share.call_args.args[2:], (None, "ecw_compat", False))

    async def test_share_rejects_an_unmatchable_patient_before_creating_a_qr(self) -> None:
        document = SimpleNamespace(
            id=7, filename="report.pdf", uploaded_at="2026-08-19T12:00:00+00:00",
            fhir_bundle={"resourceType": "Bundle", "entry": [{"resource": {
                "resourceType": "Condition", "code": {"text": "Asthma"},
            }}]},
        )
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main._load_workspace", new=AsyncMock(return_value={})),
            patch("app.main.list_documents", return_value=[document]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=None),
            patch("app.main._create_upload_shl") as create_share,
        ):
            response = await self.client.post("/api/patient/generate-link", json={"passcode": "1234"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("demographic profile", response.json()["detail"])
        create_share.assert_not_called()

    async def test_profile_endpoint_saves_the_us_core_matching_fields(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.update_patient_profile", return_value=MATCHABLE_PROFILE) as save_profile,
            patch("app.main.get_patient_profile_suggestions", return_value=SimpleNamespace(given_name="Pat", family_name="Example")),
        ):
            response = await self.client.put("/api/patient/profile", json={
                "given_name": "Pat", "family_name": "Example", "birth_date": "1980-01-01",
                "administrative_gender": "unknown",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["birth_date"], "1980-01-01")
        self.assertEqual(response.json()["suggested_given_name"], "Pat")
        save_profile.assert_called_once_with("patient-1", "Pat", "Example", "1980-01-01", "unknown")

    async def test_profile_endpoint_reads_nullable_existing_patient_demographics(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_patient_profile", return_value=None),
            patch("app.main.get_patient_profile_suggestions", return_value=SimpleNamespace(given_name=None, family_name=None)),
        ):
            response = await self.client.get("/api/patient/profile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "given_name": None, "family_name": None, "birth_date": None, "administrative_gender": None,
            "suggested_given_name": None, "suggested_family_name": None,
        })

    async def test_external_shl_import_normalizes_and_marks_user_confirmed_resources(self) -> None:
        workspace = {"patient_id": "patient-1", "documents": [], "records": [], "storage_provider": "s3", "drive_connected": False, "drive_pending_import_count": 0, "drive_sync_message": None, "drive_access_upgrade_required": False}
        bundle = {"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": "Patient", "name": [{"given": ["Outside"]}]}},
            {"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}, "subject": {"reference": "Patient/outside"}}},
            {"resource": {"resourceType": "Binary", "contentType": "application/pdf", "data": "not-imported"}},
        ]}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.add_record") as add_record,
            patch("app.main._load_workspace", new=AsyncMock(return_value=workspace)),
        ):
            response = await self.client.post("/api/patient/external-shl-import", json={"source_host": "ehr.example", "bundle": bundle})
        self.assertEqual(response.status_code, 200, response.text)
        normalized = add_record.call_args.args[3]
        self.assertEqual([entry["resource"]["resourceType"] for entry in normalized["entry"]], ["Condition"])
        condition = normalized["entry"][0]["resource"]
        self.assertEqual(condition["subject"], {"reference": "Patient/patient-1"})
        self.assertIn({"system": "https://clinicforce.example/fhir/source", "code": "external-shl", "display": "External SMART Health Link: ehr.example"}, condition["meta"]["tag"])
        self.assertIn({"system": "https://clinicforce.example/fhir/verification", "code": "unverified", "display": "External information; clinician reconciliation required"}, condition["meta"]["tag"])

    def test_epic_share_snapshot_strips_document_attachment_urls_and_data(self) -> None:
        bundle = {"resourceType": "Bundle", "entry": [{"resource": {
            "resourceType": "DocumentReference", "content": [{"attachment": {
                "contentType": "application/pdf", "url": "https://example.test/Binary/1", "data": "bytes",
            }}],
        }}]}
        snapshot = _epic_share_snapshot(bundle, {"organization_name": "Demo Health"})
        attachment = snapshot["entry"][0]["resource"]["content"][0]["attachment"]
        self.assertNotIn("url", attachment)
        self.assertNotIn("data", attachment)
        self.assertEqual(snapshot["entry"][0]["resource"]["meta"]["tag"][-1]["code"], "epic-mychart")
        self.assertIn("url", bundle["entry"][0]["resource"]["content"][0]["attachment"])

    def test_exchange_bundle_is_explicitly_source_tagged_and_pdf_metadata_only(self) -> None:
        document = SimpleNamespace(
            id=7,
            filename="discharge.pdf",
            uploaded_at="2026-08-19T12:00:00+00:00",
            fhir_bundle={"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}, "subject": {"reference": "Patient/legacy"}, "extension": [{
                    "url": "https://clinicforce.example/fhir/StructureDefinition/source-citation", "extension": [
                        {"url": "source_filename", "valueString": "discharge.pdf"}, {"url": "page_number", "valuePositiveInt": 2},
                    ],
                }]}},
                {"resource": {"resourceType": "Encounter", "status": "finished"}},
            ]},
        )
        record = SimpleNamespace(
            fhir_bundle={"resourceType": "Bundle", "entry": [{"resource": {
                "resourceType": "MedicationStatement", "status": "active",
                "medicationCodeableConcept": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "1191", "display": "Aspirin"}]},
            }}]},
        )
        epic = _epic_share_snapshot({"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": "Patient", "id": "epic-patient", "name": [{"family": "Example", "given": ["Pat"]}], "birthDate": "1980-01-01"}},
            {"resource": {"resourceType": "Observation", "status": "final", "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4"}]}, "valueQuantity": {"value": 5.8, "system": "http://unitsofmeasure.org", "code": "%", "unit": "%"}}},
            {"resource": {"resourceType": "DocumentReference", "status": "current", "content": [{"attachment": {"url": "https://epic.example/file", "data": "cGRm", "title": "Epic PDF"}}]}},
        ]}, {"organization_name": "Demo Health"})

        bundle = build_exchange_bundle("patient-1", [document], [record], epic)
        resources = [entry["resource"] for entry in bundle["entry"]]
        resource_types = [resource["resourceType"] for resource in resources]
        self.assertEqual(bundle["type"], "collection")
        self.assertEqual(resource_types.count("Patient"), 1)
        self.assertNotIn("Encounter", resource_types)

        patient = resources[0]
        self.assertEqual(patient["id"], "patient-1")
        self.assertEqual(patient["name"][0]["family"], "Example")
        self.assertEqual(patient["birthDate"], "1980-01-01")
        self.assertIn({"system": "https://clinicforce.example/fhir/source", "code": "epic-mychart", "display": "Demo Health"}, patient["meta"]["tag"])

        condition = next(resource for resource in resources if resource["resourceType"] == "Condition")
        self.assertEqual(condition["subject"], {"reference": "Patient/patient-1"})
        self.assertIn({"system": "https://clinicforce.example/fhir/source", "code": "document-extracted", "display": "Extracted from patient-uploaded document"}, condition["meta"]["tag"])
        self.assertIn({"system": "https://clinicforce.example/fhir/verification", "code": "unverified", "display": "External document extraction; clinician reconciliation required"}, condition["meta"]["tag"])
        self.assertIn({"url": "document_id", "valuePositiveInt": 7}, condition["extension"][0]["extension"])

        medication = next(resource for resource in resources if resource["resourceType"] == "MedicationStatement")
        self.assertEqual(medication["subject"], {"reference": "Patient/patient-1"})
        self.assertEqual(medication["medicationCodeableConcept"]["coding"][0]["system"], "http://www.nlm.nih.gov/research/umls/rxnorm")
        self.assertIn({"system": "https://clinicforce.example/fhir/source", "code": "patient-entered", "display": "Patient-entered"}, medication["meta"]["tag"])

        observation = next(resource for resource in resources if resource["resourceType"] == "Observation")
        self.assertEqual(observation["code"]["coding"][0]["system"], "http://loinc.org")
        self.assertEqual(observation["valueQuantity"]["system"], "http://unitsofmeasure.org")
        self.assertEqual(observation["subject"], {"reference": "Patient/patient-1"})

        document_references = [resource for resource in resources if resource["resourceType"] == "DocumentReference"]
        uploaded_reference = next(resource for resource in document_references if resource["id"] == "uploaded-document-7")
        self.assertEqual(uploaded_reference["content"][0]["attachment"], {"contentType": "application/pdf", "title": "discharge.pdf"})
        epic_reference = next(resource for resource in document_references if resource.get("id") != "uploaded-document-7")
        self.assertNotIn("url", epic_reference["content"][0]["attachment"])
        self.assertNotIn("data", epic_reference["content"][0]["attachment"])

        saved_profile = PatientProfile(
            patient_id="patient-1", given_name="Saved", family_name="Profile",
            birth_date="1979-12-31", administrative_gender="female",
        )
        profiled_bundle = build_exchange_bundle("patient-1", [], [], epic, saved_profile)
        profiled_patient = profiled_bundle["entry"][0]["resource"]
        self.assertEqual(profiled_patient["name"], [{"use": "official", "given": ["Saved"], "family": "Profile"}])
        self.assertEqual(profiled_patient["birthDate"], "1979-12-31")
        self.assertEqual(profiled_patient["gender"], "female")

        compat_bundle = build_exchange_bundle("patient-1", [document], [record], epic, saved_profile, ecw_compat=True)
        for entry in compat_bundle["entry"]:
            res = entry["resource"]
            sec = res.get("meta", {}).get("security", [])
            self.assertTrue(any(s.get("system") == "https://smarthealth.cards/ial" and s.get("code") == "IAL2" for s in sec))
            self.assertTrue(any(s.get("system") == "http://hl7.org/fhir/identity-assuranceLevel" and s.get("code") == "level3" for s in sec))

        patient_res = compat_bundle["entry"][0]["resource"]
        self.assertIn("http://hl7.org/fhir/us/identity-matching/StructureDefinition/IDI-Patient-L2", patient_res.get("meta", {}).get("profile", []))

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
            patch("app.main._create_upload_shl", return_value=("shlink:/test", "qr", "manifest-token")),
        ):
            response = await self.client.post(
                "/api/test/integration", json={"patient_id": "patient-1", "passcode": "1234"}
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["fhir_post_status"]), 2)
        self.assertTrue(payload["shl_uri"].startswith("shlink:/"))
        self.assertTrue(payload["qr_code_base64"])

    async def test_manifest_rejects_an_incorrect_passcode_with_remaining_attempts(self) -> None:
        manifest_id = "A" * 43
        with patch(
            "app.main.verify_manifest_pin",
            side_effect=InvalidManifestPin(4),
        ):
            response = await self.client.post(
                f"/manifest/{manifest_id}",
                json={"recipient": "eClinicalWorks", "passcode": "0000"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"remainingAttempts": 4})

    async def test_manifest_requires_a_recipient(self) -> None:
        response = await self.client.post(
            f"/manifest/{'A' * 43}",
            json={"passcode": "1234"},
        )
        self.assertEqual(response.status_code, 422)

    async def test_manifest_returns_404_after_the_final_lifetime_pin_failure(self) -> None:
        failures = [InvalidManifestPin(remaining) for remaining in (4, 3, 2, 1, 0)]
        with patch("app.main.verify_manifest_pin", side_effect=[*failures, None]):
            for expected_remaining in (4, 3, 2, 1, 0):
                response = await self.client.post(
                    f"/manifest/{'A' * 43}",
                    json={"recipient": "eClinicalWorks", "passcode": "0000"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"remainingAttempts": expected_remaining})

            response = await self.client.post(
                f"/manifest/{'A' * 43}",
                json={"recipient": "eClinicalWorks", "passcode": "1234"},
            )
        self.assertEqual(response.status_code, 404)

    async def test_manifest_accepts_standard_receiver_fields_and_ignores_extensions(self) -> None:
        with patch(
            "app.main.verify_manifest_pin",
            return_value={"encrypted_jwe": "encrypted", "patient_id": "patient-1", "hashed_pin": "hash"},
        ) as verify:
            response = await self.client.post(
                f"/manifest/{'A' * 43}",
                json={"recipient": "eClinicalWorks", "passcode": "1234", "embeddedLengthMax": 1_000, "futureExtension": True},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["files"][0]["embedded"], "encrypted")
        verify.assert_called_once_with("A" * 43, "1234")

    async def test_manifest_returns_404_when_revoked_or_expired(self) -> None:
        with patch("app.main.verify_manifest_pin", return_value=None):
            response = await self.client.post(f"/manifest/{'A' * 43}", json={"recipient": "eClinicalWorks", "passcode": "1234"})
        self.assertEqual(response.status_code, 404)

    async def test_manifest_uses_short_lived_location_when_receiver_rejects_embedded_size(self) -> None:
        with (
            patch("app.main.verify_manifest_pin", return_value={"encrypted_jwe": "encrypted-payload", "patient_id": "patient-1", "hashed_pin": "hash"}),
            patch("app.main.put_shl_payload", return_value="clinicforce/shl/token.jwe") as put_payload,
            patch("app.main.presign_shl_payload", return_value="https://s3.example.test/signed") as presign,
        ):
            response = await self.client.post(
                f"/manifest/{'A' * 43}",
                json={"recipient": "eClinicalWorks", "passcode": "1234", "embeddedLengthMax": 1},
            )
        self.assertEqual(response.status_code, 200, response.text)
        file = response.json()["files"][0]
        self.assertEqual(file, {"contentType": "application/fhir+json", "location": "https://s3.example.test/signed"})
        put_payload.assert_called_once_with("A" * 43, "encrypted-payload")
        presign.assert_called_once_with("clinicforce/shl/token.jwe")

    async def test_upload_requires_configured_selected_storage(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_storage_preference", return_value="s3"),
            patch("app.main.get_drive_connection", return_value=None),
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
            patch("app.main.get_storage_preference", return_value="s3"),
            patch("app.main.get_drive_connection", return_value=None),
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

    async def test_drive_upload_never_writes_the_source_to_s3(self) -> None:
        connection = Mock()
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_storage_preference", return_value="google_drive"),
            patch("app.main.get_drive_connection", return_value=connection),
            patch("app.main._store_encrypted_drive_pdf", new=AsyncMock(return_value="drive-source-id")) as put_drive,
            patch("app.main.put_patient_pdf") as put_s3,
            patch("app.main.extract_pdf_to_fhir_bundle", new=AsyncMock(return_value={"resourceType": "Bundle", "type": "collection", "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}]})),
            patch("app.main.post_to_fhir", new=AsyncMock()),
            patch("app.main.add_document", return_value=1) as add_document,
            patch("app.main.list_documents", return_value=[]),
            patch("app.main.list_records", return_value=[]),
        ):
            response = await self.client.post(
                "/api/patient/upload", data={"patient_id": "patient-1", "password": "long-password"},
                files={"file": ("record.pdf", b"%PDF-1.4 valid", "application/pdf")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        put_drive.assert_awaited_once()
        put_s3.assert_not_called()
        self.assertEqual(add_document.call_args.args[-1], "google_drive")

    async def test_drive_workspace_removes_documents_deleted_by_the_patient(self) -> None:
        deleted_drive_document = Mock(
            id=12,
            filename="deleted-report.pdf",
            uploaded_at="2026-08-13T00:00:00+00:00",
            fhir_bundle={"entry": []},
            s3_key="deleted-drive-file-id",
            storage_provider="google_drive",
            extraction_version="v1",
        )
        connection = Mock(summary_file_id=None)
        with (
            patch("app.main.list_documents", side_effect=[[deleted_drive_document], []]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main.get_storage_preference", return_value="google_drive"),
            patch("app.main.get_drive_connection", return_value=connection),
            patch("app.main.google_drive.existing_file_ids", return_value=set()),
            patch("app.main.remove_documents", return_value=1) as remove_documents,
            patch("app.main.revoke_patient_manifests", return_value=1) as revoke_manifests,
            patch("app.main.delete_drive_file_crypto"),
            patch("app.main.google_drive.list_folder_files", return_value=[]),
            patch("app.main.list_pending_drive_deletions", return_value=[]),
        ):
            workspace = await __import__("app.main", fromlist=["_load_workspace"])._load_workspace("patient-1")

        self.assertEqual(workspace["documents"], [])
        remove_documents.assert_called_once_with("patient-1", [12])
        revoke_manifests.assert_called_once_with("patient-1")

    async def test_unchanged_drive_record_does_not_replace_its_single_summary_file(self) -> None:
        bundle = {
            "resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}],
        }
        fingerprint = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        document = Mock(fhir_bundle=bundle)
        connection = Mock(summary_fingerprint=fingerprint, summary_file_id="drive-summary-id")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.list_documents", return_value=[document]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=MATCHABLE_PROFILE),
            patch("app.main._create_upload_shl", return_value=("shlink:/value", "qr", "manifest-1")),
            patch("app.main.get_storage_preference", return_value="google_drive"),
            patch("app.main.get_drive_connection", return_value=connection),
            patch("app.main.google_drive.file_exists", return_value=True),
            patch("app.main.get_drive_file_crypto", return_value=Mock()),
            patch("app.main.summarize_fhir_bundle", new=AsyncMock()) as summarize,
            patch("app.main._replace_encrypted_drive_summary") as replace_summary,
        ):
            response = await self.client.post(
                "/api/patient/generate-link",
                json={"patient_id": "patient-1", "password": "long-password", "passcode": "1234"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        summarize.assert_not_awaited()
        replace_summary.assert_not_called()

    async def test_changed_drive_record_replaces_summary_without_embedded_source_capabilities(self) -> None:
        bundle = {
            "resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}],
        }
        document = Mock(fhir_bundle=bundle)
        connection = Mock(summary_fingerprint="stale", summary_file_id="drive-summary-id")
        summary = {
            "narrative": "Clinical history reviewed.", "vitals": [], "labs": [], "medications": [],
            "conditions": [{"name": "Asthma", "citation": {"source_filename": "report.pdf", "page_number": 1, "document_id": 7}}],
            "allergies": [], "care_events": [],
        }
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.list_documents", return_value=[document]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=MATCHABLE_PROFILE),
            patch("app.main._create_upload_shl", return_value=("shlink:/value", "qr", "manifest-1")),
            patch("app.main.get_storage_preference", return_value="google_drive"),
            patch("app.main.get_drive_connection", return_value=connection),
            patch("app.main.google_drive.file_exists", return_value=True),
            patch("app.main.get_drive_file_crypto", return_value=Mock()),
            patch("app.main.summarize_fhir_bundle", new=AsyncMock(return_value=summary)) as summarize,
            patch("app.main.render_doctor_pdf_bytes", return_value=b"%PDF-1.4"),
            patch("app.main._replace_encrypted_drive_summary", new=AsyncMock(return_value="drive-summary-id")) as replace_summary,
            patch("app.main.set_drive_summary_file", return_value=True),
        ):
            response = await self.client.post(
                "/api/patient/generate-link",
                json={"patient_id": "patient-1", "password": "long-password", "passcode": "1234"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(summarize.await_args.args[0], bundle)
        self.assertNotIn("download_url", summary["conditions"][0]["citation"])
        replace_summary.assert_called_once()

    async def test_legacy_drive_summary_is_replaced_with_an_encrypted_summary(self) -> None:
        bundle = {
            "resourceType": "Bundle", "type": "collection",
            "entry": [{"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}],
        }
        fingerprint = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        document = Mock(fhir_bundle=bundle)
        connection = Mock(summary_fingerprint=fingerprint, summary_file_id="drive-summary-id")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.list_documents", return_value=[document]),
            patch("app.main.list_records", return_value=[]),
            patch("app.main._ensure_documents_extracted", new=AsyncMock()),
            patch("app.main.get_patient_profile", return_value=MATCHABLE_PROFILE),
            patch("app.main._create_upload_shl", return_value=("shlink:/value", "qr", "manifest-1")),
            patch("app.main.get_storage_preference", return_value="google_drive"),
            patch("app.main.get_drive_connection", return_value=connection),
            patch("app.main.google_drive.file_exists", return_value=True),
            patch("app.main.get_drive_file_crypto", return_value=None),
            patch("app.main._replace_encrypted_drive_summary", new=AsyncMock(return_value="drive-summary-id")) as replace_summary,
            patch("app.main.summarize_fhir_bundle", new=AsyncMock(return_value={"narrative": "", "vitals": [], "labs": [], "medications": [], "conditions": [], "allergies": [], "care_events": []})) as summarize,
            patch("app.main.render_doctor_pdf_bytes", return_value=b"%PDF-1.4"),
            patch("app.main.set_drive_summary_file", return_value=True),
        ):
            response = await self.client.post(
                "/api/patient/generate-link",
                json={"patient_id": "patient-1", "password": "long-password", "passcode": "1234"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        replace_summary.assert_awaited_once()
        summarize.assert_awaited_once()

    async def test_patient_record_contract_matches_the_browser_form(self) -> None:
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_storage_preference", return_value="s3"),
            patch("app.main.get_drive_connection", return_value=None),
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
            patch("app.main.get_s3_pdf", return_value=b"%PDF-1.4") as get_pdf,
        ):
            response = await self.client.post(
                "/api/patient/documents/7/download",
                json={"patient_id": "patient-1", "password": "long-password"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4")
        get_pdf.assert_called_once_with(document.s3_key)

    async def test_bulk_download_returns_only_the_selected_documents_as_a_private_zip(self) -> None:
        first = Mock(id=7, filename="cardiology.pdf")
        second = Mock(id=8, filename="cardiology.pdf")
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_document", side_effect=[first, second]),
            patch("app.main._read_document_bytes", new=AsyncMock(side_effect=[b"first", b"second"])),
        ):
            response = await self.client.post(
                "/api/patient/documents/bulk-download",
                json={"patient_id": "patient-1", "password": "long-password", "document_ids": [7, 8]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertIn("clinicforce-reports.zip", response.headers["content-disposition"])
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(set(archive.namelist()), {"cardiology.pdf", "cardiology_2.pdf"})
            self.assertEqual(archive.read("cardiology.pdf"), b"first")
            self.assertEqual(archive.read("cardiology_2.pdf"), b"second")

    async def test_bulk_delete_removes_every_selected_document_and_revokes_once(self) -> None:
        first = Mock(id=7, s3_key="patient-1/first.pdf", storage_provider="s3")
        second = Mock(id=8, s3_key="patient-1/second.pdf", storage_provider="s3")
        workspace = {"documents": [], "records": []}
        with (
            patch("app.main._authenticate_patient", new=AsyncMock(return_value="patient-1")),
            patch("app.main.get_document", side_effect=[first, second]),
            patch("app.main.delete_s3_object") as delete_object,
            patch("app.main.remove_documents", return_value=1) as remove_documents,
            patch("app.main.revoke_patient_manifests", return_value=1) as revoke_manifests,
            patch("app.main._load_workspace", new=AsyncMock(return_value=workspace)),
        ):
            response = await self.client.post(
                "/api/patient/documents/bulk-delete",
                json={"patient_id": "patient-1", "password": "long-password", "document_ids": [7, 8]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(delete_object.call_args_list[0].args, ("patient-1/first.pdf",))
        self.assertEqual(delete_object.call_args_list[1].args, ("patient-1/second.pdf",))
        self.assertEqual(remove_documents.call_args_list[0].args, ("patient-1", [7]))
        self.assertEqual(remove_documents.call_args_list[1].args, ("patient-1", [8]))
        revoke_manifests.assert_called_once_with("patient-1")
        self.assertEqual(response.json()["drive_sync_message"], "2 reports were deleted. Existing QR codes were revoked.")

    async def test_doctor_source_download_uses_a_verified_single_document_capability(self) -> None:
        document = Mock(s3_key="clinicforce/patients/hash/source/id/record.pdf", filename="record.pdf")
        with (
            patch("app.main.verify_document_download_token", return_value="manifest-1") as verify,
            patch("app.main.get_document_by_id", return_value=document),
            patch("app.main.get_manifest", return_value={"patient_id": "patient-1"}),
            patch("app.main.get_document", return_value=document),
            patch("app.main.get_s3_pdf", return_value=b"%PDF-1.4") as get_pdf,
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
            patch("app.main.verify_manifest_pin", return_value={"patient_id": "patient-1", "encrypted_jwe": encrypted, "hashed_pin": hash_pin("1234")}),
            patch("app.main.get_drive_connection", return_value=None),
            patch("app.main.summarize_fhir_bundle", new=AsyncMock(return_value=summary)),
            patch("app.main.render_doctor_pdf_bytes", return_value=b"%PDF-1.4 test"),
        ):
            response = await self.client.post(
                f"/api/doctor/decrypt-and-summarize", json={"manifest_id": str(uuid4()), "key": key, "passcode": "1234"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["summary"], summary)
        self.assertEqual(response.json()["pdf_url"], "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode())

    async def test_doctor_summary_requires_manifest_pin_before_decrypting(self) -> None:
        with (
            patch("app.main.verify_manifest_pin", return_value=None),
            patch("app.main._decrypt_jwe_payload") as decrypt,
        ):
            response = await self.client.post(
                "/api/doctor/decrypt-and-summarize",
                json={"manifest_id": str(uuid4()), "key": generate_symmetric_key(), "passcode": "0000"},
            )
        self.assertEqual(response.status_code, 401)
        decrypt.assert_not_called()

    async def test_doctor_summary_returns_401_when_lifetime_pin_limit_rejects_the_request(self) -> None:
        with patch("app.main.verify_manifest_pin", side_effect=InvalidManifestPin(0)):
            response = await self.client.post(
                "/api/doctor/decrypt-and-summarize",
                json={"manifest_id": "A" * 43, "key": generate_symmetric_key(), "passcode": "0000"},
            )
        self.assertEqual(response.status_code, 401)

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

    async def test_sparse_summary_preserves_qualitative_numeric_and_condition_facts(self) -> None:
        bundle = {
            "resourceType": "Bundle",
            "entry": [
                {"resource": {
                    "resourceType": "Observation", "code": {"text": "Hemoglobin A1c"},
                    "valueQuantity": {"value": 5.8, "unit": "%"}, "effectiveDateTime": "2026-08-10",
                    "extension": [{"url": "https://clinicforce.example/fhir/StructureDefinition/source-citation", "extension": [
                        {"url": "source_filename", "valueString": "labs.pdf"}, {"url": "page_number", "valuePositiveInt": 2},
                    ]}],
                }},
                {"resource": {
                    "resourceType": "Observation", "code": {"text": "COVID-19 result"},
                    "valueCodeableConcept": {"text": "Not detected"}, "effectiveDateTime": "2026-08-10",
                }},
                {"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}},
            ],
        }
        summary = await summarize_fhir_bundle(bundle)
        self.assertEqual(summary["labs"][0]["value"], 5.8)
        self.assertEqual(summary["labs"][1]["value"], "Not detected")
        self.assertEqual(summary["conditions"][0]["name"], "Asthma")
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock()
            generate_doctor_pdf(summary, "/tmp/mock-summary.pdf")
        rendered = html.call_args.kwargs["string"]
        for expected in ("Hemoglobin A1c", "5.8", "COVID-19 result", "Not detected", "Asthma", "labs.pdf · p. 2"):
            self.assertIn(expected, rendered)

    async def test_epic_allergy_and_document_details_are_in_the_clinical_summary(self) -> None:
        bundle = _epic_share_snapshot({
            "resourceType": "Bundle", "type": "collection", "entry": [
                {"resource": {"resourceType": "AllergyIntolerance", "code": {"text": "Peanuts"}, "clinicalStatus": {"text": "Active"}}},
                {"resource": {"resourceType": "DocumentReference", "description": "Hospital discharge summary", "date": "2026-08-19", "status": "current"}},
            ],
        }, {"organization_name": "Houston Methodist"})
        summary = await summarize_fhir_bundle(bundle)
        self.assertEqual(summary["allergies"][0]["name"], "Peanuts")
        self.assertEqual(summary["allergies"][0]["citation"]["organization_name"], "Houston Methodist")
        self.assertEqual(summary["care_events"][0]["details"], "Hospital discharge summary · current")
        self.assertEqual(summary["care_events"][0]["citation"]["source_type"], "epic")
        with patch("app.reports.pdf_builder.HTML") as html:
            html.return_value.write_pdf = Mock(return_value=b"%PDF-1.4")
            render_doctor_pdf_bytes(summary, bundle, {"organization_name": "Houston Methodist"})
        rendered = html.call_args.kwargs["string"]
        self.assertIn("Peanuts", rendered)
        self.assertIn("Hospital discharge summary", rendered)
        self.assertIn("Epic / MyChart · Houston Methodist", rendered)
        self.assertNotIn("Epic data appendix", rendered)

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

    def test_source_capability_is_document_and_manifest_scoped(self) -> None:
        with patch.dict(os.environ, {"SOURCE_LINK_SECRET": "a" * 32}, clear=False):
            token = create_document_download_token(9, "manifest-1")
            self.assertEqual(verify_document_download_token(token, 9), "manifest-1")
            with self.assertRaisesRegex(ValueError, "Invalid source download link"):
                verify_document_download_token(token, 10)

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
