"""Normalize Stedi eligibility responses without treating them as a price quote."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in ("definition", "description", "value", "code", "name", "organization"):
            found = text(value.get(key))
            if found:
                return found
    return None


def cents(value: Any) -> int | None:
    try:
        amount = Decimal(str(value)) * 100
        return int(amount) if amount.is_finite() and 0 <= amount <= 100_000_000 and amount == amount.to_integral_value() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def percent(value: Any) -> int | None:
    try:
        amount = Decimal(str(value))
        return int(amount) if amount.is_finite() and 0 <= amount <= 100 and amount == amount.to_integral_value() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def messages(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()[:2000]]
    if isinstance(value, list):
        return list(dict.fromkeys(note for item in value for note in messages(item)))[:20]
    if isinstance(value, dict):
        return messages([value.get(key) for key in ("description", "message", "text")])
    return []


def network(value: Any) -> str:
    value = text(value)
    normalized = "".join(char for char in str(value or "").casefold() if char.isalpha())
    if normalized in {"y", "innetwork", "inplan"}:
        return "in_network"
    if normalized in {"n", "outofnetwork", "outofplan"}:
        return "out_of_network"
    return "unconfirmed"


def _benefit(category: str, row: dict) -> dict:
    amount = cents(row.get("amount", row.get("benefitAmount")))
    period = text(row.get("timePeriod", row.get("timePeriodQualifier")))
    remaining = cents(row.get("remaining"))
    if remaining is None and period and "remaining" in period.casefold():
        remaining = amount
    share = percent(row.get("percent", row.get("benefitPercent")))
    notes = messages(row.get("messages")) + messages(row.get("additionalInformation"))
    return {"category": category, "network": network(row.get("network", row.get("inPlanNetworkIndicatorCode"))),
            "service": text(row.get("service", row.get("serviceTypeCode"))), "level": text(row.get("coverageLevel")),
            "amount": f"{amount / 100:.2f}" if amount is not None else f"{share}%" if share is not None else None,
            "remaining": f"{remaining / 100:.2f}" if remaining is not None else None,
            "total": None, "amount_cents": amount, "remaining_cents": remaining, "percent": share,
            "amountTimePeriod": period,
            "description": " ".join(notes) or text(row.get("description")),
            "authorizationRequired": str(row.get("authOrCertIndicator") or "").upper() == "Y" or any("authorization required" in note.casefold() for note in notes),
            "authorizationRequiredUnknown": str(row.get("authOrCertIndicator") or "").upper() == "U" or any("authorization" in note.casefold() and "unknown" in note.casefold() for note in notes)}


def _legacy_benefits(payload: dict) -> list[dict]:
    labels = {"A": "Coinsurance", "B": "Copayment", "C": "Deductible", "G": "Out-of-pocket maximum", "F": "Limitation"}
    return [_benefit(labels.get(str(row.get("benefitCode") or "").upper(), "Benefit"), row)
            for row in rows(payload.get("benefitsInformation"))]


def coverage(payload: dict) -> dict:
    plans, statuses = [], []
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
    if not plans and _legacy_benefits(payload):
        plans = [{"name": text(payload.get("planName")), "planName": text(payload.get("planName")),
                  "status": text(payload.get("status")), "insuranceType": None, "benefits": _legacy_benefits(payload), "notes": messages(payload)}]
        statuses = [{"status": payload.get("status")}]
    status_text = " ".join(text(row.get("status")) or "" for row in statuses).casefold()
    inactive = any(word in status_text for word in ("inactive", "ineligible", "terminated", "cancelled", "canceled", "expired"))
    active = True if not inactive and any(word in status_text for word in ("active", "eligible")) else False if inactive else None
    payer = payload.get("payer") if isinstance(payload.get("payer"), dict) else {}
    return {"source": "stedi", "check_id": payload.get("id"), "payer": text(payer.get("name")) or text(payload.get("payerId")),
            "status": "Eligibility benefits returned by Stedi", "active": active, "plans": plans, "pce_available": False,
            "provider_network": "Stedi returned benefit tiers; use an exact clinic product, provider, location and tax-ID contract match to determine participation.",
            "provider_network_status": "unconfirmed", "notes": messages(payload.get("errors")),
            "disclaimer": "Eligibility and benefits support a pre-visit estimate only; they are not a guarantee of payment, authorization, or a final bill."}
