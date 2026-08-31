"""Read the clinic-owned payer/plan matrix without contacting a payer.

The workbook is a configuration source for scheduling rules, not proof of an
individual member's benefits.  It is intentionally loaded from a local path
so the real clinic workbook is never checked into source control.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class PlanRule:
    payer: str
    plan_group: str
    plan_names: str
    plan_type: str
    referral_required: str
    referral_detail: str
    referral_source: str
    prior_authorization_needed_for: str
    prior_authorization_source: str
    verification_notes: str

    def public(self) -> dict[str, str]:
        return asdict(self)


def _normalized(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\bpreferred provider option\b", "ppo", value)
    value = re.sub(r"\bhealth maintenance organization\b", "hmo", value)
    value = re.sub(r"\bpoint of service\b", "pos", value)
    value = re.sub(r"\b(?:of|the)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _terms(value: str) -> set[str]:
    return {term for term in _normalized(value).split() if len(term) > 2}


def _matrix_path() -> Path | None:
    configured = os.getenv("INSURANCE_PLAN_MATRIX_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise ValueError("The configured clinic payer-plan matrix file was not found")
    if path.suffix.casefold() != ".xlsx":
        raise ValueError("The clinic payer-plan matrix must be an Excel .xlsx file")
    return path


@lru_cache(maxsize=4)
def _load_rules(path_string: str, modified_at: int) -> tuple[PlanRule, ...]:
    del modified_at
    workbook = load_workbook(path_string, read_only=True, data_only=True)
    try:
        if "Payer Plan Matrix" not in workbook.sheetnames:
            raise ValueError("The clinic payer-plan matrix is missing its Payer Plan Matrix sheet")
        sheet = workbook["Payer Plan Matrix"]
        header_row = next(sheet.iter_rows(min_row=5, max_row=5, values_only=True), ())
        columns = {str(value).strip(): index for index, value in enumerate(header_row) if value}
        required = {
            "Payer", "Plan Group (shared rule set)", "Specific Plan / Network Names on NMCT's Accepted List",
            "Plan Type", "Referral Required Before NMCT Visit", "Referral Rule Detail (which CPT/services trigger it)", "Referral Rule Source",
            "Prior Authorization Needed For", "Prior Auth Source / Submission Channel", "Verification Notes",
        }
        missing = required - columns.keys()
        if missing:
            raise ValueError("The clinic payer-plan matrix is missing required columns")
        rules: list[PlanRule] = []
        for row in sheet.iter_rows(min_row=6, values_only=True):
            def value(name: str) -> str:
                item = row[columns[name]] if columns[name] < len(row) else ""
                return str(item).strip() if item is not None else ""
            if value("Payer"):
                rules.append(PlanRule(
                    payer=value("Payer"), plan_group=value("Plan Group (shared rule set)"),
                    plan_names=value("Specific Plan / Network Names on NMCT's Accepted List"),
                    plan_type=value("Plan Type"), referral_required=value("Referral Required Before NMCT Visit"),
                    referral_detail=value("Referral Rule Detail (which CPT/services trigger it)"), referral_source=value("Referral Rule Source"),
                    prior_authorization_needed_for=value("Prior Authorization Needed For"),
                    prior_authorization_source=value("Prior Auth Source / Submission Channel"),
                    verification_notes=value("Verification Notes"),
                ))
    finally:
        workbook.close()
    if not rules:
        raise ValueError("The clinic payer-plan matrix has no payer-plan rules")
    return tuple(rules)


def configured_rules() -> tuple[PlanRule, ...] | None:
    path = _matrix_path()
    if path is None:
        return None
    return _load_rules(str(path), path.stat().st_mtime_ns)


def _payer_matches(rule: PlanRule, payer: str) -> bool:
    expected, observed = _normalized(rule.payer), _normalized(payer)
    aliases = {
        "bcbstx": "blue cross blue shield texas",
        "uhc": "unitedhealthcare",
    }
    observed = aliases.get(observed, observed)
    expected = aliases.get(expected, expected)
    return bool(expected and observed and (expected in observed or observed in expected))


def _plan_score(rule: PlanRule, plan_product: str) -> int:
    observed = _normalized(plan_product)
    if not observed:
        return 0
    for name in rule.plan_names.split(";"):
        candidate = _normalized(name)
        if candidate == observed:
            return 4
        if len(candidate) >= 5 and observed in candidate:
            return 3
        if len(candidate) >= 5 and candidate in observed:
            return 2
    # Product names occasionally omit a short qualifier printed in the matrix.
    # A high overlap is allowed only after the payer matches and never chooses
    # between two rows.
    observed_terms = _terms(observed)
    for name in rule.plan_names.split(";"):
        candidate_terms = _terms(name)
        if len(candidate_terms) >= 2 and len(candidate_terms & observed_terms) >= 2:
            return 1
    # A payer can print an expanded product name (for example "Preferred
    # Provider Option") while the matrix uses PPO. This fallback is safe only
    # when it identifies a plan family, not an individual benefit design; the
    # caller still refuses multiple matching matrix rows.
    family_terms = {"ppo", "hmo", "epo", "pffs"}
    return 1 if (_terms(rule.plan_type) & _terms(plan_product) & family_terms) else 0
    return False


def match_rule(payer: str, plan_product: str) -> dict[str, object]:
    """Return one exact-enough plan rule, or an explicit non-match state."""
    rules = configured_rules()
    if rules is None:
        return {"status": "not_configured", "message": "Clinic payer-plan matrix is not configured."}
    if not payer or not plan_product:
        return {"status": "insufficient_identity", "message": "The reviewed eligibility response must identify both payer and plan/product."}
    scored = [(rule, _plan_score(rule, plan_product)) for rule in rules if _payer_matches(rule, payer)]
    highest_score = max((score for _, score in scored), default=0)
    matches = [rule for rule, score in scored if score == highest_score and score > 0]
    if len(matches) == 1:
        return {"status": "matched", "rule": matches[0].public()}
    if len(matches) > 1:
        return {"status": "ambiguous", "message": "More than one clinic payer-plan rule matches this eligibility response. Confirm the exact plan with the payer."}
    return {"status": "unmatched", "message": "No clinic payer-plan rule matches the payer and plan/product in the reviewed eligibility response."}
