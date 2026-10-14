"""Normalize Availity gateway responses and safe error messages."""
from __future__ import annotations

from typing import Any

import httpx


def unwrap(payload: Any, resource: str) -> dict:
    if not isinstance(payload, dict):
        return {}
    collection = payload.get("coverages" if resource == "coverage" else "serviceReviews")
    if isinstance(collection, list):
        return collection[0] if len(collection) == 1 and isinstance(collection[0], dict) else {}
    return payload


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _normalize_coverage(
    payload: dict[str, Any], transaction_id: str | None, mock_response: bool = False,
) -> dict[str, Any]:
    coverages = payload.get("coverages")
    coverage = coverages[0] if isinstance(coverages, list) and coverages else payload
    if not isinstance(coverage, dict):
        return {"outcome": "payer_error", "message": "Availity returned no coverage result."}

    status = str(coverage.get("status", "")).lower()
    status_code = str(coverage.get("statusCode", "")).upper()
    result: dict[str, Any] = {
        "coverage_id": coverage.get("id"),
        "payer_id": _payer_id(coverage),
        "transaction_id": transaction_id,
        "status": coverage.get("status"),
        "status_code": coverage.get("statusCode"),
        "as_of_date": coverage.get("asOfDate"),
        "plan_statuses": [
            plan.get("status") for plan in coverage.get("plans", [])
            if isinstance(plan, dict) and plan.get("status")
        ],
        "mock_response": mock_response,
    }
    if status_code == "4" or status == "complete":
        return {"outcome": "complete", **result}
    if status_code == "0" or "progress" in status:
        return {"outcome": "pending", **result}
    if status_code == "R1" or "retry" in status:
        return {"outcome": "retrying", **result}
    return {"outcome": "payer_error", "message": _message(coverage, "Payer could not complete the request."), **result}


def _payer_id(coverage: dict[str, Any]) -> str | None:
    payer = coverage.get("payer")
    return payer.get("payerId") if isinstance(payer, dict) else None


def _message(payload: dict[str, Any], fallback: str) -> str:
    messages = payload.get("errors") or payload.get("validationMessages") or []
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            return str(first.get("message") or first.get("field") or fallback)
        if isinstance(first, str):
            return first
    return str(payload.get("status") or payload.get("message") or fallback)
