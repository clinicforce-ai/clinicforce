"""Rank individual cost-share rows without merging conflicting payer evidence."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.insurance.stedi_benefits.fields import _benefit_notes, _procedure, _stedi_cents, _stedi_entries, oop_plan_association, coverage_kind


@dataclass
class CostShareRows:
    wanted: list[tuple[int, dict[str, Any]]]
    deductible: dict[str, Any] | None
    oop: dict[str, Any] | None
    annual_deductible: dict[str, Any] | None
    annual_oop: dict[str, Any] | None
    copay: dict[str, Any] | None
    coinsurance: dict[str, Any] | None
    conflicts: dict[str, list[str]]
    oop_medical_candidate_ids: list[str]
    oop_quarantined_ids: list[str]


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
        # A per-service copay or percentage marked employee-and-children also
        # applies to a covered child. It is not an individual accumulator.
        shared_cost_share = row.get('code') in {'A', 'B'} and level in {'EMPLOYEE_AND_CHILDREN', 'ECH'}
        shared_service_waiver = (row.get('code') == 'F' and specificity >= 2
            and level in {'EMPLOYEE_AND_CHILDREN', 'ECH'}
            and any('Plan Ded Waived' in note for note in
                    _benefit_notes([row.get('messages'), row.get('additionalInformation')])))
        service_waiver = (not level and row.get('code') == 'C' and specificity >= 2
                          and _stedi_cents(row.get('benefitAmount')) == 0
                          and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'', '23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'})
        if (not individual and not shared_cost_share and not shared_service_waiver and not service_waiver and not (not level and row.get("code") in {"A", "B"})) or indicator not in accepted_networks or not specificity:
            continue
        wanted.append((specificity, row))
    # A CPT-specific cost share replaces a broader category cost share for
    # this CPT. Deductible and out-of-pocket accumulators remain plan-wide.
    cost_share_scope = max((score for score, row in wanted if row.get('code') in {'A', 'B'}), default=0)
    wanted = [(score, row) for score, row in wanted
              if row.get('code') not in {'A', 'B'} or score == cost_share_scope]
    conflicts: dict[str, list[str]] = {}
    supplementary_present = any(coverage_kind(row, payload) == 'supplementary'
                                for row in _stedi_entries(payload)) or any(
        coverage_kind({'planCoverage': plan.get('name')}, payload) == 'supplementary'
        for plan in payload.get('plans') or [] if isinstance(plan, dict))
    oop_rows = [row for _, row in wanted if row.get('code') == 'G']
    medical_oop_annual = [row for row in oop_rows if oop_plan_association(row, payload) == 'medical'
        and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper()
            in {'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR', 'CONTRACT'}]
    medical_deductible_annual = [row for _, row in wanted if row.get('code') == 'C'
        and oop_plan_association(row, payload) == 'medical'
        and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper()
            in {'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'}]
    def paired_medical_remaining(row: dict) -> bool:
        if oop_plan_association(row, payload) != 'unassigned':
            return False
        notes = ' '.join(_benefit_notes([row.get('messages'), row.get('additionalInformation')])).upper()
        if re.search(r'\b(?:DENTAL|VISION|PHARMACY|PRESCRIPTION|RX|HEARING)\b', notes):
            return False
        anchors = [anchor for anchor in medical_oop_annual
            if anchor.get('plan_index') == row.get('plan_index')
            and str(anchor.get('coverageLevel') or anchor.get('coverageLevelCode') or '').upper()
                == str(row.get('coverageLevel') or row.get('coverageLevelCode') or '').upper()
            and anchor.get('serviceTypeCodes') == row.get('serviceTypeCodes')
            and anchor.get('network') == row.get('network')
            and anchor.get('inPlanNetworkIndicatorCode') == row.get('inPlanNetworkIndicatorCode')]
        return len(anchors) == 1
    oop_medical_candidate_ids = [row['evidence_path'] for row in oop_rows
                                 if oop_plan_association(row, payload) == 'medical_candidate']
    oop_quarantined_ids = [row['evidence_path'] for row in oop_rows
                           if supplementary_present and oop_plan_association(row, payload) == 'unassigned'
                           and not paired_medical_remaining(row)]
    def pick(code: str, remaining: bool = False, periods: set[str] | None = None, *,
             plan_wide: bool = False, label: str) -> dict[str, Any] | None:
        periods = {'29', 'REMAINING'} if remaining else periods
        matches = [(specificity, row) for specificity, row in wanted if str(row.get("code") or "").upper() == code and (periods is None or str(row.get("timeQualifierCode") or row.get("timeQualifier") or "").upper() in periods)]
        if plan_wide:
            matches = [(score, row) for score, row in matches if not _procedure(row) and '30' in (row.get('serviceTypeCodes') or [])]
        if label in {'oop_annual', 'oop_remaining'} and supplementary_present:
            matches = [(score, row) for score, row in matches
                       if oop_plan_association(row, payload) == 'medical'
                       or label == 'oop_remaining' and paired_medical_remaining(row)]
        if label in {'deductible_annual', 'deductible_remaining'} and supplementary_present:
            matches = [(score, row) for score, row in matches if
                       _procedure(row) or '30' not in (row.get('serviceTypeCodes') or [])
                       or oop_plan_association(row, payload) == 'medical'
                       or label == 'deductible_remaining' and any(
                           anchor.get('plan_index') == row.get('plan_index')
                           and anchor.get('coverageLevel') == row.get('coverageLevel')
                           and anchor.get('serviceTypeCodes') == row.get('serviceTypeCodes')
                           and anchor.get('network') == row.get('network')
                           for anchor in medical_deductible_annual)]
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
        # Authorization metadata is evaluated separately for the selected
        # service; it does not change an otherwise identical copay or share.
        nonfinancial = {'authOrCertIndicator', 'priorAuthIndicator'} if code in {'A', 'B'} else set()
        # A plain SPECIALIST label does not change an otherwise identical
        # generic office term; conditional messages still remain distinct.
        if code in {'A', 'B'} and all(
                not row.get('messages') or row['messages'] == ['SPECIALIST'] for row in selected):
            nonfinancial.add('messages')
        signatures = {json.dumps({k: v for k, v in row.items()
                                  if k != 'evidence_path' and k not in nonfinancial}, sort_keys=True)
                      for row in selected}
        if len(signatures) != 1:
            conflicts[label] = [row['evidence_path'] for row in selected]
            return None
        return {**selected[0], 'evidence_paths': [row['evidence_path'] for row in selected]}
    deductible = pick("C", True, label='deductible_remaining')
    oop = pick("G", True, label='oop_remaining')
    annual_deductible = pick('C', periods={'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'},
                             plan_wide=True, label='deductible_annual')
    annual_oop = pick('G', periods={'23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR', 'CONTRACT'},
                      plan_wide=True, label='oop_annual')
    copay = pick("B", label='copay')
    coinsurance = pick("A", label='coinsurance')
    return CostShareRows(wanted, deductible, oop, annual_deductible, annual_oop, copay, coinsurance,
                         conflicts, oop_medical_candidate_ids, oop_quarantined_ids)
