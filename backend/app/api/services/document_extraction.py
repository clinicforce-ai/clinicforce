"""Versioned extraction of clinical facts from protected documents."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, status


async def ensure_documents_extracted(
    patient_id: str,
    documents: list[Any],
    extraction_version: str,
    read_document: Callable[[str, Any], Awaitable[bytes]],
    extract_document: Callable[[bytes, str, str], Awaitable[dict]],
    update_extraction: Callable[..., bool],
) -> None:
    """Upgrade only stale documents, preserving per-document extraction caching."""
    for document in documents:
        if getattr(document, "extraction_version", None) == extraction_version:
            continue
        bundle = await extract_document(await read_document(patient_id, document), patient_id, document.filename)
        if not bundle.get("entry"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No supported clinical facts could be verified in {document.filename}",
            )
        updated = await asyncio.to_thread(
            update_extraction, patient_id, document.id, bundle, extraction_version,
        )
        if not updated:
            raise RuntimeError(f"Document {document.id} was no longer available for extraction")
