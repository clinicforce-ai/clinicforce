"""Select the applicable service and provider network rows from a Stedi 271."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.insurance.stedi_benefits.fields import (
    _benefit_notes, _dates_apply, _network_indicator, _procedure,
    _provider_participation, _stedi_entries, coverage_kind,
)


@dataclass
class BenefitRows:
    entries: list[dict[str, Any]]
    service_type_code: str
    excluded_service_ids: list[str]
    office: bool
    priorities: list[str]
    selected_tier: str
    accepted_networks: set[str]
    network_conflict: bool
    provider_rows: list[dict[str, Any]]
    negative_provider_ids: list[str]


def select_benefit_rows(payload: dict[str, Any], service_type_code: str,
                        provider_network_verified: bool, *, provider_out_of_network_verified: bool = False,
                        procedure_code: str | None,
                        office_visit: bool = False,
                        service_date: str | None, selected_ids: list[str] | None,
                        office_service_types: list[str] | None,
                        place_of_service: str | None, visit_context: dict | None,
                        provider_npi: str | None) -> BenefitRows:
    """Apply service, location, date and provider-specific network evidence."""
    entries = [row for row in _stedi_entries(payload) if coverage_kind(row, payload) != 'supplementary']
    priorities = office_service_types or ['98', 'BY', '1']
    excluded_service_ids = []
    if place_of_service:
        # Office and telehealth terms can share STC 98 but are different benefits.
        entries = [row for row in entries if not row.get('placesOfService') or
                   any(str(pos.get('code') or '') == place_of_service for pos in row['placesOfService'] if isinstance(pos, dict))]
    diagnostic = procedure_code in {*(str(n) for n in range(95907, 95914)), '95885', '95886', '95887'}
    if diagnostic:
        # Sleep studies and radiology share STC 73 with EMG. Their presence
        # must not prevent use of an explicitly labelled diagnostic benefit.
        entries = [row for row in entries if _procedure(row) or not any(
            re.fullmatch(r'(?:sleep study|x[ -]?ray)(?:\s*-\s*professional)?', note.strip(), re.I)
            for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    if diagnostic and service_type_code == '73' and not any('73' in (row.get('serviceTypeCodes') or []) and row.get('code') in {'A', 'B'} for row in entries):
        # Some plans return diagnostic procedure terms under 98. Require that
        # description; an office-only copay is not an EMG benefit.
        diagnostic_rows = [row for row in entries if '98' in (row.get('serviceTypeCodes') or [])
            and any(re.search(r'\bdiagnostic medical(?: procedure)?\b', note, re.I)
                    for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
        if diagnostic_rows:
            service_type_code = '98'
            entries = [row for row in entries if '98' not in (row.get('serviceTypeCodes') or []) or row in diagnostic_rows]
    office = office_visit or procedure_code in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
    if office and place_of_service == '11':
        # STC 98 is broad: a specialist telemedicine or ABA copay is not the
        # in-person office copay, even when the payer omits POS on that row.
        def other_office_service(row: dict) -> bool:
            if _procedure(row):
                return False
            clauses = [part.strip() for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')])
                       for part in note.split(';') if part.strip()]
            unrelated = [bool(re.fullmatch(r'(?:telemedicine|telehealth)\b.*|applied behavior analysis\b.*|agents, drugs and/or supplies administered or obtained in connection with care',
                                           clause, re.I)) for clause in clauses]
            return bool(clauses and any(unrelated) and all(
                unrelated[index] or re.fullmatch(r'(?:specialist|primary care|PCP|provider role other)', clause, re.I)
                for index, clause in enumerate(clauses)))
        rejected = [row for row in entries if other_office_service(row)]
        excluded_service_ids.extend(row['evidence_path'] for row in rejected)
        entries = [row for row in entries if row not in rejected]
    if office and selected_ids is None:
        # Diagnosis context must establish an unrelated visit. Unknown/TMJ/jaw
        # context retains the exclusion; an office CPT alone cannot disprove TMJ.
        context = visit_context or {}
        diagnoses = [str(code).upper().replace('.', '') for code in (context.get('diagnoses') or [])]
        unrelated_tmj = (bool(diagnoses) and any(not code.startswith('Z') and code != 'R69' for code in diagnoses)
            and all(re.fullmatch(r'[A-Z][0-9][A-Z0-9]{1,5}', code) for code in diagnoses)
            and not any(code.startswith(('M26', 'R6884')) for code in diagnoses)
            and not re.search(r'\b(?:TMJ|TMD|temporomandibular|jaw)\b', str(context.get('reason') or ''), re.I))
        def unrelated_exclusion(row: dict) -> bool:
            labels = _benefit_notes([row.get('messages'), row.get('additionalInformation')])
            return bool(unrelated_tmj and row.get('code') == 'I' and not _procedure(row) and labels
                and all(re.fullmatch(r'TMJ (?:PCP AND TMJ Specialist|Specialist|PCP)', label.strip(), re.I) for label in labels))
        excluded_tmj_ids = [row['evidence_path'] for row in entries if unrelated_exclusion(row)]
        excluded_service_ids.extend(excluded_tmj_ids)
        entries = [row for row in entries if row['evidence_path'] not in excluded_tmj_ids]
        # Explicitly different services sometimes share an office STC. Retain
        # every raw row in reported_options, but exclude these from office math.
        unrelated = r'(?:injections?|diagnostic medical procedure|diagnostic lab(?:oratory)?|laboratory|(?:diagnostic )?x[ -]?ray|acupuncture|applied behavior analysis|chemotherapy)(?:\s*-\s*professional)?'
        def other_services_only(row: dict) -> bool:
            # A shared accumulator can list office visits alongside injections
            # and lab work. Discard only a list entirely naming other services.
            labels = [label.strip() for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')])
                      for label in note.split(';') if label.strip()
                      and not re.fullmatch(r'(?:no referral required|referral not required|(?:prior )?authorization (?:not )?required)\.?', label.strip(), re.I)]
            return bool(labels) and all(re.fullmatch(unrelated, label, re.I) for label in labels)
        entries = [row for row in entries if _procedure(row) or not other_services_only(row)]
        if place_of_service == '11':
            entries = [row for row in entries if _procedure(row) or not (
                (labels := _benefit_notes([row.get('messages'), row.get('additionalInformation')]))
                and all(re.fullmatch(r'Telemedicine (?:General Medicine|Specialist) Visit', label.strip(), re.I) for label in labels))]
        if 'BQ' in priorities:
            # Keep generic and specialist rows,
            # excluding rows explicitly limited to primary care.
            entries = [row for row in entries if not (
                re.search(r'\b(?:primary care|PCP)\b', ' '.join(_benefit_notes([row.get('messages'), row.get('additionalInformation')])), re.I)
                and not re.search(r'\bspecialist\b', ' '.join(_benefit_notes([row.get('messages'), row.get('additionalInformation')])), re.I))]
            for kind in ('A', 'B'):
                specialist = [row for row in entries if row.get('code') == kind and any(
                    re.search(r'\bspecialist\b|\bprovider role other\b', note, re.I)
                    for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
                if place_of_service and any(row.get('placesOfService') for row in specialist):
                    specialist = [row for row in specialist if row.get('placesOfService')]
                if specialist:
                    entries = [row for row in entries if row.get('code') != kind or _procedure(row) or row in specialist]
    def relevant_service(row: dict) -> bool:
        procedure = _procedure(row)
        if procedure:
            return procedure == procedure_code
        stcs = {str(value) for value in row.get("serviceTypeCodes") or []}
        return not stcs or bool(stcs & ({*priorities, "30"} if office and selected_ids is None else {service_type_code, "30"}))
    # A benefit row names a schedule, not the provider's participation. Only
    # an explicit statement about the requesting provider can select the
    # out-of-network tier automatically. Contrary clinic evidence blocks it.
    def provider_row_in_date(row: dict) -> bool:
        if not service_date:
            return True
        plan = (payload.get('plans') or [])[row['plan_index']] if 'plan_index' in row else payload
        person_dates = (payload.get('dependent') or payload.get('subscriber') or {}).get('dates') or {}
        bounds = [person_dates, payload.get('planDateInformation') or {}, plan.get('planDateInformation') or {},
                  plan.get('dates') or {}, row.get('benefitsDateInformation') or {}, row.get('dates') or {}]
        try:
            return all(_dates_apply(bound, service_date) for bound in bounds)
        except (ValueError, TypeError):
            return False
    provider_rows = [row for row in _stedi_entries(payload) if coverage_kind(row, payload) != 'supplementary'
                     and relevant_service(row) and provider_row_in_date(row)]
    negative_provider_ids = [row['evidence_path'] for row in provider_rows if provider_npi and any(
        re.search(r'\bprovider(?: ID you entered)? is (?:not participating|not in[ -]network|non[- ]participating|out[- ]of[- ]network)\b', note, re.I)
        for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    positive_provider_ids = [row['evidence_path'] for row in provider_rows if any(
        _provider_participation(note, provider_npi)
        for note in _benefit_notes([row.get('messages'), row.get('additionalInformation')]))]
    network_conflict = bool((negative_provider_ids and (positive_provider_ids or provider_network_verified))
                            or (provider_out_of_network_verified and positive_provider_ids))
    selected_tier = 'out_of_network' if (negative_provider_ids or provider_out_of_network_verified) and not network_conflict else 'in_network'
    accepted_networks = ({'N', 'NO', 'OUT_OF_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'} if selected_tier == 'out_of_network'
                         else {'Y', 'YES', 'IN_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'})
    entries = [row for row in entries if relevant_service(row)
               and (_network_indicator(row) in accepted_networks or
                    not _network_indicator(row) and row.get('code') in {'status', '1', '2', '3', '4', '5', '6', '7', '8', 'I', 'R', 'D'})]
    return BenefitRows(entries, service_type_code, excluded_service_ids, office,
                       priorities, selected_tier, accepted_networks, network_conflict,
                       provider_rows, negative_provider_ids)
