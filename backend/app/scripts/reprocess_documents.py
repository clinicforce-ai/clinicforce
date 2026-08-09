"""Backfill legacy ClinicForce document metadata from existing encrypted S3 PDFs."""

from __future__ import annotations

import asyncio

from app.core.object_store import get_pdf
from app.core.patient_store import list_documents_requiring_extraction, update_document_extraction
from app.ingestion.bedrock_extractor import EXTRACTION_VERSION, extract_pdf_to_fhir_bundle
from app.logs.logger import log_error, log_info


async def main() -> None:
    documents = await asyncio.to_thread(list_documents_requiring_extraction, EXTRACTION_VERSION)
    succeeded = 0
    failed = 0
    for document in documents:
        try:
            source = await asyncio.to_thread(get_pdf, document.s3_key)
            bundle = await extract_pdf_to_fhir_bundle(source, document.patient_id, document.filename)
            if not bundle.get("entry"):
                raise ValueError("No supported clinical facts were verified")
            updated = await asyncio.to_thread(
                update_document_extraction,
                document.patient_id,
                document.id,
                bundle,
                EXTRACTION_VERSION,
            )
            if not updated:
                raise RuntimeError("Document disappeared during migration")
            succeeded += 1
        except Exception as exc:
            failed += 1
            # Do not log names, patient identifiers, PDF text, or model output.
            log_error("Bedrock document migration", exc)
    log_info("Bedrock document migration", "Reprocessed %d documents; %d failed", succeeded, failed)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
