"""Commercial practice workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

def _network(practice_id: str, network_id: str) -> dict:
    value = next((row for row in _api.commercial_store.list_networks(practice_id) if row["id"] == network_id), None)
    if not value:
        raise _api.HTTPException(422, "The selected provider network is not configured for this practice.")
    return value


def _public(case: dict) -> dict:
    result = {key: case.get(key) for key in ("id", "created_at", "revision", "source_mode", "commercial", "estimate", "review_tasks") if key in case}
    # Never expose the staffing intake's patient/card fields or raw payer evidence.
    return result


def _load(case_id: str, session: dict) -> dict:
    case = _api.store.get_case(case_id, session["patient_id"], session["share_manifest_id"], **_api._scope(session))
    if not case or case.get("source_mode") != "commercial":
        raise _api.HTTPException(404, "Commercial estimate case not found.")
    return case


def _save(case: dict, session: dict, event: str) -> dict:
    snapshot = None
    if case.get('source_mode') == 'commercial_previsit' and case.get('previsit_context'):
        snapshot = {'result': _api._previsit_result({**case, 'revision': case.get('revision', 0) + 1}),
                    'patient': session['intake']['patient']}
        if 'insurance_diagnosis' in snapshot['result']:
            case['insurance_diagnosis'] = snapshot['result']['insurance_diagnosis']
    try:
        saved = _api.store.save_case(case, session["patient_id"], session["share_manifest_id"], event, snapshot=snapshot, **_api._scope(session))
    except _api.store.StaffCaseConflict as exc:
        raise _api.HTTPException(409, str(exc)) from exc
    if not saved:
        raise _api.HTTPException(404, "Commercial estimate case not found.")
    if snapshot:
        guidance = snapshot['result'].get('collection_guidance') or {}
        _api.collection_event('collection_saved', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=saved['id'], staff_id=(session.get('staff') or {}).get('id'), stage='persistence',
                         reason_code=event.replace('-', '_'), status=guidance.get('status'),
                         basis=guidance.get('basis'), payment_mode=snapshot['result'].get('payment_mode'),
                         amount_cents=guidance.get('amount_cents'),
                         revision=saved.get('revision'))
    if snapshot:
        source = case['previsit_context'].get('source') or {}
        _api.staff_store.save_ecw_account_number(session['patient_id'], session['practice_id'], source.get('_patient_id'),
                                            (snapshot['result'].get('patient_details') or {}).get('ecw_account_number'))
    return saved


def _config(practice_id: str) -> dict:
    return {"connections": _api.commercial_store.list_connections(practice_id), "networks": _api.commercial_store.list_networks(practice_id),
            "profiles": _api.commercial_store.list_profiles(practice_id), "rate_versions": _api.commercial_store.list_rate_versions(practice_id),
            "stedi_execution_mode": _api.os.getenv("STEDI_EXECUTION_MODE", "off"),
            "live_eligibility_enabled": _api.os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() == "true"}


def _ecw_practice(practice_id: str, payer_connection_id: str, provider_network_id: str) -> dict[str, _api.Any]:
    """Build only the eCW mappings needed by this commercial case.

    It intentionally does not use AVAILITY_PRACTICE_CONFIG: both integrations
    remain independently configurable.
    """
    payer = _api.commercial_store.connection(practice_id, payer_connection_id)
    network = _api._network(practice_id, provider_network_id)
    if not payer:
        raise _api.HTTPException(422, "The commercial payer configuration no longer exists.")
    aliases = {payer["display_name"]: payer["id"]}
    aliases.update({value: payer["id"] for value in payer.get("aliases", []) if isinstance(value, str) and value.strip()})
    return {
        "timezone": _api.os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"),
        "payer_aliases": aliases,
        "providers": {network["billing_npi"]: {}, network["rendering_npi"]: {}},
        "locations": {network["location_id"]: {"place_of_service": network["place_of_service"],
                                               "billing_provider_npi": network["billing_npi"]}},
    }


def _ecw_practice_config(practice_id: str) -> dict[str, _api.Any]:
    """Expose all approved practice mappings to the eCW reader, not to clinic staff."""
    alias_candidates: dict[str, set[str]] = {}
    for payer in _api.commercial_store.list_connections(practice_id):
        if payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
            continue
        for value in [payer["display_name"], *payer.get("aliases", [])]:
            if isinstance(value, str) and value.strip():
                alias_candidates.setdefault(_api.re.sub(r"[^a-z0-9]", "", value.casefold()), set()).add(payer["id"])
    providers: dict[str, dict] = {}
    location_candidates: dict[str, list[dict]] = {}
    for network in _api.commercial_store.list_networks(practice_id):
        if network["status"] != "verified":
            continue
        providers[network["billing_npi"]] = {}
        providers[network["rendering_npi"]] = {}
        location_candidates.setdefault(network["location_id"], []).append({
            "place_of_service": network["place_of_service"], "billing_provider_npi": network["billing_npi"]})
    # Conflicting payer aliases or location mappings must not depend on row order.
    aliases = {name: next(iter(ids)) for name, ids in alias_candidates.items() if len(ids) == 1}
    locations = {location: {key: rows[0][key] for key in rows[0]
                            if all(row[key] == rows[0][key] for row in rows)}
                 for location, rows in location_candidates.items()}
    return {"timezone": _api.os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"), "payer_aliases": aliases, "providers": providers, "locations": locations}


def _practice_path(practice_id: str, inputs: dict, source: dict) -> tuple[dict, dict, dict]:
    payer = _api.commercial_store.connection(practice_id, str(inputs.get("payer_id") or ""))
    if not payer or payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
        raise _api.HTTPException(409, "This practice has no approved Stedi payer mapping for this appointment's eCW coverage.")
    service_date = str(inputs.get("service_date") or "")
    rendering_npi = str((inputs.get("rendering_provider") or {}).get("npi") or "")
    billing_npi = str((source.get("billing_provider") or {}).get("npi") or "")
    plan = _api.re.sub(r"[^a-z0-9]", "", str(source.get("plan") or "").casefold())
    networks = [row for row in _api.commercial_store.list_networks(practice_id)
                if row["payer_connection_id"] == payer["id"]
                and row["billing_npi"] == billing_npi and row["rendering_npi"] == rendering_npi
                and row["location_id"] == str(source.get("location_id") or "")
                and row["place_of_service"] == str(inputs.get("place_of_service") or "")
                and (not row.get("plan_matcher") or (plan and _api.re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
                and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)]
    if any(row['status'] == 'nonparticipating' for row in networks):
        raise _api.HTTPException(409, 'A sourced nonparticipation rule applies to this provider, product, location and date.')
    networks = [row for row in networks if row['status'] == 'verified']
    if len(networks) > 1:
        raise _api.HTTPException(409, "Multiple provider-network records match this payer, provider and visit.")
    if not networks:
        raise _api.HTTPException(409, "This practice has no single verified provider-network record for this eCW provider and location.")
    profiles = [row for row in _api.commercial_store.list_profiles(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["provider_network_id"] == networks[0]["id"]
                and row["status"] == "staff_guidance_enabled" and row.get("plan_matcher", "") == networks[0].get("plan_matcher", "")]
    if len(profiles) != 1:
        raise _api.HTTPException(409, "This practice has no single enabled estimate profile for this provider and payer.")
    product_names = {_api.re.sub(r'[^a-z0-9]', '', name.casefold()) for name in source.get('payer_plan_names', [])}
    payer_networks = {_api.re.sub(r'[^a-z0-9]', '', str(name).casefold()) for name in source.get('payer_network_names', []) if name}
    expected_network = _api.re.sub(r'[^a-z0-9]', '', str(networks[0].get('network_name') or '').casefold())
    if payer_networks and payer_networks != {expected_network}:
        raise _api.HTTPException(409, 'The payer-reported member network does not match the clinic contract.')
    if product_names:
        aliases = {_api.re.sub(r'[^a-z0-9]', '', str(name).casefold()) for name in
                   [networks[0].get('plan_matcher', ''), *(profiles[0].get('rules', {}).get('plan_product_names') or [])]} - {'', 'ci', 'commercial', 'ppo', 'hmo'}
        for mapping in profiles[0].get('rules', {}).get('product_mappings', []):
            if (mapping.get('reference') and mapping.get('actor_id')
                    and mapping.get('valid_from', '') <= service_date <= mapping.get('valid_to', '')
                    and mapping.get('network_id') == networks[0]['id']
                    and mapping.get('network_reference') == networks[0].get('evidence_reference')
                    and mapping.get('plan_scope') == source.get('payer_plan_scope')):
                aliases.update(_api.insurance_identity.normalized(name) for name in mapping.get('products', []))
        generic_names = all(_api.re.fullmatch(r'(?:preferredprovider(?:option|organization)(?:plus)?|healthmaintenanceorganization)(?:medical)?', name)
                            for name in product_names)
        if not product_names <= aliases and not (payer_networks == {expected_network} and generic_names):
            raise _api.HTTPException(409, 'The clinic fee schedule is not matched to the exact payer product.')
    return payer, networks[0], profiles[0]


def _previsit_practice_path(practice_id: str, inputs: dict, source: dict) -> tuple[dict, dict, dict]:
    """A generic PPO label cannot negate the scoped clinic contract."""
    names = [_api.insurance_identity.normalized(name) for name in source.get('payer_plan_names', [])]
    generic = names and all(_api.re.fullmatch(
        r'(?:preferredprovider(?:option|organization)(?:plus)?|healthmaintenanceorganization)(?:medical)?',
        name) for name in names)
    return _api._practice_path(practice_id, inputs, {**source, 'payer_plan_names': [] if generic else source.get('payer_plan_names', [])})


def _appointment_readiness(practice_id: str, session: dict, inputs: dict, source: dict) -> dict:
    """Report real eCW and clinic prerequisites before creating a case or calling Stedi."""
    blockers: list[str] = []
    payer = network = profile = None
    connections = _api.commercial_store.list_connections(practice_id)
    all_networks = _api.commercial_store.list_networks(practice_id)
    all_profiles = _api.commercial_store.list_profiles(practice_id)
    rate_versions = _api.commercial_store.list_rate_versions(practice_id)
    payer = next((row for row in connections if row["id"] == inputs.get("payer_id")
                  and row["category"] == "commercial_medical" and row["status"] == "staff_guidance_enabled"), None)
    if not payer:
        payer_name = str(source.get("payer") or "payer not returned by eClinicalWorks")
        blockers.append(f"Clinic setup: approve the Stedi payer ID for eClinicalWorks coverage '{payer_name}' and map its exact eCW name or identifier.")
    if not any(row["status"] == "verified" for row in all_networks):
        blockers.append("Clinic setup: no verified provider-network participation record is available for this practice.")
    if not any(row["status"] == "staff_guidance_enabled" for row in all_profiles):
        blockers.append("Clinic setup: no enabled estimate profile is available for this practice.")

    service_date = str(inputs.get("service_date") or "")
    rendering_npi = str((inputs.get("rendering_provider") or {}).get("npi") or "")
    billing_npi = str((source.get("billing_provider") or {}).get("npi") or "")
    location_id = str(source.get("location_id") or "")
    pos = str(inputs.get("place_of_service") or "")
    if not rendering_npi and not (source.get("rendering_provider") or {}).get("npi"):
        blockers.append("eClinicalWorks did not return a rendering provider NPI for this appointment.")
    if not location_id:
        blockers.append("eClinicalWorks did not return the appointment location needed to verify the provider network.")
    if not pos:
        blockers.append("Clinic setup: map the eClinicalWorks appointment location to a place-of-service code.")

    if payer:
        plan = _api.re.sub(r"[^a-z0-9]", "", str(source.get("plan") or "").casefold())
        networks = [row for row in all_networks
                    if row["payer_connection_id"] == payer["id"] and row["status"] == "verified"
                    and row["billing_npi"] == billing_npi and row["rendering_npi"] == rendering_npi
                    and row["location_id"] == location_id
                    and row["place_of_service"] == pos
                    and (not row.get("plan_matcher") or (plan and _api.re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
                    and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)]
        if len(networks) == 1:
            network = networks[0]
        else:
            blockers.append("Clinic setup: verify exactly one provider-network record for this payer, plan, billing and rendering NPIs, location, place of service, and visit date.")
    if network:
        profiles = [row for row in all_profiles
                    if row["payer_connection_id"] == payer["id"] and row["provider_network_id"] == network["id"]
                    and row["status"] == "staff_guidance_enabled" and row.get("plan_matcher", "") == network.get("plan_matcher", "")]
        if len(profiles) == 1:
            profile = profiles[0]
        else:
            blockers.append("Clinic setup: enable exactly one estimate profile for this payer and provider network.")

    planned = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled"]
    if not planned and not _api._previsit_codes(inputs):
        blockers.append("This appointment has neither a scheduled CPT/HCPCS-coded service nor a supported anticipated office-service plan.")
    elif any(row.get("modifiers") for row in planned):
        blockers.append("A scheduled eClinicalWorks service has a modifier; a matching contracted rate is required.")
    elif any(not row.get("units") for row in planned):
        blockers.append("eClinicalWorks did not return units for every scheduled coded service.")
    payer_rates_active = bool(payer and any(
        row.get("payer_connection_id") == payer["id"] and row["status"] == "active"
        and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)
        for row in rate_versions))
    if payer and not payer_rates_active:
        blockers.append("Clinic setup: import and activate this payer's contracted CPT rates effective on this visit date.")
    if profile and planned and payer_rates_active:
        _, rate_tasks = _api._ecw_coded_lines(practice_id, profile, network, service_date, inputs)
        blockers.extend(rate_tasks)

    coverage = _api._coverage_snapshot(inputs, source)
    if coverage["active_coverages"] != 1 or not coverage["coverage_id"]:
        blockers.append("eClinicalWorks did not identify exactly one active primary coverage for this visit.")
    if not coverage["member_id"] or not coverage["relationship"] or not all(coverage.get(key) for key in
            ("subscriber_first", "subscriber_last")):
        blockers.append("eClinicalWorks coverage is missing the member ID, relationship, or policyholder identity needed for Stedi.")
    if payer and network and profile:
        data = {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "service_date": service_date}
        blockers.extend(_api._ecw_conflicts({"commercial": data}, session, inputs, source))
        if not payer.get("rules", {}).get("service_type_code"):
            blockers.append("Clinic setup: configure the Stedi service-type code for this payer and visit category.")
        if payer.get("rules", {}).get("future_dos_current_month") and service_date > _api.date.today().isoformat() and service_date[:7] != _api.date.today().isoformat()[:7]:
            blockers.append("This payer does not accept an eligibility check for a future visit outside the current month.")
    if _api.os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or _api.os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        blockers.append("Clinic setup: live Stedi eligibility is not enabled for this practice deployment.")
    if not _api.stedi_financial.credentials(session["practice_id"]) or not _api.object_store.enabled():
        blockers.append("Clinic setup: Stedi credentials or protected eligibility evidence storage are unavailable.")
    return {"ready": not blockers, "blockers": list(dict.fromkeys(blockers)),
            "ecw": {"payer": source.get("payer"), "plan": source.get("plan"),
                    "scheduled_codes": [{"code": row.get("code"), "units": row.get("units")} for row in planned],
                    "visit_type": inputs.get("visit_type"), "reason": inputs.get("reason")}}
