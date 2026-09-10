"""Regression checks for the source-only V1 workflow."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import fitz

from app.insurance import documented, golden


def pdf(text: str) -> bytes:
    document = fitz.open()
    document.new_page().insert_text((40, 40), text)
    return document.tobytes()


class DocumentOnlyV1Tests(unittest.TestCase):
    def test_uses_uploaded_sources_and_constrains_cpt_suggestions_to_rate_csv_codes(self) -> None:
        fields = golden.read_card(pdf("Member Name: PAT EXAMPLE\nMember ID: MEMBER-987\nGroup Number: GROUP-987"))
        case = documented.pending_case(fields)
        case["card_source"] = {"filename": "card.pdf", "sha256": "c" * 64, "observed_at": "2026-09-10T00:00:00+00:00", "reference": "Uploaded insurance card"}
        documented.confirm_intake(case, fields)
        documented.advance(case, "eligibility")

        coverage = documented.inspect_pdf(pdf("""Member ID: MEMBER-987
Plan: Example Medical Plan
In Network
Annual Deductible $500 / Calendar Year $125 Remaining
Out Of Pocket $2,000 / Calendar Year $900 Remaining
20% coinsurance after deductible
87635 - No authorization required
"""), "coverage.pdf", "coverage", "MEMBER-987")
        rates = documented.inspect_csv(b"Procedure Code,Description,Amount\n99204,New patient office visit,$100.00\n87635,Example procedure,$42.50\n93000,ECG,$50.00\n", "rates.csv")
        documented.add_document(case, "coverage", coverage)
        documented.add_document(case, "fee_schedule", rates)
        documented.advance(case, "network")
        with patch("app.insurance.documented.suggest_cpt_codes", return_value={"99204": "Matches the patient-provided condition", "87635": "Matches the patient-provided condition", "93000": "Matches the patient-provided condition"}):
            documented.set_clinical_context(case, "Patient-provided condition")

        self.assertEqual([line["code"] for line in case["service_lines"]], ["87635", "99204", "93000"])
        self.assertEqual(case["service_lines"][0]["suggested_by"], "ClinicForce")
        documented.select_services(case, ["99204", "87635", "93000"])
        documented.advance(case, "authorization")
        documented.advance(case, "benefits")

        self.assertEqual(case["estimate"]["allowed_cents"], 19250)
        self.assertEqual(case["estimate"]["patient_cents"], 13850)
        self.assertEqual(case["estimate"]["plan_cents"], 5400)
        self.assertEqual([(line["code"], line["patient_cents"]) for line in case["estimate"]["line_items"]], [("99204", 10000), ("87635", 2850), ("93000", 1000)])
