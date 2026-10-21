"""Pure, source-neutral commercial-estimate rules.

This module deliberately does not call a payer.  It validates the durable
configuration/evidence selected by staff and either produces an explainable
estimate or a precise review reason.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from typing import Any

from app.insurance.stedi_271_fields import (
    _deductible_message,
    eligibility_errors,
    _stedi_cents,
    _stedi_entries,
    _procedure,
    _network_indicator,
    payer_person,
    plan_details,
    reported_stedi_options,
    _dates_apply,
    _benefit_notes,
    coverage_evidence,
    _provider_participation,
)
from app.insurance.stedi_271 import normalize_stedi_271
from app.insurance.network_participation import verified_out_of_network


class CommercialError(ValueError):
    pass


_UHC_NAME = re.compile(r'\bunited\s*health(?:care)?\b|\buhc\b', re.I)


def discard_stale_directory_payer(result: dict, current_payer_id: str | None) -> None:
    """A recovered policy must not inherit the earlier route's payer name."""
    saved = result.get('stedi_payer')
    if isinstance(saved, dict) and saved.get('id') and str(saved['id']) != str(current_payer_id or ''):
        result.pop('stedi_payer', None)


def is_united_healthcare_payer(result: dict, directory_payer: dict | None = None) -> bool:
    """Use the resolved payer route before an eCW product label."""
    saved_payer = result.get('stedi_payer') or {}
    saved_payer = saved_payer if isinstance(saved_payer, dict) else {}
    directory_payer = directory_payer if isinstance(directory_payer, dict) else {}
    label = (directory_payer.get('displayName') or directory_payer.get('name')
             or saved_payer.get('name') or saved_payer.get('displayName') or result.get('payer'))
    return bool(label and _UHC_NAME.search(str(label)))


def eligibility_failure(payload: dict) -> dict:
    """Safe, actionable explanations; arbitrary response text stays protected."""
    errors = eligibility_errors(payload)
    codes = sorted({str(row.get('code')) for row in errors if re.fullmatch(r'\d{2}', str(row.get('code') or ''))})
    validation = any(row.get('path') for row in errors)
    title, action = 'The payer could not complete the check', 'Review the saved payer response before checking again.'
    if '72' in codes:
        title = 'The payer rejected the member ID'
        action = 'Compare the member ID and insurer in eCW with the insurance card. Correct the policy in eCW, then check again.'
    elif '73' in codes:
        title = 'The payer rejected the subscriber name'
        action = 'The member ID was sent, but the payer could not validate the subscriber name. Compare the policyholder name in eCW with the current card and payer record; a saved member ID does not resolve a name mismatch.'
    elif set(codes) & {'62', '63'}:
        title = 'The payer could not verify this appointment date'
        action = 'The requested service date is outside this payer’s inquiry window. Recheck closer to the appointment; earlier eligibility does not confirm this visit.'
    elif set(codes) & {'65', '67', '71', '75', '76'}:
        title = 'The payer could not match the patient'
        action = 'Compare the policyholder name, date of birth and member ID in eCW with the insurance card, then check again after correction.'
    elif set(codes) & {'42', '80'}:
        title = 'The payer is temporarily unavailable'
        action = 'Check again after the payer service recovers.'
    elif set(codes) & {'41', '43', '79'}:
        title = 'Payer or provider access needs attention'
        action = 'The clinic administrator should review the payer connection and provider enrollment before checking again.'
    elif validation:
        title = 'ClinicForce could not submit a valid request'
        action = 'The integration needs correction; this is not an inactive-coverage result.'
    return {'kind': 'request_validation' if validation else 'eligibility_rejected',
            'codes': codes, 'title': title, 'action': action,
            'detail': 'No usable eligibility benefits were returned. This does not establish inactive coverage.'}


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


def estimate(lines: list[dict[str, Any]], benefits: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Calculate a primary-plan share only from an allowance for the verified tier."""
    if not lines:
        raise CommercialError("Confirm at least one service before calculating an estimate.")
    if benefits.get("active") is not True:
        raise CommercialError("Active primary coverage is required for an insured estimate.")
    if benefits.get("coverage_level") != "individual":
        raise CommercialError("A compatible individual benefit level is required for an automatic estimate.")
    out_of_network = benefits.get('network_status') == 'out_of_network'
    if out_of_network:
        supported = {'payer_out_of_network', 'cms_original_medicare_estimate'}
        if (not verified_out_of_network(benefits) or rules.get('allowed_basis') not in supported or
                any(line.get('allowed_source') != rules['allowed_basis'] or not line.get('rate_id') for line in lines)):
            raise CommercialError('A sourced out-of-network payer allowance or applicable CMS estimate is required for every selected CPT.')
    elif benefits.get("network_status") != "in_network":
        raise CommercialError("Confirmed in-network provider participation is required for an automatic estimate.")
    if benefits.get("has_secondary"):
        raise CommercialError("Multiple active coverages require staff review.")
    if benefits.get("requires_review"):
        raise CommercialError(" ".join(benefits.get("review_reasons") or
                                      ["The selected benefits contain an unresolved limit, authorization, referral, or payer review."]))
    if benefits.get('separate_copay_limit'):
        raise CommercialError('Separate copay and general out-of-pocket limits require a scoped copay advance or plan review.')
    if _benefit_cents(benefits, 'oop_remaining_cents') == 0:
        # Covered in-network services cannot exceed an exhausted patient cap.
        # These calculation inputs do not overwrite the returned benefit facts.
        benefits = {**benefits, 'deductible_remaining_cents': 0, 'coinsurance_percent': '0', 'copay_cents': 0}
        rules = {**rules, 'cost_share': 'coinsurance'}
    if benefits.get('deductible_applies') is False:
        benefits = {**benefits, 'deductible_remaining_cents': 0}
    cost_share = rules.get("cost_share")
    if cost_share == "payer_returned":
        has_copay = benefits.get("copay_cents") is not None
        has_coinsurance = benefits.get("coinsurance_percent") is not None
        # A returned zero-percent term adds no cost share to the fixed copay.
        if has_copay and benefits.get('coinsurance_percent') is not None and Decimal(str(benefits['coinsurance_percent'])) == 0:
            has_coinsurance = False
        elif has_coinsurance and benefits.get("copay_cents") == 0:
            has_copay = False
        if has_copay == has_coinsurance:
            raise CommercialError("Payer benefits did not identify one unambiguous office-visit cost-share method.")
        if has_copay and _benefit_cents(benefits, "deductible_remaining_cents") and benefits.get('deductible_applies') is not True:
            raise CommercialError("Payer did not confirm whether the office-visit copay is subject to the remaining deductible.")
        cost_share = "copay" if has_copay else "coinsurance"
    if cost_share not in {"coinsurance", "copay", "cumulative"}:
        raise CommercialError("The estimation profile does not define its copay/coinsurance rule.")
    deductible_remaining = _benefit_cents(benefits, "deductible_remaining_cents")
    oop_remaining = _benefit_cents(benefits, "oop_remaining_cents")
    if deductible_remaining is None or oop_remaining is None:
        raise CommercialError("Remaining individual deductible and out-of-pocket values are required.")
    coinsurance_percent = benefits.get("coinsurance_percent")
    copay_cents = _benefit_cents(benefits, "copay_cents")
    if cost_share in {"copay", "cumulative"} and copay_cents is None:
        raise CommercialError("A copay amount or verified not-applicable benefit is required by this profile.")
    copay_cents = copay_cents or 0
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
    allowed_share = min(allowed, raw_patient)
    patient = min(allowed_share, oop_remaining)
    return {
        "allowed_cents": allowed, "deductible_cents": deductible, "coinsurance_base_cents": remainder,
        "coinsurance_percent": str(coinsurance), "coinsurance_cents": coinsurance_cents,
        "copay_cents": copay, "allowed_cap_adjustment_cents": raw_patient - allowed_share,
        "oop_adjustment_cents": allowed_share - patient,
        "patient_cents": patient, "plan_cents": allowed - patient, "lines": explanation,
        "cost_share": cost_share, "network_tier": 'out_of_network' if out_of_network else 'in_network',
        "possible_additional_out_of_network_charges_cents": None if out_of_network else 0,
        "calculation_version": "commercial-v1", "disclaimer": "Pre-service estimate only; final responsibility depends on services performed and payer adjudication." +
            (' For covered Medicare Advantage care, collect only plan-allowed cost sharing and apply member billing protections.'
             if out_of_network and rules.get('medicare_advantage') else
             ' Out-of-network balance billing may add charges above the payer allowed amount.' if out_of_network else ''),
    }
