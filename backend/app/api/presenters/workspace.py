"""Patient workspace response projections."""

from typing import Any

from app.domain.fhir.bundles import document_payload


def record_payload(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "category": record.category,
        "label": record.label,
        "created_at": record.created_at,
    }


def workspace_payload(
    patient_id: str,
    documents: list[Any],
    records: list[Any],
    storage_provider: str = "s3",
    drive_connected: bool = False,
    drive_pending_import_count: int = 0,
    drive_sync_message: str | None = None,
    drive_access_upgrade_required: bool = False,
) -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "documents": [document_payload(document) for document in documents],
        "records": [record_payload(record) for record in records],
        "storage_provider": storage_provider,
        "drive_connected": drive_connected,
        "drive_pending_import_count": drive_pending_import_count,
        "drive_sync_message": drive_sync_message,
        "drive_access_upgrade_required": drive_access_upgrade_required,
    }
