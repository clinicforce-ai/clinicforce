"""Commercial previsit pricing workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

def _ecw_case_data(inputs: dict, source: dict, conflicts: list[str]) -> dict[str, _api.Any]:
    return {"appointment_id": source.get("appointment_id"), "appointment_date": source.get("appointment_date"),
            "retrieved_at": source.get("retrieved_at"), "appointment_status": source.get("appointment_status"),
            "suggested_lines": [{"code": row.get("code"), "units": row.get("units"), "description": row.get("description")}
                                for row in inputs.get("lines", []) if row.get("code")],
            "reason": inputs.get("reason"), "conflicts": conflicts}


def _past_visit_with_saved_estimate(visit: dict[str, _api.Any], case: dict | None) -> dict[str, _api.Any]:
    """Attach only an estimate already saved for this exact eCW encounter."""
    ecw = ((case or {}).get("commercial") or {}).get("ecw") or {}
    estimate = (case or {}).get("estimate")
    if (case or {}).get("source_mode") != "commercial" or ecw.get("appointment_id") != visit.get("id") or not isinstance(estimate, dict):
        return {**visit, "saved_estimate": None}
    return {**visit, "saved_estimate": {
        "created_at": case.get("created_at"),
        "allowed_cents": estimate.get("allowed_cents"),
        "patient_cents": estimate.get("patient_cents"),
        "plan_cents": estimate.get("plan_cents"),
        "disclaimer": estimate.get("disclaimer"),
    }}


def _commercial_slot(appointment_id: str) -> str:
    return "commercial:" + _api.hashlib.sha256(appointment_id.encode()).hexdigest()


def _previsit_service_types(inputs: dict) -> list[str]:
    """Ask for the booked categories, including explicitly combined care."""
    scheduled = [row['code'] for row in inputs.get('lines', []) if row.get('service_status') == 'scheduled' and row.get('code')]
    categories = [_api.visit_estimate.service_type(code, {}) for code in scheduled]
    if not scheduled:
        reason = str(inputs.get('reason') or '')
        if _api.visit_estimate.is_emg_visit(inputs):
            categories.append('73')
        if _api.visit_estimate.is_spinal_tap(inputs, clinic_abbreviation=inputs.get('spinal_tap_abbreviation') is True):
            categories.append('2')
        if _api.re.search(r'\bskin\s+(?:punch\s+)?(?:bx|biops\w*)\b|\bpunch\s+biops\w*\b', reason, _api.re.I):
            categories.append('2')
        if not categories or _api.re.search(r'\b(?:f/u|fu|follow[ -]?up)\b', reason, _api.re.I):
            categories.append('98')
    # General medical benefits carry deductible and out-of-pocket accumulators
    # that a service-specific response may omit. Keep the booked service first.
    return list(dict.fromkeys([*(code for code in categories if code), '30']))


def _eligibility_timing(service_date: str, check_from_date: str | None = None) -> dict | None:
    service_day = _api.date.fromisoformat(service_date)
    today = _api.datetime.now(_api.ZoneInfo(_api.os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date()
    check_from = max(service_day - _api.timedelta(days=7), _api.date.fromisoformat(check_from_date) if check_from_date else _api.date.min)
    if today >= check_from:
        return None
    return {'status': 'too_early', 'appointment_date': service_date, 'check_from': check_from.isoformat(),
        'message': f"Appointment found on {service_day.strftime('%B %d, %Y')}. Insurance checking opens {check_from.strftime('%B %d, %Y')}. No Stedi request is made before then."}


def _previsit_codes(inputs: dict) -> list[dict[str, _api.Any]]:
    """Office E/M alternatives are coding standards, not a selected service."""
    scheduled = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled" and row.get("code")]
    if scheduled:
        return [{"id": row.get("id") or f"{row['code']}:{index}", "code": row["code"], "source": "eCW scheduled service",
                 "needs_confirmation": row.get("needs_confirmation", False), "description": row.get("description"), "modifiers": row.get("modifiers") or [], "units": row.get("units")}
                for index, row in enumerate(scheduled)]
    if _api.visit_estimate.is_emg_visit(inputs):
        return []  # An EMG appointment is not an office E/M just because the patient is new.
    # The appointment's explicit new/established label supports a code family;
    # it cannot establish the eventual MDM/time level or that an office visit occurs.
    family = {"new": range(99202, 99206), "established": range(99212, 99216)}.get(inputs.get("visit_type"))
    return [{"id": f"{code}:{index}", "code": str(code), "source": "conditional office E/M alternative", "units": 1}
            for index, code in enumerate(family)] if family else []


def _office_service_types(practice_id: str, source: dict) -> list[str]:
    specialist = _api.provider_specialty.office_role((source.get('rendering_provider') or {}).get('roles', [])) == 'specialist'
    return ['98', 'BQ', 'BY', '1'] if specialist else ['98', 'BY', '1']


def _benefit_context(inputs: dict, source: dict) -> dict:
    rendering = source.get('rendering_provider') or {}
    # The inquiry uses the rendering NPI only with a complete provider name.
    npi = rendering.get('npi') if rendering.get('first_name') and rendering.get('last_name') else None
    return {'place_of_service': inputs.get('place_of_service'),
            'visit_context': {key: inputs.get(key) for key in ('reason', 'diagnoses')},
            'provider_npi': npi}


def _previsit_range(practice_id: str, inputs: dict, source: dict, payer_id: str,
                    codes: list[dict], benefits: dict, *, payload: dict | None = None,
                    review: dict | None = None) -> tuple[dict | None, list[str]]:
    review = review or {}
    options = bool(codes) and all(row.get('source') == 'Contract service option' for row in codes) and not review.get('selected_ids')
    alternatives = options or (bool(codes) and all(row.get("source") == "conditional office E/M alternative" for row in codes) and not review.get("selected_ids"))
    selected = [row for row in codes if not review.get("selected_ids") or row.get("id") in review['selected_ids']]
    office_types = _api._office_service_types(practice_id, source)
    rates, rules, issues = [], {'office_service_types': office_types, **_api._benefit_context(inputs, source)}, []
    if benefits.get('network_status') == 'out_of_network':
        issues.append('The payer did not supply an out-of-network allowed amount for these CPTs; clinic contract and cash prices cannot replace it.')
    else:
        try:
            _, network, profile = _api._previsit_practice_path(practice_id, {**inputs, "payer_id": payer_id}, source)
            if network.get("plan_matcher") and not source.get("plan"):
                raise _api.commercial.CommercialError("Clinic pricing: eCW did not return the contract plan identifier.")
            if not inputs.get("place_of_service"):
                raise _api.commercial.CommercialError("Map the appointment location to its place of service.")
            rates = _api.commercial_store.matching_rates(practice_id, profile, network, inputs["service_date"], selected)
            rules = {**rules, **profile['rules']}
        except (_api.HTTPException, _api.commercial.CommercialError) as exc:
            issues.append(str(exc.detail) if isinstance(exc, _api.HTTPException) else str(exc))
    priced = _api.visit_estimate.price_lines(selected, rates, benefits, rules, payload=payload,
        service_date=inputs.get('service_date', ''), reviews=review.get('lines'), alternatives=alternatives)
    if options:
        priced['mode'] = 'options'
        priced['collection'] = {'action': 'choose_services', 'amount_cents': None,
            'reason': 'These are separate one-unit service examples, not a visit total. Choose anticipated services and units to build the visit estimate.'}
    if not selected:
        issues.append('Confirm the planned services before calculating the visit total.')
    payments = review.get('payments') or {}
    if priced.get('complete') and not priced.get('conditional') and not alternatives and payments.get('reference'):
        amount = max(0, priced['patient_max_cents'] - payments['amount_cents'])
        priced['collection'] = {'action': 'collect_estimate' if amount else 'no_collection', 'amount_cents': amount,
            'reason': 'Estimated responsibility less verified payments received for this visit.',
            'payment_reference': payments['reference'], 'verified_at': payments.get('verified_at')}
    return priced, issues


def _previsit_benefits(payload: dict, coverage: dict, service_date: str | None = None, service_type: str = "98",
                       *, inputs: dict | None = None, source: dict | None = None) -> dict:
    context = _api._benefit_context(inputs, source or {}) if inputs else {}
    codes = _api._previsit_codes(inputs) if inputs else []
    office_only = bool(codes) and all(row['code'].isdigit() and
        (99202 <= int(row['code']) <= 99205 or 99212 <= int(row['code']) <= 99215) for row in codes)
    benefits = _api.commercial.normalize_stedi_271(payload, service_type, False,
        coverage["active_coverages"] > 1 or coverage.get("other_active_coverage", False), service_date=service_date,
        procedure_code=codes[0]['code'] if office_only else None,
        office_service_types=_api._office_service_types('', source or {}), **context)
    benefits['benefit_source'] = payload.get('benefit_source') or 'Stedi'
    if (inputs and source and not coverage.get('other_active_coverage')
            and _api.stedi_discovery.corroborates_patient_policy(payload, inputs, source)
            and _api.stedi_discovery.automatic_match(payload, inputs, source)):
        recorded = _api.insurance_identity.compare(payload, coverage)
        if recorded.get('policy_differences'):
            coverage = _api._discovered_coverage(inputs, source, payload)
            benefits['policy_match'] = {'source': 'payer_response', 'status': 'verified',
                'recorded_differences': recorded['policy_differences']}
            benefits['normalization_notes'].append(
                'The payer linked this patient to the recorded member policy as a dependent. Subscriber details were matched automatically; eCW was not changed.')
    comparison = _api.insurance_identity.compare(payload, coverage)
    benefits['identity'] = comparison
    if comparison['status'] == 'review':
        benefits['requires_review'] = True
        benefits['review_reasons'].append('The subscriber relationship could not be verified automatically from eCW and payer evidence.'
            if comparison.get('policy_differences') else 'Confirm the member identity: the payer response differs from eCW.'
            if any(row['field'] == 'member_id' for row in comparison['differences']) else
            'Confirm patient identity: the payer-returned patient details differ from eCW.')
    return benefits


def _network_contract_evidence(practice_id: str, network: dict, profile: dict, service_date: str = '') -> dict:
    evidence = {key: network.get(key) for key in ('network_name', 'evidence_reference', 'effective_from', 'effective_to')}
    def current(document: dict) -> bool:
        return (document.get('kind') == 'contract' and document.get('content_type') == 'application/pdf'
                and (not service_date or not document.get('effective_from') or document['effective_from'] <= service_date)
                and (not service_date or not document.get('effective_to') or document['effective_to'] >= service_date))
    library_source = (profile.get('rules') or {}).get('library_source') or {}
    document_id = library_source.get('document_id')
    if document_id and network.get('evidence_reference') == library_source.get('reference') == f'library:{document_id}':
        document = _api.staff_store.get_library(document_id, practice_id)
        if (document and current(document)
                and document.get('sha256') == library_source.get('sha256')):
            evidence.update(document_id=document_id, document_label=document['label'],
                            locator=library_source.get('locator'))
    if not evidence.get('document_id') and network.get('payer_connection_id'):
        payer = _api.commercial_store.connection(practice_id, network['payer_connection_id'])
        if payer and payer.get('display_name'):
            contracts = [document for document in _api.staff_store.list_library(practice_id)
                         if current(document)
                         and document.get('payer')
                         and _api.insurance_identity.normalized(document['payer']) == _api.insurance_identity.normalized(payer['display_name'])]
            if len(contracts) == 1:
                evidence.update(related_document_id=contracts[0]['id'], related_document_label=contracts[0]['label'])
            elif len(contracts) > 1:
                evidence['reason'] = 'Multiple current payer contracts are in the Clinical Library; none is linked to this plan.'
    return evidence


def _requirement_candidates(result: dict) -> list[dict]:
    selected_ids = {row.get('id') for row in result.get('codes') or []}
    suggested_codes = {str(candidate.get('code') or '').upper()
                       for group in (result.get('suggestions') or {}).get('groups') or []
                       for candidate in group.get('codes') or []}
    recommended_ids = set((result.get('service_plan') or {}).get('recommended_ids') or [])
    return [row for row in result.get('available_codes') or []
            if row.get('id') not in selected_ids and
            (row.get('id') in recommended_ids or row.get('code') in suggested_codes)]


def _attach_previsit_pricing(result: dict, practice_id: str, inputs: dict, source: dict,
                             payer_id: str, benefits: dict, *, payload: dict | None = None,
                             review: dict | None = None) -> None:
    # Retain a scoped payer assertion about the requesting provider. A generic
    # in-network benefit schedule alone still cannot establish participation.
    if benefits.get('network_source') != 'payer_participation_message':
        benefits["network_status"] = "unknown"
    connections = [row for row in _api.commercial_store.list_connections(practice_id)
                   if row.get("stedi_payer_id") == payer_id and row.get("status") == "staff_guidance_enabled"]
    if len(connections) != 1:
        result["issues"].append("Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.")
        result['price_range'] = _api.visit_estimate.price_lines(result['codes'], [], benefits, {'cost_share': 'payer_returned',
            'office_service_types': _api._office_service_types(practice_id, source),
            **_api._benefit_context(inputs, source)}, payload=payload,
            service_date=inputs.get('service_date', ''), reviews=(review or {}).get('lines'),
            alternatives=not (review or {}).get('selected_ids') and bool(result['codes']) and all(row.get('source') == 'conditional office E/M alternative' for row in result['codes']))
        return
    connection = connections[0]
    service_date = str(inputs.get('service_date') or '')
    scope_complete = all((service_date, (inputs.get('rendering_provider') or {}).get('npi'),
                          (source.get('billing_provider') or {}).get('npi'),
                          source.get('location_id'), inputs.get('place_of_service'), source.get('plan')))
    try:
        if benefits.get('network_status') == 'out_of_network':
            raise _api.HTTPException(422, 'Payer confirmed out-of-network participation; in-network contract prices do not apply.')
        _, network, profile = _api._previsit_practice_path(practice_id, {**inputs, "payer_id": connection["id"]}, source)
        evidence = _api._network_contract_evidence(practice_id, network, profile, service_date)
        if evidence.get('document_id') or evidence.get('related_document_id'):
            benefits['network_status'] = 'in_network'
            benefits['network_source'] = 'clinic_library_contract'
            result['network_evidence'] = evidence
            if source.get('payer_network_names'):
                evidence['payer_network_name'] = source['payer_network_names'][0]
                evidence['payer_network_source'] = 'Stedi planNetwork'
        elif scope_complete:
            if evidence.get('reason'):
                result['network_evidence'] = evidence
            else:
                benefits['network_status'] = 'out_of_network'
                benefits['network_source'] = 'clinic_library_absent'
                result['network_evidence'] = {'reason': 'No current matching contract in the Clinical Library.'}
    except _api.HTTPException as exc:
        no_match = str(exc.detail) in {
            'A sourced nonparticipation rule applies to this provider, product, location and date.',
            'This practice has no single verified provider-network record for this eCW provider and location.',
            'The payer-reported member network does not match the clinic contract.',
            'The clinic fee schedule is not matched to the exact payer product.',
        }
        if scope_complete and no_match:
            benefits['network_status'] = 'out_of_network'
            benefits['network_source'] = 'clinic_library_absent'
            result['network_evidence'] = {'reason': 'No current contract matches this payer, plan, provider and visit in the Clinical Library.'}
    result["price_range"], issues = _api._previsit_range(practice_id, inputs, source, connection["id"], result["codes"], benefits,
                                                  payload=payload, review=review)
    result["issues"].extend(issues)
