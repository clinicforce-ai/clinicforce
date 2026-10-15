"""Rank individual cost-share rows without merging conflicting payer evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.insurance.stedi_benefits.fields import _procedure, _stedi_cents


@dataclass
class CostShareRows:
    wanted: list[tuple[int, dict[str, Any]]]
    deductible: dict[str, Any] | None
    oop: dict[str, Any] | None
    annual_deductible: dict[str, Any] | None
    copay: dict[str, Any] | None
    coinsurance: dict[str, Any] | None


def select_cost_share_rows(entries: list[dict[str, Any]], payload: dict[str, Any],
                           service_type_code: str, procedure_code: str | None,
                           accepted_networks: set[str], selected_ids: list[str] | None,
                           ) -> CostShareRows:
    """Prefer CPT-specific terms and preserve ambiguous rows for review."""
    wanted = []
    for row in entries:
        level = str(row.get("coverageLevel") or row.get("coverageLevelCode") or "").upper()
        network = row.get("network")
        indicator = str((network.get("indicator") or network.get("code")) if isinstance(network, dict)
                        else row.get("inPlanNetworkIndicatorCode") or row.get("inPlanNetworkIndicator") or "").upper()
        service_types = {str(value) for value in row.get("serviceTypeCodes", []) if value is not None}
        # Specific requested STC wins; general medical (30) is an allowed
        # fallback only when no specific counterpart exists.
        specificity = 3 if _procedure(row) and _procedure(row) == procedure_code else 2 if service_type_code in service_types else 1 if "30" in service_types else 0
        individual = level in {"INDIVIDUAL", "IND"} or (level in {"EMP", "EMPLOYEE_ONLY"} and not payload.get('dependent'))
        service_waiver = (not level and row.get('code') == 'C' and specificity >= 2
                          and _stedi_cents(row.get('benefitAmount')) == 0
                          and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'', '23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'})
        if (not individual and not service_waiver and not (not level and row.get("code") in {"A", "B"})) or indicator not in accepted_networks or not specificity:
            continue
        wanted.append((specificity, row))
    # A CPT-specific cost share replaces a broader category cost share for
    # this CPT. Deductible and out-of-pocket accumulators remain plan-wide.
    cost_share_scope = max((score for score, row in wanted if row.get('code') in {'A', 'B'}), default=0)
    wanted = [(score, row) for score, row in wanted
              if row.get('code') not in {'A', 'B'} or score == cost_share_scope]
    def pick(code: str, remaining: bool = False, periods: set[str] | None = None, *, plan_wide: bool = False) -> dict[str, Any] | None:
        periods = {'29', 'REMAINING'} if remaining else periods
        matches = [(specificity, row) for specificity, row in wanted if str(row.get("code") or "").upper() == code and (periods is None or str(row.get("timeQualifierCode") or row.get("timeQualifier") or "").upper() in periods)]
        if plan_wide:
            matches = [(score, row) for score, row in matches if not _procedure(row) and '30' in (row.get('serviceTypeCodes') or [])]
        if not matches:
            return None
        if selected_ids is not None and code in {"A", "B"}:
            matches = [(score, row) for score, row in matches if row["evidence_path"] in selected_ids]
            if not matches:
                return None
        best = max(score for score, _ in matches)
        selected = [row for score, row in matches if score == best]
        # Compare every source field, including unmodelled restrictions. Only
        # evidence location differs for an actual duplicate.
        signatures = {json.dumps({k: v for k, v in row.items() if k != 'evidence_path'}, sort_keys=True)
                      for row in selected}
        return {**selected[0], 'evidence_paths': [row['evidence_path'] for row in selected]} if len(signatures) == 1 else None
    deductible, oop = pick("C", True), pick("G", True)
    annual_deductible = pick('C', periods={'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'}, plan_wide=True)
    copay, coinsurance = pick("B"), pick("A")
    return CostShareRows(wanted, deductible, oop, annual_deductible, copay, coinsurance)
