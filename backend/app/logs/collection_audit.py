"""Structured, patient-safe events for the eCW arrival estimate in CloudWatch."""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import UUID


request_id: ContextVar[str] = ContextVar("collection_request_id", default="")
logger = logging.getLogger("clinicforce.collection_audit")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def _uuid(value: str | None) -> str | None:
    try:
        return str(UUID(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _code(value: str | None) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else None


def collection_event(event: str, *, practice_id: str | None = None, intake_id: str | None = None,
                     case_id: str | None = None, staff_id: str | None = None,
                     stage: str | None = None, outcome: str | None = None,
                     reason_code: str | None = None, status: str | None = None,
                     basis: str | None = None, source: str | None = None,
                     payment_mode: str | None = None, emg_state: str | None = None,
                     amount_cents: int | None = None, count: int | None = None,
                     emg_min_cents: int | None = None, emg_max_cents: int | None = None,
                     cash_min_cents: int | None = None, cash_max_cents: int | None = None,
                     duration_ms: int | None = None, revision: int | None = None,
                     http_status: int | None = None) -> None:
    """Only opaque UUIDs, fixed codes and numeric facts may enter this log."""
    if not _code(event):
        raise ValueError("Collection audit events require a fixed code")
    data = {"at": datetime.now(timezone.utc).isoformat(), "event": event,
            "request_id": request_id.get()}
    for key, value in {"practice_id": practice_id, "intake_id": intake_id,
                       "case_id": case_id, "staff_id": staff_id}.items():
        if safe := _uuid(value):
            data[key] = safe
    for key, value in {"stage": stage, "outcome": outcome, "reason_code": reason_code,
                       "status": status, "basis": basis, "source": source,
                       "payment_mode": payment_mode, "emg_state": emg_state}.items():
        if safe := _code(value):
            data[key] = safe
    for key, value in {"amount_cents": amount_cents, "count": count,
                       "emg_min_cents": emg_min_cents, "emg_max_cents": emg_max_cents,
                       "cash_min_cents": cash_min_cents, "cash_max_cents": cash_max_cents,
                       "duration_ms": duration_ms, "revision": revision,
                       "http_status": http_status}.items():
        if type(value) is int:
            data[key] = value
    logger.info(json.dumps(data, separators=(",", ":")))
