"""Pure, source-neutral commercial-estimate rules.

This module deliberately does not call a payer.  It validates the durable
configuration/evidence selected by staff and either produces an explainable
estimate or a precise review reason.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from typing import Any


class CommercialError(ValueError):
    pass


SERVICE_CATEGORIES = {"office", "emg_ncs"}
RATE_SOURCE_TYPES = {"payer_feed", "contract_export", "transparency_file", "manual"}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _money(value: object, label: str) -> int:
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except Exception as exc:
        raise CommercialError(f"{label} must be a dollar amount.") from exc
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        raise CommercialError(f"{label} is outside the supported range.")
    cents = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if cents != amount * 100:
        raise CommercialError(f"{label} must have at most two decimal places.")
    return int(cents)


def _integer(value: object, label: str, maximum: int = 10000) -> int:
    try:
        result = int(str(value).strip())
    except Exception as exc:
        raise CommercialError(f"{label} must be a whole number.") from exc
    if not 1 <= result <= maximum:
        raise CommercialError(f"{label} must be between 1 and {maximum}.")
    return result


def context_fingerprint(secret: str, context: dict[str, object]) -> str:
    """Return a non-reversible lookup key; callers must not log its input."""
    if not secret:
        raise CommercialError("Commercial evidence fingerprinting is not configured.")
    fields = ("practice_id", "patient_id", "coverage_id", "subscriber_relationship", "payer_connection_id",
              "service_date", "rendering_npi", "place_of_service", "service_category", "appointment_id", "member_id", "ecw_coverage_id")
    canonical = "|".join(str(context.get(field) or "") for field in fields)
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def parse_rate_csv(content: bytes, filename: str) -> list[dict[str, object]]:
    """Parse a governed CSV, never a patient or payer response file."""
    if not filename.lower().endswith(".csv"):
        raise CommercialError("Rate imports must be UTF-8 CSV files.")
    try:
        reader = csv.DictReader(StringIO(content.decode("utf-8-sig")), strict=True)
        rows = list(reader)
    except Exception as exc:
        raise CommercialError("Rate import is not a readable UTF-8 CSV.") from exc
    if not rows or len(rows) > 20000:
        raise CommercialError("Rate imports must contain 1–20,000 rows.")
    aliases = {
        "cpt": "code", "hcpcs": "code", "procedurecode": "code", "code": "code",
        "description": "description", "allowedamount": "allowed", "contractedallowedamount": "allowed",
        "cashprice": "cash", "modifier": "modifier", "unitrule": "unit_rule", "network": "network",
        "plan": "plan_matcher", "planmatcher": "plan_matcher", "pos": "pos", "placeofservice": "pos",
        "providernpi": "provider_npi", "renderingnpi": "provider_npi", "tin": "tin", "taxid": "tin",
        "effectivestart": "effective_from", "effectivefrom": "effective_from", "effectiveend": "effective_to",
        "effectiveto": "effective_to",
    }
    mapped = {_key(name): aliases.get(_key(name)) for name in reader.fieldnames or []}
    required = {"code", "allowed", "network", "pos", "effective_from"}
    if not required.issubset(set(mapped.values())):
        raise CommercialError("Rate CSV requires CPT/HCPCS, Allowed Amount, Network, POS, and Effective From columns.")
    parsed: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for row_number, raw in enumerate(rows, 2):
        row = {mapped[_key(name)]: str(value or "").strip() for name, value in raw.items()
               if mapped.get(_key(name))}
        code = row.get("code", "").replace(".", "").upper()
        if not re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
            raise CommercialError(f"Rate CSV row {row_number} has an invalid CPT/HCPCS code.")
        for field in ("network", "pos", "effective_from"):
            if not row.get(field):
                raise CommercialError(f"Rate CSV row {row_number} is missing {field.replace('_', ' ')}.")
        if row.get("effective_to") and row["effective_to"] < row["effective_from"]:
            raise CommercialError(f"Rate CSV row {row_number} ends before it starts.")
        key = (code, row.get("modifier", ""), row["network"].casefold(), row["pos"],
               row.get("plan_matcher", "").casefold(), row.get("provider_npi", ""))
        if key in seen:
            raise CommercialError(f"Rate CSV contains duplicate matching rows for {code}.")
        seen.add(key)
        parsed.append({
            "row_number": row_number, "code": code, "description": row.get("description", "")[:300],
            "allowed_cents": _money(row.get("allowed"), f"Allowed Amount on row {row_number}"),
            "cash_cents": _money(row["cash"], f"Cash Price on row {row_number}") if row.get("cash") else None,
            "modifier": row.get("modifier", "")[:10], "unit_rule": row.get("unit_rule", "per_unit")[:40],
            "network": row["network"][:120], "plan_matcher": row.get("plan_matcher", "")[:200],
            "pos": row["pos"][:20], "provider_npi": row.get("provider_npi", "")[:10],
            "tin": row.get("tin", "")[:20], "effective_from": row["effective_from"],
            "effective_to": row.get("effective_to") or None,
        })
    return parsed


def _benefit_cents(evidence: dict[str, Any], key: str) -> int | None:
    value = evidence.get(key)
    if value in {None, ""}:
        return None
    try:
        cents = int(str(value).strip())
    except Exception as exc:
        raise CommercialError(f"{key.replace('_', ' ')} must be whole cents.") from exc
    if not 0 <= cents <= 100_000_000:
        raise CommercialError(f"{key.replace('_', ' ')} is outside the supported range.")
    return cents


def _stedi_cents(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _stedi_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Read both documented 271 response layouts without guessing meanings."""
    entries: list[dict[str, Any]] = [row for row in payload.get("benefitsInformation", []) if isinstance(row, dict)]
    for plan in payload.get("plans", []):
        if not isinstance(plan, dict):
            continue
        direct = plan.get("benefitsInformation")
        if isinstance(direct, list):
            entries.extend(row for row in direct if isinstance(row, dict))
        benefits = plan.get("benefits")
        if not isinstance(benefits, dict):
            continue
        for category, code in (("coInsurance", "A"), ("coPayment", "B"), ("deductible", "C"), ("outOfPocket", "G")):
            for row in benefits.get(category, []):
                if isinstance(row, dict):
                    entries.append({"code": code, "benefitAmount": row.get("amount"), "percent": row.get("percent"),
                                    "coverageLevel": row.get("coverageLevel"), "network": row.get("network"),
                                    "timeQualifier": row.get("timePeriod"), "priorAuthIndicator": row.get("priorAuthIndicator"),
                                    "authOrCertIndicator": row.get("authOrCertIndicator"),
                                    "additionalInformation": row.get("additionalInformation"),
                                    "serviceTypeCodes": row.get("serviceTypeCodes", [])})
    return entries


def normalize_stedi_271(payload: dict[str, Any], service_type_code: str, provider_network_verified: bool,
                        other_active_coverage: bool = False) -> dict[str, Any]:
    """Normalize only unambiguous individual in-network schedule benefits.

    A 271 benefit network is not provider participation.  That is supplied by
    the separately verified provider-network record.
    """
    entries = _stedi_entries(payload)
    statuses = []
    for plan in payload.get("plans", []):
        if isinstance(plan, dict):
            benefits = plan.get("benefits") if isinstance(plan.get("benefits"), dict) else {}
            statuses.extend(row for row in benefits.get("statuses", []) if isinstance(row, dict))
    status_text = " ".join(str(row.get("status") or "") for row in statuses).casefold()
    inactive = any(token in status_text for token in ("inactive", "ineligible", "terminated", "cancelled", "canceled", "expired"))
    active = False if inactive else True if ("active" in status_text or "eligible" in status_text or any(str(row.get("code") or "") == "1" for row in entries)) else None
    wanted = []
    for row in entries:
        level = str(row.get("coverageLevel") or row.get("coverageLevelCode") or "").upper()
        network = row.get("network")
        indicator = str(network.get("indicator") if isinstance(network, dict) else row.get("inPlanNetworkIndicator") or row.get("inPlanNetworkIndicatorCode") or "").upper()
        service_types = {str(value) for value in row.get("serviceTypeCodes", []) if value is not None}
        # Specific requested STC wins; general medical (30) is an allowed
        # fallback only when no specific counterpart exists.
        specificity = 2 if service_type_code in service_types else 1 if "30" in service_types else 0
        if level not in {"INDIVIDUAL", "IND", ""} or indicator not in {"Y", "YES", "IN_NETWORK"} or not specificity:
            continue
        wanted.append((specificity, row))
    def pick(code: str, remaining: bool = False) -> dict[str, Any] | None:
        matches = [(specificity, row) for specificity, row in wanted if str(row.get("code") or "").upper() == code and (not remaining or str(row.get("timeQualifierCode") or row.get("timeQualifier") or "").upper() in {"29", "REMAINING"})]
        if not matches:
            return None
        best = max(score for score, _ in matches)
        selected = [row for score, row in matches if score == best]
        # Exact duplicate benefit rows can differ by unmodelled modifiers.
        # Do not choose one silently.
        return selected[0] if len(selected) == 1 else None
    deductible, oop = pick("C", True), pick("G", True)
    copay, coinsurance = pick("B"), pick("A")
    relevant = [row for row in entries if not row.get("serviceTypeCodes") or
                service_type_code in {str(value) for value in row.get("serviceTypeCodes", [])} or
                "30" in {str(value) for value in row.get("serviceTypeCodes", [])}]
    authorization_values = {str(row.get("authOrCertIndicator") or row.get("priorAuthIndicator") or "").upper()
                            for row in relevant}
    authorization_signal = ("required" if authorization_values & {"Y", "REQUIRED"}
                            else "unknown" if not authorization_values & {"N", "NOT_REQUIRED"} or authorization_values & {"U", "UNKNOWN"}
                            else "not_required")
    descriptions = " ".join(str(item.get("description") or item.get("message") or "" if isinstance(item, dict) else item)
                            for row in relevant for item in (row.get("additionalInformation") or [])).casefold()
    referral_signal = ("not_required" if "referral not required" in descriptions or "no referral required" in descriptions
                       else "required" if "referral" in descriptions and "required" in descriptions else "unknown")
    deductible_cents = _stedi_cents(deductible.get("benefitAmount")) if deductible else None
    oop_cents = _stedi_cents(oop.get("benefitAmount")) if oop else None
    copay_cents = _stedi_cents(copay.get("benefitAmount")) if copay else None
    try:
        raw_percent = (coinsurance.get("benefitPercent") if coinsurance.get("benefitPercent") is not None else coinsurance.get("percent")) if coinsurance else None
        coinsurance_percent = Decimal(str(raw_percent)) if raw_percent is not None else None
        # X12 percentage values are commonly serialized as fractions (for
        # example, .20 means 20%), while some payer JSON layouts use 20.
        if coinsurance_percent is not None and Decimal("0") <= coinsurance_percent <= Decimal("1"):
            coinsurance_percent *= 100
        if coinsurance_percent is not None and not Decimal("0") <= coinsurance_percent <= Decimal("100"):
            coinsurance_percent = None
    except Exception:
        coinsurance_percent = None
    missing = active is not True or deductible_cents is None or oop_cents is None or (copay_cents is None and coinsurance_percent is None)
    multiple_plans = len([plan for plan in payload.get("plans", []) if isinstance(plan, dict)]) > 1
    limitations = any(str(row.get("code") or "").upper() in {"F", "Y"} for row in relevant)
    return {"active": active, "coverage_level": "individual" if wanted else "unknown",
            "network_status": "in_network" if provider_network_verified else "unknown",
            "deductible_remaining_cents": deductible_cents, "oop_remaining_cents": oop_cents,
            "copay_cents": copay_cents, "coinsurance_percent": str(coinsurance_percent) if coinsurance_percent is not None else None,
            "has_secondary": other_active_coverage, "requires_review": bool(missing or authorization_signal in {"required", "unknown"} or referral_signal in {"required", "unknown"} or limitations or multiple_plans or other_active_coverage),
            "authorization_signal": authorization_signal, "referral_signal": referral_signal,
            "normalization_notes": ["Stedi 271 benefit schedule does not prove provider participation."]}


def estimate(lines: list[dict[str, Any]], benefits: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Calculate one in-network primary-plan encounter or fail closed."""
    if not lines:
        raise CommercialError("Confirm at least one service before calculating an estimate.")
    if benefits.get("active") is not True:
        raise CommercialError("Active primary coverage is required for an insured estimate.")
    if benefits.get("coverage_level") != "individual":
        raise CommercialError("A compatible individual benefit level is required for an automatic estimate.")
    if benefits.get("network_status") != "in_network":
        raise CommercialError("Confirmed in-network provider participation is required for an automatic estimate.")
    if benefits.get("has_secondary"):
        raise CommercialError("Multiple active coverages require staff review.")
    if benefits.get("requires_review"):
        raise CommercialError("The selected benefits contain an unresolved limit, authorization, referral, or payer review.")
    cost_share = rules.get("cost_share")
    if cost_share == "payer_returned":
        has_copay = benefits.get("copay_cents") is not None
        has_coinsurance = benefits.get("coinsurance_percent") is not None
        if has_copay == has_coinsurance:
            raise CommercialError("Payer benefits did not identify one unambiguous office-visit cost-share method.")
        if has_copay and _benefit_cents(benefits, "deductible_remaining_cents"):
            raise CommercialError("Payer did not confirm whether the office-visit copay is subject to the remaining deductible.")
        cost_share = "copay" if has_copay else "coinsurance"
    if cost_share not in {"coinsurance", "copay", "cumulative"}:
        raise CommercialError("The estimation profile does not define its copay/coinsurance rule.")
    deductible_remaining = _benefit_cents(benefits, "deductible_remaining_cents")
    oop_remaining = _benefit_cents(benefits, "oop_remaining_cents")
    if deductible_remaining is None or oop_remaining is None:
        raise CommercialError("Remaining individual deductible and out-of-pocket values are required.")
    coinsurance_percent = benefits.get("coinsurance_percent")
    copay_cents = _benefit_cents(benefits, "copay_cents") or 0
    if cost_share in {"coinsurance", "cumulative"}:
        try:
            coinsurance = Decimal(str(coinsurance_percent))
        except Exception as exc:
            raise CommercialError("A coinsurance percentage is required by this profile.") from exc
        if not Decimal("0") <= coinsurance <= Decimal("100"):
            raise CommercialError("Coinsurance percentage must be between 0 and 100.")
    else:
        coinsurance = Decimal("0")
    allowed = 0
    explanation = []
    for line in lines:
        amount = line.get("allowed_cents")
        if not isinstance(amount, int) or amount < 0:
            raise CommercialError(f"No matching allowed rate exists for {line.get('code') or 'a selected service'}.")
        units = _integer(line.get("units"), f"Units for {line.get('code') or 'service'}")
        total = amount * units
        allowed += total
        explanation.append({"code": line.get("code"), "units": units, "allowed_cents": total, "rate_id": line.get("rate_id")})
    deductible = min(allowed, deductible_remaining)
    remainder = allowed - deductible
    coinsurance_cents = int((Decimal(remainder) * coinsurance / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    copay = copay_cents if cost_share in {"copay", "cumulative"} else 0
    raw_patient = deductible + coinsurance_cents + copay
    patient = min(allowed, raw_patient, oop_remaining)
    return {
        "allowed_cents": allowed, "deductible_cents": deductible, "coinsurance_cents": coinsurance_cents,
        "copay_cents": copay, "oop_adjustment_cents": raw_patient - patient,
        "patient_cents": patient, "plan_cents": allowed - patient, "lines": explanation,
        "calculation_version": "commercial-v1", "disclaimer": "Pre-service estimate only; final responsibility depends on services performed and payer adjudication.",
    }
