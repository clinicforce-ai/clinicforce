"""Normalize a Stedi 271 response without losing source or unknown states."""
from __future__ import annotations

import json
import re
from decimal import Decimal

from app.insurance.stedi_benefits.selection import select_benefit_rows
from app.insurance.stedi_benefits.status import resolve_coverage_status
from app.insurance.stedi_benefits.terms import select_cost_share_rows

from app.insurance.stedi_benefits.fields import (
    _deductible_message,
    _stedi_cents,
    _procedure,
    _network_indicator,
    plan_details,
    reported_stedi_options,
    _benefit_notes,
    _provider_participation,
    _stedi_entries,
    coverage_kind,
    oop_plan_association,
)


def _oop_conflict_reason(payload: dict, wanted: list[tuple[int, dict]], ids: list[str],
                         *, annual: bool) -> str:
    label = ('a conflicting annual individual out-of-pocket maximum' if annual
             else 'conflicting remaining individual out-of-pocket balances')
    rows = [row for _, row in wanted if row.get('evidence_path') in ids]
    plans = payload.get('plans') or []
    def varied(value) -> bool:
        return len({json.dumps(value(row), sort_keys=True, default=str) for row in rows}) > 1
    differences = []
    if varied(lambda row: (row.get('plan_index'), row.get('planCoverage'))):
        differences.append('payer plans')
    if varied(_network_indicator):
        differences.append('benefit network tiers')
    if varied(lambda row: (row.get('serviceTypeCodes'), _procedure(row), row.get('placesOfService'))):
        differences.append('service scopes')
    if varied(lambda row: row.get('coverageLevel') or row.get('coverageLevelCode')):
        differences.append('coverage levels')
    def date_scope(row: dict) -> dict:
        index = row.get('plan_index')
        plan = plans[index] if type(index) is int and 0 <= index < len(plans) else {}
        return {'plan': plan.get('dates'), 'plan_information': plan.get('planDateInformation'),
                'benefit': row.get('dates'), 'benefit_information': row.get('benefitsDateInformation')}
    if varied(date_scope):
        differences.append('effective periods')
    if differences:
        return (f'The payer returned {label} across {", ".join(differences)}. '
                'Match the applicable plan, service scope, network tier, coverage level and effective dates with the payer.')
    suffix = ('limit' if annual else 'balance')
    return (f'The payer returned {label} for the same plan and network tier. '
            f'Ask the payer which {suffix} applies on this service date.')


def normalize_stedi_271(payload: dict[str, Any], service_type_code: str, provider_network_verified: bool,
                        other_active_coverage: bool = False, *, procedure_code: str | None = None,
                        provider_out_of_network_verified: bool = False,
                        office_visit: bool = False,
                        service_date: str | None = None, selected_ids: list[str] | None = None,
                        office_service_types: list[str] | None = None, place_of_service: str | None = None,
                        visit_context: dict | None = None, provider_npi: str | None = None) -> dict[str, Any]:
    """Normalize individual benefits for the provider's verified network tier.

    A benefit network indicator is not provider participation. Use a verified
    clinic record or an explicit payer assertion about the requesting provider.
    """
    echoed_npi = str((payload.get('provider') or {}).get('npi') or '')
    provider_npi = provider_npi if re.fullmatch(r'\d{10}', provider_npi or '') and (not echoed_npi or echoed_npi == provider_npi) else None
    selected = select_benefit_rows(
        payload, service_type_code, provider_network_verified,
        provider_out_of_network_verified=provider_out_of_network_verified,
        procedure_code=procedure_code, office_visit=office_visit, service_date=service_date,
        selected_ids=selected_ids, office_service_types=office_service_types,
        place_of_service=place_of_service, visit_context=visit_context,
        provider_npi=provider_npi,
    )
    entries = selected.entries
    service_type_code = selected.service_type_code
    excluded_service_ids = selected.excluded_service_ids
    office = selected.office
    priorities = selected.priorities
    selected_tier = selected.selected_tier
    accepted_networks = selected.accepted_networks
    network_conflict = selected.network_conflict
    provider_rows = selected.provider_rows
    negative_provider_ids = selected.negative_provider_ids
    status = resolve_coverage_status(
        payload, entries, service_date, service_type_code, selected_tier,
        office, selected_ids, priorities, accepted_networks,
    )
    entries = status.entries
    service_type_code = status.service_type_code
    coverage = status.coverage
    active = status.active
    inactive = status.inactive
    has_active = status.has_active
    invalid_dates = status.invalid_dates
    date_matches = status.date_matches
    returned_service = status.returned_service
    response_failed = status.response_failed
    cost_share = select_cost_share_rows(
        entries, payload, service_type_code, procedure_code,
        accepted_networks, selected_ids,
    )
    wanted = cost_share.wanted
    deductible = cost_share.deductible
    oop = cost_share.oop
    annual_deductible = cost_share.annual_deductible
    annual_oop = cost_share.annual_oop
    copay = cost_share.copay
    coinsurance = cost_share.coinsurance
    relevant = [row for row in entries if not row.get("serviceTypeCodes") or
                service_type_code in {str(value) for value in row.get("serviceTypeCodes", [])} or
                "30" in {str(value) for value in row.get("serviceTypeCodes", [])}]
    def scoped_auth(row: dict) -> str:
        indicator = str(row.get('authOrCertIndicator') or row.get('priorAuthIndicator') or '').upper()
        # A category contains many procedures. A requirement tied to a named
        # benefit (for example ABA) is not a requirement for every CPT in it.
        if indicator in {'Y', 'REQUIRED'} and row.get('messages') and not _procedure(row):
            return 'UNKNOWN'
        return indicator
    authorization_values = {scoped_auth(row) for row in relevant}
    authorization_signal = ("required" if authorization_values & {"Y", "REQUIRED"}
                            else "unknown" if not authorization_values & {"N", "NOT_REQUIRED"} or authorization_values & {"U", "UNKNOWN"}
                            else "not_required")
    def notes(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [note for item in value for note in notes(item)]
        if isinstance(value, dict):
            return [note for key in ("description", "message", "text", "messages") for note in notes(value.get(key))]
        return []
    # A met family out-of-pocket limit covers every member's covered in-network
    # cost share. Keep it separate from the individual accumulator: a positive
    # family balance cannot establish this member's remaining responsibility.
    supplementary_present = any(coverage_kind(row, payload) == 'supplementary'
                                for row in _stedi_entries(payload))
    family_oop = [row for row in relevant if row.get('code') == 'G'
                  and (not _procedure(row) or _procedure(row) == procedure_code)
                  and str(row.get('coverageLevel') or row.get('coverageLevelCode') or '').upper() in {'FAMILY', 'FAM'}
                  and _network_indicator(row) in accepted_networks
                  and (not supplementary_present or oop_plan_association(row, payload) == 'medical')
                  and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'29', 'REMAINING'}]
    family_scores = [(3 if _procedure(row) == procedure_code and procedure_code else
                      2 if service_type_code in (row.get('serviceTypeCodes') or []) else 1, row)
                     for row in family_oop]
    family_best = [row for score, row in family_scores if score == max(s for s, _ in family_scores)] if family_scores else []
    family_amounts = {_stedi_cents(row.get('benefitAmount')) for row in family_best}
    family_oop_cents = next(iter(family_amounts)) if len(family_amounts) == 1 else None
    family_oop_zero_applies = (family_oop_cents == 0 and not any(
        re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I)
        for row in relevant for note in notes([row.get('messages'), row.get('additionalInformation')])))
    descriptions = [note.casefold() for row in relevant for field in ("additionalInformation", "messages")
                    for note in notes(row.get(field))]
    authorization_basis = 'Payer indicator' if authorization_values - {''} else 'Not confirmed'
    if any(scoped_auth(row) == 'UNKNOWN' and row.get('messages') for row in relevant):
        authorization_basis = 'Payer requirement is attached to a named benefit; applicability to this service is not established'
    auth_notes = [note for note in descriptions if re.search(r'authoriz|precert|pre-cert', note)]
    if auth_notes:
        # Free text is evidence to review, not a standardized approval signal.
        if authorization_signal != 'required':
            authorization_signal = 'unknown'
        authorization_basis = 'Payer notes require review'
    elif authorization_values <= {''} and active is True and wanted and not response_failed:
        authorization_signal = 'not_required'
        authorization_basis = 'Derived from absent indicator in applicable response (Stedi guidance)'
    # Contradictory terms must not be cleared by one "not required" message.
    no_referral = any("referral not required" in note or "no referral required" in note for note in descriptions)
    referral_required = any(
        re.search(r"referral(?:s)? (?:is |are )?required|\b(?:policy|plan) requires? (?:that )?services be referred by (?:a )?(?:primary care physician|pcp)",
                  re.sub(r"no referral required|referral not required", "", note))
        and not re.search(r'\bneurolog(?:ist|y)\b', note.split('except', 1)[-1] if 'except' in note else '')
        for note in descriptions)
    referral_signal = "required" if referral_required else "not_required" if no_referral else "unknown"
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
    other_active_coverage = other_active_coverage or any(row.get('code') == 'R' for row in relevant)
    qmb_ids = [row['evidence_path'] for row in entries if any(
        re.search(r'\b(?:qualified medicare beneficiary|QMB)\b', note, re.I)
        and not re.search(r'\b(?:not|no|non[- ]?)\s+(?:a\s+)?(?:qualified medicare beneficiary|QMB)\b', note, re.I)
        for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    multiple_plans = len({row["plan_index"] for row in entries if "plan_index" in row and row.get("code") in {"status", "A", "B", "C", "G"}}) > 1
    # Some plans track office copays separately from the general OOP balance.
    # Retain both buckets: a met general maximum must not erase that copay.
    copay_caps = [(score, row) for score, row in wanted if row.get('code') == 'F'
                  and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'29', 'REMAINING'}
                  and any(re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I)
                          for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    cap_rows = [row for score, row in copay_caps if score == max(s for s, _ in copay_caps)] if copay_caps else []
    cap_values = {_stedi_cents(row.get('benefitAmount')) for row in cap_rows}
    copay_cap = next(iter(cap_values)) if len(cap_values) == 1 and None not in cap_values else None
    waived_rows = [row for _, row in wanted if office and row.get('code') == 'F' and any(
        re.fullmatch(r'(?:Specialist Visit or Evaluation|(?:Specialist )?Office Visit)/Plan Ded Waived', note.strip(), re.I)
        for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    deductible_signals = [(False, row) for row in waived_rows]
    for specificity, row in wanted:
        period = str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper()
        amount = _stedi_cents(row.get('benefitAmount')) if row.get('code') == 'C' else None
        # A zero remaining balance means met, never waived. General positive
        # accumulators do not prove that today's service uses the deductible.
        if amount is not None and (specificity >= 2 or amount == 0) and (period in {'', '23', 'CALENDAR_YEAR', '22', 'SERVICE_YEAR'}
                or specificity >= 2 and amount > 0 and period in {'29', 'REMAINING'}):
            deductible_signals.append((amount > 0, row))
        if (row.get('evidence_path') in {x.get('evidence_path') for x in (copay, coinsurance) if x}
                or specificity >= 2 and row.get('code') == 'C'):
            for note in notes([row.get('messages'), row.get('additionalInformation')]):
                for label in note.split(';'):
                    applies = _deductible_message(label)
                    if applies is not None:
                        deductible_signals.append((applies, row))
    deductible_values = {value for value, _ in deductible_signals}
    deductible_applies = next(iter(deductible_values)) if len(deductible_values) == 1 else None
    deductible_conflict = len(deductible_values) > 1
    deductible_applicability = ('UNKNOWN' if deductible_conflict else 'DOES_NOT_APPLY' if deductible_applies is False
                               else 'ALREADY_MET' if deductible_cents == 0 else 'APPLIES' if deductible_applies is True else 'UNKNOWN')
    def unresolved_limit(row):
        if str(row.get('code') or '').upper() not in {'F', 'Y'}:
            return False
        labels = notes([row.get('messages'), row.get('additionalInformation')])
        # General administrative messages do not establish a service limit.
        if not row.get('benefitAmount') and not row.get('quantity') and all(
            (_provider_participation(note, provider_npi) or re.search(r'participating in this patient|may require precert|Plan includes NAP|^SELF-FUNDED$|^All Other In-Network Providers$|/Plan Ded Waived$', note, re.I))
            for note in labels) and labels:
            return False
        if copay_cap is not None and row.get('code') == 'F' and row.get('benefitAmount') is not None:
            # Annual/remaining copay maxima come as individual and family rows.
            # Only recognize the annual row when paired with an explicit cap.
            return not (row in cap_rows or any(re.fullmatch(r'COPAY MAX(?:IMUM)?', note.strip(), re.I) for note in labels)
                        or str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'23', 'CALENDAR_YEAR'}
                        and labels == ['All Other In-Network Providers'])
        return True
    limitations = any(unresolved_limit(row) for row in relevant)
    conditional_terms = selected_ids is None and any(re.search(r"\b(first|after|before|beginning|visits?|waived|unless|only|limit|specialist|primary care)\b", note, re.IGNORECASE) for row in (copay, coinsurance) if row for note in notes([row.get("messages"), row.get("additionalInformation")])
        if not all(_deductible_message(label) is not None or re.fullmatch(r"(?:All Other In-Network Providers\s+)?(?:(?:specialist |physician )?office visits?(?: (?:specialist|primary care(?: prov(?:ider)?)?|with (?:labs|xray)))?|Specialist(?: AND PCP)?|PCP AND Specialist|per visit|(?:Specialist|Primary Care) Visit or Evaluation|GYN Visit|Walk In Clinic Visit|Telemedicine (?:General Medicine|Specialist) Visit|phy visit\s*-\s*specialist|(?:PCP AND )?BenefitTier Non Tier 1 Specialist|diagnostic medical procedure)(?:\s*-\s*professional)?(?:\s*,\s*COINS APPLIES TO OUT OF POCKET)?", label.strip(), re.I)
                   for label in note.split(';') if label.strip()))
    source_errors = []
    for _, row in wanted:
        for field in ('benefitAmount', 'benefitPercent', 'percent'):
            value = row.get(field)
            if value is None or value == '':
                continue
            try:
                number = Decimal(str(value))
                invalid = not number.is_finite() or number < 0 or number > (100 if field != 'benefitAmount' else 1000000)
            except Exception:
                invalid = True
            if invalid:
                source_errors.append({'path': row['evidence_path'] + '.' + field,
                    'kind': 'invalid_financial_value',
                    'message': 'Invalid financial evidence: the payer returned an invalid amount or percentage. Verify the source before collection.'})
    review_reasons = list(dict.fromkeys(error['message'] for error in source_errors))
    if cost_share.conflicts.get('oop_annual'):
        review_reasons.append(_oop_conflict_reason(payload, wanted, cost_share.conflicts['oop_annual'], annual=True))
    if cost_share.conflicts.get('oop_remaining'):
        review_reasons.append(_oop_conflict_reason(payload, wanted, cost_share.conflicts['oop_remaining'], annual=False))
    if cost_share.oop_medical_candidate_ids or cost_share.oop_quarantined_ids:
        review_reasons.append('Separate medical and supplementary coverage was returned; unassigned out-of-pocket rows are retained but cannot set this medical visit’s limit.')
    if inactive and has_active:
        review_reasons.append('The payer returned both active coverage and inactive or noncovered benefits for the selected service scope. Verify which coverage restriction applies before estimating.')
    if invalid_dates:
        review_reasons.append("Verify the payer eligibility dates; an applicable date value could not be interpreted.")
    if date_matches is False:
        review_reasons.append(f"Payer evidence covers service date {returned_service['start']}; eligibility for the requested appointment on {service_date} needs verification.")
    if active is not True:
        review_reasons.append("Confirm active coverage for the visit date with the payer.")
    if deductible_conflict:
        review_reasons.append('Conflicting deductible applicability evidence requires review before calculation.')
    if deductible_cents is None and deductible_applies is not False:
        review_reasons.append("Confirm the remaining individual deductible that applies to this visit.")
    if oop_cents is None:
        review_reasons.append("Confirm the remaining individual out-of-pocket maximum.")
    if copay_cents is None and coinsurance_percent is None:
        review_reasons.append("Confirm which copay or coinsurance term applies, including any visit-count conditions.")
    if authorization_signal != "not_required":
        review_reasons.append("Confirm whether authorization applies to this service and, if required, that it is approved.")
    if referral_signal != "not_required":
        review_reasons.append("Confirm the plan's referral requirement and, if required, that a referral is on file.")
    if conditional_terms:
        review_reasons.append("Confirm which conditional benefit applies to the selected service.")
    if limitations:
        review_reasons.append("Review the payer's benefit limits before estimating patient cost.")
    if multiple_plans or other_active_coverage:
        review_reasons.append("Confirm which plan pays first and how additional coverage applies.")
    # Provider-specific messages can have no benefit-network indicator. They
    # remain valid participation evidence even when excluded from cost sharing.
    participation = [row['evidence_path'] for row in provider_rows if any(
        _provider_participation(note, provider_npi)
        for note in notes([row.get('messages'), row.get('additionalInformation')]))]
    conflicting_network = any(re.search(r'\b(?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
                              for note in descriptions)
    # A contrary statement about this provider may sit on an out-of-network
    # row excluded from the in-network cost-sharing calculation above.
    conflicting_network |= any(re.search(r'\bprovider(?: ID you entered)? is (?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
        for row in provider_rows for note in notes([row.get('messages'), row.get('additionalInformation')]))
    if network_conflict:
        review_reasons.append('Provider-network evidence reports nonparticipation or contradicts the in-network assertion. Verify participation before estimating.')
    payer_network_verified = bool(participation and not conflicting_network and not network_conflict and active is True
        and not multiple_plans and not invalid_dates and date_matches is not False and not source_errors)
    out_of_network_verified = bool((negative_provider_ids or provider_out_of_network_verified)
        and not network_conflict and active is True
        and not multiple_plans and not invalid_dates and date_matches is not False and not source_errors)
    return {"active": active, "coverage_evidence": coverage,
            "excluded_service_evidence_ids": excluded_service_ids,
            "coverage_status_conflict": bool(inactive and has_active) or coverage['status'] == 'CONFLICT',
            "coverage_dates_invalid": invalid_dates,
            "source_errors": source_errors, "service_date_matches": date_matches, "returned_service_dates": returned_service,
            "coverage_level": "individual" if wanted else "unknown",
            "network_status": "out_of_network" if out_of_network_verified else "in_network" if (provider_network_verified and not network_conflict) or payer_network_verified else "unknown",
            "network_conflict": network_conflict,
            "network_source": 'payer_participation_message' if (negative_provider_ids and out_of_network_verified) or payer_network_verified
                else 'clinic_nonparticipation_rule' if out_of_network_verified and provider_out_of_network_verified
                else 'clinic_record' if provider_network_verified and not network_conflict else None,
            "network_evidence_ids": negative_provider_ids if out_of_network_verified else participation if payer_network_verified else [],
            "network_provider_npi": provider_npi if out_of_network_verified or payer_network_verified else None,
            "qmb_status": "protected" if qmb_ids else "unknown",
            "qmb_evidence_ids": qmb_ids,
            "deductible_remaining_cents": deductible_cents, "oop_remaining_cents": oop_cents,
            "deductible_annual_cents": _stedi_cents(annual_deductible.get('benefitAmount')) if annual_deductible else None,
            "oop_annual_cents": _stedi_cents(annual_oop.get('benefitAmount')) if annual_oop else None,
            "oop_annual_conflict_ids": cost_share.conflicts.get('oop_annual', []),
            "oop_medical_candidate_ids": cost_share.oop_medical_candidate_ids,
            "oop_quarantined_ids": cost_share.oop_quarantined_ids,
            "oop_remaining_conflict_ids": cost_share.conflicts.get('oop_remaining', []),
            "deductible_applicability": deductible_applicability,
            "deductible_applicability_ids": list(dict.fromkeys(row['evidence_path'] for _, row in deductible_signals)),
            "family_oop_remaining_cents": family_oop_cents,
            "family_oop_zero_applies": family_oop_zero_applies,
            "family_oop_ids": [row['evidence_path'] for row in family_best] if family_oop_zero_applies else [],
            "copay_cents": copay_cents, "coinsurance_percent": str(coinsurance_percent) if coinsurance_percent is not None else None,
            "has_secondary": other_active_coverage or multiple_plans, "requires_review": bool(review_reasons),
            "selected_benefit_ids": [path for row in (copay, coinsurance) if row for path in row.get('evidence_paths', [row['evidence_path']])],
            "deductible_id": deductible.get("evidence_path") if deductible else None,
            "deductible_annual_id": annual_deductible.get("evidence_path") if annual_deductible else None,
            "oop_annual_id": annual_oop.get("evidence_path") if annual_oop else None,
            "oop_id": oop.get("evidence_path") if oop else None,
            "copay_id": copay.get("evidence_path") if copay else None,
            "coinsurance_id": coinsurance.get("evidence_path") if coinsurance else None,
            "copay_remaining_cents": copay_cap,
            "copay_limit_ids": [row['evidence_path'] for row in cap_rows],
            "separate_copay_limit": bool(cap_rows),
            **({'deductible_applies': deductible_applies} if deductible_applies is not None else {}),
            'deductible_waiver_ids': [row['evidence_path'] for value, row in deductible_signals if value is False],
            "authorization_basis": authorization_basis,
            "benefit_assumptions": ['The returned non-Tier-1 specialist benefit is used; the provider tier has not been confirmed.'] if any(
                'non tier 1 specialist' in note.casefold() for row in (copay, coinsurance) if row
                for note in _benefit_notes(row.get('messages'))) else [],
            "copay_period": str((copay or {}).get("timeQualifierCode") or (copay or {}).get("timeQualifier") or ""),
            "plan_details": plan_details(payload),
            "authorization_signal": authorization_signal, "referral_signal": referral_signal,
            "review_reasons": review_reasons,
            "normalization_notes": ["Stedi 271 benefit schedule does not prove provider participation."],
            "resolved_service_type": service_type_code,
            "network_independent": bool(copay or coinsurance) and all(_network_indicator(row) in {"W", "IN_AND_OUT_OF_NETWORK"} for row in (copay, coinsurance, deductible, oop) if row),
            "reported_options": reported_stedi_options(payload, service_type_code)}
