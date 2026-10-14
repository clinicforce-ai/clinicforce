"""Read payer aliases, relationship, group and plan from eCW coverage."""
from __future__ import annotations

from typing import Any

from app.insurance.ecw.ecw_fields import _display, _norm


def _payer_id(payer: dict[str, Any], aliases: Any) -> str | None:
    if not isinstance(aliases, dict):
        return None
    candidates = [_display(payer), *(item.get("value") for item in payer.get("identifier", []) if isinstance(item, dict))]
    observed = {_norm(candidate) for candidate in candidates if candidate}
    matches = {value for key, value in aliases.items()
               if isinstance(key, str) and isinstance(value, str) and _norm(key) in observed}
    return next(iter(matches)) if len(matches) == 1 else None

def _relationship(coverage: dict[str, Any]) -> str | None:
    values = [str(c.get("code", "")).casefold() for c in (coverage.get("relationship") or {}).get("coding", []) if isinstance(c, dict)]
    mapping = {"self": "18", "18": "18", "spouse": "01", "01": "01", "child": "19", "19": "19", "other": "G8", "g8": "G8"}
    return next((mapping[value] for value in values if value in mapping), None)

def _group(coverage: dict[str, Any]) -> str | None:
    for item in coverage.get("class", []):
        if not isinstance(item, dict):
            continue
        codes = [str(c.get("code", "")).casefold() for c in (item.get("type") or {}).get("coding", []) if isinstance(c, dict)]
        if "group" in codes:
            return str(item.get("value")) if item.get("value") else None
    return None

def _plan_number(coverage: dict[str, Any]) -> str | None:
    for item in coverage.get("class", []):
        if not isinstance(item, dict):
            continue
        codes = [str(c.get("code", "")).casefold() for c in (item.get("type") or {}).get("coding", []) if isinstance(c, dict)]
        if any(code in {"plan-number", "plannumber", "planid"} for code in codes):
            return str(item.get("value")) if item.get("value") else None
    return None

def _plan_details(coverage: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in coverage.get("class", []):
        if not isinstance(item, dict):
            continue
        for coding in (item.get("type") or {}).get("coding", []):
            key = str(coding.get("code") or "").casefold()
            value = str(item.get("value") or item.get("name") or "").strip()
            if key and value and value not in result.setdefault(key, []):
                result[key].append(value)
    return result

def _plan(coverage: dict[str, Any]) -> str | None:
    details = _plan_details(coverage)
    for kind in ("plan", "subplan", "network"):
        values = details.get(kind, [])
        if values:
            return values[0] if len(values) == 1 else None
    return None
