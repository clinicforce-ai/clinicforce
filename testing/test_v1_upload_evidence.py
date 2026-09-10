"""V1 source isolation, OCR, table boundaries and API regressions (no live services)."""
import unittest
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import fitz
import httpx
from PIL import Image

from app.insurance import documented, golden, v1_sources
from app.main import app


def pdf(text):
    with fitz.open() as doc:
        doc.new_page().insert_text((30, 30), text)
        return doc.tobytes()


class SourceTests(unittest.TestCase):
    def test_card_without_benefits_does_not_get_defaults(self):
        fields, evidence = v1_sources.card_fields([(1, "Member Name: PAT EXAMPLE\nMember ID: ABC123")])
        self.assertEqual(set(fields), {"member_name", "member_id"})
        self.assertEqual(set(fields), set(evidence))

    def test_split_card_identity_labels(self):
        fields, evidence = v1_sources.card_fields([(1, "Member Name\nPAT EXAMPLE\nIdentification Number ABC123\nGroup Number: XYZ123")])
        self.assertEqual(fields["member_name"], "PAT EXAMPLE")
        self.assertEqual(fields["member_id"], "ABC123")
        self.assertEqual(evidence["member_name"]["quote"], "Member Name\nPAT EXAMPLE")

    def test_missing_identity_is_not_filled_from_other_fields(self):
        fields, _ = v1_sources.card_fields([(1, "Member Name:\nMember ID: ABC123\nGroup Number: XYZ123")])
        self.assertNotIn("member_name", fields)

    def test_card_preserves_source_wording_with_correct_section(self):
        text = "Deductible Information\nInd/Fam In Network $600/$1,800\nInd/Fam Out of Network $900/$2,700\nOut of Pocket Maximum Information\nInd/Fam In Network $3,000/$9,000"
        fields, evidence = v1_sources.card_fields([(2, text)])
        self.assertEqual(fields["out_of_network_deductible"], "Ind/Fam Out of Network $900/$2,700")
        self.assertTrue(evidence["out_of_network_deductible"]["quote"].startswith("Deductible Information"))
        self.assertEqual(evidence["in_network_oop_maximum"]["page"], 2)

    def test_malformed_money_is_not_displayed(self):
        for amount in ("$900 individual / $2, family", "$900/$2,70", "$3,000.1"):
            with self.subTest(amount=amount):
                fields, _ = v1_sources.card_fields([(1, f"Out-of-network deductible: {amount}")])
                self.assertNotIn("out_of_network_deductible", fields)

    def test_unrelated_network_text_does_not_become_benefit(self):
        fields, _ = v1_sources.card_fields([(1, "Deductible\nCall customer service\nIn Network: $999\nOut-of-pocket\n"), (2, "In Network: $123")])
        self.assertEqual(fields, {})

    def test_conflicting_card_values_are_omitted(self):
        fields, evidence = v1_sources.card_fields([(1, "In-network deductible: $123"), (2, "In-network deductible: $456")])
        self.assertEqual(fields, {})
        self.assertEqual(evidence, {})

    def test_invisible_pdf_text_is_not_used_as_evidence(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((30, 30), "In-network deductible: $999", render_mode=3)
            content = doc.tobytes()
        with patch("app.insurance.golden._image_text", return_value="Member ID: ABC123") as ocr:
            fields, _ = v1_sources.card_fields(v1_sources.read_pages(content))
        ocr.assert_called_once()
        self.assertEqual(fields, {"member_id": "ABC123"})

    def test_card_embedded_below_pdf_header_is_ocr_read(self):
        image = BytesIO()
        Image.new("RGB", (800, 500), "white").save(image, format="PNG")
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((30, 30), "Exported insurance card")
            page.insert_image(fitz.Rect(30, 70, 570, 500), stream=image.getvalue())
            content = doc.tobytes()
        with patch("app.insurance.golden._image_text", return_value="Member ID: ABC123") as ocr:
            fields, _ = v1_sources.card_fields(v1_sources.read_pages(content, 2))
        ocr.assert_called_once()
        self.assertEqual(fields["member_id"], "ABC123")

    def test_coverage_does_not_cross_benefit_sections_or_infer_network(self):
        fields, _ = documented._coverage_fields([(1, "In Network\nIndividual Deductible $600 total\nIndividual Out Of Pocket $900 Remaining")])
        self.assertEqual(fields, {"oop_remaining_cents": 90000})

    def test_family_amounts_and_conflicting_plans_are_not_individual_facts(self):
        fields, _ = documented._coverage_fields([(1, "Plan: Dental\nPlan: Medical\nFamily Deductible $125 Remaining\nFamily Out Of Pocket $900 Remaining\nPlan pays 80%")])
        self.assertEqual(fields, {})

    def test_coverage_cents_and_evidence_stay_with_the_field(self):
        text = "Individual deductible remaining: $123.45\nIndividual out-of-pocket remaining: $2,910.91\nPatient coinsurance: 20%"
        fields, evidence = documented._coverage_fields([(3, text)])
        self.assertEqual(fields, {"deductible_remaining_cents": 12345, "oop_remaining_cents": 291091, "coinsurance_percent": 20})
        self.assertEqual(evidence["deductible_remaining_cents"], {"page": 3, "quote": text.splitlines()[0]})

    def test_pdf_table_individual_column_does_not_take_family_balance(self):
        with fitz.open() as doc:
            page = doc.new_page()
            for x in (30, 210, 390, 570):
                page.draw_line((x, 30), (x, 180))
            for y in (30, 80, 130, 180):
                page.draw_line((30, y), (570, y))
            for x, value in zip((40, 220, 400), ("Benefit", "Individual", "Family")):
                page.insert_text((x, 55), value)
            for y, row in zip((105, 155), (("Annual Deductible", "$0 Remaining", "$520.91 Remaining"), ("Out Of Pocket", "$12.34 Remaining", "$2,910.91 Remaining"))):
                for x, value in zip((40, 220, 400), row):
                    page.insert_text((x, y), value)
            content = doc.tobytes()
        values = {key: value for key, value, _ in v1_sources.individual_balance_cells(content)}
        self.assertEqual(values, {"deductible_remaining_cents": 0, "oop_remaining_cents": 1234})

    def test_short_clinical_pdf_is_accepted_and_not_authorization(self):
        source = documented.inspect_pdf(pdf("Patient reports foot numbness."), "note.pdf", "clinical")
        self.assertIn("foot numbness", source["findings"]["clinical_context"])
        self.assertNotIn("authorizations", source["findings"])

    def test_image_evidence_uses_ocr(self):
        with patch("app.insurance.golden._image_text", return_value="Patient reports foot numbness."):
            source = documented.inspect_pdf(b"\x89PNG\r\n\x1a\nimage", "note.png", "clinical")
        self.assertEqual(source["filename"], "note.png")

    def test_wrong_member_coverage_is_rejected(self):
        with self.assertRaises(golden.GoldenCaseError):
            documented.inspect_pdf(pdf("Member ID: WRONG123"), "coverage.pdf", "coverage", "ABC123")

    def test_invalid_and_duplicate_rates_are_rejected(self):
        for rows in ("99204,-1", "99204,NaN", "99204,Infinity", "99204,10\n99204,20"):
            with self.subTest(rows=rows), self.assertRaises(golden.GoldenCaseError):
                documented.inspect_csv(("CPT,Amount\n" + rows).encode(), "rates.csv")

    def test_only_suggested_codes_are_shown_not_entire_csv(self):
        case = documented.pending_case({})
        case["next_stage"] = "service"
        documented.add_document(case, "fee_schedule", documented.inspect_csv(b"CPT,Amount\n99204,100\n93000,50", "rates.csv"))
        with patch("app.insurance.documented.suggest_cpt_codes", return_value={"99204": "Visit context"}) as suggest:
            documented.set_clinical_context(case, "Entered condition", "established")
        self.assertEqual([line["code"] for line in case["service_lines"]], ["99204"])
        self.assertIn("established", suggest.call_args.args[0])
        documented.add_document(case, "clinical", documented.inspect_pdf(pdf("A different visit reason"), "new.pdf", "clinical"))
        self.assertEqual(case["service_lines"], [])

    def test_authorization_visible_before_advancing_page(self):
        case = documented.pending_case({})
        case.update(next_stage="service", service_lines=[{"code": "93000"}])
        documented.add_document(case, "coverage", documented.inspect_pdf(pdf("93000 - No authorization required"), "coverage.pdf", "coverage"))
        documented.select_services(case, ["93000"])
        self.assertEqual(case["service_lines"][0]["authorization_status"], "not_required")
        documented.add_document(case, "authorization", documented.inspect_pdf(pdf("93000 - Authorization required"), "auth.pdf", "authorization"))
        self.assertEqual(case["service_lines"][0]["authorization_status"], "not_stated")

    def test_missing_balance_never_means_zero_patient_cost(self):
        case = documented.pending_case({})
        case["service_lines"] = [{"code": "93000", "description": "Uploaded description", "allowed_cents": 5000, "selected": True, "confirmed": True}]
        estimate = documented.calculate(case)
        self.assertFalse(estimate["estimate_available"])
        self.assertIsNone(estimate["patient_cents"])
        self.assertEqual(estimate["line_items"][0]["allowed_cents"], 5000)


class V1RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.session = patch("app.api.routers.insurance._session", return_value={"patient_id": "patient-1", "share_manifest_id": "share-1"})
        self.session.start()
        self.addCleanup(self.session.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_image_upload_returns_only_current_file_fields_and_evidence(self):
        def stored(patient, share, binding, workflow):
            return {**workflow, "id": "case-1"}
        with patch("app.insurance.golden._image_text", return_value="Member ID: ABC123"), patch("app.api.routers.insurance.store.create_case", side_effect=stored):
            response = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"\x89PNG\r\n\x1a\nimage", "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        case = response.json()
        self.assertEqual(case["ocr"], {"member_id": "ABC123", "confirmed": False})
        self.assertEqual(set(case["ocr_evidence"]), {"member_id"})
        self.assertEqual(case["extraction_version"], v1_sources.EXTRACTION_VERSION)

    async def test_legacy_case_cannot_reintroduce_unsourced_values(self):
        with patch("app.api.routers.insurance.store.get_case", return_value={"source_mode": "documents"}):
            response = await self.client.get("/api/doctor/insurance/cases/old")
        self.assertEqual(response.status_code, 409)

    async def test_intake_cannot_inject_a_different_identity(self):
        case = documented.pending_case({"member_id": "ABC123"})
        case.update(extraction_version=v1_sources.EXTRACTION_VERSION, evidence_manifest_id=golden.evidence_binding())
        with patch("app.api.routers.insurance.store.get_case", return_value=deepcopy(case)), patch("app.api.routers.insurance.store.save_case") as save:
            response = await self.client.put("/api/doctor/insurance/cases/case-1/intake", json={"member_name": "Invented Name", "member_id": "ABC123"})
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    async def test_ocr_failure_does_not_return_default_data(self):
        with patch("app.insurance.golden._image_text", side_effect=golden.OCRServiceError("OCR unavailable")), patch("app.api.routers.insurance.log_error"):
            response = await self.client.post("/api/doctor/insurance/cases", files={"file": ("card.png", b"\x89PNG\r\n\x1a\nimage", "image/png")})
        self.assertEqual(response.status_code, 503)
