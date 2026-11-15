"""Synthetic card PDFs for patient upload field extraction."""

import unittest
import os
from io import BytesIO
from unittest.mock import patch

import fitz
from PIL import Image

from app.insurance import patient_card_extraction


def scanned_pdf() -> bytes:
    picture = BytesIO()
    Image.new("RGB", (900, 500), "white").save(picture, format="PNG")
    with fitz.open() as document:
        first = document.new_page(width=600, height=400)
        first.insert_image(fitz.Rect(20, 20, 580, 350), stream=picture.getvalue())
        second = document.new_page(width=600, height=400)
        second.insert_image(fitz.Rect(20, 20, 580, 350), stream=picture.getvalue())
        second.insert_text((20, 380), "Date of Birth: 05/30/1990")
        return document.tobytes()


class PatientCardExtractionTests(unittest.TestCase):
    def test_two_scanned_pages_keep_selectable_dob(self):
        with patch.object(patient_card_extraction.golden, "_image_text",
                          side_effect=["Member Name: ALEX EXAMPLE\nMember ID: SAMPLE123", "Back of card"]) as ocr:
            fields = patient_card_extraction.read_identity([scanned_pdf()])
        self.assertEqual(ocr.call_count, 2)
        self.assertEqual(fields, {"given_name": "ALEX", "family_name": "EXAMPLE",
                                  "birth_date": "1990-05-30", "member_id": "SAMPLE123"})

    def test_model_fills_unlabeled_id_without_overriding_pdf_dob(self):
        with patch.object(patient_card_extraction.golden, "_image_text", return_value="ALEX EXAMPLE\nID ABC123"):
            with patch.object(patient_card_extraction, "_model_fields", return_value={
                "given_name": "ALEX", "family_name": "EXAMPLE", "member_id": "ABC123",
                "birth_date": "2000-01-01"}):
                fields = patient_card_extraction.read_identity([scanned_pdf()], {"member_id"})
        self.assertEqual(fields["member_id"], "ABC123")
        self.assertEqual(fields["birth_date"], "1990-05-30")

    def test_bedrock_reads_ocr_text_when_labels_are_missing(self):
        response = {"output": {"message": {"content": [{"text": '{"given_name":"ALEX","family_name":"EXAMPLE","birth_date":"","member_id":"ABC123"}'}]}}}
        with patch.dict(os.environ, {"INSURANCE_CARD_OCR": "textract"}):
            with patch.object(patient_card_extraction.boto3, "client") as client:
                client.return_value.converse.return_value = response
                result = patient_card_extraction._model_fields([(1, "ALEX EXAMPLE ID ABC123")])
        self.assertEqual(result["member_id"], "ABC123")
        self.assertEqual(client.return_value.converse.call_args.kwargs["modelId"], patient_card_extraction.BEDROCK_MODEL_ID)


if __name__ == "__main__":
    unittest.main()
