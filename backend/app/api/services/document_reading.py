"""Read one authorized document from its configured backing store."""

import asyncio
from collections.abc import Callable
from typing import Any


async def read_document_bytes(
    patient_id: str,
    document: Any,
    get_drive_connection: Callable[[str], Any],
    get_drive_file_crypto: Callable[[str, str], Any],
    decrypt_drive_pdf: Callable[[str, Any, bytes], bytes],
    get_s3_pdf: Callable[[str], bytes],
    drive_client: Any,
) -> bytes:
    """Read only the persistence-mapped source for the authenticated patient."""
    if getattr(document, "storage_provider", "s3") != "google_drive":
        return await asyncio.to_thread(get_s3_pdf, document.s3_key)
    connection = await asyncio.to_thread(get_drive_connection, patient_id)
    if connection is None:
        raise RuntimeError("Google Drive is no longer connected for this patient")
    ciphertext = await asyncio.to_thread(drive_client.get_file_bytes, connection, document.s3_key)
    crypto = await asyncio.to_thread(get_drive_file_crypto, patient_id, document.s3_key)
    return ciphertext if crypto is None else await asyncio.to_thread(decrypt_drive_pdf, patient_id, crypto, ciphertext)
