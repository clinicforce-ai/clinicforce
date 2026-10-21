"""Shared eCW FHIR names, references, codes, providers and numbers."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

def _name(resource: dict[str, Any]) -> tuple[str | None, str | None]:
    names = [item for item in resource.get("name", []) if isinstance(item, dict)]
    selected = next((item for item in names if item.get("use") in {"official", "usual"}), names[0] if names else {})
    given = selected.get("given", [])
    return (str(given[0]) if isinstance(given, list) and given else None,
            str(selected.get("family")) if selected.get("family") else None)

def _given_words(resource: dict[str, Any]) -> list[str]:
    names = [item for item in resource.get("name", []) if isinstance(item, dict)]
    selected = next((item for item in names if item.get("use") in {"official", "usual"}), names[0] if names else {})
    given = selected.get("given", [])
    return [word for part in given if isinstance(part, str) for word in part.split()] if isinstance(given, list) else []

def _phone(resource: dict[str, Any]) -> str | None:
    value = next((item.get("value") for item in resource.get("telecom", [])
                  if isinstance(item, dict) and item.get("system") == "phone" and item.get("value")), None)
    digits = re.sub(r"\D", "", str(value or ""))[-10:]
    return digits if len(digits) == 10 else None

def _reference(value: Any) -> str | None:
    return value.get("reference") if isinstance(value, dict) and isinstance(value.get("reference"), str) else None

def _period_contains(resource: dict[str, Any], service_date: str) -> bool:
    period = resource.get("period", {}) if isinstance(resource.get("period"), dict) else {}
    return (not period.get("start") or str(period["start"])[:10] <= service_date) and (not period.get("end") or str(period["end"])[:10] >= service_date)

def _coding(resource: Any, kind: str) -> list[str]:
    found: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            code, system = value.get("code"), str(value.get("system", "")).lower()
            compact = str(code or "").replace(".", "").upper()
            if kind == "procedure" and re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", compact) and (not system or "cpt" in system or "hcpcs" in system):
                found.append(compact)
            if kind == "diagnosis" and re.fullmatch(r"[A-Z][0-9][A-Z0-9]{1,5}", compact) and "icd" in system:
                found.append(compact)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(resource)
    return list(dict.fromkeys(found))

def _npi(resource: dict[str, Any]) -> str | None:
    for identifier in resource.get("identifier", []):
        if not isinstance(identifier, dict):
            continue
        value = str(identifier.get("value", "")).strip()
        label = " ".join((str(identifier.get("system", "")), str(identifier.get("type", "")))).lower()
        if re.fullmatch(r"[1-4]\d{9}", value) and ("npi" in label or not identifier.get("system")):
            return value
    return None


def _tin_values(resource: dict[str, Any]) -> set[str]:
    """Read billing TINs only from specifically labelled FHIR identifiers."""
    values = set()
    for identifier in resource.get('identifier') or []:
        if not isinstance(identifier, dict):
            continue
        value = re.sub(r'\D', '', str(identifier.get('value') or ''))
        kind = identifier.get('type') or {}
        codings = kind.get('coding') or [] if isinstance(kind, dict) else []
        label = ' '.join((str(identifier.get('system') or ''), str(kind.get('text') or '')
                          if isinstance(kind, dict) else str(kind),
                          *(str(code.get('code') or '') + ' ' + str(code.get('display') or '')
                            for code in codings if isinstance(code, dict)))).casefold()
        if len(value) == 9 and re.search(r'\b(?:tin|ein|tax(?:payer)?[ -]?(?:id|identification))\b', label):
            values.add(value)
    return values


def _tin(resource: dict[str, Any]) -> str | None:
    values = _tin_values(resource)
    return next(iter(values)) if len(values) == 1 else None


def _tin_conflict(resource: dict[str, Any]) -> bool:
    return len(_tin_values(resource)) > 1

def _display(resource: dict[str, Any]) -> str | None:
    if resource.get("name") and isinstance(resource.get("name"), str):
        return resource["name"]
    given, family = _name(resource)
    names = [item for item in resource.get('name', []) if isinstance(item, dict)]
    selected = next((item for item in names if item.get('use') in {'official', 'usual'}), names[0] if names else {})
    return " ".join(x for x in (given, family) if x) or selected.get('text') or None

def _concept_labels(value: Any) -> list[str]:
    concepts = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        if concept.get("text"):
            labels.append(str(concept["text"]))
        for coding in concept.get("coding") or []:
            if isinstance(coding, dict) and (coding.get("display") or coding.get("code")):
                labels.append(str(coding.get("display") or coding["code"]))
    return labels

def _number(value: Any, *, money: bool = False) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0 or number > (Decimal("1000000") if money else Decimal("10000")):
        return None
    if money:
        rounded = number.quantize(Decimal("0.01"))
        return f"{rounded:.2f}" if rounded == number else None
    return format(number.normalize(), "f")

def _source_references(resource: dict[str, Any]) -> list[str]:
    values: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("reference"), str):
                values.append(value["reference"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(resource)
    return list(dict.fromkeys(values))
