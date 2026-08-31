"""Operational health endpoints."""

import asyncio

from fastapi import APIRouter

from app.core.patient_store import check_database_connection

router = APIRouter(tags=["system"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Confirm that the HTTP process is live."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz() -> dict[str, str]:
    """Confirm that the configured patient store is reachable."""
    await asyncio.to_thread(check_database_connection)
    return {"status": "ok"}
