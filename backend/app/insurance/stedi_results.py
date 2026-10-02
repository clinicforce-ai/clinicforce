"""Normalize Stedi 271 eligibility responses for the existing V2 benefit view."""
from __future__ import annotations

from typing import Any


def rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("definition", "description", "value", "code", "name"):
            found = text(value.get(key))
            if found:
                return found
    return None


def messages(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()[:2000]]
    if isinstance(value, list):
        return list(dict.fromkeys(note for item in value for note in messages(item)))[:20]
    if isinstance(value, dict):
        return messages([value.get(key) for key in ("description", "message", "text")])
    return []


def _benefit(category: str, row: dict) -> dict:
    amount = text(row.get("amount"))
    percent = text(row.get("percent"))
    notes = messages(row.get("messages"))
    return {"category": category, "network": text(row.get("network")) or "Not returned",
            "service": text(row.get("service")), "level": text(row.get("coverageLevel")),
            "amount": amount or (f"{percent}%" if percent else None), "remaining": None, "total": None,
            "amountTimePeriod": text(row.get("timePeriod")), "description": " ".join(notes) or None,
            "authorizationRequired": any("authorization required" in note.casefold() for note in notes),
            "authorizationRequiredUnknown": any("authorization" in note.casefold() and "unknown" in note.casefold() for note in notes)}


def coverage(payload: dict) -> dict:
    plans = []
    statuses = []
    for source_plan in rows(payload.get("plans")):
        source_benefits = source_plan.get("benefits") if isinstance(source_plan.get("benefits"), dict) else {}
        plan_statuses = rows(source_benefits.get("statuses"))
        statuses.extend(plan_statuses)
        benefits = []
        for source, category in (("coPayment", "Copayment"), ("coInsurance", "Coinsurance"), ("deductible", "Deductible"),
                                 ("outOfPocket", "Out-of-pocket maximum"), ("limitations", "Limitation")):
            benefits.extend(_benefit(category, row) for row in rows(source_benefits.get(source)))
        status = plan_statuses[0] if plan_statuses else {}
        plans.append({"name": text(status.get("planCoverageDescription")), "planName": text(status.get("planCoverageDescription")),
                      "status": text(status.get("status")), "insuranceType": text(status.get("insuranceType")),
                      "benefits": benefits, "notes": messages(source_plan)})
    status_text = " ".join(text(row.get("status")) or "" for row in statuses).casefold()
    inactive = any(word in status_text for word in ("inactive", "ineligible", "terminated", "cancelled", "canceled", "expired"))
    active = True if not inactive and any(word in status_text for word in ("active", "eligible")) else False if inactive else None
    payer = payload.get("payer") if isinstance(payload.get("payer"), dict) else {}
    return {"source": "stedi", "check_id": payload.get("id"), "payer": text(payer.get("name")) or text(payload.get("payerId")),
            "status": "Eligibility benefits returned by Stedi", "active": active, "plans": plans, "pce_available": False,
            "provider_network": "Unverified — Stedi benefit schedule does not establish provider network status",
            "provider_network_status": "unconfirmed", "confidence": "review_required",
            "notes": messages(payload.get("errors")),
            "disclaimer": "Eligibility and benefits only. This is not a payment guarantee, authorization, or patient-price estimate."}
