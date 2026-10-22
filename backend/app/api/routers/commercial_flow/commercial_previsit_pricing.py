"""Commercial previsit pricing workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_rate_reference import _attach_temporary_uhc_rates
from app.insurance import botox_review, cms_locality, cms_pfs, cpt_rules, insurance_product, inventory_certification, library_network_exclusions, group_contract_support

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
    categories.extend(inputs.get('_selected_service_types') or [])
    if botox_review.is_botox_visit(inputs):
        categories.extend(category for family in ('botulinum_drug', 'chemodenervation')
                          for category in cpt_rules.SERVICE_FAMILIES[family]['eligibility_service_types'])
    if not scheduled:
        reason = str(inputs.get('reason') or '')
        if _api.visit_estimate.is_emg_visit(inputs):
            categories.append('73')
        if _api.visit_estimate.is_spinal_tap(inputs, clinic_abbreviation=inputs.get('spinal_tap_abbreviation') is True):
            categories.append('2')
        if _api.re.search(r'\bskin\s+(?:punch\s+)?(?:bx|biops\w*)\b|\bpunch\s+biops\w*\b', reason, _api.re.I):
            categories.append('2')
        if (not categories and not botox_review.is_botox_visit(inputs)
                or _api.re.search(r'\b(?:f/u|fu|follow[ -]?up)\b', reason, _api.re.I)):
            categories.append('98')
    # General medical benefits carry deductible and out-of-pocket accumulators
    # that a service-specific response may omit. Keep the booked service first.
    return list(dict.fromkeys([*(code for code in categories if code), '30']))


def _reviewed_service_types(cases: list[dict], appointment_id: str, service_date: str,
                            context_token: str) -> list[str]:
    """Carry selected visit CPT categories into a fresh payer inquiry."""
    for case in reversed(cases):
        context = case.get('previsit_context') or {}
        source = context.get('source') or {}
        prior_inputs = context.get('inputs') or {}
        review = case.get('visit_review') or {}
        if (source.get('appointment_id') != appointment_id
                or prior_inputs.get('service_date') != service_date
                or review.get('context_token') != context_token):
            continue
        saved_result = context.get('result') or {}
        selected = set(review.get('selected_ids') or saved_result.get('selected_ids') or [])
        rows = [row for row in saved_result.get('codes') or []
                if row.get('id') in selected]
        if selected and len(rows) == len(selected):
            return list(dict.fromkeys(
                category for row in rows
                if (category := _api.visit_estimate.service_type(str(row.get('code') or ''), {}))))
    return []


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
    if botox_review.is_botox_visit(inputs):
        return []  # Booking labels do not select drug, injection or separate E/M.
    scheduled = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled" and row.get("code")]
    if scheduled:
        return [{"id": row.get("id") or f"{row['code']}:{index}", "code": row["code"], "source": "eCW scheduled service",
                 "source_detail": row.get("source"), "needs_confirmation": row.get("needs_confirmation", False),
                 "description": row.get("description"), "modifiers": row.get("modifiers") or [], "units": row.get("units")}
                for index, row in enumerate(scheduled)]
    if _api.visit_estimate.is_emg_visit(inputs):
        documented = cpt_rules.documented_ncs_code(str(inputs.get('reason') or ''))
        if documented:
            return [{"id": f"ecw-count:{documented}", "code": documented,
                     "source": "eCW documented study count", "units": 1,
                     "description": f"Nerve conduction, {cpt_rules.NCS_STUDY_RANGES[documented]} studies"}]
        # The booking supports an NCS planning scenario, but says nothing about
        # the number of studies or whether needle muscle work will be done.
        # Price these levels independently; none is a performed or chosen CPT.
        return [{"id": f"ncs-option:{code}", "code": str(code),
                 "source": "conditional NCS alternative", "units": 1,
                 "description": f"Nerve conduction, {cpt_rules.NCS_STUDY_RANGES[str(code)]} studies"}
                for code in range(95907, 95914)]
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
    # Eligibility submission still requires its own name fields; CMS pricing
    # uses a validated NPI and rejects contradictory visit/provider records.
    source_npi = str(rendering.get('npi') or '')
    visit_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    conflict = bool(source_npi and visit_npi and source_npi != visit_npi)
    npi = None if conflict else (visit_npi or source_npi)
    npi = npi if npi and len(npi) == 10 and npi.isdigit() else None
    return {'place_of_service': inputs.get('place_of_service'),
            'visit_context': {key: inputs.get(key) for key in ('reason', 'diagnoses')},
            'provider_office_role': _api.provider_specialty.office_role(rendering.get('roles') or []),
            'provider_npi': npi, 'provider_npi_conflict': conflict}


def _previsit_range(practice_id: str, inputs: dict, source: dict, payer_id: str,
                    codes: list[dict], benefits: dict, *, payload: dict | None = None,
                    review: dict | None = None, payer_route_id: str | None = None) -> tuple[dict | None, list[str]]:
    review = review or {}
    options = bool(codes) and all(row.get('source') == 'Contract service option' for row in codes) and not review.get('selected_ids')
    alternatives = options or (bool(codes) and all(row.get("source") in {
        "conditional office E/M alternative", "conditional NCS alternative"} for row in codes)
        and not review.get("selected_ids"))
    selected = [row for row in codes if not review.get("selected_ids") or row.get("id") in review['selected_ids']]
    office_types = _api._office_service_types(practice_id, source)
    rates, rules, issues = [], {'office_service_types': office_types, **_api._benefit_context(inputs, source)}, []
    fep_product = ((benefits.get('plan_details') or {}).get('identified_product') == 'FEP Blue Basic PSHB'
                   and source.get('payer_plan_bound') is True
                   and source.get('payer_plan_names') == ['FEP Blue Basic PSHB'])
    rules['fep_preferred_verified'] = bool(fep_product and
        benefits.get('network_source') == 'payer_participation_message' and
        _api.network_participation.verified_in_network(benefits) and
        benefits.get('network_provider_npi') == rules.get('provider_npi') and rules.get('provider_npi'))
    if rules['provider_npi_conflict']:
        issues.append('The eCW rendering NPI conflicts with the selected visit provider; resolve it before pricing.')
    medical_names = (benefits.get('plan_details') or {}).get('names') or []
    rules['medicare_advantage'] = insurance_product.is_medicare_advantage(medical_names)
    if benefits.get('network_status') == 'out_of_network':
        binding = (benefits.get('identity') or {}).get('binding') or ''
        rules['patient_binding'] = binding
        rules['product_binding'] = _api.payer_allowance.product_binding(benefits.get('plan_details'))
        rules['cms_locality'], rules['cms_locality_evidence'] = cms_locality.pricing_locality(
            source.get('cms_service_zip'), source.get('cms_locality'))
        if rules['cms_locality_evidence']['status'] == 'clinic_location_conflict':
            issues.append('The eCW service ZIP does not match the clinic-configured CMS locality; resolve the service location before using a CMS benchmark.')
        rules['cms_qp_status'] = source.get('cms_qp_status')
        if review.get('selected_ids') and _api.network_participation.verified_out_of_network(benefits):
            rates = _api.payer_allowance.quoted_rates(review, selected, payer_id=payer_route_id or payer_id,
                service_date=str(inputs.get('service_date') or ''),
                provider_npi=str(rules.get('provider_npi') or ''),
                place_of_service=str(rules.get('place_of_service') or ''), patient_binding=binding,
                product_binding=_api.payer_allowance.product_binding(benefits.get('plan_details')))
            quoted = {rate['line_id'] for rate in rates}
            if rules['medicare_advantage']:
                for line in selected:
                    if line['id'] in quoted:
                        continue
                    estimated = cms_pfs.estimate_rate(line['code'], line.get('modifiers') or [],
                        line.get('units'), service_date=str(inputs.get('service_date') or ''),
                        locality=str(rules.get('cms_locality') or ''),
                        qp_status=str(source.get('cms_qp_status') or ''),
                        place_of_service=str(rules.get('place_of_service') or ''),
                        provider_npi=str(rules.get('provider_npi') or ''),
                        patient_binding=binding)
                    if estimated:
                        rates.append({**estimated, 'line_id': line['id'], 'units': line['units'],
                                      'product_binding': rules['product_binding']})
        if len(rates) != len(selected):
            issues.append('Obtain the payer out-of-network allowed amount or complete CMS locality and provider payment scope for each selected CPT; clinic contract and cash prices cannot replace it.')
    else:
        try:
            _, network, profile = _api._previsit_practice_path(practice_id, {**inputs, "payer_id": payer_id}, source)
            if network.get("plan_matcher") and not source.get("plan"):
                raise _api.commercial.CommercialError("Clinic pricing: eCW did not return the contract plan identifier.")
            contract = _api._network_contract_evidence(practice_id, network, profile, inputs['service_date'])
            if not contract.get('document_id'):
                raise _api.commercial.CommercialError(
                    'The Clinical Library has no contract linked to this exact payer product and provider.')
            if (fep_product and 'fep' in str(network.get('network_name') or '').casefold()
                    and 'preferred' in str(network.get('network_name') or '').casefold()
                    and _api.network_participation.verified_in_network(benefits)):
                rules['fep_preferred_verified'] = True
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
    context.pop('provider_npi_conflict', None)  # Pricing-only guard, not a Stedi normalization field.
    context.pop('provider_office_role', None)  # Published plan pricing only.
    rendering = (source or {}).get('rendering_provider') or {}
    if not rendering.get('first_name') or not rendering.get('last_name'):
        context['provider_npi'] = None  # Do not bind a payer participation statement to an unnamed request provider.
    codes = _api._previsit_codes(inputs) if inputs else []
    office_only = bool(codes) and all(row['code'].isdigit() and
        (99202 <= int(row['code']) <= 99205 or 99212 <= int(row['code']) <= 99215) for row in codes)
    benefits = _api.commercial.normalize_stedi_271(payload, service_type, False,
        coverage["active_coverages"] > 1 or coverage.get("other_active_coverage", False), service_date=service_date,
        procedure_code=codes[0]['code'] if office_only and len(codes) == 1 else None,
        office_visit=office_only,
        office_service_types=_api._office_service_types('', source or {}), **context)
    benefits['benefit_source'] = (payload.get('benefit_source') or 'Stedi') if payload else None
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
    if not payload:
        # Normalization of an empty response is a safe pricing placeholder, not payer evidence.
        benefits['review_reasons'] = []
        benefits['requires_review'] = False
        return benefits
    if comparison['status'] == 'review':
        benefits['requires_review'] = True
        benefits['review_reasons'].append('Confirm the member identity: the payer response differs from eCW.'
            if any(row['field'] == 'member_id' for row in comparison['differences']) else
            'Confirm patient identity: the payer and eCW records lack enough matching identifiers.'
            if comparison.get('missing_fields') else
            'The subscriber relationship could not be verified automatically from eCW and payer evidence.'
            if comparison.get('policy_differences') else
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
                and (not service_date or not library_source.get('effective_from')
                     or library_source['effective_from'] <= service_date)
                and document.get('sha256') == library_source.get('sha256')):
            evidence.update(document_id=document_id, document_label=document['label'],
                            locator=library_source.get('locator'),
                            origin=library_source.get('origin'))
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



def _product_mapping_evidence(profile: dict, network: dict, source: dict, service_date: str) -> dict | None:
    """Retain the administrator record used to alias a payer product."""
    payer_names = source.get('payer_plan_names') or []
    if not payer_names and source.get('payer_plan_bound'):
        payer_names = [source.get('plan')]
    product = _api.insurance_identity.normalized(payer_names[0]) if len(payer_names) == 1 else ''
    if (not source.get('payer_plan_bound') or not product
            or product == _api.insurance_identity.normalized(network.get('plan_matcher'))):
        return None
    row = _api._applicable_product_mapping(network, profile, source, service_date)
    if row and product in {_api.insurance_identity.normalized(name) for name in row.get('products') or []}:
        return {key: row.get(key) for key in ('reference', 'actor_id', 'verified_at',
                                              'valid_from', 'valid_to', 'contract_product',
                                              'rate_plan_matcher', 'network_identifiers')}
    return None


def _requirement_candidates(result: dict) -> list[dict]:
    selected_ids = {row.get('id') for row in result.get('codes') or []}
    suggested_codes = {str(candidate.get('code') or '').upper()
                       for group in (result.get('suggestions') or {}).get('groups') or []
                       for candidate in group.get('codes') or []}
    recommended_ids = set((result.get('service_plan') or {}).get('recommended_ids') or [])
    return [row for row in result.get('available_codes') or []
            if row.get('id') not in selected_ids and
            (row.get('id') in recommended_ids or row.get('code') in suggested_codes)]


def _apply_inventory_tier(result: dict, benefits: dict, inventory: dict, exclusion: dict) -> None:
    """Use a certified clinic inventory as an operational, never payer-confirmed, tier."""
    if benefits.get('network_source') == 'payer_participation_message' and benefits.get('network_status') == 'in_network':
        benefits['network_conflict'] = True
        benefits['network_status'] = 'unknown'
        result['network_evidence'] = {**inventory, 'decision': 'conflicting_network_evidence',
            'reason': 'A current payer participation statement conflicts with NMCT’s certified UHC agreement inventory.'}
        return
    benefits['network_status'] = 'out_of_network'
    benefits['network_source'] = 'clinic_contract_inventory'
    benefits['network_evidence_ids'] = [exclusion['document_id']]
    result['network_evidence'] = {**inventory, 'agreement_exclusion': exclusion,
        'document_id': exclusion['document_id'], 'page': exclusion.get('page'),
        'locator': exclusion.get('locator'),
        'reason': 'NMCT’s certified complete agreement inventory excludes this Regional or Local Medicare Advantage PPO product; out-of-network benefits are used for estimation, without claiming payer confirmation.'}


def _record_confirmed_out_of_network(result: dict, benefits: dict) -> None:
    """Describe only the source that actually established nonparticipation."""
    source = benefits.get('network_source')
    if source == 'clinic_contract_inventory':
        return  # Keep the certified scope and exclusion passage already attached.
    if source not in {'payer_participation_message', 'clinic_nonparticipation_rule'}:
        return
    result['network_evidence'] = {
        'decision': ('out_of_network_confirmed_by_payer' if source == 'payer_participation_message'
                     else 'out_of_network_confirmed_by_scoped_nonparticipation_record'),
        'source': source, 'evidence_ids': benefits.get('network_evidence_ids') or [],
        'reason': ('Product-specific payer evidence identifies this requesting provider as out of network.'
                   if source == 'payer_participation_message' else
                   'A scoped clinic nonparticipation rule applies to this provider and product.')}


def _attach_previsit_pricing(result: dict, practice_id: str, inputs: dict, source: dict,
                             payer_id: str, benefits: dict, *, payload: dict | None = None,
                             review: dict | None = None) -> None:
    # Retain a scoped payer assertion about the requesting provider. A generic
    # in-network benefit schedule alone still cannot establish participation.
    if benefits.get('network_source') != 'payer_participation_message':
        benefits["network_status"] = "unknown"
    result.pop('temporary_rate_reference', None)
    result.pop('agreement_exclusion', None)
    payer_connections = _api.commercial_store.list_connections(practice_id)
    route_connections = [row for row in payer_connections if row.get('stedi_payer_id') == payer_id]
    connections = [row for row in route_connections if row.get('status') == 'staff_guidance_enabled']
    route_payer = route_connections[0] if len(route_connections) == 1 else None
    service_date = str(inputs.get('service_date') or '')
    exclusion = library_network_exclusions.applicable_exclusion(
        practice_id, (route_payer or {}).get('display_name') or result.get('payer') or '',
        [*((source.get('payer_plan_names') or []) if source.get('payer_plan_bound') else []),
         source.get('plan') or ''], service_date)
    inventory = (inventory_certification.applicable(practice_id, route_payer, inputs, source, exclusion)
                 if route_payer else None)
    if exclusion:
        result['agreement_exclusion'] = exclusion
        result['network_evidence'] = {**exclusion,
            'reason': 'UHC Regional or Local Medicare Advantage PPO is excluded from the uploaded agreement; check for a separate applicable agreement or product-specific provider participation evidence.'}
    if _api.network_participation.verified_out_of_network(benefits):
        _record_confirmed_out_of_network(result, benefits)
    if _api.commercial.is_united_healthcare_payer(
            result, {'displayName': route_payer.get('display_name')} if route_payer else None):
        _attach_temporary_uhc_rates(result, practice_id, inputs, source)
    if len(connections) != 1:
        if inventory and not _api.network_participation.verified_out_of_network(benefits):
            _apply_inventory_tier(result, benefits, inventory, exclusion)
        if _api.network_participation.verified_out_of_network(benefits):
            priced_source = {**source, **((result.get('network_evidence') or {}).get('scope') or {})}
            result['price_range'], issues = _api._previsit_range(
                practice_id, inputs, priced_source, payer_id, result['codes'], benefits,
                payload=payload, review=review, payer_route_id=payer_id)
            result['issues'].extend(issues)
            return
        draft = [row for row in payer_connections if row.get('stedi_payer_id') == payer_id
                 and row.get('status') == 'draft']
        result["issues"].append(
            "Clinic pricing: this payer's clinic estimator connection is still a draft."
            if not connections and len(draft) == 1 else
            "Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.")
        result['price_range'] = _api.visit_estimate.price_lines(result['codes'], [], benefits, {'cost_share': 'payer_returned',
            'office_service_types': _api._office_service_types(practice_id, source),
            **_api._benefit_context(inputs, source)}, payload=payload,
            service_date=inputs.get('service_date', ''), reviews=(review or {}).get('lines'),
            alternatives=not (review or {}).get('selected_ids') and bool(result['codes']) and all(row.get('source') in {
                'conditional office E/M alternative', 'conditional NCS alternative'} for row in result['codes']))
        return
    connection = connections[0]
    scope_complete = all((service_date, (inputs.get('rendering_provider') or {}).get('npi'),
                          (source.get('billing_provider') or {}).get('npi'),
                          source.get('location_id'), inputs.get('place_of_service'), source.get('plan')))
    try:
        if benefits.get('network_status') == 'out_of_network':
            raise _api.HTTPException(422, 'Payer confirmed out-of-network participation; in-network contract prices do not apply.')
        _, network, profile = _api._previsit_practice_path(practice_id, {**inputs, "payer_id": connection["id"]}, source)
        evidence = _api._network_contract_evidence(practice_id, network, profile, service_date)
        if exclusion and evidence.get('document_id') == exclusion['document_id']:
            raise _api.HTTPException(409, 'This exact product is excluded from the linked Clinical Library agreement; check a separate applicable agreement.')
        if source.get('pricing_plan_source'):
            evidence['product_source'] = source['pricing_plan_source']
        mapping = _product_mapping_evidence(profile, network, source, service_date)
        if mapping:
            evidence['product_mapping_approval'] = mapping
        if evidence.get('document_id'):
            benefits['network_status'] = 'in_network'
            group_agreement = evidence.get('origin') == 'signed_group_agreement'
            benefits['network_source'] = ('clinic_group_agreement' if group_agreement
                                          else 'clinic_library_contract')
            result['network_evidence'] = {**evidence,
                'decision': 'in_network_contract_supported' if group_agreement else 'confirmed_participating',
                'reason': ('The signed clinic agreement names this network and is linked to the current Library document.'
                           if group_agreement else evidence.get('reason'))}
            if source.get('payer_network_names'):
                evidence['payer_network_name'] = source['payer_network_names'][0]
                evidence['payer_network_source'] = 'Stedi planNetwork'
        elif scope_complete and not exclusion:
            result['network_evidence'] = {**evidence, 'decision': 'lacking_applicable_network_evidence',
                'reason': evidence.get('reason') or
                'The Clinical Library has no contract linked to this exact payer product and provider.'}
    except _api.HTTPException as exc:
        if str(exc.detail) == 'The clinic fee schedule is not matched to the exact payer product.':
            result['issues'].append(str(exc.detail))
        if scope_complete and str(exc.detail) == 'A sourced nonparticipation rule applies to this provider, product, location and date.':
            benefits['network_status'] = 'out_of_network'
            benefits['network_source'] = 'clinic_nonparticipation_rule'
        if not exclusion:
            result['network_evidence'] = {'decision': 'out_of_network_confirmed_by_scoped_nonparticipation_record'
                if benefits.get('network_source') == 'clinic_nonparticipation_rule'
                else 'lacking_applicable_network_evidence',
                'reason_code': 'product_link_missing' if str(exc.detail) == 'The clinic fee schedule is not matched to the exact payer product.'
                else 'payer_network_unmatched' if str(exc.detail) == 'The payer-reported member network does not match the clinic contract.'
                else 'contract_scope_unresolved', 'reason': str(exc.detail)}
    if ((result.get('network_evidence') or {}).get('decision') == 'lacking_applicable_network_evidence'
            and not exclusion and not _api.network_participation.verified_out_of_network(benefits)):
        supported = group_contract_support.resolve(practice_id, connection, inputs, source, result, benefits)
        if supported:
            result['network_evidence'] = supported
            benefits['network_status'] = 'in_network'
            benefits['network_source'] = 'clinic_group_agreement'
    if (inventory and benefits.get('network_source') != 'clinic_library_contract'
            and not _api.network_participation.verified_out_of_network(benefits)):
        _apply_inventory_tier(result, benefits, inventory, exclusion)
    if _api.network_participation.verified_out_of_network(benefits):
        _record_confirmed_out_of_network(result, benefits)
    priced_source = {**source, **((result.get('network_evidence') or {}).get('scope') or {})}
    result["price_range"], issues = _api._previsit_range(practice_id, inputs, priced_source, connection["id"], result["codes"], benefits,
                                                  payload=payload, review=review, payer_route_id=payer_id)
    result["issues"].extend(issues)
