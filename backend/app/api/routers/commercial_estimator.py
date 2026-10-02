"""Staff-only Stedi commercial eligibility and estimate workflow.

This route is deliberately separate from the existing Availity workflow.
"""
from __future__ import annotations

from pathlib import Path

import hashlib
import asyncio
import json
import os
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import StrictInt, BaseModel, ConfigDict, Field, ValidationError

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store, staff_store
from app.insurance import identity as insurance_identity
from app.insurance import commercial, commercial_store, cpt_rules, cpt_suggester, ecw_backend, golden, stedi_discovery, stedi_payers, store, visit_estimate, nmct_cash, practice_prices, plan_benefits
from app.insurance import stedi_financial, collection_guidance, ecw_benefits


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
    cash_selection: str | None = Field(default=None, max_length=40)
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
    try:
        saved = store.save_case(case, session["patient_id"], session["share_manifest_id"], event, snapshot=snapshot, **_scope(session))
    except store.StaffCaseConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if not saved:
        raise HTTPException(404, "Commercial estimate case not found.")
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
                if row["payer_connection_id"] == payer["id"] and row["status"] == "verified"
                and row["billing_npi"] == billing_npi and row["rendering_npi"] == rendering_npi
                and row["location_id"] == str(source.get("location_id") or "")
                and row["place_of_service"] == str(inputs.get("place_of_service") or "")
                and (not row.get("plan_matcher") or (plan and re.sub(r"[^a-z0-9]", "", row["plan_matcher"].casefold()) == plan))
                and row["effective_from"] <= service_date and (not row.get("effective_to") or row["effective_to"] >= service_date)]
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
        if not product_names <= aliases and not plan_benefits.network_match(payer.get('stedi_payer_id', ''), product_names, networks[0].get('network_name', '')):
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
    if not planned:
        blockers.append("eClinicalWorks did not return a CPT/HCPCS-coded service linked to this scheduled visit. The planned service must be coded in eCW before a dollar estimate is possible.")
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
            ("subscriber_first", "subscriber_last", "subscriber_birth_date")):
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
    # NMCT is a neurology practice even when eCW omits PractitionerRole.
    specialist = practice_id == nmct_cash.PRACTICE_ID or any('neurolog' in role.lower()
        for role in (source.get('rendering_provider') or {}).get('roles', []))
    return ['BQ', 'BY', '98'] if specialist else ['BY', '98']


def _previsit_range(practice_id: str, inputs: dict, source: dict, payer_id: str,
                    codes: list[dict], benefits: dict, *, payload: dict | None = None,
                    review: dict | None = None) -> tuple[dict | None, list[str]]:
    review = review or {}
    options = bool(codes) and all(row.get('source') == 'Contract service option' for row in codes) and not review.get('selected_ids')
    alternatives = options or (bool(codes) and all(row.get("source") == "conditional office E/M alternative" for row in codes) and not review.get("selected_ids"))
    selected = [row for row in codes if not review.get("selected_ids") or row.get("id") in review['selected_ids']]
    office_types = _office_service_types(practice_id, source)
    rates, rules, issues = [], {'office_service_types': office_types, 'place_of_service': inputs.get('place_of_service')}, []
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


def _previsit_benefits(payload: dict, coverage: dict, service_date: str | None = None, service_type: str = "98") -> dict:
    benefits = commercial.normalize_stedi_271(payload, service_type, False, coverage["active_coverages"] > 1 or coverage.get("other_active_coverage", False), service_date=service_date)
    benefits['benefit_source'] = payload.get('benefit_source') or 'Stedi'
    comparison = insurance_identity.compare(payload, coverage)
    benefits['identity'] = comparison
    if comparison['status'] == 'review':
        benefits['requires_review'] = True
        benefits['review_reasons'].append('Confirm the member identity: the payer response differs from eCW.'
            if any(row['field'] == 'member_id' for row in comparison['differences']) else
            'Confirm patient identity: the payer-returned patient details differ from eCW.')
    return benefits


def _attach_previsit_pricing(result: dict, practice_id: str, inputs: dict, source: dict,
                             payer_id: str, benefits: dict, *, payload: dict | None = None,
                             review: dict | None = None) -> None:
    benefits["network_status"] = "unknown"
    connections = [row for row in commercial_store.list_connections(practice_id)
                   if row.get("stedi_payer_id") == payer_id and row.get("status") == "staff_guidance_enabled"]
    if len(connections) != 1:
        result["issues"].append("Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.")
        result['price_range'] = visit_estimate.price_lines(result['codes'], [], benefits, {'cost_share': 'payer_returned',
            'office_service_types': _office_service_types(practice_id, source),
            'place_of_service': inputs.get('place_of_service')}, payload=payload,
            service_date=inputs.get('service_date', ''), reviews=(review or {}).get('lines'),
            alternatives=not (review or {}).get('selected_ids') and bool(result['codes']) and all(row.get('source') == 'conditional office E/M alternative' for row in result['codes']))
        return
    connection = connections[0]
    try:
        _, network, _ = _previsit_practice_path(practice_id, {**inputs, "payer_id": connection["id"]}, source)
        benefits["network_status"] = "in_network"
        result['network_evidence'] = {key: network.get(key) for key in ('network_name', 'evidence_reference', 'effective_from', 'effective_to')}
        products = {re.sub(r'[^a-z0-9]', '', name.casefold()) for name in source.get('payer_plan_names', [])}
        if plan_benefits.network_match(payer_id, products, network.get('network_name', '')):
            result['network_evidence']['product_source'] = plan_benefits.FEP_NETWORK
    except HTTPException:
        pass
    result["price_range"], issues = _previsit_range(practice_id, inputs, source, connection["id"], result["codes"], benefits,
                                                  payload=payload, review=review)
    result["issues"].extend(issues)


def _visit_context_token(inputs: dict, source: dict) -> str:
    context = {'coverage': {k: v for k, v in _coverage_snapshot(inputs, source).items() if k != 'other_active_coverage'},
               'codes': _previsit_codes(inputs),
               'provider': inputs.get('rendering_provider'), 'billing': source.get('billing_provider'),
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
    connections = [row for row in commercial_store.list_connections(session['practice_id'])
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
    if nmct_cash.schedule(session['practice_id'], inputs['service_date']):
        available.extend(row for row in nmct_cash.catalog() if row['code'] not in {item['code'] for item in available})
    uploaded_cash = practice_prices.current(session['practice_id'], inputs['service_date']) if session['practice_id'] != nmct_cash.PRACTICE_ID else None
    if uploaded_cash:
        known_codes = {row['code'] for row in available}
        available.extend({'id': 'cash:' + row['code'], 'code': row['code'], 'description': row['description'], 'source': 'Practice cash schedule', 'units': 1, 'modifiers': []} for row in uploaded_cash['rows'] if row['code'] not in known_codes)
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
        cash_schedule = nmct_cash.schedule(session['practice_id'], inputs['service_date'])
        planning_source = {**source, 'spinal_tap_abbreviation': session['practice_id'] == nmct_cash.PRACTICE_ID, 'planning_cash_packages': cash_schedule['packages'] if cash_schedule else []}
        prior_plan = {'key': plan_key, 'plan': cpt_suggester.recommend_visit_plan(inputs, planning_source, available)}
        case['previsit_plan'] = prior_plan
    result['service_plan'] = {**prior_plan['plan'], 'selection_source': 'clinician' if review.get('selected_ids') else 'suggested'}
    auto_units = result['service_plan'].get('recommended_units') or {}
    units = {**auto_units, **(review.get('confirmed_units') or {})}
    result['available_codes'] = [{**row, 'units': units.get(row['id'], row.get('units'))} for row in available]
    result['payment_mode'] = review.get('payment_mode', 'cash' if source.get('self_pay_recorded') else 'insurance')
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
    cash_review = {'cash_selection': result['service_plan'].get('cash_selection', ''), **review}
    nmct_cash.apply(result, session['practice_id'], inputs['service_date'], cash_review)
    if uploaded_cash:
        practice_prices.apply(result, uploaded_cash)
    result['collection_guidance'] = collection_guidance.build(result, review)
    result.pop('emg_option', None)
    if inputs.get('visit_type') == 'new' and result['payment_mode'] == 'insurance':
        emg_rates, emg_rules = [], {'place_of_service': inputs.get('place_of_service'),
            'office_service_types': _office_service_types(session['practice_id'], source)}
        if (review.get('emg_required') or any(row['code'] in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS for row in result['codes'])) and len(connections) == 1:
            try:
                _, network, profile = _previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']}, exact_source)
                emg_rates = commercial_store.matching_rates(session['practice_id'], profile, network, inputs['service_date'], None)
                emg_rules.update(profile['rules'])
            except (HTTPException, commercial.CommercialError):
                pass
        result['emg_option'] = collection_guidance.emg_option(result, emg_rates, emg_rules, payload, inputs['service_date'], review)
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
    if not all(coverage.get(key) for key in ("subscriber_first", "subscriber_last", "subscriber_birth_date")):
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
                       *, include_identity_hints: bool = True) -> dict:
    needed = ("member_id", "relationship", "subscriber_first", "subscriber_last", "subscriber_birth_date",
              "patient_first", "patient_last", "patient_birth_date")
    if any(not coverage.get(key) for key in needed) or not coverage.get("active_coverages") or (coverage["active_coverages"] > 1 and not coverage.get("coverage_id")):
        raise commercial.CommercialError("eClinicalWorks did not return one usable primary coverage with member and policyholder details.")
    if network.get("rendering_first") and network.get("rendering_last") and network.get("rendering_npi"):
        provider = {"npi": network["rendering_npi"], "name": {"person": {
            "firstName": network["rendering_first"], "lastName": network["rendering_last"]}}}
    elif network.get("billing_npi") and network.get("organization_name"):
        provider = {"npi": network["billing_npi"], "name": {"organization": network["organization_name"]}}
    else:
        raise commercial.CommercialError("eClinicalWorks did not return one matching provider name and NPI for the Stedi inquiry.")
    body = {"payerId": payer["stedi_payer_id"], "provider": provider,
            "subscriber": {"memberId": coverage["member_id"], "dateOfBirth": coverage["subscriber_birth_date"],
                           "name": {"person": {"firstName": coverage["subscriber_first"], "lastName": coverage["subscriber_last"]}}},
            "encounter": {"dates": {"service": {"start": service_date}}, "services": [{"system": "STC", "value": code} for code in ([stc] if isinstance(stc, str) else stc)]}}
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


@router.get("/appointments")
async def ecw_appointments(request: Request) -> dict:
    """List the confirmed patient's scheduled visits without creating an eligibility check."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        values = await ecw_backend.list_appointments(_ecw_identity(session, date.today().isoformat()), _ecw_practice_config(session["practice_id"]))
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"appointments": values}


@router.post("/appointments/readiness")
async def ecw_appointment_readiness(value: EcwStartInput, request: Request) -> dict:
    """Read one selected visit and explain all known prerequisites without a Stedi transaction."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured for this deployment.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, date.today().isoformat()),
            _ecw_practice_config(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
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
    benefits = _previsit_benefits(payload, coverage, inputs['service_date'], _previsit_service_types(inputs)[0])
    if recovered:
        benefits['normalization_notes'].append('Coverage was selected from Stedi discovery for this estimate; eCW was not changed.')
    return benefits


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
    recovery = case.get('previsit_discovery') or {}
    if (result.get('eligibility_error') == 'payer_routing_unresolved'
            and recovery.get('request_state') in {'complete', 'rejected'}
            and recovery.get('outcome') != 'recovered'
            and (result.get('cash_quote') or {}).get('complete')
            and not (case.get('visit_review') or {}).get('payment_mode')):
        result['payment_mode'] = 'cash'
    result['collection_guidance'] = collection_guidance.build(result, case.get('visit_review'))
    return result


async def _recover_previsit(case: dict, session: dict, provider: dict) -> None:
    """Persist each stage before network I/O; never repeat a POST with an unknown outcome."""
    context = case['previsit_context']
    inputs, source, payer_id = context['inputs'], context['source'], context.get('payer_id')
    if _previsit_result(case).get('eligibility_timing'):
        return
    discovery = case['previsit_discovery']
    api_key = stedi_financial.credentials(session['practice_id'])
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(140, connect=5)) as client:
            headers = {'Authorization': api_key}
            payload = None
            if discovery.get('raw_object_key'):
                payload = json.loads(await run_in_threadpool(object_store.get_object_bytes, discovery['raw_object_key']))
            if payload is None:
                response = await client.post(stedi_discovery.URL, headers=headers,
                    json=stedi_discovery.request_body(inputs, provider))
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError('Invalid discovery response')
                if response.is_error:
                    payload['status'] = 'ERROR'
                discovery['discovery_id'] = payload.get('discoveryId')
                discovery['raw_object_key'] = await run_in_threadpool(object_store.put_staff_document,
                    session['practice_id'], case['id'], 'stedi-discovery.json', json.dumps(payload).encode(), 'application/json')
                case = await run_in_threadpool(_save, case, session, 'stedi-discovery-response')
                discovery = case['previsit_discovery']
            # Stedi may return 202. Poll that ID, never submit a second search.
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
            discovery['raw_object_key'] = await run_in_threadpool(object_store.put_staff_document,
                session['practice_id'], case['id'], 'stedi-discovery.json', json.dumps(payload).encode(), 'application/json')
            if payload.get('status') == 'PENDING':
                discovery.update(request_state='indeterminate', stage='awaiting_result', reason_code='discovery_pending')
                await run_in_threadpool(_save, case, session, 'stedi-discovery-awaiting-result')
                return
            items = payload.get('items') or []
            candidates = stedi_discovery.candidates(payload, inputs)
            if not payer_id:
                for row in candidates:
                    row['matches_ecw_policy'] = stedi_discovery.matches_ecw_policy(
                        items[row['index']], inputs, source)
            discovery.update(request_state='complete', candidates=candidates,
                             outcome='no_match' if not candidates else 'review_needed')
            if payload.get('status') != 'COMPLETE' or payload.get('errors'):
                discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                                 reason_code='discovery_request_rejected')
            # With no directory route, discovery must prove the same active eCW
            # policy before choosing a payer automatically.
            matching = [row for row in candidates if
                        (stedi_discovery.patient_matches(items[row['index']], inputs) if payer_id else
                         stedi_discovery.matches_ecw_policy(items[row['index']], inputs, context['source']))]
            if discovery['request_state'] != 'complete' or len(matching) != 1:
                if not payer_id:
                    if discovery['request_state'] != 'complete':
                        context['result']['eligibility_failure']['detail'] = (
                            'The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not '
                            'complete. Coverage remains unverified, not proven inactive.')
                    else:
                        reason = ('no_matching_ecw_policy' if not matching else 'multiple_matching_ecw_policies')
                        context['result']['eligibility_failure']['detail'] = (
                            'The eCW payer name and ID did not identify one Stedi route. Insurance discovery did not return '
                            + ('an active policy matching this patient, eCW member ID, and visit date.' if not matching else
                               'one unique active policy matching this patient, eCW member ID, and visit date.')
                            + ' Coverage remains unverified, not proven inactive.')
                        discovery.update(stage='candidate_match_review', reason_code=reason)
                    discovery['selection_error'] = context['result']['eligibility_failure']['detail']
                elif discovery['request_state'] == 'complete':
                    discovery.update(stage='candidate_review', reason_code='multiple_discovery_candidates')
                await run_in_threadpool(_save, case, session, 'stedi-discovery-finished')
                return
            candidate = matching[0]
            item = items[candidate['index']]
            payer = item.get('payer') or {}
            match = await stedi_payers.search_payer(payer.get('name') or payer.get('lastName') or '',
                [str(value) for value in (item.get('payerId'), payer.get('payorIdentification')) if value], api_key)
            same_policy = (stedi_discovery.patient_matches(item, inputs) if payer_id else
                           stedi_discovery.matches_ecw_policy(item, inputs, context['source']))
            if not match or (payer_id and match.get('stediId') != payer_id) or not same_policy:
                if not payer_id:
                    context['result']['eligibility_failure']['detail'] = (
                        'The eCW payer name and ID did not identify one Stedi route. The discovered policy did not uniquely confirm the same active patient, member ID, and visit date. Coverage remains unverified, not proven inactive.')
                    discovery['selection_error'] = context['result']['eligibility_failure']['detail']
                    discovery.update(stage='payer_route_review', reason_code='discovered_payer_not_routable')
                await run_in_threadpool(_save, case, session, 'stedi-discovery-match-needs-review')
                return
            discovery.update(request_state='pending', stage='eligibility', selected_candidate_id=candidate['id'])
            case = await run_in_threadpool(_save, case, session, 'stedi-recovered-member-pending')
            await _recheck_discovery_candidate(case, session, provider, discovery_payload=payload)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        case['previsit_discovery']['request_state'] = 'indeterminate'
        await run_in_threadpool(_save, case, session, 'stedi-discovery-indeterminate')


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
        candidates = stedi_discovery.candidates(payload, inputs)
        for row in candidates:
            row['matches_ecw_policy'] = stedi_discovery.matches_ecw_policy(items[row['index']], inputs, source)
        discovery.update(request_state='complete', candidates=candidates,
                         outcome='no_match' if not candidates else 'review_needed', checked_at=_now())
        if payload.get('status') != 'COMPLETE' or payload.get('errors'):
            discovery.update(request_state='rejected', outcome='rejected', stage='discovery_rejected',
                             reason_code='discovery_request_rejected')
        else:
            matching = [row for row in candidates if row['matches_ecw_policy']]
            reason = ('no_matching_ecw_policy' if not matching else 'multiple_matching_ecw_policies'
                      if len(matching) > 1 else 'matching_ecw_policy_ready')
            discovery.update(stage='candidate_match_review', reason_code=reason)
            if not matching:
                context['result']['eligibility_failure']['detail'] = (
                    'The eCW payer name and ID did not identify one Stedi route. Insurance discovery did not return '
                    'an active policy matching this patient, eCW member ID, and visit date. Coverage remains unverified, not proven inactive.')
            elif len(matching) > 1:
                context['result']['eligibility_failure']['detail'] = (
                    'The eCW payer name and ID did not identify one Stedi route. Insurance discovery returned multiple '
                    'active policies matching this patient, eCW member ID, and visit date. Coverage remains unverified, not proven inactive.')
            else:
                context['result']['eligibility_failure']['detail'] = (
                    'Insurance discovery found one active policy matching this patient, eCW member ID, and visit date. '
                    'Review and select it before ClinicForce sends a follow-up eligibility check.')
            discovery['selection_error'] = context['result']['eligibility_failure']['detail']
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
    coverage['relationship'] = '18' if identity['relationship'] == 'self' else '19'
    return coverage


def _discovery_pricing_source(source: dict, item: dict, payer: dict) -> dict:
    details = commercial.plan_details(item)
    return {**source, 'payer': payer.get('displayName') or (item.get('payer') or {}).get('name'),
            'payer_identifiers': [str((item.get('payer') or {}).get('payorIdentification') or '')],
            'plan': (details.get('names') or [None])[0], 'plan_details': source.get('plan_details') or {}}


async def _recheck_discovery_candidate(case: dict, session: dict, provider: dict, *, discovery_payload: dict | None = None) -> None:
    """Use a selected, persisted discovery candidate without altering eCW."""
    context, discovery = case['previsit_context'], case['previsit_discovery']
    inputs, source = context['inputs'], context['source']
    if _previsit_result(case).get('eligibility_timing'):
        return
    try:
        payload = discovery_payload or json.loads(await run_in_threadpool(object_store.get_object_bytes, discovery['raw_object_key']))
        candidate = next(row for row in discovery.get('candidates', []) if row['id'] == discovery.get('selected_candidate_id'))
        item = (payload.get('items') or [])[candidate['index']]
        coverage = _discovered_coverage(inputs, source, item)
        api_key = stedi_financial.credentials(session['practice_id'])
        payer_data = item.get('payer') or {}
        payer = await stedi_payers.search_payer(payer_data.get('name') or payer_data.get('lastName') or '',
            [str(value) for value in (item.get('payerId'), payer_data.get('payorIdentification')) if value], api_key)
        if not payer:
            raise ValueError('Stedi could not map the selected payer.')
        body = stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _previsit_service_types(inputs))
        basic_body = stedi_request_body({'stedi_payer_id': payer['stediId']}, provider, coverage, inputs['service_date'], _previsit_service_types(inputs),
                                        include_identity_hints=False)
        if inputs['service_date'] == date.today().isoformat():
            body['encounter'].pop('dates', None)
            basic_body['encounter'].pop('dates', None)
        async with httpx.AsyncClient(timeout=httpx.Timeout(35, connect=5)) as client:
            response, verified, _ = await _post_stedi_eligibility(
                client, {'Authorization': api_key, 'Content-Type': 'application/json'}, body, basic_body)
        if not isinstance(verified, dict):
            raise ValueError('Invalid eligibility response')
        discovery['eligibility_object_key'] = await run_in_threadpool(object_store.put_staff_document,
            session['practice_id'], case['id'], 'stedi-recovered-eligibility.json', json.dumps(verified).encode(), 'application/json')
        returned = (verified.get('subscriber') or {}).get('memberId')
        if response.is_error or str(verified.get('status') or '').upper() in {'ERROR', 'FAILED', 'REJECTED'} or commercial.eligibility_errors(verified) or not returned or stedi_discovery.normalized(returned) != stedi_discovery.normalized(coverage['member_id']):
            discovery.update(request_state='complete', outcome='review_needed', stage='eligibility_rejected', selection_error='The selected policy was not accepted by the payer. Select another returned policy or check the current card.')
            if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
                context['result']['eligibility_failure']['detail'] = (
                    'The eCW payer name and ID did not identify one Stedi route. Discovery matched the eCW policy, but the payer rejected its follow-up eligibility check. Coverage remains unverified, not proven inactive.')
                discovery['selection_error'] = context['result']['eligibility_failure']['detail']
            await run_in_threadpool(_save, case, session, 'stedi-discovery-selection-rejected')
            return
        pricing_source = _discovery_pricing_source(source, item, payer)
        discovery.update(request_state='complete', outcome='recovered', stage='complete', verified_coverage=coverage,
                         pricing_source=pricing_source, checked_at=_now())
        result = deepcopy(context['result'])
        result.pop('eligibility_failure', None); result.pop('eligibility_error', None)
        result.update(payer=pricing_source.get('payer'), plan=pricing_source.get('plan'), issues=[], checked_at=_now(),
                      benefits=_case_benefits(verified, inputs, source, case))
        await run_in_threadpool(_finish_previsit, result, case, inputs, source, verified, session, payer['stediId'], pricing_source=pricing_source)
        case['previsit'].update(request_state='complete', checked_at=result['checked_at'], benefits=result['benefits'], requested_service_types=_previsit_service_types(inputs))
        case['previsit_original_error_object_key'] = case.get('previsit_raw_object_key')
        case['previsit_raw_object_key'] = discovery['eligibility_object_key']
        await run_in_threadpool(_save, case, session, 'stedi-coverage-recovered')
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration, IndexError):
        discovery.update(request_state='complete', outcome='review_needed', stage='selection_error', reason_code='selected_policy_check_failed',
                         selection_error='The selected policy could not be checked. Select another returned policy or check the current card.')
        if context.get('payer_id') is None and context['result'].get('eligibility_error') == 'payer_routing_unresolved':
            context['result']['eligibility_failure']['detail'] = (
                'The eCW payer name and ID did not identify one Stedi route. Discovery found the matching policy, but its follow-up eligibility check could not be completed. Coverage remains unverified, not proven inactive.')
            discovery['selection_error'] = context['result']['eligibility_failure']['detail']
        await run_in_threadpool(_save, case, session, 'stedi-discovery-selection-error')


async def _start_previsit_recovery(case: dict, session: dict, provider: dict, background_tasks: BackgroundTasks | None) -> dict:
    result = case['previsit_context']['result']
    if _previsit_result(case).get('eligibility_timing'):
        return _previsit_result(case)
    recovery = case.get('previsit_discovery') or {}
    if recovery and stedi_discovery.public_status(recovery)['state'] == 'indeterminate':
        recovery['request_state'] = 'indeterminate'
    legacy_result = bool(
        not case['previsit_context'].get('payer_id')
        and recovery.get('request_state') == 'complete'
        and recovery.get('outcome') == 'review_needed'
        and recovery.get('raw_object_key')
        and any('matches_ecw_policy' not in row for row in recovery.get('candidates') or [])
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
        recovery.update(request_state='pending', started_at=recovery.get('started_at') or _now(),
                        context_token=case['previsit_context']['context_token'],
                        identity_binding=_recovery_binding(case['previsit_context']['inputs'], case['previsit_context']['source']))
        case['previsit_discovery'] = recovery
        case = await run_in_threadpool(_save, case, session, 'stedi-discovery-pending')
        background_tasks.add_task(_recover_previsit, case, session, provider)
    return _previsit_result(case)


@router.get('/cases/{case_id}/previsit')
async def saved_previsit(case_id: str, request: Request) -> dict:
    session = await _session(request)
    case = await run_in_threadpool(store.get_case, case_id, session['patient_id'], session['share_manifest_id'], **_scope(session))
    if not case or case.get('source_mode') != 'commercial_previsit' or not case.get('previsit_context'):
        raise HTTPException(404, 'Saved visit estimate not found.')
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
    if not case['previsit_context'].get('payer_id'):
        candidate = next(row for row in discovery['candidates'] if row['id'] == value.candidate_id)
        if not candidate.get('matches_ecw_policy'):
            raise HTTPException(409, 'The selected policy does not match the active eClinicalWorks member and visit date.')
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
                                    background_tasks: BackgroundTasks | None, detail: str, *, refresh_benefits: bool = False) -> dict:
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
        return _previsit_result(case)
    discovery_ready = bool(stedi_discovery.request_body(inputs, provider))
    routing_failure = bool(source.get('payer') and coverage.get('member_id'))
    if routing_failure and not discovery_ready:
        detail = ('The eCW payer name and ID did not identify one Stedi route. Insurance discovery could not run '
                  'because eCW did not provide the patient and provider details it requires. Coverage remains unverified, not proven inactive.')
    result.update(eligibility_error='payer_routing_unresolved' if routing_failure else 'coverage_identity_missing', eligibility_failure={
        'kind': 'payer_routing_unresolved' if routing_failure else 'coverage_identity_missing',
        'title': 'Insurance payer needs recovery' if routing_failure else 'eCW coverage needs recovery', 'detail': detail,
        'action': ('ClinicForce is searching Stedi using the patient details from eCW. It will use a policy automatically only when it uniquely matches the eCW patient, member ID, and visit date.'
                   if routing_failure and discovery_ready else
                   'Insurance discovery could not start from the available eCW data.' if routing_failure else
                   'ClinicForce is searching Stedi using the patient details from eCW. Select a returned policy before it is used for this estimate.'),
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
        return _previsit_result(await run_in_threadpool(_save, case, session, 'previsit-discovery-reused'))
    case['previsit'] = {'request_state': 'rejected'}
    case['previsit_discovery'] = {'recovery_requested': discovery_ready,
                                  'request_state': 'not_requested' if discovery_ready else 'complete',
                                  'identity_binding': _recovery_binding(inputs, source),
                                  'stage': 'not_started' if discovery_ready else 'not_available',
                                  'reason_code': 'payer_route_unresolved' if discovery_ready else 'discovery_input_missing'}
    if not discovery_ready:
        case['previsit_discovery'].update(outcome='not_available', checked_at=_now(), selection_error=detail)
    case = await run_in_threadpool(_save, case, session, 'previsit-coverage-recovery-requested')
    return await _start_previsit_recovery(case, session, provider, background_tasks)


@router.post("/appointments/previsit")
async def previsit_estimate(value: PrevisitInput, request: Request, background_tasks: BackgroundTasks = None) -> dict:
    """Check a future office visit before a final CPT level exists."""
    session = await _session(request)
    if not ecw_backend.configured():
        raise HTTPException(503, "The eClinicalWorks backend connection is not configured.")
    try:
        inputs, _, source = await ecw_backend.fetch_inputs(
            _ecw_identity(session, date.today().isoformat()),
            _ecw_practice_config(session["practice_id"]), value.appointment_id)
    except ecw_backend.EcwBackendError as exc:
        raise HTTPException(502, str(exc)) from exc
    if source.get("appointment_id") != value.appointment_id:
        raise HTTPException(409, "The selected upcoming eClinicalWorks appointment could not be verified.")

    try:
        source['saved_eligibility'] = await asyncio.wait_for(
            ecw_backend.fetch_saved_eligibility(source, inputs['service_date']), timeout=6)
    except asyncio.TimeoutError:
        source['saved_eligibility'] = {'status': 'check_with_stedi', 'message': 'The saved eCW check took too long to load. Continue with Stedi verification and retry eCW later.'}

    inputs['spinal_tap_abbreviation'] = session['practice_id'] == nmct_cash.PRACTICE_ID
    codes = _previsit_codes(inputs)
    result: dict[str, Any] = {"appointment_id": value.appointment_id, "service_date": inputs["service_date"],
        "payer": source.get("payer"), "plan": source.get("plan"), "visit_type": inputs.get("visit_type"),
        "reason": inputs.get("appointment_reason") or inputs.get("appointment_type") or inputs.get("reason"), "codes": codes, "benefits": None, "price_range": None,
        "suggestions": None, "issues": [], "checked_at": None,
        "ecw_saved_eligibility": {k: v for k, v in source['saved_eligibility'].items() if not k.startswith('_')}, "ecw_coverage_active": source.get('active_coverages', 0) > 0}
    coverage = _coverage_snapshot(inputs, source)

    async def incomplete_result():
        binding = _visit_context_token(inputs, source)
        case = await run_in_threadpool(store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'],
            workflow_slot='commercial-unresolved:' + binding, **_scope(session))
        result['check_id'] = case['id']
        result['benefits'] = _previsit_benefits({}, coverage, inputs['service_date'])
        await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, '')
        return _previsit_result(await run_in_threadpool(_save, case, session, 'previsit-awaiting-coverage'))

    check_from = ((source.get('coverage_period') or {}).get('start') or '')[:10] or None
    workflows = list(session['intake'].get('workflows', {}).values())
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
        return await incomplete_result()
    if source.get('self_pay_recorded'):
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
            benefits=_previsit_benefits(ecw_payload, coverage, inputs['service_date'], _previsit_service_types(inputs)[0]))
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
        return await incomplete_result()
    api_key = stedi_financial.credentials(session["practice_id"])
    if (os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot" or
        os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true" or
        not api_key or not object_store.enabled()):
        result["issues"].append("Live Stedi eligibility or protected response storage is not configured.")
        return await incomplete_result()
    service_day = date.fromisoformat(inputs["service_date"])
    usable_coverage = (coverage['active_coverages'] >= 1 and coverage['coverage_id'] and coverage['member_id'] and
                       coverage['relationship'] and all(coverage.get(key) for key in ('subscriber_first', 'subscriber_last', 'subscriber_birth_date')))
    if not usable_coverage or not source.get('payer'):
        detail = ('eCW did not return one usable primary policy with member and policyholder details for this visit.'
                  if not usable_coverage else 'eCW did not return an insurer name for the selected policy.')
        return await _begin_coverage_discovery(result, session, inputs, source, provider, background_tasks, detail,
                                               refresh_benefits=value.refresh_benefits)
    try:
        payer = await stedi_payers.search_payer(str(source["payer"]), source.get("payer_identifiers") or [], api_key, plan=source.get("plan"))
    except (httpx.HTTPError, ValueError):
        result["issues"].append("The Stedi payer directory is temporarily unavailable. Retry later.")
        return await incomplete_result()
    if not payer:
        return await _begin_coverage_discovery(result, session, inputs, source, provider, background_tasks,
            'The eCW payer name and ID did not identify one Stedi route. ClinicForce is checking the same policy through insurance discovery.',
            refresh_benefits=value.refresh_benefits)
    payer_id = payer["stediId"]
    requested_stc = _previsit_service_types(inputs)
    result['requested_service_types'] = requested_stc
    result["stedi_payer"] = {"name": payer.get("displayName"), "id": payer_id}
    try:
        body = stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], requested_stc)
        basic_body = stedi_request_body({"stedi_payer_id": payer_id}, provider, coverage, inputs["service_date"], requested_stc,
                                        include_identity_hints=False)
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
            return _previsit_result(saved_case)
    if saved.get("request_state") in {"pending", "indeterminate"}:
        result["issues"].append("The payer request is pending or its outcome is unknown. Check Stedi activity before retrying.")
        return result
    if payer.get('transactionSupport', {}).get('eligibilityCheck') == 'ENROLLMENT_REQUIRED':
        npi = rendering_npi if rendering_first and rendering_last and rendering_npi else billing_npi
        try:
            enrolled = await stedi_payers.eligibility_enrolled(payer_id, npi, api_key)
        except (httpx.HTTPError, ValueError):
            enrolled = False
        if not enrolled:
            result['eligibility_failure'] = {'kind': 'provider_enrollment', 'title': 'Provider eligibility enrollment required',
                'detail': 'A live Stedi eligibility enrollment was not verified for this payer and provider NPI.',
                'action': 'The clinic administrator must verify and complete the payer eligibility enrollment in Stedi. No paid eligibility request was submitted.', 'codes': []}
            result['issues'].append(result['eligibility_failure']['action'])
            case['previsit'] = {'request_state': 'not_requested'}
            result['benefits'] = _previsit_benefits({}, coverage, inputs['service_date'])
            await run_in_threadpool(_finish_previsit, result, case, inputs, source, None, session, payer_id)
            return _previsit_result(await run_in_threadpool(_save, case, session, 'previsit-enrollment-required'))
    case.pop('previsit_discovery', None)
    case["previsit"] = {"request_state": "pending"}
    case = await run_in_threadpool(_save, case, session, "previsit-stedi-pending")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await _post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (httpx.HTTPError, ValueError):
        case["previsit"] = {"request_state": "indeterminate"}
        await run_in_threadpool(_save, case, session, "previsit-stedi-indeterminate")
        result["issues"].append("The Stedi request outcome is unknown. Clinic staff must check payer activity before retrying.")
        return result
    if response.is_error or not isinstance(payload, dict) or str(payload.get("status", "")).upper() in {"ERROR", "FAILED", "REJECTED"} or commercial.eligibility_errors(payload):
        case["previsit"] = {"request_state": "rejected", "identity_retry_version": 1}
        if isinstance(payload, dict):
            case["previsit_raw_object_key"] = await run_in_threadpool(
                object_store.put_staff_document, session["practice_id"], case["id"],
                "previsit-stedi-error.json", json.dumps(payload).encode(), "application/json")
        failure = commercial.eligibility_failure(payload if isinstance(payload, dict) else {})
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
    benefits = _previsit_benefits(payload, coverage, inputs["service_date"], requested_stc[0])
    result["benefits"] = benefits
    result["checked_at"] = _now()
    await run_in_threadpool(_finish_previsit, result, case, inputs, source, payload, session, payer_id)
    case["previsit"] = {"request_state": "complete", "benefits": benefits,
                        "checked_at": result["checked_at"], "requested_service_types": requested_stc}
    case["previsit_raw_object_key"] = raw_key
    saved_case = await run_in_threadpool(_save, case, session, "previsit-stedi-complete")
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
        config = nmct_cash.schedule(session['practice_id'], inputs['service_date'])
        if not config or value.cash_selection not in {'', 'friends_family', *(row['id'] for row in config['packages'])}:
            raise HTTPException(422, 'Select a cash option from this clinic’s current schedule.')
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
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5)) as client:
            response, payload, _ = await _post_stedi_eligibility(
                client, {"Authorization": api_key, "Content-Type": "application/json"}, body, basic_body)
    except (httpx.HTTPError, ValueError):
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi request outcome is indeterminate; check Stedi activity before retrying."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-indeterminate")))
    if response.is_error or not isinstance(payload, dict):
        case = await run_in_threadpool(_load, case_id, session); case["commercial"]["request_state"] = "review"; case["review_tasks"] = ["Stedi/payer did not return usable eligibility. Review payer response in the protected audit record."]
        return _response(_public(await run_in_threadpool(_save, case, session, "stedi-error")))
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
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-calculated")))
    except commercial.CommercialError as exc:
        case["estimate"] = None; case["review_tasks"] = [str(exc)]
        return _response(_public(await run_in_threadpool(_save, case, session, "estimate-review-required")))


@router.post('/cash-schedules')
async def upload_cash_schedule(request: Request, effective_from: date, file: UploadFile = File(...)) -> dict:
    session = await _session(request); _admin(session)
    if session['practice_id'] == nmct_cash.PRACTICE_ID:
        raise HTTPException(409, 'NMCT uses its supplied, versioned Word schedule including clinical packages.')
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
    session = await _session(request); _admin(session)
    if session['practice_id'] == nmct_cash.PRACTICE_ID:
        raise HTTPException(409, 'NMCT uses its supplied Word schedule.')
    try:
        return await run_in_threadpool(practice_prices.activate, session['practice_id'], version_id)
    except commercial.CommercialError as exc:
        raise HTTPException(404, str(exc)) from exc
