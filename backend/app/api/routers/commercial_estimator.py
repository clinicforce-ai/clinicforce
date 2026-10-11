"""Staff-only Stedi commercial eligibility and estimate workflow.

This route is deliberately separate from the existing Availity workflow.
"""
from __future__ import annotations

from pathlib import Path

import hashlib
import ipaddress
import asyncio
import json
import os
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi.responses import JSONResponse
from fastapi import BackgroundTasks, APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import StrictInt, BaseModel, ConfigDict, Field, ValidationError

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store, staff_store
from app.insurance import identity as insurance_identity
from app.insurance import commercial, commercial_store, cpt_rules, cpt_suggester, ecw_backend, golden, stedi_discovery, stedi_payers, store, visit_estimate, practice_prices
from app.insurance import stedi_financial, collection_guidance, ecw_benefits, ecw_billing, library_pricing
from app.insurance import provider_specialty
from app.logs.collection_audit import collection_event
from app.logs.logger import log_info, log_warning


router = APIRouter(prefix="/api/doctor/insurance/commercial", tags=["doctor-commercial-estimator"])
STEDI_URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=160)
    stedi_payer_id: str = Field(min_length=1, max_length=80)
    category: str = "commercial_medical"
    status: Literal["draft", "enrollment", "fixture_validated", "shadow", "staff_guidance_enabled", "suspended"] = "draft"
    aliases: list[str] = Field(default_factory=list, max_length=100)
    rules: dict[str, Any] = Field(default_factory=dict)


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    billing_npi: str = Field(pattern=r"^\d{10}$")
    rendering_npi: str = Field(pattern=r"^\d{10}$")
    organization_name: str = Field(min_length=1, max_length=160)
    tin: str = Field(min_length=4, max_length=20)
    location_id: str = Field(min_length=1, max_length=100)
    place_of_service: str = Field(min_length=1, max_length=20)
    network_name: str = Field(min_length=1, max_length=120)
    plan_matcher: str = Field(default="", max_length=200)
    effective_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    status: Literal["draft", "verified", "suspended"] = "draft"
    evidence_reference: str = Field(min_length=3, max_length=500)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    service_category: Literal["office", "emg_ncs"]
    plan_matcher: str = Field(default="", max_length=200)
    status: Literal["review_only", "staff_guidance_enabled", "suspended"] = "review_only"
    rules: dict[str, Any]


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payer_connection_id: str
    provider_network_id: str
    profile_id: str
    service_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class EcwStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,200}$")


class PrevisitInput(EcwStartInput):
    refresh_benefits: bool = False


class ServiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^(?:\d{5}|[A-Za-z]\d{4}|\d{4}[A-Za-z])$")
    units: int = Field(ge=1, le=10000, strict=True)


class ServicesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[ServiceLine] = Field(min_length=1, max_length=20)


class VisitVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    line_id: str = Field(min_length=1, max_length=200)
    kind: Literal["referral", "authorization", "benefits"]
    status: Literal["valid", "not_required", "pending", "denied"] = "valid"
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)
    valid_from: date
    valid_to: date
    units: int = Field(ge=1, le=10000, strict=True)
    selected_ids: list[str] = Field(default_factory=list, max_length=2)
    cost_share: Literal["copay", "coinsurance", "cumulative"] = "copay"
    deductible_applies: bool = True


class IdentityConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    binding: str = Field(pattern=r'^[a-f0-9]{64}$')
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)


class ProductMapping(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(min_length=10, max_length=2000)
    valid_from: date
    valid_to: date


class VisitReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    selected_ids: list[str] | None = Field(default=None, max_length=20)
    payment_mode: Literal["insurance", "cash"] | None = None
    emg_required: bool | None = None
    insurance_check_from: date | None = None
    cash_selection: str | None = Field(default=None, max_length=40, description="Legacy cash package selection. Only an empty value is accepted to clear it; prices use the active per-service schedule.")
    confirmed_units: dict[str, StrictInt] | None = Field(default=None, max_length=20)
    verification: VisitVerification | None = None
    payments_received_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    payment_reference: str | None = Field(default=None, min_length=3, max_length=500)
    identity_confirmation: IdentityConfirmation | None = None
    product_mapping: ProductMapping | None = None


class CoverageCandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    candidate_id: str = Field(pattern=r"^[a-f0-9]{20}$")


class PayerRouteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context_token: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    route_id: str = Field(min_length=1, max_length=100, description='A supported Stedi route returned for this saved policy; never an arbitrary payer ID.')


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["staff_export", "ecw_document", "fixture", "stedi"]
    response_reference: str = Field(min_length=3, max_length=500)
    requested_dos: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    payer_checked_at: str
    expires_at: str | None = None
    benefits: dict[str, Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _session(request: Request) -> dict:
    session = await insurance_session(request)
    if not session.get("practice_id"):
        raise HTTPException(403, "Open the commercial estimator from the staff workspace.")
    return session


def _admin(session: dict) -> None:
    if session["staff"].get("role") != "admin":
        raise HTTPException(403, "Only a clinic administrator can change commercial-estimator configuration.")


def _network(practice_id: str, network_id: str) -> dict:
    value = next((row for row in commercial_store.list_networks(practice_id) if row["id"] == network_id), None)
    if not value:
        raise HTTPException(422, "The selected provider network is not configured for this practice.")
    return value


def _public(case: dict) -> dict:
    result = {key: case.get(key) for key in ("id", "created_at", "revision", "source_mode", "commercial", "estimate", "review_tasks") if key in case}
    # Never expose the staffing intake's patient/card fields or raw payer evidence.
    return result


def _load(case_id: str, session: dict) -> dict:
    case = store.get_case(case_id, session["patient_id"], session["share_manifest_id"], **_scope(session))
    if not case or case.get("source_mode") != "commercial":
        raise HTTPException(404, "Commercial estimate case not found.")
    return case


def _save(case: dict, session: dict, event: str) -> dict:
    snapshot = None
    if case.get('source_mode') == 'commercial_previsit' and case.get('previsit_context'):
        snapshot = {'result': _previsit_result({**case, 'revision': case.get('revision', 0) + 1}),
                    'patient': session['intake']['patient']}
        if 'insurance_diagnosis' in snapshot['result']:
            case['insurance_diagnosis'] = snapshot['result']['insurance_diagnosis']
    try:
        saved = store.save_case(case, session["patient_id"], session["share_manifest_id"], event, snapshot=snapshot, **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not saved:
        raise HTTPException(404, "Commercial estimate case not found.")
    if snapshot:
        guidance = snapshot['result'].get('collection_guidance') or {}
        collection_event('collection_saved', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=saved['id'], staff_id=(session.get('staff') or {}).get('id'), stage='persistence',
                         reason_code=event.replace('-', '_'), status=guidance.get('status'),
                         basis=guidance.get('basis'), payment_mode=snapshot['result'].get('payment_mode'),
                         amount_cents=guidance.get('amount_cents'),
                         revision=saved.get('revision'))
    if snapshot:
        source = case['previsit_context'].get('source') or {}
        staff_store.save_ecw_account_number(session['patient_id'], session['practice_id'], source.get('_patient_id'),
                                            (snapshot['result'].get('patient_details') or {}).get('ecw_account_number'))
    return saved


def _config(practice_id: str) -> dict:
    return {"connections": commercial_store.list_connections(practice_id), "networks": commercial_store.list_networks(practice_id),
            "profiles": commercial_store.list_profiles(practice_id), "rate_versions": commercial_store.list_rate_versions(practice_id),
            "stedi_execution_mode": os.getenv("STEDI_EXECUTION_MODE", "off"),
            "live_eligibility_enabled": os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() == "true"}


def _ecw_practice(practice_id: str, payer_connection_id: str, provider_network_id: str) -> dict[str, Any]:
    """Build only the eCW mappings needed by this commercial case.

    It intentionally does not use AVAILITY_PRACTICE_CONFIG: both integrations
    remain independently configurable.
    """
    payer = commercial_store.connection(practice_id, payer_connection_id)
    network = _network(practice_id, provider_network_id)
    if not payer:
        raise HTTPException(422, "The commercial payer configuration no longer exists.")
    aliases = {payer["display_name"]: payer["id"]}
    aliases.update({value: payer["id"] for value in payer.get("aliases", []) if isinstance(value, str) and value.strip()})
    return {
        "timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"),
        "payer_aliases": aliases,
        "providers": {network["billing_npi"]: {}, network["rendering_npi"]: {}},
        "locations": {network["location_id"]: {"place_of_service": network["place_of_service"],
                                               "billing_provider_npi": network["billing_npi"]}},
    }


def _ecw_practice_config(practice_id: str) -> dict[str, Any]:
    """Expose all approved practice mappings to the eCW reader, not to clinic staff."""
    alias_candidates: dict[str, set[str]] = {}
    for payer in commercial_store.list_connections(practice_id):
        if payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
            continue
        for value in [payer["display_name"], *payer.get("aliases", [])]:
            if isinstance(value, str) and value.strip():
                alias_candidates.setdefault(re.sub(r"[^a-z0-9]", "", value.casefold()), set()).add(payer["id"])
    providers: dict[str, dict] = {}
    location_candidates: dict[str, list[dict]] = {}
    for network in commercial_store.list_networks(practice_id):
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
    return {"timezone": os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"), "payer_aliases": aliases, "providers": providers, "locations": locations}


def _practice_path(practice_id: str, inputs: dict, source: dict) -> tuple[dict, dict, dict]:
    payer = commercial_store.connection(practice_id, str(inputs.get("payer_id") or ""))
    if not payer or payer["category"] != "commercial_medical" or payer["status"] != "staff_guidance_enabled":
        raise HTTPException(409, "This practice has no approved Stedi payer mapping for this appointment's eCW coverage.")
    service_date = str(inputs.get("service_date") or "")
    rendering_npi = str((inputs.get("rendering_provider") or {}).get("npi") or "")
    billing_npi = str((source.get("billing_provider") or {}).get("npi") or "")
    plan = re.sub(r"[^a-z0-9]", "", str(source.get("plan") or "").casefold())
    networks = [row for row in commercial_store.list_networks(practice_id)
                if row["payer_connection_id"] == payer["id"]
                and row["billing_npi"] == billing_npi and row["rendering_npi"] == rendering_npi
                and row["location_id"] == str(source.get("location_id") or "")
                and row["place_of_service"] == str(inputs.get("place_of_service") or "")
                and (not row.get("plan_matcher") or (plan and re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
                and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)]
    if any(row['status'] == 'nonparticipating' for row in networks):
        raise HTTPException(409, 'A sourced nonparticipation rule applies to this provider, product, location and date.')
    networks = [row for row in networks if row['status'] == 'verified']
    if len(networks) != 1:
        raise HTTPException(409, "This practice has no single verified provider-network record for this eCW provider and location.")
    profiles = [row for row in commercial_store.list_profiles(practice_id)
                if row["payer_connection_id"] == payer["id"] and row["provider_network_id"] == networks[0]["id"]
                and row["status"] == "staff_guidance_enabled" and row.get("plan_matcher", "") == networks[0].get("plan_matcher", "")]
    if len(profiles) != 1:
        raise HTTPException(409, "This practice has no single enabled estimate profile for this provider and payer.")
    product_names = {re.sub(r'[^a-z0-9]', '', name.casefold()) for name in source.get('payer_plan_names', [])}
    if product_names:
        aliases = {re.sub(r'[^a-z0-9]', '', str(name).casefold()) for name in
                   [networks[0].get('plan_matcher', ''), *(profiles[0].get('rules', {}).get('plan_product_names') or [])]} - {'', 'ci', 'commercial', 'ppo', 'hmo'}
        for mapping in profiles[0].get('rules', {}).get('product_mappings', []):
            if (mapping.get('reference') and mapping.get('actor_id')
                    and mapping.get('valid_from', '') <= service_date <= mapping.get('valid_to', '')
                    and mapping.get('network_id') == networks[0]['id']
                    and mapping.get('network_reference') == networks[0].get('evidence_reference')
                    and mapping.get('plan_scope') == source.get('payer_plan_scope')):
                aliases.update(insurance_identity.normalized(name) for name in mapping.get('products', []))
        if not product_names <= aliases:
            raise HTTPException(409, 'The clinic fee schedule is not matched to the exact payer product.')
    return payer, networks[0], profiles[0]


def _previsit_practice_path(practice_id: str, inputs: dict, source: dict) -> tuple[dict, dict, dict]:
    """Only price the payer product verified for this visit."""
    return _practice_path(practice_id, inputs, source)


def _appointment_readiness(practice_id: str, session: dict, inputs: dict, source: dict) -> dict:
    """Report real eCW and clinic prerequisites before creating a case or calling Stedi."""
    blockers: list[str] = []
    payer = network = profile = None
    connections = commercial_store.list_connections(practice_id)
    all_networks = commercial_store.list_networks(practice_id)
    all_profiles = commercial_store.list_profiles(practice_id)
    rate_versions = commercial_store.list_rate_versions(practice_id)
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
        plan = re.sub(r"[^a-z0-9]", "", str(source.get("plan") or "").casefold())
        networks = [row for row in all_networks
                    if row["payer_connection_id"] == payer["id"] and row["status"] == "verified"
                    and row["billing_npi"] == billing_npi and row["rendering_npi"] == rendering_npi
                    and row["location_id"] == location_id
                    and row["place_of_service"] == pos
                    and (not row.get("plan_matcher") or (plan and re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
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
    if not planned and not _previsit_codes(inputs):
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
        _, rate_tasks = _ecw_coded_lines(practice_id, profile, network, service_date, inputs)
        blockers.extend(rate_tasks)

    coverage = _coverage_snapshot(inputs, source)
    if coverage["active_coverages"] != 1 or not coverage["coverage_id"]:
        blockers.append("eClinicalWorks did not identify exactly one active primary coverage for this visit.")
    if not coverage["member_id"] or not coverage["relationship"] or not all(coverage.get(key) for key in
            ("subscriber_first", "subscriber_last")):
        blockers.append("eClinicalWorks coverage is missing the member ID, relationship, or policyholder identity needed for Stedi.")
    if payer and network and profile:
        data = {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "service_date": service_date}
        blockers.extend(_ecw_conflicts({"commercial": data}, session, inputs, source))
        if not payer.get("rules", {}).get("service_type_code"):
            blockers.append("Clinic setup: configure the Stedi service-type code for this payer and visit category.")
        if payer.get("rules", {}).get("future_dos_current_month") and service_date > date.today().isoformat() and service_date[:7] != date.today().isoformat()[:7]:
            blockers.append("This payer does not accept an eligibility check for a future visit outside the current month.")
    if os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        blockers.append("Clinic setup: live Stedi eligibility is not enabled for this practice deployment.")
    if not stedi_financial.credentials(session["practice_id"]) or not object_store.enabled():
        blockers.append("Clinic setup: Stedi credentials or protected eligibility evidence storage are unavailable.")
    return {"ready": not blockers, "blockers": list(dict.fromkeys(blockers)),
            "ecw": {"payer": source.get("payer"), "plan": source.get("plan"),
                    "scheduled_codes": [{"code": row.get("code"), "units": row.get("units")} for row in planned],
                    "visit_type": inputs.get("visit_type"), "reason": inputs.get("reason")}}


def _ecw_case_data(inputs: dict, source: dict, conflicts: list[str]) -> dict[str, Any]:
    return {"appointment_id": source.get("appointment_id"), "appointment_date": source.get("appointment_date"),
            "retrieved_at": source.get("retrieved_at"), "appointment_status": source.get("appointment_status"),
            "suggested_lines": [{"code": row.get("code"), "units": row.get("units"), "description": row.get("description")}
                                for row in inputs.get("lines", []) if row.get("code")],
            "reason": inputs.get("reason"), "conflicts": conflicts}


def _past_visit_with_saved_estimate(visit: dict[str, Any], case: dict | None) -> dict[str, Any]:
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
    return "commercial:" + hashlib.sha256(appointment_id.encode()).hexdigest()


def _previsit_service_types(inputs: dict) -> list[str]:
    """Ask for the booked categories, including explicitly combined care."""
    scheduled = [row['code'] for row in inputs.get('lines', []) if row.get('service_status') == 'scheduled' and row.get('code')]
    categories = [visit_estimate.service_type(code, {}) for code in scheduled]
    if not scheduled:
        reason = str(inputs.get('reason') or '')
        if visit_estimate.is_emg_visit(inputs):
            categories.append('73')
        if visit_estimate.is_spinal_tap(inputs, clinic_abbreviation=inputs.get('spinal_tap_abbreviation') is True):
            categories.append('2')
        if re.search(r'\bskin\s+(?:punch\s+)?(?:bx|biops\w*)\b|\bpunch\s+biops\w*\b', reason, re.I):
            categories.append('2')
        if not categories or re.search(r'\b(?:f/u|fu|follow[ -]?up)\b', reason, re.I):
            categories.append('98')
    # General medical benefits carry deductible and out-of-pocket accumulators
    # that a service-specific response may omit. Keep the booked service first.
    return list(dict.fromkeys([*(code for code in categories if code), '30']))


def _eligibility_timing(service_date: str, check_from_date: str | None = None) -> dict | None:
    service_day = date.fromisoformat(service_date)
    today = datetime.now(ZoneInfo(os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date()
    check_from = max(service_day - timedelta(days=7), date.fromisoformat(check_from_date) if check_from_date else date.min)
    if today >= check_from:
        return None
    return {'status': 'too_early', 'appointment_date': service_date, 'check_from': check_from.isoformat(),
        'message': f"Appointment found on {service_day.strftime('%B %d, %Y')}. Insurance checking opens {check_from.strftime('%B %d, %Y')}. No Stedi request is made before then."}


def _previsit_codes(inputs: dict) -> list[dict[str, Any]]:
    """Office E/M alternatives are coding standards, not a selected service."""
    scheduled = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled" and row.get("code")]
    if scheduled:
        return [{"id": row.get("id") or f"{row['code']}:{index}", "code": row["code"], "source": "eCW scheduled service",
                 "needs_confirmation": row.get("needs_confirmation", False), "description": row.get("description"), "modifiers": row.get("modifiers") or [], "units": row.get("units")}
                for index, row in enumerate(scheduled)]
    if visit_estimate.is_emg_visit(inputs):
        return []  # An EMG appointment is not an office E/M just because the patient is new.
    # The appointment's explicit new/established label supports a code family;
    # it cannot establish the eventual MDM/time level or that an office visit occurs.
    family = {"new": range(99202, 99206), "established": range(99212, 99216)}.get(inputs.get("visit_type"))
    return [{"id": f"{code}:{index}", "code": str(code), "source": "conditional office E/M alternative", "units": 1}
            for index, code in enumerate(family)] if family else []


def _office_service_types(practice_id: str, source: dict) -> list[str]:
    specialist = provider_specialty.office_role((source.get('rendering_provider') or {}).get('roles', [])) == 'specialist'
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
    office_types = _office_service_types(practice_id, source)
    rates, rules, issues = [], {'office_service_types': office_types, **_benefit_context(inputs, source)}, []
    try:
        _, network, profile = _previsit_practice_path(practice_id, {**inputs, "payer_id": payer_id}, source)
        if not network.get("plan_matcher") or not source.get("plan"):
            raise commercial.CommercialError("Clinic pricing: verify the eCW plan against the contracted rate schedule.")
        if not inputs.get("place_of_service"):
            raise commercial.CommercialError("Map the appointment location to its place of service.")
        rates = commercial_store.matching_rates(practice_id, profile, network, inputs["service_date"], selected)
        rules = {**rules, **profile['rules']}
    except (HTTPException, commercial.CommercialError) as exc:
        issues.append(str(exc.detail) if isinstance(exc, HTTPException) else str(exc))
    priced = visit_estimate.price_lines(selected, rates, benefits, rules, payload=payload,
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
    context = _benefit_context(inputs, source or {}) if inputs else {}
    codes = _previsit_codes(inputs) if inputs else []
    office_only = bool(codes) and all(row['code'].isdigit() and
        (99202 <= int(row['code']) <= 99205 or 99212 <= int(row['code']) <= 99215) for row in codes)
    benefits = commercial.normalize_stedi_271(payload, service_type, False,
        coverage["active_coverages"] > 1 or coverage.get("other_active_coverage", False), service_date=service_date,
        procedure_code=codes[0]['code'] if office_only else None,
        office_service_types=_office_service_types('', source or {}), **context)
    benefits['benefit_source'] = payload.get('benefit_source') or 'Stedi'
    if (inputs and source and not coverage.get('other_active_coverage')
            and stedi_discovery.corroborates_patient_policy(payload, inputs, source)
            and stedi_discovery.automatic_match(payload, inputs, source)):
        recorded = insurance_identity.compare(payload, coverage)
        if recorded.get('policy_differences'):
            coverage = _discovered_coverage(inputs, source, payload)
            benefits['policy_match'] = {'source': 'payer_response', 'status': 'verified',
                'recorded_differences': recorded['policy_differences']}
            benefits['normalization_notes'].append(
                'The payer linked this patient to the recorded member policy as a dependent. Subscriber details were matched automatically; eCW was not changed.')
    comparison = insurance_identity.compare(payload, coverage)
    benefits['identity'] = comparison
    if comparison['status'] == 'review':
        benefits['requires_review'] = True
        benefits['review_reasons'].append('The subscriber relationship could not be verified automatically from eCW and payer evidence.'
            if comparison.get('policy_differences') else 'Confirm the member identity: the payer response differs from eCW.'
            if any(row['field'] == 'member_id' for row in comparison['differences']) else
            'Confirm patient identity: the payer-returned patient details differ from eCW.')
    return benefits


def _attach_previsit_pricing(result: dict, practice_id: str, inputs: dict, source: dict,
                             payer_id: str, benefits: dict, *, payload: dict | None = None,
                             review: dict | None = None) -> None:
    # Retain a scoped payer assertion about the requesting provider. A generic
    # in-network benefit schedule alone still cannot establish participation.
    if benefits.get('network_source') != 'payer_participation_message':
        benefits["network_status"] = "unknown"
    connections = [row for row in commercial_store.list_connections(practice_id)
                   if row.get("stedi_payer_id") == payer_id and row.get("status") == "staff_guidance_enabled"]
    if len(connections) != 1:
        result["issues"].append("Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.")
        result['price_range'] = visit_estimate.price_lines(result['codes'], [], benefits, {'cost_share': 'payer_returned',
            'office_service_types': _office_service_types(practice_id, source),
            **_benefit_context(inputs, source)}, payload=payload,
            service_date=inputs.get('service_date', ''), reviews=(review or {}).get('lines'),
            alternatives=not (review or {}).get('selected_ids') and bool(result['codes']) and all(row.get('source') == 'conditional office E/M alternative' for row in result['codes']))
        return
    connection = connections[0]
    try:
        _, network, _ = _previsit_practice_path(practice_id, {**inputs, "payer_id": connection["id"]}, source)
        benefits["network_status"] = "in_network"
        benefits['network_source'] = 'clinic_record'
        result['network_evidence'] = {key: network.get(key) for key in ('network_name', 'evidence_reference', 'effective_from', 'effective_to')}
    except HTTPException:
        pass
    result["price_range"], issues = _previsit_range(practice_id, inputs, source, connection["id"], result["codes"], benefits,
                                                  payload=payload, review=review)
    result["issues"].extend(issues)


def _visit_context_token(inputs: dict, source: dict) -> str:
    context = {'coverage': {k: v for k, v in _coverage_snapshot(inputs, source).items() if k != 'other_active_coverage'},
               'codes': _previsit_codes(inputs),
               'provider': inputs.get('rendering_provider'),
               'provider_evidence': {key: (source.get('rendering_provider') or {}).get(key) for key in ('npi', 'roles')},
               'diagnoses': inputs.get('diagnoses'), 'billing': source.get('billing_provider'),
               'reason': inputs.get('reason'), 'location': source.get('location_id'), 'pos': inputs.get('place_of_service'), 'date': inputs.get('service_date')}
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def _finish_previsit(result: dict, case: dict, inputs: dict, source: dict, payload: dict | None, session: dict, payer_id: str,
                     *, pricing_source: dict | None = None) -> None:
    token = _visit_context_token(inputs, source)
    review = case.get('visit_review') or {}
    if review.get('context_token') != token:
        review = {'context_token': token}
        case['visit_review'] = review
    comparison = result['benefits'].get('identity') or {}
    remembered = ((session.get('intake') or {}).get('patient') or {}).get('insurance_identities') or {}
    evidence = review.get('identity_confirmation') or remembered.get(comparison.get('binding'))
    if (comparison.get('can_confirm') and evidence and evidence.get('binding') == comparison.get('binding')
            and evidence.get('reference') and evidence.get('actor_id') and evidence.get('verified_at')):
        comparison = {**comparison, 'status': 'reconciled', 'evidence': evidence, 'message': ''}
        result['benefits']['review_reasons'] = [r for r in result['benefits'].get('review_reasons', []) if 'identity' not in r]
        result['benefits']['requires_review'] = bool(result['benefits']['review_reasons'])
    result['library_pricing'] = session.get('library_pricing', case.get('library_pricing', []))
    case['library_pricing'] = result['library_pricing']
    result['benefit_source'] = (payload or {}).get('benefit_source') or ('Stedi' if result.get('checked_at') else None)
    result['identity'] = comparison
    result['benefits']['identity'] = comparison
    result['eligibility_reconciliation'] = visit_estimate.reconcile_eligibility(
        source.get('saved_eligibility') or {}, result['benefits'], result.get('checked_at'))
    if result['eligibility_reconciliation']['requires_review']:
        result['benefits'].setdefault('review_reasons', []).append(result['eligibility_reconciliation']['message'])
        result['benefits']['requires_review'] = True
    payer_plan = commercial.plan_details(payload or {})
    exact_source = {**(pricing_source or source), 'payer_plan_names': payer_plan['names'],
                    'payer_plan_scope': {key: payer_plan.get(key) for key in ('plan_number', 'group_number')}}
    codes = _previsit_codes(inputs)
    available = list(codes)
    known = {row['code'] for row in codes}
    # A clinic-scoped catalog is useful before final coding exists. Prices and
    # descriptions come from its active contract, never from AI or example defaults.
    configured_connections = commercial_store.list_connections(session['practice_id'])
    if not payer_id and payload is None:
        # A deferred payer check must not discard the already mapped eCW policy.
        # A returned payer route/product still takes precedence once benefits arrive.
        mapped = [row for row in configured_connections if row.get('id') == inputs.get('payer_id')
                  and row.get('category') == 'commercial_medical' and row.get('status') == 'staff_guidance_enabled']
        if len(mapped) == 1:
            payer_id = str(mapped[0].get('stedi_payer_id') or '')
    connections = [row for row in configured_connections
                   if row.get('stedi_payer_id') == payer_id and row.get('status') == 'staff_guidance_enabled']
    catalog = []
    if len(connections) == 1:
        try:
            _, network, profile = _previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']}, exact_source)
            if network.get('plan_matcher') and (pricing_source or source).get('plan'):
                catalog = commercial_store.matching_rates(session['practice_id'], profile, network, inputs['service_date'], None)
        except (HTTPException, commercial.CommercialError):
            pass
    catalog_codes = []
    for rate in catalog:
        if rate.get('modifier') or rate['code'] in known:
            continue
        row = {'id': 'contract:' + rate['code'], 'code': rate['code'], 'source': 'Contract service option',
               'description': rate.get('description') or rate['code'], 'units': 1, 'modifiers': []}
        available.append(row)
        catalog_codes.append(row)
        known.add(rate['code'])
    if not codes and catalog_codes:
        matches = {row['code'] for row in cpt_suggester._matching_candidate_rates(str(inputs.get('reason') or ''), catalog)}
        if matches & cpt_rules.EMG_ADD_ONS:
            matches |= cpt_rules.NCS_CODES
        codes = [row for row in catalog_codes if row['code'] in matches] or catalog_codes
    for group in (result.get('suggestions') or {}).get('groups', []):
        for candidate in group.get('codes', []):
            code = str(candidate.get('code') or '').upper()
            if re.fullmatch(r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])', code) and code not in known:
                available.append({'id': 'suggested:' + code, 'code': code, 'source': 'Clinician-confirmed candidate',
                                  'description': candidate.get('reason'), 'units': None, 'modifiers': []})
                known.add(code)
    for key in ('cash_schedule', 'cash_quote', 'practice_cash_source', 'collection_policy'):
        result.pop(key, None)
    uploaded_cash = practice_prices.current(session['practice_id'], inputs['service_date'])
    if uploaded_cash:
        known_codes = {row['code'] for row in available}
        available.extend({'id': 'cash:' + row['code'], 'code': row['code'], 'description': row['description'], 'source': 'Practice cash schedule', 'units': 1, 'modifiers': []} for row in uploaded_cash['rows'] if row['code'] not in known_codes and not row['code'].startswith('package:'))
    # Clinical options work for every practice, even before its prices arrive.
    # This catalog supplies no prices or coverage assumptions.
    known_codes = {row['code'] for row in available}
    descriptions = {**{str(n): 'Nerve conduction studies' for n in range(95907, 95914)},
                    '95885': 'Limited needle EMG', '95886': 'Complete needle EMG',
                    '62270': 'Diagnostic spinal tap · without imaging', '62328': 'Diagnostic spinal tap · with imaging',
                    **{str(n): 'Office consultation' for n in range(99202, 99206)},
                    **{str(n): 'Office consultation' for n in range(99212, 99216)}}
    available.extend({'id': 'planning:' + code, 'code': code, 'description': description,
                      'source': 'CPT planning catalog', 'units': 1, 'modifiers': []}
                     for code, description in descriptions.items() if code not in known_codes)
    for row in available:
        matches = [rate for rate in catalog if rate['code'] == row['code'] and (rate.get('modifier') or '') == ','.join(row.get('modifiers') or [])]
        if len(matches) == 1:
            row.update(unit_allowed_cents=matches[0]['allowed_cents'], unit_cash_cents=matches[0].get('cash_cents'),
                       description=row.get('description') or matches[0].get('description'), rate_source=matches[0].get('source_reference'))
    plan_key = hashlib.sha256(json.dumps([token, source.get('recent_completed_appointments', []),
        [(row['id'], row.get('units')) for row in available]], sort_keys=True).encode()).hexdigest()
    prior_plan = case.get('previsit_plan') or {}
    if prior_plan.get('key') != plan_key or prior_plan.get('plan', {}).get('version') != 5:
        prior_plan = {'key': plan_key, 'plan': cpt_suggester.recommend_visit_plan(inputs, source, available)}
        case['previsit_plan'] = prior_plan
    result['service_plan'] = {**prior_plan['plan'], 'selection_source': 'clinician' if review.get('selected_ids') else 'suggested'}
    auto_units = result['service_plan'].get('recommended_units') or {}
    units = {**auto_units, **(review.get('confirmed_units') or {})}
    result['available_codes'] = [{**row, 'units': units.get(row['id'], row.get('units'))} for row in available]
    result['billing_note'] = ecw_billing.collection_note(source.get('appointment_fields') or [])
    note_self_pay = (result['billing_note'] or {}).get('kind') == 'self_pay'
    result['payment_mode'] = review.get('payment_mode', 'cash' if source.get('self_pay_recorded') or note_self_pay else 'insurance')
    result['ecw_saved_eligibility'] = {k: v for k, v in (source.get('saved_eligibility') or {}).items() if not k.startswith('_')}
    labels = {'selected_ids': 'Services', 'confirmed_units': 'Service quantities', 'payment_mode': 'Payment method',
              'cash_selection': 'Cash package', 'emg_required': 'EMG testing', 'insurance_check_from': 'Insurance checking date', 'verification': 'Benefit or approval evidence',
              'identity_confirmation': 'Patient identity', 'product_mapping': 'Plan mapping',
              'payments_received_cents': 'Recorded payments'}
    result['audit_trail'] = [{'checked_at': entry.get('verified_at'), 'by': entry.get('verified_by') or 'Clinic staff',
                            'changes': [label for key, label in labels.items() if key in entry.get('changes', {})]}
                           for entry in case.get('visit_review_history', [])]
    result['referral_evidence'] = source.get('referral_evidence', [])
    result['appointment_context'] = {key: source.get(key) for key in ('location', 'rendering_provider', 'appointment_status')}
    result['appointment_context'].update(appointment_type=inputs.get('appointment_type'), imported_fields=source.get('appointment_fields', []),
                                         notes_status=source.get('appointment_notes_status', 'not_exposed'),
                                         timezone=source.get('timezone') or os.getenv('COMMERCIAL_PRACTICE_TIMEZONE', 'America/New_York'))
    result['coverage_records'] = source.get('coverage_records', [])
    result['insurance_fallback'] = {key: value for key, value in source.get('insurance_fallback', {}).items() if key != 'source'}
    selected_ids = review.get('selected_ids') or result['service_plan']['recommended_ids']
    if selected_ids:
        by_id = {row['id']: row for row in available}
        codes = [by_id[key] for key in selected_ids if key in by_id]
    result['codes'] = [{**row, 'confirmed': bool(review.get('selected_ids')), 'units': units.get(row['id'], row.get('units'))} for row in codes]
    if selected_ids:
        for row in result['codes']:
            if row.get('source') in {'conditional office E/M alternative', 'Contract service option'}:
                row['source'] = 'Anticipated service from appointment'
    result['patient_details'] = source.get('patient_details', {})
    result['insurance_details'] = {**_coverage_snapshot(inputs, source), 'record_status': source.get('coverage_record_status'), 'coverage_period': source.get('coverage_period', {})}
    recovered = case.get('previsit_discovery') or {}
    if recovered.get('outcome') == 'recovered' and recovered.get('identity_binding') == _recovery_binding(inputs, source):
        result['insurance_details'].update(recovered.get('verified_coverage') or {})
        result['insurance_details'].update(ecw_member_id=inputs.get('member_id'),
            member_source=recovered.get('evidence_source') or 'Stedi verified coverage')
    elif not inputs.get('member_id') and result['benefits'].get('active') is True:
        returned_identity = stedi_discovery.candidate_identity(payload or {}, inputs)
        if returned_identity:
            result['insurance_details'].update(member_id=returned_identity['member_id'], ecw_member_id=None, member_source='Stedi eligibility')
    subscriber = (payload or {}).get('subscriber') or {}
    dependent = (payload or {}).get('dependent') or {}
    result['insurance_details'].update(payer_subscriber=commercial.payer_person(subscriber),
        payer_patient=commercial.payer_person(dependent or subscriber),
        payer_relationship=dependent.get('relationToSubscriber') or ('SELF' if subscriber else None),
        payer_coverage_period=((dependent or subscriber).get('dates') or {}).get('plan') or {})
    result.update(ecw_coverage_active=source.get('active_coverages', 0) > 0, calculated_at=_now(), context_token=token, revision=case.get('revision', 0), plan_details={
        'ecw': source.get('plan_details') or {'plan': [source['plan']] if source.get('plan') else []},
        'stedi': commercial.plan_details(payload) if payload else {}}, ecw_retrieved_at=source.get('retrieved_at'),
        response_reference=(payload or {}).get('id'), selected_ids=selected_ids)
    result.pop('network_evidence', None)
    result.pop('pricing_assumptions', None)
    _attach_previsit_pricing(result, session['practice_id'], inputs, exact_source, payer_id, result['benefits'], payload=payload,
                            review=review)
    result['arrival_collection'] = visit_estimate.arrival_collection(result.get('price_range') or {}, result['benefits'], review)
    result['planning_estimate'] = (visit_estimate.planning_estimate(result.get('price_range') or {}, result['benefits'], comparison)
                                   if result['arrival_collection']['amount_cents'] is None else None)
    if len(connections) == 1:
        try:
            _, network, profile = _previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']},
                                                {**exact_source, 'payer_plan_names': []})
            if network.get('network_name') and network.get('evidence_reference'):
                result['mapping_candidate'] = {'network': network['network_name'], 'products': exact_source['payer_plan_names'],
                                               'reference': network['evidence_reference']}
        except HTTPException:
            result.pop('mapping_candidate', None)
    if uploaded_cash:
        practice_prices.apply(result, uploaded_cash)
    result['collection_guidance'] = collection_guidance.build(result, review)
    result.pop('emg_option', None)
    if inputs.get('visit_type') == 'new' and result['payment_mode'] == 'insurance':
        emg_rates, emg_rules = [], {**_benefit_context(inputs, source),
            'office_service_types': _office_service_types(session['practice_id'], source)}
        if (review.get('emg_required') or any(row['code'] in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for row in result['codes'])) and len(connections) == 1:
            try:
                _, network, profile = _previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']}, exact_source)
                emg_rates = commercial_store.matching_rates(session['practice_id'], profile, network, inputs['service_date'], None)
                emg_rules.update(profile['rules'])
            except (HTTPException, commercial.CommercialError):
                pass
        result['emg_option'] = collection_guidance.emg_option(result, emg_rates, emg_rules, payload, inputs['service_date'], review)
    guidance = result['collection_guidance']
    emg = result.get('emg_option') or {}
    price = result.get('price_range') or {}
    collection_event('collection_calculated', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case.get('id'), stage='calculation',
                     outcome='amount_available' if guidance.get('amount_cents') is not None else 'review_required',
                     status=guidance.get('status'), basis=guidance.get('basis'), payment_mode=result.get('payment_mode'),
                     source='ecw_saved' if str(result.get('benefit_source') or '').lower().startswith('ecw') else
                     'stedi' if result.get('checked_at') else 'none',
                     emg_state='separate' if emg.get('requested') and not emg.get('included') else
                     'included' if emg.get('included') else 'none',
                     amount_cents=guidance.get('amount_cents'), emg_min_cents=emg.get('total_min_cents'),
                     emg_max_cents=emg.get('total_max_cents'), cash_min_cents=price.get('cash_min_cents'),
                     cash_max_cents=price.get('cash_max_cents'), count=len(result.get('codes') or []))
    if result.get('eligibility_timing', {}).get('status') == 'too_early':
        case.setdefault('visit_review', {'context_token': token}).setdefault('insurance_check_from', result['eligibility_timing']['check_from'])
    case['previsit_context'] = {'inputs': inputs, 'source': source, 'payer_id': payer_id,
                              'result': result, 'context_token': token}


def _ecw_coded_lines(practice_id: str, profile: dict, network: dict, service_date: str,
                     inputs: dict) -> tuple[list[dict[str, Any]], list[str]]:
    """Use only explicit eCW codes and units; never turn visit text into a charge."""
    try:
        planned = [row for row in inputs.get("lines", []) if row.get("service_status") == "scheduled"]
        if any(row.get("modifiers") for row in planned):
            return [], ["The eClinicalWorks service has a modifier that needs a matching contract rate. Review before estimating."]
        selected = [ServiceLine(code=str(row.get("code") or "").upper(), units=int(str(row.get("units") or "")))
                    for row in planned]
    except (TypeError, ValueError, ValidationError):
        return [], ["eClinicalWorks returned a coded service without a whole-number unit. Update the appointment coding before estimating."]
    if not selected:
        return [], ["eClinicalWorks did not return a CPT/HCPCS-coded service for this appointment. Add the planned service in eClinicalWorks before estimating."]
    try:
        matched = commercial_store.matching_rates(practice_id, profile, network, service_date,
                                                  [line.model_dump() for line in selected])
    except commercial.CommercialError as exc:
        return [], [str(exc)]
    by_code = {row["code"]: row for row in matched}
    lines = [{"code": line.code, "units": line.units, "rate_id": by_code.get(line.code, {}).get("id"),
              "allowed_cents": by_code.get(line.code, {}).get("allowed_cents"),
              "cash_cents": by_code.get(line.code, {}).get("cash_cents")} for line in selected]
    missing = [line["code"] for line in lines if line["allowed_cents"] is None]
    return lines, ([f"No active contracted rate matches: {', '.join(missing)}."] if missing else [])


def _ecw_identity(session: dict, service_date: str) -> dict[str, str]:
    patient = session["intake"]["patient"]
    values = {"patient_first_name": patient.get("given_name"), "patient_middle_initial": session["intake"]["fields"].get("middle_initial", ""), "patient_last_name": patient.get("family_name"),
              "patient_birth_date": str(patient.get("birth_date") or ""), "ecw_appointment_date": service_date}
    if patient.get("ecw_patient_id"):
        values["ecw_patient_id"] = str(patient["ecw_patient_id"])
    if not all(values[key] for key in ("patient_first_name", "patient_last_name", "patient_birth_date")):
        raise HTTPException(422, "Confirm patient name and birth date before reading eClinicalWorks.")
    return {key: str(value) for key, value in values.items()}


def _coverage_snapshot(inputs: dict, source: dict) -> dict:
    subscriber, patient = inputs.get("subscriber") or {}, inputs.get("patient") or {}
    relationship = inputs.get("relationship")
    snapshot = {"patient_id": source.get("_patient_id"), "coverage_id": source.get("coverage_id"),
                "appointment_id": source.get("appointment_id"), "active_coverages": source.get("active_coverages", 0),
                "plan": source.get("plan"), "payer_id": inputs.get("payer_id"), "group_number": inputs.get("group_number"),
                "plan_number": inputs.get("plan_number") or source.get("plan_number"),
                "member_id": inputs.get("member_id"), "relationship": relationship,
                "member_source": (source.get('subscriber_evidence') or {}).get('member_id_source'),
                "subscriber_patient_linked": (source.get('subscriber_evidence') or {}).get('patient_linked') is True,
                "subscriber_first": subscriber.get("firstName"), "subscriber_last": subscriber.get("lastName"),
                "subscriber_birth_date": subscriber.get("birthDate"),
                "patient_first": patient.get("patient_first_name"), "patient_last": patient.get("patient_last_name"),
                "patient_birth_date": patient.get("patient_birth_date"), "patient_gender": patient.get("gender"),
                "patient_address": patient.get("address")}
    saved_response = (source.get('saved_eligibility') or {}).get('_response') or {}
    if any(row.get('inforce') is True and ecw_backend._reference(row.get('coverage')) !=
           'Coverage/' + str(source.get('coverage_id')) for row in saved_response.get('insurance', []) if isinstance(row, dict)):
        snapshot['other_active_coverage'] = True
    if relationship == "18" and (source.get('subscriber_evidence') or {}).get('reference') in {
            None, 'Patient/' + str(source.get('_patient_id'))}:
        snapshot["subscriber_first"] = snapshot["subscriber_first"] or snapshot["patient_first"]
        snapshot["subscriber_last"] = snapshot["subscriber_last"] or snapshot["patient_last"]
        snapshot["subscriber_birth_date"] = snapshot["subscriber_birth_date"] or snapshot["patient_birth_date"]
    return snapshot


def _ecw_conflicts(case: dict, session: dict, inputs: dict, source: dict) -> list[str]:
    data, fields = case["commercial"], session["intake"]["fields"]
    network = _network(session["practice_id"], data["provider_network_id"])
    checks = (("service date", inputs.get("service_date"), data["service_date"]),
              ("payer", inputs.get("payer_id"), data["payer_connection_id"]),
              ("billing provider NPI", source.get("billing_provider", {}).get("npi"), network["billing_npi"]),
              ("rendering provider NPI", inputs.get("rendering_provider", {}).get("npi"), network["rendering_npi"]),
              ("place of service", inputs.get("place_of_service"), network["place_of_service"]),
              ("member ID", inputs.get("member_id"), fields.get("member_id")))
    conflicts = [f"eClinicalWorks {label} does not match the confirmed intake." for label, observed, expected
                 in checks if (label != "member ID" or expected not in {None, "", "unknown"})
                 and (not observed or not expected or str(observed).strip().casefold() != str(expected).strip().casefold())]
    coverage = _coverage_snapshot(inputs, source)
    if not coverage["coverage_id"] or not coverage["member_id"] or not coverage["relationship"]:
        conflicts.append("eClinicalWorks did not return one identifiable coverage with member and relationship details.")
    if coverage["active_coverages"] != 1:
        conflicts.append("Multiple or no active eClinicalWorks coverages require coordination-of-benefits review.")
    if not all(coverage.get(key) for key in ("subscriber_first", "subscriber_last")):
        conflicts.append("eClinicalWorks did not return the policyholder identity needed for Stedi.")
    if fields.get("subscriber_birth_date") and str(fields["subscriber_birth_date"]) != str(coverage["subscriber_birth_date"]):
        conflicts.append("The card policyholder birth date differs from eClinicalWorks coverage.")
    if fields.get("subscriber_name") and re.sub(r"\W", "", fields["subscriber_name"]).casefold() != re.sub(r"\W", "", f"{coverage['subscriber_first']} {coverage['subscriber_last']}").casefold():
        conflicts.append("The card policyholder name differs from eClinicalWorks coverage.")
    # eCW may omit charge items, phone, or historical visits. Those inputs are
    # needed by other integrations, but are not prerequisites for this Stedi
    # estimate.  Only the explicit appointment/coverage comparison can block it.
    return list(dict.fromkeys(conflicts))


def _evidence_context(case: dict, session: dict, network: dict) -> dict:
    data, coverage = case["commercial"], case["ecw_coverage"]
    return {"practice_id": session["practice_id"], "patient_id": session["patient_id"],
            "coverage_id": session["intake"]["id"], "subscriber_relationship": coverage["relationship"],
            "payer_connection_id": data["payer_connection_id"], "service_date": data["service_date"],
            "rendering_npi": network["rendering_npi"], "place_of_service": network["place_of_service"],
            "service_category": data["service_category"], "appointment_id": coverage["appointment_id"],
            "member_id": coverage["member_id"], "ecw_coverage_id": coverage["coverage_id"]}


async def _assert_ecw_current(case: dict, session: dict) -> dict:
    data, saved = case["commercial"], case.get("ecw_coverage")
    if not saved or not saved.get("appointment_id"):
        raise commercial.CommercialError("Start a new estimate from a confirmed eClinicalWorks appointment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, data["service_date"]), _ecw_practice_config(session["practice_id"]), saved["appointment_id"])
        payer, network, profile = _practice_path(session["practice_id"], inputs, source)
    except (ecw_backend.EcwBackendError, HTTPException) as exc:
        raise commercial.CommercialError("The eClinicalWorks appointment or coverage could not be reverified. Refresh this visit before estimating.") from exc
    if (_coverage_snapshot(inputs, source) != saved or _ecw_conflicts(case, session, inputs, source)
            or payer["id"] != data["payer_connection_id"] or network["id"] != data["provider_network_id"]
            or profile["id"] != data["profile_id"] or inputs["service_date"] != data["service_date"]):
        raise commercial.CommercialError("The eClinicalWorks appointment, coverage, or contract changed. Start a new review.")
    lines, tasks = _ecw_coded_lines(session["practice_id"], profile, network, data["service_date"], inputs)
    if tasks or lines != data["lines"]:
        raise commercial.CommercialError("The scheduled services or contracted rates changed. Start a new review.")
    return source


def _stedi_address(value: object) -> dict[str, str] | None:
    """Translate eCW's FHIR-ish address names to Stedi's JSON names."""
    if not isinstance(value, dict):
        return None
    address = {
        "addressLine1": str(value.get("addressLine1") or value.get("address1") or "").strip(),
        "city": str(value.get("city") or "").strip(),
        "state": str(value.get("state") or "").strip(),
        "postalCode": str(value.get("postalCode") or value.get("postal_code") or "").strip(),
    }
    if not all(address.values()):
        return None
    line2 = str(value.get("addressLine2") or value.get("address2") or "").strip()
    if line2:
        address["addressLine2"] = line2
    return address


def stedi_request_body(payer: dict, network: dict, coverage: dict, service_date: str, stc: str | list[str],
                       *, include_identity_hints: bool = True, allow_demographic: bool = False,
                       inquiry_only: bool = False) -> dict:
    if (coverage.get('subscriber_patient_linked') and coverage.get('relationship') in {'18', 'self'}
            and coverage.get('subscriber_first') and coverage.get('patient_first')
            and insurance_identity.normalized(coverage['subscriber_first']) != insurance_identity.normalized(coverage['patient_first'])):
        # A linked different subscriber needs a dependent inquiry, not the patient's DOB in their slot.
        coverage = {**coverage, 'relationship': 'dependent'}
    if inquiry_only and coverage.get('active_coverages', 0) <= 1:
        # An inquiry slot is not a policyholder assertion. Only the returned
        # patient/member identity can create verified coverage.
        coverage = dict(coverage)
        if coverage.get('relationship') in {None, '', 'unknown'}:
            coverage.update(relationship='18', subscriber_first=coverage.get('patient_first'),
                subscriber_last=coverage.get('patient_last'), subscriber_birth_date=coverage.get('patient_birth_date'))
        coverage['active_coverages'] = 1
    needed = ("relationship", "subscriber_first", "subscriber_last",
              "patient_first", "patient_last", "patient_birth_date")
    if (any(not coverage.get(key) for key in needed) or not coverage.get("active_coverages")
            or (not coverage.get('member_id') and not allow_demographic)
            or (coverage["active_coverages"] > 1 and not coverage.get("coverage_id"))):
        raise commercial.CommercialError("eClinicalWorks did not return one usable primary coverage with member and policyholder details.")
    if network.get("rendering_first") and network.get("rendering_last") and network.get("rendering_npi"):
        provider = {"npi": network["rendering_npi"], "name": {"person": {
            "firstName": network["rendering_first"], "lastName": network["rendering_last"]}}}
    elif network.get("billing_npi") and network.get("organization_name"):
        provider = {"npi": network["billing_npi"], "name": {"organization": network["organization_name"]}}
    else:
        raise commercial.CommercialError("eClinicalWorks did not return one matching provider name and NPI for the Stedi inquiry.")
    body = {"payerId": payer["stedi_payer_id"], "provider": provider,
            "subscriber": {"memberId": coverage.get("member_id"), "dateOfBirth": coverage.get("subscriber_birth_date"),
                           "name": {"person": {"firstName": coverage["subscriber_first"], "lastName": coverage["subscriber_last"]}}},
            "encounter": {"dates": {"service": {"start": service_date}}, "services": [{"system": "STC", "value": code} for code in ([stc] if isinstance(stc, str) else stc)]}}
    body['subscriber'] = {key: value for key, value in body['subscriber'].items() if value}
    if coverage["relationship"] not in {"18", "self"}:
        body["dependent"] = {"dateOfBirth": coverage["patient_birth_date"],
                             "name": {"person": {"firstName": coverage["patient_first"], "lastName": coverage["patient_last"]}}}
    # Stedi recommends beginning with its small, documented eligibility request.
    # eCW's group/plan/address/gender can improve a few payer matches, but must
    # never make a valid core check fail validation.  The caller retries once
    # without these optional hints when Stedi rejects their request shape.
    if include_identity_hints:
        subscriber = body['subscriber']
        if coverage.get('group_number'):
            subscriber['groupNumber'] = coverage['group_number']
        if coverage.get('plan_number'):
            subscriber['planNumber'] = coverage['plan_number']
        patient_identity = body.get('dependent') or subscriber
        address = _stedi_address(coverage.get('patient_address'))
        if address:
            patient_identity['address'] = address
        gender = {"M": "MALE", "MALE": "MALE", "F": "FEMALE", "FEMALE": "FEMALE"}.get(
            str(coverage.get('patient_gender') or '').upper())
        if gender:
            patient_identity['gender'] = gender
    return body


def _request_validation(payload: object) -> bool:
    return isinstance(payload, dict) and commercial.eligibility_failure(payload)['kind'] == 'request_validation'


async def _post_stedi_eligibility(client: httpx.AsyncClient, headers: dict[str, str], body: dict,
                                  basic_body: dict | None = None) -> tuple[httpx.Response, dict, bool]:
    """Send one eligibility request, falling back once from optional eCW hints."""
    response = await client.post(STEDI_URL, headers=headers, json=body)
    payload = response.json() if response.content else {}
    retried = False
    if basic_body and basic_body != body and _request_validation(payload):
        response = await client.post(STEDI_URL, headers=headers, json=basic_body)
        payload = response.json() if response.content else {}
        retried = True
    # AAA 73 rejects the name, not necessarily the supplied ID. The API permits
    # a member-ID + DOB lookup. Never substitute guessed names or dependents.
    if isinstance(payload, dict) and '73' in commercial.eligibility_failure(payload)['codes'] and not body.get('dependent'):
        subscriber = body.get('subscriber') or {}
        if subscriber.get('memberId') and subscriber.get('dateOfBirth'):
            retry = deepcopy(basic_body or body)
            retry['subscriber'] = {key: subscriber[key] for key in ('memberId', 'dateOfBirth')}
            response = await client.post(STEDI_URL, headers=headers, json=retry)
            payload = response.json() if response.content else {}
            return response, payload, True
    return response, payload, retried


def _record_evidence(case: dict, session: dict, data: dict[str, Any], raw_object_key: str | None = None) -> dict:
    commercial_case = case["commercial"]
    if data["requested_dos"] != commercial_case["service_date"]:
        raise commercial.CommercialError("Eligibility evidence must use the case service date.")
    network = _network(session["practice_id"], commercial_case["provider_network_id"])
    context = _evidence_context(case, session, network)
    if raw_object_key:
        data["raw_object_key"] = raw_object_key
    evidence = commercial_store.save_evidence(session["practice_id"], case["id"], commercial.context_fingerprint(os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), context), data)
    commercial_case["evidence_id"] = evidence["id"]; commercial_case["request_state"] = "complete"
    benefits = data["benefits"]
    commercial_case["benefits_summary"] = {key: benefits.get(key) for key in (
        "active", "network_status", "deductible_remaining_cents", "oop_remaining_cents", "copay_cents",
        "coinsurance_percent", "authorization_signal", "referral_signal", "requires_review")}
    case["review_tasks"] = (["Prior authorization is required for the returned benefit. Confirm approval before estimating."] if benefits.get("authorization_signal") == "required" else
                            ["The payer could not determine prior-authorization requirements. Verify before estimating."] if benefits.get("authorization_signal") == "unknown" else
                            ["A referral requirement was returned for the selected benefit. Confirm it before estimating."] if benefits.get("referral_signal") == "required" else
                            ["The payer did not confirm that a referral is unnecessary. Verify the plan requirement before estimating."] if benefits.get("referral_signal") == "unknown" else [])
    return evidence


def _subscriber_identity(fields: dict[str, Any], patient: dict[str, Any]) -> tuple[str, str, str, bool]:
    relationship = str(fields.get("relationship") or "self")
    is_self = relationship in {"self", "18"}
    name = str(fields.get("subscriber_name") or "").strip() or f"{patient.get('given_name') or ''} {patient.get('family_name') or ''}".strip()
    parts = name.split()
    birth_date = str(patient.get("birth_date") if is_self else fields.get("subscriber_birth_date") or "")
    if len(parts) < 2 or not birth_date:
        raise HTTPException(422, "Confirm the card member ID, policyholder first and last name, and policyholder birth date before sending Stedi eligibility.")
    return parts[0], " ".join(parts[1:]), birth_date, is_self


@router.get("/configuration")
async def configuration(request: Request) -> dict:
    session = await _session(request)
    return _config(session["practice_id"])


@router.get("/appointments", responses={409: {"description": "ecw_patient_selection_required: return to staff lookup and confirm the patient before checking benefits."}})
async def ecw_appointments(request: Request) -> dict:
    """List confirmed scheduled visits without an eligibility check.

    Identity no-match/ambiguity returns HTTP 409 with the machine-readable code
    ecw_patient_selection_required. The UI opens a prefilled staff lookup; it
    never switches the patient or repeats eligibility from a similarity score.

    The shared reader has a 24-second deadline. HTTP 502 errors identify the
    failed lookup phase; X-ClinicForce-Request-ID joins the safe stage logs in
    CloudWatch. A failed read does not establish that no appointment exists.
    """
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        values = await ecw_backend.list_appointments(_ecw_identity(session, date.today().isoformat()), _ecw_practice_config(session["practice_id"]))
    except ecw_backend.EcwBackendError as exc:
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise HTTPException(502, str(exc)) from exc
    return {"appointments": values}


@router.post("/appointments/readiness")
async def ecw_appointment_readiness(value: EcwStartInput, request: Request) -> dict:
    """Read visit prerequisites without a payer transaction. An uncoded routine
    office visit can use the estimator's anticipated service without staff
    confirmation; scheduled_codes remains the actual eCW coding, possibly empty.
    Readiness does not establish payer benefits or a dollar amount. Unresolved identity
    returns 409 with code ecw_patient_selection_required for staff selection.
    """
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, date.today().isoformat()),
            _ecw_practice_config(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise HTTPException(502, str(exc)) from exc
    return _appointment_readiness(session["practice_id"], session, inputs, source)


def _recovery_binding(inputs: dict, source: dict) -> str:
    # Choosing anticipated services must not invalidate the recovered member identity.
    return _visit_context_token({**inputs, 'lines': [], 'reason': None, 'visit_type': None}, source)


def _case_benefits(payload: dict, inputs: dict, source: dict, case: dict) -> dict:
    coverage = _coverage_snapshot(inputs, source)
    recovery = case.get('previsit_discovery') or {}
    recovered = (recovery.get('outcome') == 'recovered' and recovery.get('identity_binding') == _recovery_binding(inputs, source)
                 and recovery.get('verified_coverage'))
    if recovered:
        coverage.update(recovery['verified_coverage'])
    benefits = _previsit_benefits(payload, coverage, inputs['service_date'], _previsit_service_types(inputs)[0], inputs=inputs, source=source)
    if recovered:
        benefits['normalization_notes'].append('Coverage was selected from Stedi discovery for this estimate; eCW was not changed.')
    return benefits


def _enrollment_failure(enrollment: dict) -> dict:
    descriptions = {
        'LOOKUP_FAILED': 'The provider enrollment status could not be read. This does not establish missing enrollment. Retry the enrollment lookup before eligibility.',
        'MISSING': 'No eligibility enrollment was returned for this exact payer and provider NPI.',
        'REJECTED': 'The payer rejected this provider eligibility enrollment. Review the enrollment decision.',
        'CANCELED': 'This provider eligibility enrollment was canceled.',
        'DRAFT': 'The provider eligibility enrollment is still a draft and has not been submitted.',
        'PROVIDER_ACTION_REQUIRED': 'The provider must complete the outstanding enrollment requirements.',
    }
    detail = descriptions.get(enrollment['status'], 'Provider eligibility enrollment is pending or not verified as live.')
    return {'kind': 'provider_enrollment', 'title': 'Provider enrollment status', 'detail': detail,
            'action': detail + ' No paid eligibility request was submitted.', 'codes': [],
            'reason_code': enrollment['reason_code']}


def _insurance_diagnosis(case: dict, result: dict, guidance: dict) -> dict:
    """Independent, persisted stage results; never put candidate identities in logs."""
    recovery = result.get('recovery') or {}
    benefits = result.get('benefits') or {}
    candidates = recovery.get('candidates') or []
    price = result.get('price_range') or {}
    failure = result.get('eligibility_failure') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    verified = (recovery.get('outcome') == 'recovered' and recovery.get('state') == 'complete') or (
        not recovery and benefits.get('active') is True and identity.get('status') in {'matched', 'verified', 'reconciled'})
    identity_verified = identity.get('status') in {'matched', 'verified', 'reconciled'} or any(
        row.get('identity_status') == 'MATCHED' for row in candidates)
    reasons = [reason for row in candidates for reason in row.get('reason_codes', [])]
    raw_recovery = case.get('previsit_discovery') or {}
    retrieval = case['previsit_context'].get('source', {}).get('coverage_retrieval') or {}
    if retrieval.get('status') == 'empty':
        reasons.append('ECW_UPSTREAM_NO_COVERAGE')
    elif retrieval.get('status') == 'unavailable':
        reasons.append('ECW_COVERAGE_UNAVAILABLE')
    elif retrieval.get('raw_count') and not retrieval.get('retained_count'):
        reasons.append('ECW_COVERAGE_FILTERED')
    if raw_recovery.get('card_failure_reason'):
        reasons.append(raw_recovery['card_failure_reason'].upper())
    states = list(dict.fromkeys(row.get('state', 'CANDIDATE_FOUND') for row in candidates))
    raw_count = recovery.get('raw_candidate_count')
    if raw_count == 0 and recovery.get('state') == 'complete' and recovery.get('outcome') == 'no_match':
        states.append('UPSTREAM_NO_POLICIES')
    elif raw_count and not candidates:
        states.append('CANDIDATES_REJECTED')
    if sum(row.get('active_medical') is True for row in candidates) > 1:
        reasons.append('MULTIPLE_COVERAGES'); states.append('MULTIPLE_COVERAGES')
    states.append('IDENTITY_VERIFIED' if identity_verified else 'IDENTITY_REVIEW_REQUIRED')
    enrollment = raw_recovery.get('enrollment') or result.get('enrollment')
    if enrollment and enrollment.get('status') != 'LIVE':
        reasons.append(enrollment['reason_code'].upper()); states.append('ENROLLMENT_BLOCKED')
    if not verified or benefits.get('active') is not True:
        states.append('ELIGIBILITY_NOT_VERIFIED')
    else:
        states.append('BENEFITS_VERIFIED')
    candidate_states = {row.get('coverage_evidence', {}).get('status') for row in candidates}
    eligibility_state = (benefits.get('coverage_evidence') or {}).get('status')
    if not eligibility_state and len(candidate_states) == 1:
        eligibility_state = next(iter(candidate_states))
    available = guidance.get('amount_cents') is not None
    details = []
    if not available:
        details = list(dict.fromkeys([guidance.get('reason') or '', *benefits.get('review_reasons', []), *result.get('issues', []),
            *price.get('issues', []), *price.get('service_issues', []),
            *(issue for row in price.get('lines', []) for issue in row.get('issues', []))]))
        details = [value for value in details if value]
        if benefits.get('active') is not True:
            reasons.append('APPLICABLE_BENEFITS_MISSING')
        if benefits.get('coverage_status_conflict'):
            reasons.append('COVERAGE_STATUS_CONFLICT')
        if identity.get('status') == 'review':
            reasons.append('PATIENT_IDENTITY_REVIEW_REQUIRED')
        if benefits.get('service_date_matches') is False:
            reasons.append('COVERAGE_DATE_MISMATCH')
        if benefits.get('network_status') not in {'in_network', 'not_applicable'}:
            reasons.append('PROVIDER_NETWORK_NOT_VERIFIED')
        if not price.get('complete') and any(row.get('allowed_cents') is None for row in price.get('lines', [])):
            reasons.append('CONTRACTED_RATE_MISSING')
        if not reasons:
            reasons.append('BENEFIT_OR_PRICE_REQUIREMENT_UNRESOLVED')
    if failure.get('kind'):
        reasons.append(failure['kind'].upper())
    states.append('ESTIMATE_READY' if available else 'PRICING_UNAVAILABLE')
    return {'coverageSource': (case.get('previsit_discovery') or {}).get('evidence_source') or
                ('stedi_discovery' if recovery else result.get('benefit_source') or 'ecw'),
            'rawCandidateCount': raw_count, 'retainedCandidateCount': len(candidates),
            'verifiedCandidateCount': recovery.get('verified_candidate_count', 0),
            'identityStatus': 'VERIFIED' if identity_verified else 'REVIEW_REQUIRED',
            'eligibilityStatus': 'BENEFITS_VERIFIED' if verified and benefits.get('active') is True else eligibility_state if eligibility_state in {'INACTIVE', 'EXPIRED', 'FUTURE', 'CONFLICT'} else 'NOT_VERIFIED',
            'pricingStatus': 'ESTIMATE_READY' if available else 'NOT_CALCULATED',
            'reasonCodes': list(dict.fromkeys(reasons)), 'states': list(dict.fromkeys(states)),
            'pricingReasons': details, 'enrollment': enrollment, 'ecwCoverage': retrieval,
            'discoveryEnrollment': raw_recovery.get('discovery_enrollment'), 'mbiEnrollment': raw_recovery.get('mbi_enrollment')}


def _previsit_result(case: dict) -> dict:
    result = deepcopy(case['previsit_context']['result'])
    result['revision'] = case.get('revision', 0)
    check_from = max((case.get('visit_review') or {}).get('insurance_check_from') or '',
        ((case['previsit_context'].get('source', {}).get('coverage_period') or {}).get('start') or '')[:10])
    timing = _eligibility_timing(result['service_date'], check_from or None) if result.get('service_date') else None
    if timing:
        result['eligibility_timing'] = timing
    else:
        result.pop('eligibility_timing', None)
    if case.get('previsit_discovery'):
        result['recovery'] = stedi_discovery.public_status(case['previsit_discovery'])
        result['coverage_status'] = result['recovery']['coverage_status']
    recovery = result.get('recovery') or {}
    source = case['previsit_context'].get('source') or {}
    hints = {}
    evidence = [{'payer': ' / '.join(row.get('payer') or []), 'member_id': row.get('member_id'), 'source': 'eCW Coverage'}
                for row in source.get('coverage_records', [])]
    evidence += [{**row, 'source': 'eCW appointment notes'} for row in source.get('insurance_candidates', [])]
    evidence += (case.get('previsit_discovery') or {}).get('card_candidates', [])
    for row in evidence:
        key = (stedi_discovery.normalized(row.get('payer')), stedi_discovery.normalized(row.get('member_id')))
        if not any(key):
            continue
        hint = hints.setdefault(key, {'payer': row.get('payer'), 'member_id': row.get('member_id'), 'sources': []})
        if row['source'] not in hint['sources']:
            hint['sources'].append(row['source'])
    result['insurance_hints'] = list(hints.values())
    insurance_guidance = collection_guidance.build(result, case.get('visit_review'))
    result['insurance_diagnosis'] = _insurance_diagnosis(case, result, insurance_guidance)
    pricing_unavailable = (result.get('payment_mode') != 'cash' and insurance_guidance.get('amount_cents') is None
        and case.get('previsit', {}).get('request_state') in {'complete', 'rejected', 'not_requested'}
        and not recovery)
    if (not timing and recovery.get('state') in {'complete', 'rejected'}
            and recovery.get('outcome') != 'recovered'
            and recovery.get('exhausted') is not False
            and (case.get('visit_review') or {}).get('payment_mode') != 'insurance'):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {'reason': 'Automatic insurance recovery finished without usable coverage. Cash / self-pay pricing selected; insurance remains unverified.'}
        result.pop('emg_option', None)
    elif (not timing and (pricing_unavailable or (recovery.get('state') == 'complete' and recovery.get('outcome') == 'recovered'
            and insurance_guidance.get('amount_cents') is None))
            and (case.get('visit_review') or {}).get('payment_mode') != 'insurance'):
        result['payment_mode'] = 'cash'
        result['cash_fallback'] = {'reason': 'An insurance amount could not be established: ' + insurance_guidance['reason'] +
            ' Showing the separate clinic self-pay price; insurance status is unchanged.'}
        result.pop('emg_option', None)
    # Cash is a separate price path, never evidence of inactive insurance.
    result['collection_guidance'] = collection_guidance.build(result, case.get('visit_review'))
    return result


def _recovery_event(case: dict, session: dict, stage: str, outcome: str, *, reason: str | None = None,
                    count: int | None = None, http_status: int | None = None) -> None:
    collection_event('coverage_recovery', practice_id=session['practice_id'],
        intake_id=(session.get('intake') or {}).get('id'), case_id=case['id'], stage=stage,
        outcome=outcome, reason_code=reason, count=count, http_status=http_status)


async def _read_card_hints(session: dict, patient_id: str | None) -> dict:
    """Read one patient-bound card set; OCR fields are inquiry hints, not benefits."""
    from app.insurance import v1_sources
    from app.api.routers.staff import inspect_file
    contents = []
    source = None
    try:
        intakes = await run_in_threadpool(staff_store.patient_card_intakes, session['practice_id'], session['patient_id'])
        card_intakes = [intake for intake in intakes if intake.get('documents')]
        if len(card_intakes) > 1:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        docs = card_intakes[0]['documents'] if card_intakes else []
        if len(docs) > 2:
            return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
        for doc in docs:
            if doc.get('size_bytes', 0) <= 10_000_000 and doc.get('object_key'):
                contents.append(await run_in_threadpool(object_store.get_object_bytes, doc['object_key']))
        if contents:
            source = 'clinicforce_card'
        elif patient_id:
            cards = await asyncio.wait_for(ecw_backend.insurance_cards(patient_id), timeout=24)
            if len(cards) > 2:
                return {'status': 'multiple_cards', 'fields': {}, 'count': 0}
            for card in cards:
                data, _ = await asyncio.wait_for(ecw_backend.insurance_cards(patient_id, document_id=card['id']), timeout=24)
                contents.append(data)
            if contents:
                source = 'ecw_card'
        if not contents:
            return {'status': 'not_found', 'fields': {}, 'count': 0}
        if any(len(content) > 10_000_000 for content in contents):
            return {'status': 'unavailable', 'fields': {}, 'count': len(contents), 'failure_reason': 'card_too_large'}
        for content in contents:
            await run_in_threadpool(inspect_file, content)
        fields, evidence, _ = await asyncio.wait_for(asyncio.to_thread(v1_sources.read_card_fields, contents), timeout=20)
        return {'status': 'read', 'fields': fields, 'evidence': evidence, 'count': len(contents), 'source': source}
    except (ecw_backend.EcwBackendError, httpx.HTTPError, TimeoutError, ValueError, HTTPException,
            golden.OCRServiceError, golden.GoldenCaseError) as exc:
        return {'status': 'unavailable', 'fields': {}, 'count': len(contents),
                'failure_reason': 'card_read_timeout' if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else 'card_unavailable'}


def _compare_card_hints(card: dict, inputs: dict, source: dict) -> dict:
    """Fill absent eCW inquiry fields only when OCR names the linked patient/policy."""
    status = card['status']
    result = {'status': status, 'source': card.get('source'), 'fields_added': [], 'conflicts': [], 'differences': []}
    if status != 'read':
        return result
    fields = card['fields']
    if not fields.get('member_id'):
        result['status'] = 'unverified'
        return result
    for field in ('member_id', 'group_number'):
        if (inputs.get(field) and fields.get(field)
                and stedi_discovery.normalized(inputs[field]) != stedi_discovery.normalized(fields[field])):
            result['conflicts'].append(field)
    if result['conflicts']:
        result['status'] = 'conflict'
        return result
    candidate = stedi_discovery.card_candidate({**fields, 'payer': fields.get('payer') or source.get('payer')}, inputs, source)
    if not candidate:
        result['status'] = 'unverified'
        result['differences'].append('member_name_or_birth_date')
        return result
    if (source.get('payer') and fields.get('payer')
            and stedi_discovery.normalized(source['payer']) != stedi_discovery.normalized(fields['payer'])):
        result['differences'].append('payer_name')
    for field in ('member_id', 'group_number'):
        if not inputs.get(field) and fields.get(field):
            inputs[field] = fields[field]
            result['fields_added'].append(field)
    if not source.get('payer') and fields.get('payer'):
        source['payer'] = fields['payer']
        source['payer_source'] = 'card_ocr'
        result['fields_added'].append('payer')
    if 'member_id' in result['fields_added']:
        source['subscriber_evidence'] = {**(source.get('subscriber_evidence') or {}), 'member_id_source': 'card_ocr'}
    if fields.get('birth_date') and not (inputs.get('subscriber') or {}).get('birthDate'):
        subscriber = inputs.get('subscriber') or {}
        name = f"{subscriber.get('firstName') or ''} {subscriber.get('lastName') or ''}"
        if stedi_discovery.normalized(name) == stedi_discovery.normalized(fields.get('member_name')):
            subscriber['birthDate'] = fields['birth_date']
            inputs['subscriber'] = subscriber
            result['fields_added'].append('subscriber_birth_date')
    result['status'] = 'filled_missing' if result['fields_added'] else 'matched'
    return result


async def _recover_card(case: dict, session: dict, provider: dict) -> bool:
    """Read patient-bound stored/eCW cards once; OCR is never benefit evidence."""
    context, recovery = case['previsit_context'], case['previsit_discovery']
    patient_id = context['source'].get('_patient_id')
    if recovery.get('card_status'):
        return False
    recovery['card_status'] = 'reading'
    case.update(await run_in_threadpool(_save, case, session, 'coverage-card-started'))
    _recovery_event(case, session, 'insurance_card', 'started')
    unavailable = False
    count = 0
    try:
        card = await _read_card_hints(session, patient_id)
        count = card.get('count', 0)
        recovery['card_status'] = card['status']
        unavailable = card['status'] == 'unavailable'
        if unavailable:
            recovery['card_failure_reason'] = card.get('failure_reason') or 'card_unavailable'
        if card['status'] == 'read':
            fields, evidence = card['fields'], card['evidence']
            _recovery_event(case, session, 'card_download', 'complete', count=count)
            _recovery_event(case, session, 'card_ocr', 'complete', count=len(fields))
            recovery['card_evidence'] = evidence
            item = stedi_discovery.card_candidate(fields, context['inputs'], context['source'])
            if item:
                payload = {'items': [item]}
                key = await run_in_threadpool(object_store.put_staff_document, session['practice_id'], case['id'],
                    'card-coverage-hints.json', json.dumps(payload).encode(), 'application/json')
                recovery.update(card_status='read', card_object_key=key,
                    candidates=stedi_discovery.candidates(payload, context['inputs'], context['source']))
                recovery['card_candidates'] = recovery['candidates']
                if recovery['candidates'] and context['source'].get('active_coverages', 0) <= 1:
                    recovery.update(selected_candidate_id=recovery['candidates'][0]['id'], stage='eligibility',
                                    request_state='pending', evidence_source='insurance_card')
                    case.update(await run_in_threadpool(_save, case, session, 'coverage-card-eligibility-pending'))
                    recovery = case['previsit_discovery']
                    await _recheck_discovery_candidate(case, session, provider, discovery_payload=payload, inquiry_only=True)
                    if recovery.get('outcome') == 'recovered' or recovery.get('request_state') == 'indeterminate':
                        return True
            recovery['card_status'] = 'read_no_verified_policy'
    except (ecw_backend.EcwBackendError, httpx.HTTPError, TimeoutError, ValueError, HTTPException, golden.OCRServiceError) as exc:
        unavailable = True
        recovery['card_status'] = 'unavailable'
        recovery['card_failure_reason'] = 'card_read_timeout' if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else 'card_unavailable'
    if recovery.get('card_status') == 'reading':
        recovery['card_status'] = 'not_found'
    _recovery_event(case, session, 'insurance_card', 'unavailable' if unavailable else 'complete',
                    reason=recovery['card_status'], count=count)
    recovery.update(request_state='pending', stage='discovery', outcome=None, selection_error=None,
                    candidates=[], selected_candidate_id=None, evidence_source='stedi_discovery')
    case.update(await run_in_threadpool(_save, case, session, 'coverage-card-finished'))
    return False


def _recovery_stopped(case: dict) -> bool:
    recovery = case['previsit_discovery']
    return recovery.get('outcome') == 'recovered' or recovery.get('request_state') == 'indeterminate'


def _request_ip_chain(request: Request) -> str | None:
    """Preserve upstream HETS traceability privately, never in public results."""
    forwarded = request.headers.get('x-forwarded-for', '')
    if len(forwarded) > 2048:
        return None
    try:
        hops = [str(ipaddress.ip_address(part.strip())) for part in forwarded.split(',') if part.strip()]
        if request.client:
            peer = str(ipaddress.ip_address(request.client.host))
            if peer not in hops:
                hops.append(peer)
        return ', '.join(hops) if 0 < len(hops) <= 20 else None
    except ValueError:
        return None


async def _recover_saved_policy(case: dict, session: dict, provider: dict, *, allow_query: bool = True) -> None:
    """Reuse protected evidence only from explicitly linked patient records."""
    context, recovery = case['previsit_context'], case['previsit_discovery']
    if recovery.get('steps', {}).get('saved_policy') or context['source'].get('active_coverages', 0) > 1:
        return
    recovery.setdefault('steps', {})['saved_policy'] = 'not_found'
    rows = await run_in_threadpool(store.patient_checks, session['practice_id'], session['patient_id'])
    policies = {}
    for row in rows[:10]:
        old = row['case']
        if old.get('id') == case['id'] or (old.get('previsit') or {}).get('request_state') != 'complete':
            continue
        key = old.get('previsit_raw_object_key')
        if not key:
            continue
        try:
            item = json.loads(await run_in_threadpool(object_store.get_object_bytes, key))
            identity = stedi_discovery.candidate_identity(item, context['inputs'])
            payer = item.get('payer') or {}
            if not identity or not (payer.get('name') or payer.get('lastName')):
                continue
            if context['inputs'].get('member_id') and stedi_discovery.normalized(identity['member_id']) != stedi_discovery.normalized(context['inputs']['member_id']):
                continue
            # Retain distinct policies; never choose the newest policy as primary.
            token = (str(payer.get('payorIdentification') or payer.get('name') or payer.get('lastName')),
                     identity['member_id'])
            policies.setdefault(token, (old, item))
        except (ValueError, TypeError, KeyError):
            continue
    if len(policies) != 1:
        recovery['steps']['saved_policy'] = 'multiple_policies' if policies else 'not_found'
        _recovery_event(case, session, 'saved_policy', 'complete', count=len(policies))
        return
    old, item = next(iter(policies.values()))
    previous = old.get('previsit_context') or {}
    old_source = previous.get('source') or {}
    expected_npi = provider.get('rendering_npi') or provider.get('billing_npi')
    previous_npi = (old_source.get('rendering_provider') or {}).get('npi') or (old_source.get('billing_provider') or {}).get('npi')
    try:
        fresh = datetime.fromisoformat(old['previsit']['checked_at']) + timedelta(hours=24) > datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        fresh = False
    reusable = (fresh and expected_npi == previous_npi and previous.get('inputs', {}).get('service_date') == context['inputs']['service_date']
        and old['previsit'].get('requested_service_types') == _previsit_service_types(context['inputs'])
        and not visit_estimate.reconcile_eligibility(context['source'].get('saved_eligibility') or {},
            old['previsit'].get('benefits') or {}, old['previsit'].get('checked_at'))['requires_review']
        and previous.get('payer_id') and stedi_discovery.automatic_match(item, context['inputs'], context['source']))
    recovery.update(evidence_source='saved_payer_response', route_source='returned_policy')
    if reusable:
        recovery['eligibility_object_key'] = old['previsit_raw_object_key']
        await _accept_recovered_coverage(case, session, item,
            {'stediId': previous['payer_id'], 'displayName': (item.get('payer') or {}).get('name')},
            _discovered_coverage(context['inputs'], context['source'], item))
        recovery['steps']['saved_policy'] = 'reused'
    elif allow_query:
        recovery['candidates'] = stedi_discovery.candidates({'items': [item]}, context['inputs'])
        if recovery['candidates']:
            recovery['selected_candidate_id'] = recovery['candidates'][0]['id']
            recovery['steps']['saved_policy'] = 'rechecked'
            await _recheck_discovery_candidate(case, session, provider, discovery_payload={'items': [item]})
    else:
        recovery['steps']['saved_policy'] = 'needs_current_eligibility'
    _recovery_event(case, session, 'saved_policy', recovery['steps']['saved_policy'], count=1)


async def _recover_known_payer(case: dict, session: dict, provider: dict) -> None:
    context, recovery = case['previsit_context'], case['previsit_discovery']
    source = context['source']
    # The normal path already queried a resolved payer; do not pay to repeat it.
    if (context.get('payer_id') or recovery.get('steps', {}).get('known_payer')
            or not source.get('payer') or source.get('active_coverages', 0) > 1
            or context['result'].get('eligibility_error') == 'payer_routing_unresolved'):
        return
    recovery.setdefault('steps', {})['known_payer'] = 'unavailable'
    routes = []
    try:
        payer = await stedi_payers.search_payer(source['payer'], source.get('payer_identifiers') or [],
            stedi_financial.credentials(session['practice_id']), plan=source.get('plan'), candidates=routes)
        if not payer:
            recovery.update(payer_routes=routes, route_source='ecw')
            return
        recovery.update(route_source='ecw', evidence_source='ecw_insurance_hints')
        await _recheck_discovery_candidate(case, session, provider, selected_route=payer, inquiry_only=True)
        recovery['steps']['known_payer'] = recovery.get('outcome') or recovery['request_state']
    except (httpx.HTTPError, ValueError):
        _recovery_event(case, session, 'known_payer', 'unavailable', reason='payer_directory_unavailable')


async def _recover_mbi(case: dict, session: dict, provider: dict) -> None:
    context, recovery = case['previsit_context'], case['previsit_discovery']
    if recovery.get('steps', {}).get('mbi'):
        return
    if recovery.get('coverage_count') or recovery.get('candidates'):
        recovery.setdefault('steps', {})['mbi'] = 'policy_order_unresolved'
        return
    body = stedi_discovery.mbi_request_body(context['inputs'], context['source'], provider)
    recovery.setdefault('steps', {})['mbi'] = 'not_applicable' if not body else 'enrollment_unavailable'
    if not body:
        return
    ip_chain = context['source'].get('_request_ip_chain')
    if not ip_chain:
        recovery['steps']['mbi'] = 'request_trace_unavailable'
        return
    api_key = stedi_financial.credentials(session['practice_id'])
    submitted = False
    try:
        # Both are read-only checks. Never submit or attest enrollment here.
        cms = await stedi_payers.search_payer('CMS', ['CMS'], api_key)
        lookup = await stedi_payers.search_payer('CMS MBI Lookup Without SSN', ['MBILUNOSSN'], api_key)
        for payer in (cms, lookup):
            enrollment = (await stedi_payers.eligibility_enrollment(payer['stediId'], provider['billing_npi'], api_key)
                          if payer else {'status': 'MISSING', 'reason_code': 'payer_route_missing'})
            recovery['mbi_enrollment'] = enrollment
            if enrollment['status'] != 'LIVE':
                _recovery_event(case, session, 'mbi', 'skipped', reason=enrollment['reason_code'])
                return
        request_key = await run_in_threadpool(object_store.put_staff_document, session['practice_id'], case['id'],
            'mbi-request.json', json.dumps(body).encode(), 'application/json')
        recovery.update(request_state='pending', stage='eligibility', outcome=None, mbi_request_object_key=request_key)
        recovery['steps']['mbi'] = 'pending'
        case.update(await run_in_threadpool(_save, case, session, 'mbi-lookup-submitting'))
        recovery = case['previsit_discovery']
        _recovery_event(case, session, 'mbi', 'requested')
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5)) as client:
            submitted = True
            response = await client.post(stedi_discovery.MBI_URL,
                headers={'Authorization': api_key, 'X-Forwarded-For': ip_chain}, json=body)
        item = response.json()
        if not isinstance(item, dict):
            raise ValueError('Invalid MBI response')
        recovery['mbi_response_object_key'] = await run_in_threadpool(object_store.put_staff_document,
            session['practice_id'], case['id'], 'mbi-response.json', json.dumps(item).encode(), 'application/json')
        if str(item.get('status') or '').upper() == 'PENDING':
            recovery.update(request_state='indeterminate', stage='eligibility', reason_code='mbi_outcome_unknown')
            recovery['steps']['mbi'] = 'indeterminate'
            return
        identity = stedi_discovery.candidate_identity(item, context['inputs'])
        managed_plan = any(row.get('code') == 'U' and row.get('insuranceTypeCode') in {'HM', 'HN'}
                           for row in commercial._stedi_entries(item))
        if (response.is_error or str(item.get('status') or '').upper() in {'ERROR', 'FAILED', 'REJECTED'}
                or item.get('tradingPartnerServiceId') not in {'CMS', 'CMSMED', cms['stediId']}
                or managed_plan or not identity or identity['relationship'] != 'self'
                or not stedi_discovery.active_medical(item, context['inputs'])):
            recovery.update(request_state='complete', outcome='review_needed', reason_code='mbi_not_verified')
            recovery['steps']['mbi'] = 'not_verified'
            return
        recovery.update(eligibility_object_key=recovery['mbi_response_object_key'], evidence_source='medicare_mbi_lookup')
        recovery['steps']['mbi'] = 'recovered'
        # STC 30 can verify a policy without supplying specialist cost sharing.
        # The normalizer preserves unavailable service benefits and prices.
        await _accept_recovered_coverage(case, session, item, cms,
            _discovered_coverage(context['inputs'], context['source'], item), http_status=response.status_code)
        case['previsit']['requested_service_types'] = ['30']
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        recovery['steps']['mbi'] = 'indeterminate' if submitted else 'unavailable'
        if submitted:
            recovery.update(request_state='indeterminate', stage='eligibility', reason_code='mbi_outcome_unknown')
        _recovery_event(case, session, 'mbi', recovery['steps']['mbi'])


async def _recover_previsit(case: dict, session: dict, provider: dict) -> None:
    """Finish every applicable automatic route before choosing cash pricing."""
    if _previsit_result(case).get('eligibility_timing'):
        return
    recovery = case['previsit_discovery']
    recovery['exhausted'] = False
    # Resuming a saved discovery polls/consumes that transaction first. Opening
    # a completed legacy result never starts these new paid recovery stages.
    if not recovery.get('raw_object_key'):
        await _recover_saved_policy(case, session, provider)
        if not _recovery_stopped(case):
            await _recover_card(case, session, provider)
        if not _recovery_stopped(case):
            await _recover_known_payer(case, session, provider)
    if not _recovery_stopped(case):
        recovery = case['previsit_discovery']
        recovery.update(request_state='pending', outcome=None, stage='discovery')
        await _recover_discovery(case, session, provider)
    if not _recovery_stopped(case):
        await _recover_mbi(case, session, provider)
    recovery = case['previsit_discovery']
    recovery['exhausted'] = recovery.get('request_state') in {'complete', 'rejected'}
    case.update(await run_in_threadpool(_save, case, session, 'coverage-recovery-finished'))
    _recovery_event(case, session, 'recovery', 'complete' if recovery['exhausted'] else 'indeterminate',
        reason='coverage_recovered' if recovery.get('outcome') == 'recovered' else 'available_paths_exhausted' if recovery['exhausted'] else 'outcome_unknown')


async def _recover_discovery(case: dict, session: dict, provider: dict) -> None:
    """Discovery with distinct documented addresses; never replay an unknown POST."""
    context = case['previsit_context']
    inputs = context['inputs']
    if _previsit_result(case).get('eligibility_timing'):
        return
    discovery = case['previsit_discovery']
    api_key = stedi_financial.credentials(session['practice_id'])
    try:
        if api_key and not discovery.get('raw_object_key') and not discovery.get('steps', {}).get('discovery_enrollment'):
            try:
                route = await stedi_payers.search_payer('Stedi Insurance Discovery', ['DISCOVERY'], api_key)
                enrollment = (await stedi_payers.eligibility_enrollment(route['stediId'],
                    provider.get('rendering_npi') or provider.get('billing_npi'), api_key)
                    if route else {'status': 'MISSING', 'reason_code': 'payer_route_missing'})
                discovery['discovery_enrollment'] = enrollment
                status = 'live' if enrollment['status'] == 'LIVE' else 'unavailable' if enrollment['status'] == 'LOOKUP_FAILED' else 'limited_search'
            except (httpx.HTTPError, ValueError):
                status = 'unavailable'
            discovery.setdefault('steps', {})['discovery_enrollment'] = status
            _recovery_event(case, session, 'discovery_enrollment', status)
            # Enrollment expands the search but is not required by DISCOVERY.
        addresses = [inputs.get('patient', {}).get('address') or {},
                     *inputs.get('patient', {}).get('previous_addresses', [])[:2]]
        # Deduplicate before changing state for a retry, including when the last
        # recorded address would produce the same request as the current one.
        addresses = list({json.dumps(stedi_discovery.request_body(
            {**inputs, 'patient': {**inputs['patient'], 'address': address}}, provider), sort_keys=True): address
            for address in addresses}.values())
        for address_index in range(discovery.get('address_index', 0), len(addresses)):
            discovery['address_index'] = address_index
            async with httpx.AsyncClient(timeout=httpx.Timeout(140, connect=5)) as client:
                headers = {'Authorization': api_key}
                payload = None
                if discovery.get('raw_object_key'):
                    payload = json.loads(await run_in_threadpool(object_store.get_object_bytes, discovery['raw_object_key']))
                    _recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_response')
                if payload is None:
                    request_inputs = {**inputs, 'patient': {**inputs['patient'], 'address': addresses[address_index]}}
                    body = stedi_discovery.request_body(request_inputs, provider)
                    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                    attempts = discovery.setdefault('attempts', [])
                    if any(row['fingerprint'] == fingerprint for row in attempts):
                        continue
                    request_key = await run_in_threadpool(object_store.put_staff_document,
                        session['practice_id'], case['id'], 'stedi-discovery-request.json', json.dumps(body).encode(), 'application/json')
                    attempts.append({'fingerprint': fingerprint, 'request_object_key': request_key})
                    discovery.update(request_state='pending', stage='discovery', outcome=None, selection_error=None)
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-submitting'))
                    discovery = case['previsit_discovery']
                    _recovery_event(case, session, 'discovery', 'requested', count=len(attempts),
                                    reason='previous_address' if address_index else 'current_address')
                    response = await client.post(stedi_discovery.URL, headers=headers, json=body)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid discovery response')
                    if response.is_error:
                        payload['status'] = 'ERROR'
                    discovery['discovery_id'] = payload.get('discoveryId')
                    discovery['raw_object_key'] = await run_in_threadpool(object_store.put_staff_document,
                        session['practice_id'], case['id'], 'stedi-discovery.json', json.dumps(payload).encode(), 'application/json')
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-response'))
                    discovery = case['previsit_discovery']
                    _recovery_event(case, session, 'discovery', 'received', http_status=response.status_code)
                for _ in range(2):
                    if payload.get('status') != 'PENDING' or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', str(discovery.get('discovery_id') or '')):
                        break
                    response = await client.get(stedi_discovery.URL + '/' + str(discovery['discovery_id']), headers=headers)
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError('Invalid discovery response')
                    if response.is_error:
                        payload['status'] = 'ERROR'
                discovery.update(checked_at=_now(), coverage_count=payload.get('coveragesFound'))
                if any(row.get('code') == 'discovery::enrollment::auto_enrollment_created' for row in payload.get('warnings') or []):
                    _recovery_event(case, session, 'discovery', 'warning', reason='auto_enrollment_created')
                discovery['raw_object_key'] = await run_in_threadpool(object_store.put_staff_document,
                    session['practice_id'], case['id'], 'stedi-discovery.json', json.dumps(payload).encode(), 'application/json')
                if discovery.get('attempts'):
                    discovery['attempts'][-1].update(response_object_key=discovery['raw_object_key'],
                        discovery_id=discovery.get('discovery_id'), status=payload.get('status'))
                if payload.get('status') == 'PENDING':
                    discovery.update(request_state='indeterminate', stage='awaiting_result', reason_code='discovery_pending')
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-awaiting-result'))
                    _recovery_event(case, session, 'discovery', 'pending', reason='discovery_pending')
                    return
                items = payload.get('items') or []
                candidates = stedi_discovery.candidates(payload, inputs, context['source'])
                _recovery_event(case, session, 'discovery_result', 'received', count=len(items),
                                reason='upstream_no_policies' if payload.get('status') == 'COMPLETE' and not payload.get('errors') and not items else 'upstream_response')
                _recovery_event(case, session, 'candidate_filter', 'complete', count=len(candidates),
                                reason='candidates_retained' if candidates else 'all_candidates_filtered' if items else 'no_upstream_candidates')
                for row in candidates:
                    row['matches_ecw_policy'] = stedi_discovery.matches_ecw_policy(items[row['index']], inputs, context['source'])
                discovery.update(request_state='complete', candidates=candidates, raw_candidate_count=len(items), candidate_version=4,
                                 outcome='no_match' if not items else 'review_needed')
                if payload.get('status') != 'COMPLETE' or payload.get('errors'):
                    discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                                     reason_code='discovery_request_rejected')
                    _recovery_event(case, session, 'discovery', 'rejected', reason='discovery_request_rejected')
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-finished'))
                    return
                matching = [row for row in candidates if row['verification_status'] == 'VERIFIED']
                # Do not use a first match or infer payer primacy from discovery.
                if (len(matching) == 1 and sum(row['active_medical'] for row in candidates) == 1
                        and context['source'].get('active_coverages', 0) <= 1):
                    candidate = matching[0]
                    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=candidate['id'],
                                     evidence_source='stedi_discovery')
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-recovered-member-pending'))
                    _recovery_event(case, session, 'candidate_match', 'matched', count=1)
                    await _recheck_discovery_candidate(case, session, provider, discovery_payload=payload, reuse_benefits=True)
                    return
                inquiry = [row for row in candidates if row.get('inquiry_eligible')]
                if len(inquiry) == 1 and sum(row['active_medical'] for row in candidates) <= 1 and context['source'].get('active_coverages', 0) <= 1:
                    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=inquiry[0]['id'], evidence_source='stedi_discovery')
                    case.update(await run_in_threadpool(_save, case, session, 'stedi-candidate-inquiry-pending'))
                    await _recheck_discovery_candidate(case, session, provider, discovery_payload=payload, inquiry_only=True)
                    return
                reason = 'no_coverage_found' if not items else 'multiple_active_policies' if len(matching) > 1 else 'identity_or_coverage_review'
                detail = ('No coverage was returned by the completed insurance search. This does not establish that the patient is uninsured.'
                          if not items else 'Returned coverage requires identity, service-date or payer-order reconciliation before it can supply an insured estimate.')
                discovery.update(stage='candidate_match_review', reason_code=reason, selection_error=detail)
                context['result'].setdefault('eligibility_failure', {})['detail'] = detail
                _recovery_event(case, session, 'candidate_match', discovery['outcome'], reason=reason, count=len(candidates))
                case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-finished'))
                if items or address_index + 1 >= len(addresses):
                    return
                # Retry only a completed no-match with a different recorded address.
                discovery.pop('raw_object_key', None)
                discovery.pop('discovery_id', None)
                discovery.update(request_state='pending', stage='discovery', address_index=address_index + 1)
                case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-address-retry'))
                discovery = case['previsit_discovery']
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        case['previsit_discovery'].update(request_state='indeterminate', reason_code='transport_or_parse_error')
        case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-indeterminate'))
        _recovery_event(case, session, 'discovery', 'indeterminate', reason='transport_or_parse_error')


async def _run_coverage_recovery(case: dict, session: dict, provider: dict) -> None:
    """Bound background work and retain unknown submissions for reconciliation."""
    try:
        await asyncio.wait_for(_recover_previsit(case, session, provider), timeout=540)
    except Exception as exc:
        reason = 'recovery_deadline' if isinstance(exc, TimeoutError) else 'recovery_failed'
        _recovery_event(case, session, 'recovery', 'indeterminate', reason=reason)
        case['previsit_discovery'].update(request_state='indeterminate', reason_code=reason)
        try:
            case.update(await run_in_threadpool(_save, case, session, 'coverage-recovery-interrupted'))
        except Exception:
            _recovery_event(case, session, 'persistence', 'unavailable', reason='recovery_save_failed')


async def _reconcile_saved_discovery(case: dict, session: dict) -> None:
    """Add current safe match diagnostics to a legacy discovery response.

    This reads the protected response already obtained from Stedi. It must never
    turn opening an older estimate into a new discovery or eligibility request.
    """
    context, discovery = case['previsit_context'], case['previsit_discovery']
    try:
        payload = json.loads(await run_in_threadpool(object_store.get_object_bytes, discovery['raw_object_key']))
        if not isinstance(payload, dict):
            raise ValueError('Invalid saved discovery response')
        inputs, source = context['inputs'], context['source']
        items = payload.get('items') or []
        candidates = stedi_discovery.candidates(payload, inputs, source)
        for row in candidates:
            row['matches_ecw_policy'] = stedi_discovery.matches_ecw_policy(items[row['index']], inputs, source)
        discovery.update(request_state='complete', candidates=candidates, coverage_count=payload.get('coveragesFound'),
                         raw_candidate_count=len(items), candidate_version=4,
                         outcome='no_match' if not items else 'review_needed', checked_at=_now())
        if payload.get('status') != 'COMPLETE' or payload.get('errors'):
            discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                             reason_code='discovery_request_rejected')
        else:
            matching = [row for row in candidates if row['verification_status'] == 'VERIFIED']
            reason = ('upstream_no_policies' if not items else 'identity_or_coverage_review' if not matching else
                      'multiple_active_policies' if len(matching) > 1 else 'verified_candidate_retained')
            discovery.update(stage='candidate_match_review', reason_code=reason)
            detail = ('The saved search returned no policies; this does not establish uninsured status.' if not items else
                'Saved insurance candidates are retained for review. No new payer request was submitted.')
            context['result'].setdefault('eligibility_failure', {})['detail'] = detail
            discovery['selection_error'] = detail
        await run_in_threadpool(_save, case, session, 'stedi-discovery-reconciled')
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        discovery.update(request_state='complete', outcome='review_needed', stage='saved_discovery_unavailable',
                         reason_code='saved_discovery_unavailable',
                         selection_error='The saved discovery response could not be read. Check the current insurance card before another discovery request.')
        await run_in_threadpool(_save, case, session, 'stedi-discovery-reconcile-failed')


def _discovered_coverage(inputs: dict, source: dict, item: dict) -> dict:
    identity = stedi_discovery.candidate_identity(item, inputs)
    if not identity:
        raise ValueError('Discovery candidate no longer matches the eCW patient.')
    coverage = _coverage_snapshot(inputs, source)
    coverage.update(identity, active_coverages=1, coverage_id=coverage.get('coverage_id') or 'stedi-discovery')
    coverage['relationship'] = identity['relationship_code']
    return coverage


def _discovery_pricing_source(source: dict, item: dict, payer: dict) -> dict:
    details = commercial.plan_details(item)
    return {**source, 'payer': payer.get('displayName') or (item.get('payer') or {}).get('name'),
            'payer_identifiers': [str((item.get('payer') or {}).get('payorIdentification') or '')],
            'plan': (details.get('names') or [None])[0],
            'plan_details': {'plan': details.get('names') or [],
                             'group_number': [details['group_number']] if details.get('group_number') else []}}


async def _accept_recovered_coverage(case: dict, session: dict, verified: dict, payer: dict,
                                     coverage: dict, *, http_status: int = 200) -> None:
    context, discovery = case['previsit_context'], case['previsit_discovery']
    inputs, source = context['inputs'], context['source']
    pricing_source = _discovery_pricing_source(source, verified, payer)
    discovery.update(request_state='complete', outcome='recovered', stage='complete', verified_coverage=coverage,
                     pricing_source=pricing_source, checked_at=_now(), reason_code='coverage_recovered', selection_error=None)
    result = deepcopy(context['result'])
    result.pop('eligibility_failure', None); result.pop('eligibility_error', None)
    # A prior route's blocked enrollment does not describe verified reused
    # benefits or a successful check through another route. Snapshots retain it.
    result.pop('enrollment', None)
    if (discovery.get('enrollment') or {}).get('status') != 'LIVE':
        discovery.pop('enrollment', None)
    for candidate in discovery.get('candidates') or []:
        if candidate['id'] == discovery.get('selected_candidate_id'):
            candidate.update(verification_status='VERIFIED',
                verification={'status': 'VERIFIED', 'pathway': 'accepted_payer_evidence', 'reason_codes': []},
                reason_codes=(stedi_discovery.parse_discovery_candidate(verified) or {}).get('reason_codes', []))
    result.update(payer=pricing_source.get('payer'), plan=pricing_source.get('plan'), issues=[], checked_at=_now(),
                  benefits=_case_benefits(verified, inputs, source, case))
    await run_in_threadpool(_finish_previsit, result, case, inputs, source, verified, session, payer['stediId'], pricing_source=pricing_source)
    guidance = result.get('collection_guidance') or collection_guidance.build(result, case.get('visit_review'))
    _recovery_event(case, session, 'pricing', 'priced' if guidance.get('amount_cents') is not None else 'unavailable',
                    reason=guidance.get('basis') if guidance.get('amount_cents') is not None else 'benefits_or_rates_incomplete')
    case['previsit'].update(request_state='complete', checked_at=result['checked_at'], benefits=result['benefits'], requested_service_types=_previsit_service_types(inputs))
    case['previsit_original_error_object_key'] = case.get('previsit_raw_object_key')
    case['previsit_raw_object_key'] = discovery['eligibility_object_key']
    case.update(await run_in_threadpool(_save, case, session, 'stedi-coverage-recovered'))
    _recovery_event(case, session, 'eligibility', 'verified', reason='coverage_recovered', http_status=http_status)


async def _recheck_discovery_candidate(case: dict, session: dict, provider: dict, *, discovery_payload: dict | None = None,
                                      selected_route: dict | None = None, inquiry_only: bool = False,
                                      reuse_benefits: bool = False) -> None:
    """Use a selected, persisted discovery candidate without altering eCW."""
    context, discovery = case['previsit_context'], case['previsit_discovery']
    inputs, source = context['inputs'], context['source']
    if _previsit_result(case).get('eligibility_timing'):
        return
    submitted = False
    try:
        ecw_route = selected_route is not None and discovery.get('route_source') == 'ecw'
        if ecw_route:
            item = {}
            coverage = _coverage_snapshot(inputs, source)
        else:
            payload = discovery_payload or json.loads(await run_in_threadpool(object_store.get_object_bytes,
                discovery.get('route_payload_key') if selected_route else discovery['raw_object_key']))
            candidate = ({'index': discovery['route_item_index']} if selected_route else
                next(row for row in discovery.get('candidates', []) if row['id'] == discovery.get('selected_candidate_id')))
            item = (payload.get('items') or [])[candidate['index']]
            if 'RELATIONSHIP_CONFLICT' in stedi_discovery.verify_candidate(item, inputs, source)['reason_codes']:
                discovery.update(request_state='complete', outcome='review_needed', stage='candidate_match_review',
                    reason_code='subscriber_relationship_conflict',
                    selection_error='Available eCW and payer evidence could not link this subscriber policy to the patient.')
                case.update(await run_in_threadpool(_save, case, session, 'subscriber-link-unresolved'))
                return
            coverage = _discovered_coverage(inputs, source, item)
        api_key = stedi_financial.credentials(session['practice_id'])
        payer_data = item.get('payer') or {}
        routes = []
        payer = selected_route or await stedi_payers.search_payer(payer_data.get('name') or payer_data.get('lastName') or '',
            [str(value) for value in (item.get('payerId'), payer_data.get('payorIdentification')) if value], api_key, candidates=routes)
        if not payer:
            if routes:
                discovery.update(payer_routes=routes, route_source='returned_policy',
                    route_item_index=candidate['index'],
                    route_payload_key=discovery.get('card_object_key') if item.get('evidence_source') == 'insurance_card' else discovery.get('raw_object_key'))
            discovery.update(request_state='complete', outcome='review_needed', stage='payer_route_review',
                reason_code='discovered_payer_not_routable', selection_error='The returned payer has no unique supported eligibility route.')
            case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-route-unresolved'))
            _recovery_event(case, session, 'payer_routing', 'review_needed', reason='discovered_payer_not_routable')
            return
        returned_services = {code for row in commercial._stedi_entries(item) for code in row.get('serviceTypeCodes', [])}
        discovery.pop('enrollment', None)
        if (reuse_benefits and stedi_discovery.automatic_match(item, inputs, source)
                and set(_previsit_service_types(inputs)) <= returned_services):
            key = await run_in_threadpool(object_store.put_staff_document, session['practice_id'], case['id'],
                'discovered-eligibility.json', json.dumps(item).encode(), 'application/json')
            discovery.update(eligibility_object_key=key, evidence_source='stedi_discovery')
            await _accept_recovered_coverage(case, session, item, payer, coverage)
            _recovery_event(case, session, 'eligibility', 'reused', reason='discovery_benefits_sufficient')
            return
        if payer.get('transactionSupport', {}).get('eligibilityCheck') == 'ENROLLMENT_REQUIRED':
            enrollment = await stedi_payers.eligibility_enrollment(payer['stediId'], provider.get('rendering_npi') or provider.get('billing_npi'), api_key)
            discovery['enrollment'] = enrollment
            if enrollment['status'] != 'LIVE':
                discovery.update(request_state='complete', outcome='review_needed', stage='provider_enrollment',
                    reason_code=enrollment['reason_code'], selection_error=_enrollment_failure(enrollment)['detail'])
                case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-enrollment-required'))
                _recovery_event(case, session, 'eligibility', 'unavailable', reason=enrollment['reason_code'])
                return
        body = stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _previsit_service_types(inputs), allow_demographic=not coverage.get('member_id'), inquiry_only=inquiry_only)
        basic_body = stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _previsit_service_types(inputs),
                                        include_identity_hints=False, allow_demographic=not coverage.get('member_id'), inquiry_only=inquiry_only)
        if inputs['service_date'] == date.today().isoformat():
            body['encounter'].pop('dates', None)
            basic_body['encounter'].pop('dates', None)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        attempts = discovery.setdefault('eligibility_attempts', [])
        if any(row['fingerprint'] == fingerprint for row in attempts):
            discovery.update(request_state='complete', outcome='review_needed', reason_code='duplicate_inquiry_skipped')
            return
        request_key = await run_in_threadpool(object_store.put_staff_document, session['practice_id'], case['id'],
            'recovery-eligibility-request.json', json.dumps(body).encode(), 'application/json')
        attempts.append({'fingerprint': fingerprint, 'request_object_key': request_key})
        discovery.update(request_state='pending', stage='eligibility', outcome=None)
        case.update(await run_in_threadpool(_save, case, session, 'recovery-eligibility-submitting'))
        discovery = case['previsit_discovery']
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5)) as client:
            _recovery_event(case, session, 'eligibility', 'requested')
            submitted = True
            response, verified, _ = await _post_stedi_eligibility(
                client, {'Authorization': api_key, 'Content-Type': 'application/json'}, body, basic_body)
        if not isinstance(verified, dict):
            raise ValueError('Invalid eligibility response')
        discovery['eligibility_object_key'] = await run_in_threadpool(object_store.put_staff_document,
            session['practice_id'], case['id'], 'stedi-recovered-eligibility.json', json.dumps(verified).encode(), 'application/json')
        returned_identity = stedi_discovery.candidate_identity(verified, inputs)
        identity_matches = False
        if returned_identity:
            identity_matches = bool((not coverage.get('member_id') or stedi_discovery.normalized(returned_identity['member_id']) == stedi_discovery.normalized(coverage['member_id'])) and
                all(stedi_discovery.normalized(returned_identity[key]) == stedi_discovery.normalized(coverage.get(key))
                    for key in ('subscriber_first', 'subscriber_last')) and
                (not coverage.get('subscriber_birth_date') or not returned_identity.get('subscriber_birth_date') or
                 insurance_identity.birth_date(coverage['subscriber_birth_date']) == insurance_identity.birth_date(returned_identity['subscriber_birth_date'])) and
                ((returned_identity['relationship'] == 'self') == (coverage['relationship'] in {'18', 'self'})))
            if inquiry_only and coverage.get('relationship') in {None, '', 'unknown'}:
                identity_matches = bool(not coverage.get('member_id') or
                    stedi_discovery.normalized(returned_identity['member_id']) == stedi_discovery.normalized(coverage['member_id']))
            if returned_identity.get('address_conflict'):
                identity_matches = False
            recorded = insurance_identity.compare(verified, _coverage_snapshot(inputs, source))
            if recorded.get('policy_differences') and not stedi_discovery.automatic_match(verified, inputs, source):
                identity_matches = False
        if (response.is_error or str(verified.get('status') or '').upper() in {'ERROR', 'FAILED', 'REJECTED'}
                or commercial.eligibility_errors(verified) or not identity_matches or not stedi_discovery.active_medical(verified, inputs, _previsit_service_types(inputs)[0])):
            discovery.update(request_state='complete', outcome='review_needed', stage='eligibility_rejected', selection_error='The selected policy was not accepted by the payer. Select another returned policy or check the current card.')
            discovery['reason_code'] = ('eligibility_rejected' if response.is_error or commercial.eligibility_errors(verified)
                                        else 'returned_identity_or_coverage_mismatch')
            _recovery_event(case, session, 'eligibility', 'rejected', reason=discovery['reason_code'], http_status=response.status_code)
            if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
                context['result']['eligibility_failure']['detail'] = (
                    'The eCW payer name and ID did not identify one Stedi route. Discovery matched the eCW policy, but the payer rejected its follow-up eligibility check. Coverage remains unverified, not proven inactive.')
                discovery['selection_error'] = context['result']['eligibility_failure']['detail']
            case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-selection-rejected'))
            return
        coverage.update({key: returned_identity[key] for key in ('member_id', 'group_number', 'plan_number') if returned_identity.get(key)})
        if inquiry_only:
            coverage = _discovered_coverage(inputs, source, verified)
        await _accept_recovered_coverage(case, session, verified, payer, coverage, http_status=response.status_code)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, IndexError, commercial.CommercialError):
        discovery.update(request_state='indeterminate' if submitted else 'complete', outcome='review_needed',
                         stage='eligibility' if submitted else 'selection_error', reason_code='selected_policy_check_failed',
                         selection_error='The selected policy could not be checked. Select another returned policy or check the current card.')
        _recovery_event(case, session, 'eligibility', 'indeterminate' if submitted else 'unavailable', reason='selected_policy_check_failed')
        if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
            context['result']['eligibility_failure']['detail'] = (
                'The eCW payer name and ID did not identify one Stedi route. Discovery found the matching policy, but its follow-up eligibility check could not be completed. Coverage remains unverified, not proven inactive.')
            discovery['selection_error'] = context['result']['eligibility_failure']['detail']
        case.update(await run_in_threadpool(_save, case, session, 'stedi-discovery-selection-error'))


async def _start_previsit_recovery(case: dict, session: dict, provider: dict, background_tasks: BackgroundTasks | None) -> dict:
    result = case['previsit_context']['result']
    if _previsit_result(case).get('eligibility_timing'):
        return _previsit_result(case)
    recovery = case.get('previsit_discovery') or {}
    if recovery and stedi_discovery.public_status(recovery)['state'] == 'indeterminate':
        recovery['request_state'] = 'indeterminate'
    legacy_result = bool(
        recovery.get('request_state') == 'complete'
        and recovery.get('outcome') in {'review_needed', 'no_match'}
        and recovery.get('raw_object_key')
        and recovery.get('candidate_version') != 4
    )
    if legacy_result and background_tasks is not None:
        recovery.update(request_state='pending', stage='reconciling_saved_discovery',
                        reason_code='reconciling_saved_discovery')
        case['previsit_discovery'] = recovery
        case = await run_in_threadpool(_save, case, session, 'stedi-discovery-reconciling')
        background_tasks.add_task(_reconcile_saved_discovery, case, session)
        return _previsit_result(case)
    failure = result.get('eligibility_failure') or {}
    identity_errors = {'65', '67', '71', '72', '73', '75'}
    ready = bool(background_tasks is not None and
                 (recovery.get('recovery_requested') or identity_errors.intersection(failure.get('codes', []))) and
                 stedi_discovery.request_body(case['previsit_context']['inputs'], provider))
    # Completed externally retrieved results can be consumed once without a new
    # transaction. All subsequent opens use the recorded outcome.
    process_saved = recovery.get('request_state') == 'complete' and not recovery.get('outcome') and recovery.get('raw_object_key')
    resume_poll = recovery.get('request_state') == 'indeterminate' and recovery.get('discovery_id') and recovery.get('stage') != 'eligibility'
    if ready and (not recovery or recovery.get('request_state') == 'not_requested' or process_saved or resume_poll):
        recovery.update(request_state='pending', exhausted=False, started_at=recovery.get('started_at') or _now(),
                        context_token=case['previsit_context']['context_token'],
                        identity_binding=_recovery_binding(case['previsit_context']['inputs'], case['previsit_context']['source']))
        case['previsit_discovery'] = recovery
        case = await run_in_threadpool(_save, case, session, 'stedi-discovery-pending')
        background_tasks.add_task(_run_coverage_recovery, case, session, provider)
    return _previsit_result(case)


@router.get('/cases/{case_id}/previsit')
async def saved_previsit(case_id: str, request: Request) -> dict:
    """Read saved recovery without submitting or retrying a paid transaction.

    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND.
    NOT_FOUND means a completed search returned no policies, not uninsured.
    recovery.state separately reports pending/indeterminate transport state.
    Coverage verification does not imply that a price is available.

    Parseable candidates include protected member/subscriber/dependent details,
    nullable subscriber DOB, source and verification reason_codes. Only this
    authenticated patient/practice scope may read them. A completed legacy
    response may be reparsed and its diagnosis saved, without payer submission
    or automatic adoption. Historical snapshots are unchanged.

    insurance_diagnosis separates raw/retained/verified candidate counts,
    identityStatus, eligibilityStatus, pricingStatus, reasonCodes, states,
    pricingReasons and enrollment status. A completed missing insurance price
    selects separate cash pricing unless explicitly overridden; it never
    changes coverage verification or manufactures a missing cash amount.
    """
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
    recovery = case.get('previsit_discovery') or {}
    if (recovery.get('request_state') == 'complete' and recovery.get('outcome') in {'review_needed', 'no_match'}
            and recovery.get('raw_object_key') and recovery.get('candidate_version') != 4):
        await _reconcile_saved_discovery(case, session)
    return _previsit_result(case)


@router.get('/cases/{case_id}/payer-response')
async def saved_payer_response(case_id: str, request: Request):
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if (not case or case.get('source_mode') != 'commercial_previsit'
            or case.get('previsit', {}).get('request_state') not in {'complete', 'rejected'}
            or not case.get('previsit_raw_object_key')):
        raise HTTPException(404, 'No completed payer response is available for this visit.')
    try:
        payload = json.loads(await run_in_threadpool(object_store.get_object_bytes, case['previsit_raw_object_key']))
    except Exception as exc:
        raise HTTPException(502, 'The saved payer response could not be loaded.') from exc
    return _response({'source': 'Stedi', 'response': payload})


@router.post('/cases/{case_id}/payer-route')
async def select_payer_route(case_id: str, value: PayerRouteInput, request: Request,
                            background_tasks: BackgroundTasks) -> dict:
    """Select a saved directory suggestion and run eligibility with known identity.

    Selection is not coverage verification. Stale, arbitrary and already-pending
    selections are rejected before any payer transaction. No eCW data is changed.
    """
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
    context = case['previsit_context']
    if case.get('revision', 0) != value.revision or context['context_token'] != value.context_token:
        raise HTTPException(409, 'This estimate changed. Reopen it before selecting a payer route.')
    recovery = case.get('previsit_discovery') or {}
    if stedi_discovery.public_status(recovery)['state'] not in {'complete', 'rejected'} or recovery.get('outcome') == 'recovered':
        raise HTTPException(409, 'Wait for the existing recovery request before selecting a payer route.')
    payer = next((row for row in recovery.get('payer_routes', []) if row['stediId'] == value.route_id), None)
    if not payer:
        raise HTTPException(422, 'Select one of the payer routes returned for this policy.')
    inputs, source = context['inputs'], context['source']
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
        'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
        'billing_npi': str(billing.get('npi') or ''),
        'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    recovery.update(request_state='pending', outcome='review_needed', stage='eligibility',
        selected_route_id=value.route_id, selection_error=None, started_at=_now())
    case = await run_in_threadpool(_save, case, session, 'stedi-payer-route-selected')
    _recovery_event(case, session, 'payer_routing', 'selected', reason=payer.get('match_basis', 'name_or_alias'))
    background_tasks.add_task(_recheck_discovery_candidate, case, session, provider, selected_route=payer)
    return _previsit_result(case)


@router.post('/cases/{case_id}/coverage-candidate')
async def select_coverage_candidate(case_id: str, value: CoverageCandidateInput, request: Request,
                                    background_tasks: BackgroundTasks) -> dict:
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
    if case.get('revision', 0) != value.revision or case['previsit_context'].get('context_token') != value.context_token:
        raise HTTPException(409, 'This estimate changed in another session. Reopen it before selecting coverage.')
    discovery = case.get('previsit_discovery') or {}
    if discovery.get('request_state') != 'complete' or discovery.get('outcome') != 'review_needed':
        raise HTTPException(409, 'There is no coverage candidate ready to select.')
    if not any(row.get('id') == value.candidate_id for row in discovery.get('candidates', [])):
        raise HTTPException(422, 'Select a coverage option returned for this patient.')
    source, inputs = case['previsit_context']['source'], case['previsit_context']['inputs']
    # Staff may reconcile a different/new policy. Its patient identity and active
    # service-date coverage are still revalidated against the payer response.
    rendering, billing = source.get('rendering_provider') or {}, source.get('billing_provider') or {}
    provider = {'rendering_npi': str(rendering.get('npi') or (inputs.get('rendering_provider') or {}).get('npi') or ''),
                'rendering_first': str(rendering.get('first_name') or ''), 'rendering_last': str(rendering.get('last_name') or ''),
                'billing_npi': str(billing.get('npi') or ''),
                'organization_name': str(billing.get('name') or session.get('staff', {}).get('practice_name') or '')}
    discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=value.candidate_id,
                     selection_error=None, checked_at=_now())
    case = await run_in_threadpool(_save, case, session, 'stedi-discovery-candidate-selected')
    background_tasks.add_task(_recheck_discovery_candidate, case, session, provider)
    return _previsit_result(case)


async def _begin_coverage_discovery(result: dict, session: dict, inputs: dict, source: dict, provider: dict,
                                    background_tasks: BackgroundTasks | None, detail: str, *, refresh_benefits: bool = False,
                                    payer_routes: list[dict] | None = None) -> dict:
    """Save a no-policy appointment as a recoverable Stedi discovery case."""
    coverage = _coverage_snapshot(inputs, source)
    binding = hashlib.sha256(json.dumps({'patient': inputs.get('patient'), 'appointment': source.get('appointment_id'),
        'provider': provider, 'service_date': inputs.get('service_date')}, sort_keys=True).encode()).hexdigest()
    slot = 'commercial-previsit-recovery:' + hashlib.sha256(f"{source.get('appointment_id')}:{binding}".encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'], workflow_slot=slot, **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    result['check_id'] = case['id']
    if (case.get('previsit_context') or {}).get('context_token') == _visit_context_token(inputs, source):
        result['suggestions'] = case['previsit_context']['result'].get('suggestions')
    elif inputs.get('reason'):
        result['suggestions'] = await run_in_threadpool(cpt_suggester.suggest_preliminary_cpt_codes,
            str(inputs['reason'])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
            source.get('recent_completed_appointments'))
    saved = case.get('previsit') or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await _start_previsit_recovery(case, session, provider, background_tasks)
    if not refresh_benefits and saved.get('request_state') == 'complete' and case.get('previsit_context'):
        try:
            fresh = datetime.fromisoformat(saved['checked_at']) + timedelta(hours=24) > datetime.now(timezone.utc)
        except (KeyError, TypeError, ValueError):
            fresh = False
        # Reopening completed recovery never submits a payer request. Reprice
        # current evidence; stale/unreadable evidence cannot authorize insurance.
        old_context = case['previsit_context']
        raw_key = case.get('previsit_raw_object_key')
        payload = None
        if fresh and raw_key:
            try:
                payload = json.loads(await run_in_threadpool(object_store.get_object_bytes, raw_key))
            except Exception:
                payload = None
        result = deepcopy(old_context['result'])
        result['benefits'] = _case_benefits(payload, inputs, source, case) if payload else _previsit_benefits({}, coverage, inputs['service_date'])
        if not payload:
            result.setdefault('issues', []).append('Saved payer evidence is stale or unavailable for insurance recalculation.')
        pricing_source = case.get('previsit_discovery', {}).get('pricing_source')
        await run_in_threadpool(_finish_previsit, result, case, inputs, source, payload, session,
                                old_context.get('payer_id') or '', pricing_source=pricing_source)
        case.update(await run_in_threadpool(_save, case, session, 'previsit-library-recalculated'))
        _recovery_event(case, session, 'discovery_cache', 'reused', reason='saved_result')
        return _previsit_result(case)
    discovery_ready = bool(stedi_discovery.request_body(inputs, provider))
    routing_failure = bool(source.get('payer') and coverage.get('member_id'))
    if routing_failure and not discovery_ready:
        detail = ('The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not run '
                  'because eCW did not provide the patient and provider details it requires. Coverage remains unverified, not proven inactive.')
    result.update(eligibility_error='payer_routing_unresolved' if routing_failure else 'coverage_identity_missing', eligibility_failure={
        'kind': 'payer_routing_unresolved' if routing_failure else 'coverage_identity_missing',
        'title': 'Insurance payer needs recovery' if routing_failure else 'eCW coverage needs recovery', 'detail': detail,
        'action': ('ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'
                   if routing_failure and discovery_ready else
                   'Insurance discovery could not start from the available eCW data.' if routing_failure else
                   'ClinicForce is reading accessible insurance cards and searching Stedi. A unique active patient match continues automatically to service-specific eligibility.'),
        'codes': [], 'attempted_at': _now()}, benefits=_previsit_benefits({}, coverage, inputs['service_date']))
    await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, '')
    recovery = case.get('previsit_discovery') or {}
    try:
        reusable = (not refresh_benefits and recovery.get('request_state') == 'complete'
                    and recovery.get('identity_binding') == _recovery_binding(inputs, source)
                    and datetime.fromisoformat(recovery['checked_at']) + timedelta(hours=24) > datetime.now(timezone.utc))
    except (KeyError, TypeError, ValueError):
        reusable = False
    if reusable:
        _recovery_event(case, session, 'discovery_cache', 'reused', reason='completed_search')
        case = await run_in_threadpool(_save, case, session, 'previsit-discovery-reused')
        return await _start_previsit_recovery(case, session, provider, background_tasks)
    case['previsit'] = {'request_state': 'rejected'}
    case['previsit_discovery'] = {'recovery_requested': discovery_ready,
                                  'request_state': 'not_requested' if discovery_ready else 'complete',
                                  'identity_binding': _recovery_binding(inputs, source),
                                  'stage': 'not_started' if discovery_ready else 'not_available',
                                  'reason_code': 'payer_route_unresolved' if discovery_ready else 'discovery_input_missing'}
    if payer_routes:
        case['previsit_discovery'].update(payer_routes=payer_routes, route_source='ecw')
    if not discovery_ready:
        case['previsit_discovery'].update(outcome='not_available', exhausted=True, checked_at=_now(), selection_error=detail)
    case = await run_in_threadpool(_save, case, session, 'previsit-coverage-recovery-requested')
    return await _start_previsit_recovery(case, session, provider, background_tasks)


@router.post("/appointments/previsit")
async def previsit_estimate(value: PrevisitInput, request: Request, background_tasks: BackgroundTasks = None) -> dict:
    """Estimate selected visit CPTs from current eligibility and clinic prices.

    Uses applicable saved eCW or Stedi benefits, verified network mappings and
    active clinic rate schedules. A disclosed clinic-price reference may support
    a provisional advance when no contracted allowance is available. Missing
    eligibility or secondary-benefit coordination does not imply zero liability.
    Explicit visit-linked eCW billing notes can supply a collection instruction
    or a fallback office copay; their source and timestamp accompany the result.
    They do not establish payer eligibility.
    Before any new Stedi request, patient-bound card OCR compares with eCW and
    fills only missing matched inquiry fields. Member/group conflicts or multiple
    card sets hold the request for review. card_check excludes image bytes.
    Self-pay uses recorded cash mode or the terminal coverage-recovery fallback
    and configured cash prices, without built-in clinic prices or plan-name
    benefit overrides. Earlier snapshots are unchanged.

    Missing policy/routing or failed identity checks start bounded background
    recovery through linked saved policies, card/OCR, uniquely routed eCW hints,
    discovery and, for eligible enrolled Original Medicare cases, MBI lookup.
    Applicable saved/discovered 271 benefits are reused before another request.
    A unique patient/address/active medical match continues automatically;
    multiple active policies never select a primary without evidence.
    Recovery may try up to two recorded prior addresses
    after completed no-match responses, never guessing identifiers or replaying
    an indeterminate submission. Pending results are read from /cases/{id}/previsit.
    recovery.coverage_status is VERIFIED, REVIEW_REQUIRED, or NOT_FOUND;
    NOT_FOUND describes a completed empty electronic search, not uninsured.
    Returned policies rejected by identity filtering remain REVIEW_REQUIRED.
    Labeled appointment-note payer/member candidates are retained without a
    Coverage record; they never supply an inferred subscriber or eligibility.
    An eCW patient no-match/ambiguity returns HTTP 409 with code
    ecw_patient_selection_required before payer requests; staff must confirm
    a patient through the lookup workflow.

    Terminal and unavailable searches do not imply recovery is still pending.
    recovery.exhausted remains false until every applicable stage has finished;
    recovery.steps records safe outcomes and unavailable prerequisites.
    Completed recovery without usable coverage, or completed insurance processing
    without a supported insurance amount, falls back to configured cash pricing
    unless staff explicitly retained insurance mode. Missing cash rates leave
    collection_guidance.amount_cents null; no price is invented.
    Office estimates reconcile corroborated Cigna base/suffix identifiers, use the
    rendering provider specialty and visit setting, scope named TMJ exclusions to
    linked clinical context, and recognize provider-specific network evidence.
    identity.reconciliation_rule and benefits.excluded_service_evidence_ids retain
    the applied rules; appointment_context.rendering_provider.specialty_evidence
    identifies an optional public NPPES fallback. Unknown ledger values remain
    unknown, and missing cards or contract rates do not alone block a verified
    fixed office copay.

    insurance_diagnosis separately reports identity, eligibility and pricing status,
    raw/retained/verified candidate counts, enrollment state and specific blockers.
    eCW Coverage.subscriber references are resolved to patient-linked identities.
    Dated payer evidence can automatically reconcile a stale subscriber/relationship
    using the same full member ID and corroborating patient, address or policy
    evidence. A one-edit surname difference additionally requires matching DOB,
    first name, full member ID and address. benefits.policy_match records applied
    policy differences; original eCW evidence remains unchanged. There is no
    policy-reconciliation input form or write endpoint. Neither API is treated
    as a roster of all members enrolled in a plan.
    Protected recovery.candidates retain partial subscriber/dependent identities;
    absent subscriber DOB stays null and does not discard a dependent's policy.
    Library rules are prepared automatically before pricing. library_pricing
    retains source processing summaries and missing dependencies. Candidates
    expose identity_status, group_number and dated coverage_evidence separately.
    Multiple policy/payer suggestions are optional; choosing one for display
    does not submit eligibility or postpone terminal automatic cash fallback.
    """
    session = await _session(request)
    library_documents = await run_in_threadpool(library_pricing.prepare, session['practice_id'])
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'), 'summary': row.get('extraction_summary')} for row in library_documents]
    collection_event('previsit_started', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     staff_id=(session.get('staff') or {}).get('id'), stage='appointment')
    if not ecw_backend.configured():
        collection_event('previsit_blocked', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_not_configured')
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, date.today().isoformat()),
            _ecw_practice_config(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
        collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='ecw_error')
        if exc.reason_code in {"no_match", "ambiguous_match"}:
            return JSONResponse({"detail": "Confirm the eCW patient to continue.", "code": "ecw_patient_selection_required"},
                                status_code=409, headers={"Cache-Control": "no-store"})
        raise HTTPException(502, str(exc)) from exc
    if source.get("appointment_id") != value.appointment_id:
        collection_event('previsit_failed', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='appointment', reason_code='appointment_mismatch')
        raise HTTPException(409, "The selected upcoming eClinicalWorks appointment could not be verified.")
    source['_request_ip_chain'] = _request_ip_chain(request)
    collection_event('previsit_appointment_loaded', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     stage='appointment', count=len(inputs.get('lines') or []), source='ecw')

    try:
        source['saved_eligibility'] = await asyncio.wait_for(
            ecw_backend.fetch_saved_eligibility(source, inputs['service_date']), timeout=6)
    except asyncio.TimeoutError:
        source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW check took too long to load. Continue with Stedi verification and retry eCW later.'}

    codes = _previsit_codes(inputs)
    result: dict[str, Any] = {"appointment_id": value.appointment_id, "service_date": inputs["service_date"],
        "payer": source.get("payer"), "plan": source.get("plan"), "visit_type": inputs.get("visit_type"),
        "reason": inputs.get("appointment_reason") or inputs.get("appointment_type") or inputs.get("reason"), "codes": codes, "benefits": None, "price_range": None,
        "suggestions": None, "issues": [], "checked_at": None,
        "ecw_saved_eligibility": {k: v for k, v in source['saved_eligibility'].items() if not k.startswith('_')}, "ecw_coverage_active": source.get('active_coverages', 0) > 0}
    coverage = _coverage_snapshot(inputs, source)

    async def incomplete_result(terminal_reason: str | None = None):
        if terminal_reason:
            for prior in prior_cases:
                if (prior.get('previsit_context', {}).get('source', {}).get('appointment_id') == value.appointment_id
                        and any(prior.get(key, {}).get('request_state') in {'pending', 'indeterminate'}
                                for key in ('previsit', 'previsit_discovery'))):
                    held = deepcopy(prior)
                    held['previsit_discovery'] = {**held.get('previsit_discovery', {}),
                        'request_state': 'indeterminate', 'outcome': None, 'exhausted': False}
                    return _previsit_result(held)
        binding = _visit_context_token(inputs, source)
        case = await run_in_threadpool(store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'],
            workflow_slot='commercial-unresolved:' + binding, **_scope(session))
        result['check_id'] = case['id']
        result['benefits'] = _previsit_benefits({}, coverage, inputs['service_date'])
        await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, '')
        if terminal_reason:
            case['previsit_discovery'] = {'request_state': 'complete', 'outcome': 'not_available',
                'exhausted': True, 'reason_code': terminal_reason, 'stage': 'not_available',
                'steps': {'payer_checks': terminal_reason}, 'checked_at': _now()}
            if terminal_reason == 'stedi_unavailable' and object_store.enabled():
                case['previsit_discovery']['identity_binding'] = _recovery_binding(inputs, source)
                await _recover_saved_policy(case, session, provider, allow_query=False)
        return _previsit_result(await run_in_threadpool(_save, case, session, 'previsit-awaiting-coverage'))

    check_from = ((source.get('coverage_period') or {}).get('start') or '')[:10] or None
    workflows = list(session['intake'].get('workflows', {}).values())
    prior_cases = []
    if workflows:
        prior_cases = await run_in_threadpool(store.get_cases, workflows, session['patient_id'], session['share_manifest_id'], **_scope(session))
        holds = [row['visit_review']['insurance_check_from'] for row in prior_cases
                 if row.get('previsit_context', {}).get('source', {}).get('appointment_id') == value.appointment_id
                 and row.get('visit_review', {}).get('insurance_check_from')]
        if holds:
            check_from = max([*holds, *([check_from] if check_from else [])])
    timing = _eligibility_timing(inputs['service_date'], check_from)
    if timing:
        result['eligibility_timing'] = timing
        collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='outside_check_window')
        return await incomplete_result()
    billing_note = ecw_billing.collection_note(source.get('appointment_fields') or [])
    if source.get('self_pay_recorded') or (billing_note or {}).get('kind') == 'self_pay':
        collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='self_pay_recorded')
        return await incomplete_result()

    try:
        card = await asyncio.wait_for(_read_card_hints(session, source.get('_patient_id')), timeout=8)
    except asyncio.TimeoutError:
        card = {'status': 'unavailable', 'fields': {}, 'count': 0}
    result['card_check'] = _compare_card_hints(card, inputs, source)
    coverage = _coverage_snapshot(inputs, source)
    result['payer'] = source.get('payer')
    collection_event('card_precheck', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     stage='before_eligibility', outcome=result['card_check']['status'], count=card.get('count', 0))
    if result['card_check']['status'] in {'conflict', 'multiple_cards'}:
        conflicting = result['card_check']['status'] == 'conflict'
        kind = 'card_conflict' if conflicting else 'multiple_cards'
        result.update(eligibility_error=kind, eligibility_failure={
            'kind': kind, 'title': 'Card and eCW policy differ' if conflicting else 'Multiple card sets need review',
            'detail': ('The card member ID or group differs from the eCW policy.' if conflicting
                       else 'More than one saved card set or more than two card pages were found.'),
            'action': ('Review the current card and correct the eCW policy before requesting eligibility.' if conflicting
                       else 'Confirm the current insurance card before requesting eligibility.'),
            'codes': [], 'attempted_at': _now()})
        result['issues'].append(result['eligibility_failure']['detail'])
        return await incomplete_result()

    # Evaluate saved eCW evidence before credentials, payer directory or any paid request.
    ecw_payload = ecw_benefits.payload(source['saved_eligibility'], source, inputs)
    if ecw_payload and object_store.enabled() and coverage['active_coverages'] == 1:
        connections = [row for row in commercial_store.list_connections(session['practice_id'])
                       if row.get('status') == 'staff_guidance_enabled'
                       and (str(source.get('payer') or '').casefold() in
                            {str(name).casefold() for name in [row.get('display_name'), *(row.get('aliases') or [])]}
                            or row.get('stedi_payer_id') in source.get('payer_identifiers', []))]
        ecw_payer_id = connections[0]['stedi_payer_id'] if len(connections) == 1 else ''
        binding = _visit_context_token(inputs, source)
        ecw_case = await run_in_threadpool(store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'],
            workflow_slot='commercial-ecw:' + binding, **_scope(session))
        candidate = deepcopy(result)
        candidate.update(check_id=ecw_case['id'], checked_at=source['saved_eligibility']['checked_at'],
            benefits=_previsit_benefits(ecw_payload, coverage, inputs['service_date'], _previsit_service_types(inputs)[0], inputs=inputs, source=source))
        await run_in_threadpool(_finish_previsit, candidate, ecw_case, inputs, source, ecw_payload, session, ecw_payer_id)
        guidance = candidate['collection_guidance']
        # Never skip a check just because an unsupported result has a numeric fallback.
        if (guidance['basis'] in {'office_copay', 'anticipated_services', 'patient_obligations'}
                and (guidance['basis'] != 'office_copay' or len(candidate['codes']) == 1)
                and not candidate.get('price_range', {}).get('service_issues')
                and all((line.get('benefits') or {}).get('copay_cents') is not None
                        or line.get('calculation') is not None for line in candidate['price_range']['lines'])
                and not any((line.get('benefits') or {}).get('source_errors') for line in candidate['price_range']['lines'])):
            raw_key = await run_in_threadpool(object_store.put_staff_document, session['practice_id'], ecw_case['id'],
                'previsit-ecw-eligibility.json', json.dumps(ecw_payload).encode(), 'application/json')
            ecw_case['previsit'] = {'request_state': 'complete', 'benefits': candidate['benefits'],
                'checked_at': candidate['checked_at'], 'benefit_source': 'eCW saved eligibility',
                'requested_service_types': _previsit_service_types(inputs)}
            ecw_case['previsit_raw_object_key'] = raw_key
            candidate['ecw_saved_eligibility']['message'] = 'Current eCW benefits support this advance estimate. No Stedi eligibility request was needed.'
            collection_event('eligibility_reused', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                             case_id=ecw_case['id'], stage='eligibility', source='ecw_saved')
            return _previsit_result(await run_in_threadpool(_save, ecw_case, session, 'previsit-ecw-benefits-reused'))

    rendering = source.get("rendering_provider") or {}
    billing = source.get("billing_provider") or {}
    rendering_npi = str(rendering.get("npi") or (inputs.get("rendering_provider") or {}).get("npi") or "")
    billing_npi = str(billing.get("npi") or "")
    organization_name = str(billing.get("name") or session.get("staff", {}).get("practice_name") or "")
    rendering_first, rendering_last = str(rendering.get("first_name") or ""), str(rendering.get("last_name") or "")
    provider = {"rendering_npi": rendering_npi, "rendering_first": rendering_first,
                "rendering_last": rendering_last, "billing_npi": billing_npi,
                "organization_name": organization_name}
    if not ((re.fullmatch(r"\d{10}", rendering_npi) and rendering_first and rendering_last) or
            (re.fullmatch(r"\d{10}", billing_npi) and organization_name)):
        result["issues"].append("eCW did not return a matched provider name and NPI needed for the payer check.")
        collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='provider_identity_missing')
        return await incomplete_result('provider_identity_missing')
    api_key = stedi_financial.credentials(session["practice_id"])
    if (os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or
        os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true" or
        not api_key or not object_store.enabled()):
        result["issues"].append("Live Stedi eligibility or protected response storage is not configured.")
        collection_event('previsit_deferred', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         stage='eligibility', reason_code='stedi_unavailable')
        return await incomplete_result('stedi_unavailable')
    service_day = date.fromisoformat(inputs["service_date"])
    usable_coverage = (coverage['active_coverages'] >= 1 and coverage['coverage_id'] and
                       coverage['relationship'] and all(coverage.get(key) for key in ('subscriber_first', 'subscriber_last')))
    if not usable_coverage or not source.get('payer'):
        detail = ('eCW did not return one usable primary policy with member and policyholder details for this visit.'
                  if not usable_coverage else 'eCW did not return an insurer name for the selected policy.')
        return await _begin_coverage_discovery(result, session, inputs, source, provider, background_tasks, detail,
                                               refresh_benefits=value.refresh_benefits)
    payer_routes = []
    try:
        payer = await stedi_payers.search_payer(str(source["payer"]), source.get("payer_identifiers") or [], api_key, plan=source.get("plan"), candidates=payer_routes)
    except (httpx.HTTPError, ValueError):
        return await _begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
            'The payer directory is unavailable. Continuing with other automatic coverage sources.',
            refresh_benefits=value.refresh_benefits)
    if not payer:
        return await _begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
            'The eCW payer name and ID did not identify one Stedi route. ClinicForce is checking the same policy through insurance discovery.',
            refresh_benefits=value.refresh_benefits, payer_routes=payer_routes)
    payer_id = payer["stediId"]
    requested_stc = _previsit_service_types(inputs)
    result['requested_service_types'] = requested_stc
    result["stedi_payer"] = {"name": payer.get("displayName"), "id": payer_id}
    try:
        body = stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], requested_stc, allow_demographic=not coverage.get('member_id'))
        basic_body = stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], requested_stc,
                                        include_identity_hints=False, allow_demographic=not coverage.get('member_id'))
    except commercial.CommercialError as exc:
        result["issues"].append(str(exc))
        return await incomplete_result()
    if service_day == date.today():
        body["encounter"].pop("dates", None)
        basic_body["encounter"].pop("dates", None)
    binding = hashlib.sha256(json.dumps({"coverage": coverage, "payer": payer_id,
        "provider": provider, "service_date": inputs["service_date"]}, sort_keys=True).encode()).hexdigest()
    slot = "commercial-previsit:" + hashlib.sha256(f"{value.appointment_id}:{binding}".encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding,
            {"source_mode": "commercial_previsit", "previsit": {"request_state": "not_requested"}},
            expected_intake_updated_at=session["intake"]["updated_at"], workflow_slot=slot, **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    result["check_id"] = case["id"]
    if (case.get('previsit_context') or {}).get('context_token') == _visit_context_token(inputs, source):
        result['suggestions'] = case['previsit_context']['result'].get('suggestions')
    elif inputs.get("reason") and not (inputs.get("visit_type") or visit_estimate.is_emg_visit(inputs)):
        result["suggestions"] = await run_in_threadpool(
            cpt_suggester.suggest_preliminary_cpt_codes,
            str(inputs["reason"])[:8000] + f"\nPatient visit status from eCW: {inputs.get('visit_type') or 'unknown'}.",
            source.get("recent_completed_appointments"))
    saved = case.get("previsit") or {}
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        return await _start_previsit_recovery(case, session, provider, background_tasks)
    if not value.refresh_benefits and saved.get('request_state') == 'rejected' and saved.get('identity_retry_version') == 1 and case.get('previsit_context'):
        old = case['previsit_context']['result']
        failure = old.get('eligibility_failure') or {}
        if failure.get('codes'):
            failure = {**commercial.eligibility_failure({'errors': [{'code': code} for code in failure['codes']]}), 'attempted_at': failure.get('attempted_at')}
        if {'65', '67', '71', '72', '73', '75'}.intersection(failure.get('codes', [])):
            result.update(eligibility_failure=failure, eligibility_error=old.get('eligibility_error'),
                          issues=[f"{failure['title']}. {failure['action']}"],
                          benefits=_previsit_benefits({}, coverage, inputs['service_date']))
            await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, payer_id)
            result['response_reference'] = old.get('response_reference')
            case = await run_in_threadpool(_save, case, session, 'previsit-rejection-reused')
            return await _start_previsit_recovery(case, session, provider, background_tasks)
    if not value.refresh_benefits and saved.get("request_state") == "complete":
        try:
            fresh = datetime.fromisoformat(saved["checked_at"]) + timedelta(hours=24) > datetime.now(timezone.utc)
        except (TypeError, ValueError, KeyError):
            fresh = False
        if visit_estimate.reconcile_eligibility(source.get('saved_eligibility') or {},
                saved.get('benefits') or {}, saved.get('checked_at'))['requires_review']:
            fresh = False
        if fresh and (saved.get("requested_service_types", [saved.get("requested_service_type", "98")]) == requested_stc):
            result["checked_at"] = saved.get("checked_at")
            result["benefits"] = dict(saved.get("benefits") or {})
            payload = None
            if case.get("previsit_raw_object_key"):
                try:
                    raw = await run_in_threadpool(object_store.get_object_bytes, case["previsit_raw_object_key"])
                    payload = json.loads(raw)
                    result["benefits"] = _case_benefits(payload, inputs, source, case)
                except Exception:
                    result["benefits"]["requires_review"] = True
                    result["benefits"].setdefault("review_reasons", []).append("Saved benefit evidence could not be verified.")
                    result["issues"].append("Saved benefit evidence could not be verified. Clinic staff should check the protected Stedi response before quoting a patient amount.")
            if payload is None:
                result['benefits']['requires_review'] = True
                result['benefits'].setdefault('review_reasons', []).append('Saved benefit evidence is unavailable for recalculation.')
            await run_in_threadpool(_finish_previsit, result, case, inputs, source, payload, session, payer_id)
            saved_case = await run_in_threadpool(_save, case, session, "previsit-recalculated")
            if (result['benefits'].get('identity') or {}).get('policy_differences'):
                saved_case.setdefault('previsit_discovery', {'request_state': 'not_requested', 'recovery_requested': True})
                return await _start_previsit_recovery(saved_case, session, provider, background_tasks)
            return _previsit_result(saved_case)
    if saved.get("request_state") in {"pending", "indeterminate"}:
        result["issues"].append("The payer request is pending or its outcome is unknown. Check Stedi activity before retrying.")
        collection_event('eligibility_held', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', status=saved.get('request_state'))
        return result
    if payer.get('transactionSupport', {}).get('eligibilityCheck') == 'ENROLLMENT_REQUIRED':
        npi = rendering_npi if rendering_first and rendering_last and rendering_npi else billing_npi
        enrollment = await stedi_payers.eligibility_enrollment(payer_id, npi, api_key)
        result['enrollment'] = enrollment
        if enrollment['status'] != 'LIVE':
            result['eligibility_failure'] = _enrollment_failure(enrollment)
            result['issues'].append(result['eligibility_failure']['action'])
            case['previsit'] = {'request_state': 'not_requested'}
            result['benefits'] = _previsit_benefits({}, coverage, inputs['service_date'])
            await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, payer_id)
            case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
            case = await run_in_threadpool(_save, case, session, 'previsit-enrollment-required')
            return await _start_previsit_recovery(case, session, provider, background_tasks)
    case.pop('previsit_discovery', None)
    case["previsit"] = {"request_state": "pending"}
    case = await run_in_threadpool(_save, case, session, "previsit-stedi-pending")
    collection_event('eligibility_requested', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case['id'], stage='eligibility', source='stedi', count=len(requested_stc))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await _post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (httpx.HTTPError, ValueError):
        case["previsit"] = {"request_state": "indeterminate"}
        await run_in_threadpool(_save, case, session, "previsit-stedi-indeterminate")
        collection_event('eligibility_indeterminate', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', reason_code='transport_or_parse_error')
        result["issues"].append("The Stedi request outcome is unknown. Clinic staff must check payer activity before retrying.")
        return result
    demographic_unresolved = bool(not coverage.get('member_id') and isinstance(payload, dict)
                                  and not stedi_discovery.candidate_identity(payload, inputs))
    if response.is_error or not isinstance(payload, dict) or str(payload.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED"} or commercial.eligibility_errors(payload) or demographic_unresolved:
        case["previsit"] = {"request_state": "rejected", "identity_retry_version": 1}
        if isinstance(payload, dict):
            case["previsit_raw_object_key"] = await run_in_threadpool(
                object_store.put_staff_document, session["practice_id"], case["id"],
                "previsit-stedi-error.json", json.dumps(payload).encode(), "application/json")
        failure = commercial.eligibility_failure(payload if isinstance(payload, dict) else {})
        if demographic_unresolved and not commercial.eligibility_errors(payload):
            failure = {'kind': 'member_identity_unresolved', 'title': 'Demographic lookup needs recovery',
                'detail': 'The payer did not return matching patient identity and a usable subscriber ID.',
                'action': 'Continuing through card and insurance discovery recovery.', 'codes': []}
        if not coverage.get('member_id'):
            case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        collection_event('eligibility_rejected', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                         case_id=case['id'], stage='eligibility', source='stedi', http_status=response.status_code,
                         reason_code=failure.get('kind'))
        failure['attempted_at'] = _now()
        result['eligibility_failure'] = failure
        result['eligibility_error'] = failure['kind']
        result['issues'].append(f"{failure['title']}. {failure['action']}")
        result['benefits'] = _previsit_benefits({}, coverage, inputs['service_date'])
        await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, payer_id)
        result['response_reference'] = payload.get('id') if isinstance(payload, dict) else None
        # Planned services remain editable; failed benefits cannot be verified away.
        case = await run_in_threadpool(_save, case, session, "previsit-stedi-rejected")
        return await _start_previsit_recovery(case, session, provider, background_tasks)
    raw_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case["id"],
                                      "previsit-stedi-271.json", json.dumps(payload).encode(), "application/json")
    benefits = _previsit_benefits(payload, coverage, inputs["service_date"], requested_stc[0], inputs=inputs, source=source)
    result["benefits"] = benefits
    result["checked_at"] = _now()
    collection_event('eligibility_received', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                     case_id=case['id'], stage='eligibility', source='stedi', http_status=response.status_code)
    await run_in_threadpool(_finish_previsit, result, case, inputs, source, payload, session, payer_id)
    case["previsit"] = {"request_state": "complete", "benefits": benefits,
                        "checked_at": result["checked_at"], "requested_service_types": requested_stc}
    case["previsit_raw_object_key"] = raw_key
    saved_case = await run_in_threadpool(_save, case, session, "previsit-stedi-complete")
    if (benefits.get('identity') or {}).get('policy_differences'):
        saved_case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        return await _start_previsit_recovery(saved_case, session, provider, background_tasks)
    if benefits.get('active') is False:
        saved_case['previsit_discovery'] = {'request_state': 'not_requested', 'recovery_requested': True}
        result.update(eligibility_error='coverage_inactive', eligibility_failure={
            'kind': 'coverage_inactive', 'title': 'Searching for current coverage',
            'detail': 'The known policy returned inactive coverage. Continuing through insurance recovery.',
            'action': 'Searching for active medical coverage for this appointment.', 'codes': []})
        return await _start_previsit_recovery(saved_case, session, provider, background_tasks)
    result["revision"] = saved_case.get("revision", 0)
    return result


@router.post("/cases/{case_id}/visit-review")
async def update_visit_review(case_id: str, value: VisitReviewInput, request: Request) -> dict:
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found. Check this visit first.')
    if case.get('revision', 0) != value.revision:
        raise HTTPException(409, 'This estimate changed in another session. Check this visit again before saving.')
    context = case['previsit_context']
    not_requested = case.get('previsit', {}).get('request_state') == 'not_requested'
    failed = not_requested or case.get('previsit', {}).get('request_state') == 'rejected'
    if case.get('previsit_discovery', {}).get('request_state') in {'pending', 'indeterminate'}:
        raise HTTPException(409, 'Coverage recovery is still in progress. Wait for the result before changing the estimate.')
    if failed and (value.verification or value.payments_received_cents is not None or value.identity_confirmation or value.product_mapping):
        raise HTTPException(409, 'Complete the eligibility check before verifying benefits or recording payments against an insurance estimate.')
    try:
        checked = datetime.now(timezone.utc) if failed else datetime.fromisoformat(case['previsit']['checked_at'])
        if not failed and checked + timedelta(hours=24) <= datetime.now(timezone.utc):
            raise ValueError('expired')
    except (KeyError, ValueError, TypeError):
        raise HTTPException(409, 'The benefit response is stale. Check this visit again before saving verification.') from None
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(_ecw_identity(session, context['inputs']['service_date']),
            _ecw_practice_config(session['practice_id']), context['source']['appointment_id'])
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    library_documents = await run_in_threadpool(library_pricing.prepare, session['practice_id'])
    session['library_pricing'] = [{'document_id': row['id'], 'label': row['label'], 'payer': row.get('payer'), 'summary': row.get('extraction_summary')} for row in library_documents]
    if value.context_token != context['context_token'] or _visit_context_token(inputs, source) != value.context_token:
        raise HTTPException(409, 'The appointment, coverage or services changed in eCW. Check the visit again.')
    saved_eligibility = (context.get('source') or {}).get('saved_eligibility')
    if saved_eligibility:
        source['saved_eligibility'] = saved_eligibility
        if saved_eligibility.get('checked_at') and datetime.fromisoformat(saved_eligibility['checked_at']) + timedelta(hours=24) <= datetime.now(timezone.utc):
            source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW verification is older than 24 hours. Use the current Stedi check.'}
    try:
        payload = {} if failed else json.loads(await run_in_threadpool(object_store.get_object_bytes, case['previsit_raw_object_key']))
    except Exception:
        raise HTTPException(503, 'Protected payer evidence could not be read. Retry before saving verification.') from None
    import copy
    result = copy.deepcopy(context['result'])
    review = copy.deepcopy(case.get('visit_review') or {'context_token': value.context_token})
    if value.identity_confirmation:
        current_identity = _case_benefits(payload, inputs, source, case).get('identity') or {}
        if not current_identity.get('can_confirm') or current_identity.get('binding') != value.identity_confirmation.binding:
            raise HTTPException(409, 'Patient identity evidence changed, or the birth date/member identity needs correction at its source.')
        review['identity_confirmation'] = {**value.identity_confirmation.model_dump(), 'verified_at': _now(),
                                           'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name')}
    available = {row['id']: row for row in result.get('available_codes', result['codes'])}
    if value.selected_ids is not None:
        if not value.selected_ids or len(set(value.selected_ids)) != len(value.selected_ids) or set(value.selected_ids) - available.keys():
            raise HTTPException(422, 'Select valid planned services from this visit.')
        visit_levels = [available[key] for key in value.selected_ids if available[key]['code'].isdigit() and 99202 <= int(available[key]['code']) <= 99215]
        if len(visit_levels) > 1:
            raise HTTPException(422, 'Select one office visit level; alternatives cannot be added together.')
        selection_issues = cpt_rules.service_selection_issues([available[key]['code'] for key in value.selected_ids])
        if selection_issues:
            raise HTTPException(422, selection_issues[0])
        # Pricing follows the clinical procedure sequence, never a user-entered
        # billing order. Preserve source order within each procedure group.
        group_order = [group['id'] for group in visit_estimate.service_plan(list(available.values()), inputs, source)['groups']]
        review['selected_ids'] = sorted(value.selected_ids, key=lambda key: (group_order.index(visit_estimate.clinical_group(available[key]['code'])), list(available).index(key)))
        review.pop('payments', None)
    if value.emg_required is not None:
        review['emg_required'] = value.emg_required
    if value.insurance_check_from is not None:
        review['insurance_check_from'] = value.insurance_check_from.isoformat()
    if value.payment_mode is not None:
        review['payment_mode'] = value.payment_mode
    if value.cash_selection is not None:
        if value.cash_selection:
            raise HTTPException(422, 'The active per-service cash schedule does not define packages or discounts.')
        review['cash_selection'] = value.cash_selection
        review.pop('payments', None)
    if value.confirmed_units is not None:
        if set(value.confirmed_units) - available.keys() or any(type(units) is not int or not 1 <= units <= 10000 for units in value.confirmed_units.values()):
            raise HTTPException(422, 'Confirm whole-number service units between 1 and 10,000.')
        previous = review.get('confirmed_units') or {}
        for key, units in value.confirmed_units.items():
            if previous.get(key, available[key].get('units')) != units:
                review.setdefault('lines', {}).pop(key, None)
        review['confirmed_units'] = {**previous, **value.confirmed_units}
        review.pop('payments', None)
    if value.verification:
        item = value.verification
        if item.line_id not in available or not item.valid_from <= date.fromisoformat(inputs['service_date']) <= item.valid_to:
            raise HTTPException(422, 'Verification must match a service and include the appointment date.')
        units = (review.get('confirmed_units') or {}).get(item.line_id, available[item.line_id].get('units'))
        try:
            units = commercial._integer(units, 'Service units')
        except commercial.CommercialError:
            units = None
        if units is None or item.units < units:
            raise HTTPException(422, 'Verification must cover the confirmed number of service units.')
        evidence = item.model_dump(mode='json')
        if item.kind == 'benefits':
            options = {row['id']: row for row in commercial.reported_stedi_options(payload, '98')}
            wanted = {'copay'} if item.cost_share == 'copay' else {'coinsurance'} if item.cost_share == 'coinsurance' else {'copay', 'coinsurance'}
            picked = [options[key] for key in item.selected_ids if key in options]
            if len(picked) != len(wanted) or {row['kind'] for row in picked} != wanted:
                raise HTTPException(422, 'Select exactly the returned payer terms needed for this cost-share method.')
            if len(set(item.selected_ids)) != len(item.selected_ids):
                raise HTTPException(422, 'Select each benefit term once.')
            if item.status != 'valid':
                raise HTTPException(422, 'Benefit applicability must be verified before selecting terms.')
        evidence.update(verified_at=_now(), verified_by=session['staff'].get('display_name'), actor_id=session['staff']['id'])
        review.setdefault('lines', {}).setdefault(item.line_id, {})[item.kind] = evidence
        review.pop('payments', None)
    if value.payments_received_cents is not None:
        if not value.payment_reference:
            raise HTTPException(422, 'Record the payment ledger reference, including when no payment was received.')
        review['payments'] = {'amount_cents': value.payments_received_cents, 'reference': value.payment_reference,
                              'verified_at': _now(), 'actor_id': session['staff']['id']}
    if value.product_mapping:
        _admin(session)
        item = value.product_mapping
        if not item.valid_from <= date.fromisoformat(inputs['service_date']) <= item.valid_to:
            raise HTTPException(422, 'The contract confirmation must include this appointment date.')
        products = commercial.plan_details(payload)['names']
        if not products:
            raise HTTPException(422, 'The payer did not return a product to map.')
        _, network, profile = _practice_path(session['practice_id'], inputs, {**source, 'payer_plan_names': []})
        await run_in_threadpool(commercial_store.save_product_mapping, session['practice_id'], profile['id'], products,
            {**item.model_dump(mode='json'), 'verified_at': _now(), 'actor_id': session['staff']['id'],
             'plan_scope': {key: commercial.plan_details(payload).get(key) for key in ('plan_number', 'group_number')},
             'network_id': network['id'], 'network_reference': network['evidence_reference']})
    case.setdefault('visit_review_history', []).append({'revision': value.revision, 'context_token': value.context_token, 'changes': value.model_dump(mode='json', exclude_none=True), 'actor_id': session['staff']['id'], 'verified_by': session['staff'].get('display_name'), 'verified_at': _now()})
    case['visit_review'] = review
    if not failed:
        result['issues'] = []
    result['benefits'] = _case_benefits({} if failed else payload, inputs, source, case)
    recovery = case.get('previsit_discovery') or {}
    pricing_source = recovery.get('pricing_source') if recovery.get('outcome') == 'recovered' else None
    await run_in_threadpool(_finish_previsit, result, case, inputs, source, None if failed else payload, session, context['payer_id'], pricing_source=pricing_source)
    if failed:
        result['response_reference'] = context['result'].get('response_reference')
    saved = await run_in_threadpool(_save, case, session, 'visit-evidence-verified')
    if value.identity_confirmation:
        await run_in_threadpool(staff_store.save_insurance_identity, session['patient_id'], session['practice_id'], review['identity_confirmation'])
    return _previsit_result(saved)


@router.get('/cases/{case_id}/financials')
async def previsit_financials(case_id: str, request: Request) -> dict:
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
    return _response(await saved_visit_financials(case, session['practice_id']))


async def saved_visit_financials(case: dict, practice_id: str) -> dict:
    context = case['previsit_context']
    inputs, source = context['inputs'], context['source']
    billing_npi = (source.get('billing_provider') or {}).get('npi')
    networks = await run_in_threadpool(commercial_store.list_networks, practice_id)
    tins = {row['tin'] for row in networks if row.get('status') == 'verified' and row.get('billing_npi') == billing_npi}
    identity = {**_coverage_snapshot(inputs, source), 'billing_npi': billing_npi,
                'tin': next(iter(tins)) if len(tins) == 1 else None}
    identity['claim_identifiers'] = await ecw_backend.fetch_claim_identifiers(source)
    return await stedi_financial.fetch_patient_financials(practice_id, identity)


@router.get("/history")
async def ecw_completed_appointments(request: Request) -> dict:
    """List prior eCW visits and a matching saved estimate without recalculating."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        visits = await ecw_backend.list_completed_appointments(
            _ecw_identity(session, date.today().isoformat()), _ecw_practice_config(session["practice_id"]),
        )
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    workflows = session["intake"].get("workflows", {})
    case_ids = [workflows.get("commercial"), *(workflows.get(_commercial_slot(visit["id"])) for visit in visits)]
    saved_cases = await run_in_threadpool(store.get_cases, case_ids, session["patient_id"], session["share_manifest_id"], **_scope(session))
    by_appointment = {str(((case.get("commercial") or {}).get("ecw") or {}).get("appointment_id") or ""): case
                      for case in saved_cases if case.get("source_mode") == "commercial"}
    return {"visits": [_past_visit_with_saved_estimate(visit, by_appointment.get(visit["id"])) for visit in visits]}


@router.post("/cases/from-ecw")
async def start_from_ecw(value: EcwStartInput, request: Request) -> Any:
    """Create a practice-scoped estimate case from the selected eCW appointment only."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(_ecw_identity(session, date.today().isoformat()), _ecw_practice_config(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    payer, network, profile = _practice_path(session["practice_id"], inputs, source)
    lines, line_tasks = _ecw_coded_lines(session["practice_id"], profile, network, inputs["service_date"], inputs)
    commercial_data = {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"],
                       "service_date": inputs["service_date"], "service_category": profile["service_category"], "lines": lines,
                       "evidence_id": None, "request_state": "not_requested"}
    conflicts = _ecw_conflicts({"commercial": commercial_data}, session, inputs, source)
    snapshot = _coverage_snapshot(inputs, source)
    workflow = {"source_mode": "commercial", "commercial": {**commercial_data, "ecw": _ecw_case_data(inputs, source, conflicts)},
                "ecw_coverage": snapshot,
                # An empty list means the automatic eCW → Stedi path may continue.
                # It is not a clinician confirmation step.
                "review_tasks": conflicts or line_tasks}
    binding = hashlib.sha256(json.dumps({"payer": payer["id"], "network": network["id"], "profile": profile["id"],
                                          "date": inputs["service_date"], "appointment": value.appointment_id,
                                          "coverage": snapshot, "lines": lines}, sort_keys=True).encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], workflow_slot=_commercial_slot(value.appointment_id), **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if case.get("estimate"):
        evidence_id = case.get("commercial", {}).get("evidence_id")
        evidence = commercial_store.evidence(session["practice_id"], evidence_id) if evidence_id else None
        try:
            current = bool(evidence and evidence.get("source") == "stedi" and evidence.get("expires_at")
                           and datetime.fromisoformat(evidence["expires_at"]) > datetime.now(timezone.utc))
        except ValueError:
            current = False
        if not current:
            case["estimate"] = None
            case["review_tasks"] = ["The saved Stedi response is stale. Start a new visit intake before estimating again."]
            case = await run_in_threadpool(_save, case, session, "stedi-evidence-expired")
    # Upgrade a case opened by the pre-autopilot screen.  This was a display-only
    # prompt, not a real condition requiring staff action.
    if case.get("review_tasks") == ["Ready to check Stedi eligibility using the eClinicalWorks-coded services."]:
        case["review_tasks"] = []
        case = await run_in_threadpool(_save, case, session, "automatic-path-ready")
    return _response(_public(case))


@router.post("/cases/{case_id}/ecw-intake")
async def read_ecw_appointment(case_id: str, request: Request) -> Any:
    """Read the scheduled appointment and fail closed on any card/eCW mismatch."""
    session = await _session(request)
    case = await run_in_threadpool(_load, case_id, session)
    data = case["commercial"]
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, data["service_date"]),
            _ecw_practice(session["practice_id"], data["payer_connection_id"], data["provider_network_id"]),
        )
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    conflicts = _ecw_conflicts(case, session, inputs, source)
    data["ecw"] = _ecw_case_data(inputs, source, conflicts)
    case["review_tasks"] = (conflicts or [
        "Confirm eClinicalWorks-suggested CPT/HCPCS codes and units; they are not automatically selected.",
        "Run Stedi eligibility or record a staff-reviewed saved payer response.",
    ])
    return _response(_public(await run_in_threadpool(_save, case, session, "ecw-appointment-read")))


@router.post("/ecw/saved-eligibility-probe")
async def saved_eligibility_probe(request: Request) -> dict:
    """Administrator-only, read-only proof of saved TriZetto/eCW evidence access."""
    session = await _session(request)
    _admin(session)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        # This only searches saved resources. It cannot create an eligibility transaction.
        return await ecw_backend.probe_saved_eligibility(_ecw_identity(session, date.today().isoformat()))
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/connections")
async def create_connection(value: ConnectionInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_connection(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/networks")
async def create_network(value: NetworkInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_network(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/profiles")
async def create_profile(value: ProfileInput, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.create_profile(session["practice_id"], value.model_dump())
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/rates")
async def import_rates(request: Request, payer_connection_id: str, source_type: str, source_reference: str, effective_from: str, effective_to: str | None = None, change_ceiling_bps: int = 0, file: UploadFile = File(...)) -> dict:
    session = await _session(request); _admin(session)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024: raise HTTPException(413, "Rate imports must be 20 MB or smaller.")
    try:
        rows = commercial.parse_rate_csv(content, file.filename or "rates.csv")
        payer = commercial_store.connection(session['practice_id'], payer_connection_id)
        if not payer or source_type not in commercial.RATE_SOURCE_TYPES or not source_reference.strip() or not 0 <= change_ceiling_bps <= 10000:
            raise commercial.CommercialError('Provide a valid clinic payer, source reference, source type and change ceiling.')
        from app.core import library_files
        if not object_store.enabled():
            raise HTTPException(503, 'Clinic document storage is unavailable.')
        await run_in_threadpool(library_files.save, session['staff'], content, file.filename or 'rates.csv',
            'text/csv', 'fee_schedule', file.filename or 'Contracted rates', payer['display_name'], effective_from, effective_to)
        return commercial_store.import_rates(session["practice_id"], payer_connection_id, source_type, source_reference, hashlib.sha256(content).hexdigest(), effective_from, effective_to, change_ceiling_bps, rows)
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/rates/{version_id}/activate")
async def activate_rates(version_id: str, request: Request) -> dict:
    session = await _session(request); _admin(session)
    try: return commercial_store.activate_rates(session["practice_id"], version_id)
    except commercial.CommercialError as exc: raise HTTPException(422, str(exc)) from exc


@router.post("/cases")
async def start_case(value: StartInput, request: Request) -> Any:
    session = await _session(request)
    try: date.fromisoformat(value.service_date)
    except ValueError: raise HTTPException(422, "Service date is invalid.") from None
    practice_id = session["practice_id"]
    payer = commercial_store.connection(practice_id, value.payer_connection_id)
    network, profile = _network(practice_id, value.provider_network_id), commercial_store.profile(practice_id, value.profile_id)
    if not payer or not profile or profile["payer_connection_id"] != payer["id"] or profile["provider_network_id"] != network["id"]:
        raise HTTPException(422, "Payer, provider network, and estimation profile must be a matching practice configuration.")
    if payer["category"] != "commercial_medical" or payer["status"] == "suspended" or network["status"] != "verified":
        raise HTTPException(409, "This payer/provider configuration is not eligible for commercial estimation.")
    workflow = {"source_mode": "commercial", "commercial": {"payer_connection_id": payer["id"], "provider_network_id": network["id"], "profile_id": profile["id"], "service_date": value.service_date, "service_category": profile["service_category"], "lines": [], "evidence_id": None, "request_state": "not_requested"}, "review_tasks": ["Confirm expected CPT/HCPCS codes and units before checking eligibility."]}
    binding = hashlib.sha256(f"commercial-v1|{payer['id']}|{network['id']}|{profile['id']}|{value.service_date}".encode()).hexdigest()
    try:
        case = await run_in_threadpool(store.create_case, session["patient_id"], "", binding, workflow, expected_intake_updated_at=session["intake"]["updated_at"], **_scope(session))
    except store.StaffCaseConflict as exc: raise HTTPException(409, str(exc)) from exc
    return _response(_public(case))


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> Any:
    return _response(_public(await run_in_threadpool(_load, case_id, await _session(request))))


@router.post("/cases/{case_id}/services")
async def set_services(case_id: str, value: ServicesInput, request: Request) -> Any:
    await _session(request)
    raise HTTPException(409, "This automatic estimate uses only services coded on the selected eClinicalWorks appointment.")


@router.post("/cases/{case_id}/evidence")
async def record_evidence(case_id: str, value: EvidenceInput, request: Request) -> Any:
    await _session(request)
    raise HTTPException(409, "Automatic insured estimates require a direct Stedi response for the selected eClinicalWorks visit.")


@router.post("/cases/{case_id}/eligibility")
async def stedi_eligibility(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if data.get("request_state") != "not_requested":
        raise HTTPException(409, "A Stedi eligibility request already exists for this case. Check its saved result before retrying.")
    if case.get("review_tasks"):
        raise HTTPException(409, "Resolve the eClinicalWorks coverage, coding, or contract review before a paid Stedi check.")
    if not data.get("lines") or any(line.get("allowed_cents") is None for line in data["lines"]):
        raise HTTPException(409, "Confirm the expected CPT/HCPCS codes, units, and matching clinic rates before a paid Stedi check.")
    if os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true":
        raise HTTPException(409, "Stedi eligibility is disabled. Set STEDI_EXECUTION_MODE=commercial_pilot and STEDI_LIVE_ELIGIBILITY_ENABLED=true only when this clinic is ready for paid production checks.")
    if not object_store.enabled():
        raise HTTPException(503, "Protected evidence storage must be configured before a live Stedi request.")
    try:
        current_source = await _assert_ecw_current(case, session)
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    api_key = stedi_financial.credentials(session["practice_id"])
    if not api_key: raise HTTPException(503, "Stedi production credentials are not configured on the backend.")
    payer, network = commercial_store.connection(session["practice_id"], data["payer_connection_id"]), _network(session["practice_id"], data["provider_network_id"])
    profile = commercial_store.profile(session["practice_id"], data["profile_id"])
    if not payer or not profile or payer["status"] != "staff_guidance_enabled" or network["status"] != "verified" or profile["status"] != "staff_guidance_enabled":
        raise HTTPException(409, "Payer, provider network, and estimation profile must be approved before Stedi eligibility.")
    if not network["effective_from"] <= data["service_date"] <= (network.get("effective_to") or "9999-12-31"):
        raise HTTPException(409, "Provider-network evidence is not effective for this appointment.")
    rules = payer["rules"]
    service_day = date.fromisoformat(data["service_date"])
    if rules.get("future_dos_current_month") and service_day > date.today() and (service_day.year, service_day.month) != (date.today().year, date.today().month):
        raise HTTPException(422, "This payer permits future eligibility dates only through the end of the current month.")
    stc = rules.get("service_type_code")
    if not isinstance(stc, str) or not stc: raise HTTPException(422, "Configure exactly one Stedi service-type code for this payer/profile before eligibility.")
    try:
        rendering = current_source.get("rendering_provider") or {}
        stedi_network = {**network, "rendering_first": rendering.get("first_name"),
                         "rendering_last": rendering.get("last_name")}
        body = stedi_request_body(payer, stedi_network, case["ecw_coverage"], data["service_date"], stc)
        basic_body = stedi_request_body(payer, stedi_network, case["ecw_coverage"], data["service_date"], stc,
                                        include_identity_hints=False)
    except commercial.CommercialError as exc:
        raise HTTPException(409, str(exc)) from exc
    data["request_state"] = "pending"; case = await run_in_threadpool(_save, case, session, "stedi-request-pending")
    started = time.monotonic()
    log_info("Commercial estimator", "Stedi eligibility started: case=%s service_lines=%d", case_id, len(data["lines"]))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await _post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (httpx.HTTPError, ValueError):
        log_warning("Commercial estimator", "Stedi eligibility indeterminate: case=%s duration_ms=%d", case_id, (time.monotonic() - started) * 1000)
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi request outcome is indeterminate; check Stedi activity before retrying."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-indeterminate")))
    if response.is_error or not isinstance(payload, dict):
        log_warning("Commercial estimator", "Stedi eligibility rejected: case=%s http_status=%s duration_ms=%d", case_id, response.status_code, (time.monotonic() - started) * 1000)
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi/payer did not return usable eligibility. Review payer response in the protected audit record."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-error")))
    log_info("Commercial estimator", "Stedi eligibility response: case=%s http_status=%s transaction_id=%s duration_ms=%d", case_id, response.status_code, payload.get("id"), (time.monotonic() - started) * 1000)
    raw_key = None
    if object_store.enabled(): raw_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case_id, "stedi-eligibility.json", json.dumps(payload).encode(), "application/json")
    # The normalizer only accepts Stedi's documented individual, remaining,
    # in-network benefit rows. Missing/ambiguous values stay review-required.
    benefits = commercial.normalize_stedi_271(payload, stc, True, case["ecw_coverage"].get("active_coverages", 0) > 1)
    returned_member = (payload.get("subscriber") or {}).get("memberId") if isinstance(payload.get("subscriber"), dict) else None
    if returned_member and re.sub(r"\W", "", str(returned_member)).casefold() != re.sub(r"\W", "", str(case["ecw_coverage"]["member_id"])).casefold():
        benefits["requires_review"] = True
        benefits["normalization_notes"].append("Stedi returned a different member ID.")
    evidence = EvidenceInput(source="stedi", response_reference=str(payload.get("id") or "Stedi eligibility response"), requested_dos=data["service_date"], payer_checked_at=_now(), expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(), benefits=benefits)
    _record_evidence(case, session, evidence.model_dump(), raw_key)
    return _response(_public(await run_in_threadpool(_save, case, session, "stedi-evidence-recorded")))


@router.post("/cases/{case_id}/calculate")
async def calculate(case_id: str, request: Request) -> Any:
    session = await _session(request); case = await run_in_threadpool(_load, case_id, session); data = case["commercial"]
    if not data.get("evidence_id"): raise HTTPException(409, "Record usable eligibility evidence before calculating.")
    if case.get("review_tasks"):
        raise HTTPException(409, "Resolve the returned authorization or referral requirement before calculating.")
    try:
        await _assert_ecw_current(case, session)
        practice_id = session["practice_id"]
        evidence = commercial_store.evidence(practice_id, data["evidence_id"])
        profile = commercial_store.profile(practice_id, data["profile_id"])
        payer = commercial_store.connection(practice_id, data["payer_connection_id"])
        network = _network(practice_id, data["provider_network_id"])
        if not evidence or not profile or not payer or evidence["source"] != "stedi":
            raise commercial.CommercialError("A direct, protected Stedi response is required for an automatic insured estimate.")
        if payer["status"] != "staff_guidance_enabled" or network["status"] != "verified" or profile["status"] != "staff_guidance_enabled":
            raise commercial.CommercialError("Payer, network, or estimation profile is no longer approved.")
        expiry = evidence.get("expires_at")
        if not expiry or datetime.fromisoformat(expiry) <= datetime.now(timezone.utc):
            raise commercial.CommercialError("Stedi benefits are stale. Start a new visit review.")
        expected = commercial.context_fingerprint(os.getenv("COMMERCIAL_EVIDENCE_HMAC_KEY", ""), _evidence_context(case, session, network))
        if evidence["case_id"] != case_id or evidence["context_hash"] != expected or evidence["requested_dos"] != data["service_date"]:
            raise commercial.CommercialError("Stedi evidence does not match this patient, policy, provider, and appointment.")
        case["estimate"] = commercial.estimate(data["lines"], evidence["normalized"], profile["rules"]); case["review_tasks"] = []
        log_info("Commercial estimator", "Estimate recalculated: case=%s estimate_available=true", case_id)
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-calculated")))
    except commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        log_warning("Commercial estimator", "Estimate requires review: case=%s", case_id)
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-review-required")))


@router.post('/cash-schedules')
async def upload_cash_schedule(request: Request, effective_from: date, file: UploadFile = File(...)) -> dict:
    """Import a draft per-service cash schedule for this clinic; activation is separate."""
    session = await _session(request); _admin(session)
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, 'Price schedules must be 20 MB or smaller.')
    try:
        from app.core import library_files
        filename = file.filename or ''
        await run_in_threadpool(practice_prices.read_rows, content, filename)
        if not object_store.enabled():
            raise HTTPException(503, 'Clinic document storage is unavailable.')
        content_type = {'.csv': 'text/csv', '.pdf': 'application/pdf', **library_files.OFFICE_TYPES}[Path(filename).suffix.lower()]
        if Path(filename).suffix.lower() in library_files.OFFICE_TYPES:
            try:
                await run_in_threadpool(library_files.office_preview, content, filename)
            except Exception:
                raise commercial.CommercialError('Upload a readable Word document without macros.') from None
        await run_in_threadpool(library_files.save, session['staff'], content, filename, content_type,
                               'self_pay', filename, '', effective_from.isoformat())
        return await run_in_threadpool(practice_prices.create, session['practice_id'], session['staff']['id'], content, filename, effective_from.isoformat())
    except commercial.CommercialError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/cash-schedules/{version_id}/activate')
async def activate_cash_schedule(version_id: str, request: Request) -> dict:
    """Activate a clinic-owned cash schedule used on or after its effective date."""
    session = await _session(request); _admin(session)
    try:
        return await run_in_threadpool(practice_prices.activate, session['practice_id'], version_id)
    except commercial.CommercialError as exc:
        raise HTTPException(404, str(exc)) from exc
