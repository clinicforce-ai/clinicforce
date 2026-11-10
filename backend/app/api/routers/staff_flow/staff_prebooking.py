"""Staff-only eligibility screening before a clinic patient or eCW visit exists."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.routers import staff as _staff
from app.api.schemas.staff import (PrebookingEligibility, PrebookingOptions, PrebookingResult,
                                   PrebookingPlanRequest, PrebookingEstimateRequest,
                                   PrebookingPlanResponse, PrebookingContextsResponse,
                                   PrebookingEstimateResponse)
from app.core import clinic_store
from app.insurance import (commercial, commercial_store, cpt_suggester, identity,
                           network_participation, prebooking, stedi, stedi_financial,
                           stedi_payers, visit_estimate)


router = _staff.router


def _today() -> str:
    try:
        return datetime.now(ZoneInfo(os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date().isoformat()
    except ZoneInfoNotFoundError:
        raise HTTPException(503, "The clinic timezone is not configured correctly.") from None


@router.get("/prebooking-eligibility/options", response_model=PrebookingOptions, description="Search Stedi's current payer directory by insurance company name and return ranked medical eligibility suggestions, including close matches. Staff select the exact insurer; no patient or appointment is created.")
async def prebooking_options(request: Request, query: str = Query(min_length=2, max_length=100)):
    staff = await _staff.require_staff(request)
    api_key = stedi_financial.credentials(staff["practice_id"])
    if not api_key:
        raise HTTPException(503, "The clinic's Stedi credential is not configured.")
    try:
        payers = await stedi_payers.payer_suggestions(query, api_key)
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "Insurance companies are temporarily unavailable.") from None
    return JSONResponse({"payers": payers}, headers=_staff.NO_STORE)


@router.post("/prebooking-eligibility/check", response_model=PrebookingResult, description="Check today's general medical coverage (STC 30) through Stedi using prospective patient name, birth date, selected insurer and optional member ID. The clinic's saved name and NPI supply the provider identity. A missing member ID or dependent identity may prevent a conclusive payer match. Returns active_today, inactive_today, or needs_review. No patient, intake, case, appointment, or saved eligibility result is created; repeat the check after booking for the visit date.")
async def prebooking_check(value: PrebookingEligibility, request: Request):
    staff = await _staff.require_staff(request)
    if (os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot"
            or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true"):
        raise HTTPException(503, "Live Stedi eligibility is not enabled for this clinic deployment.")
    api_key = stedi_financial.credentials(staff["practice_id"])
    if not api_key:
        raise HTTPException(503, "The clinic's Stedi credential is not configured.")
    today = _today()
    provider = await run_in_threadpool(clinic_store.get_for_practice, staff["practice_id"], staff["practice_name"])
    if not clinic_store.valid_npi(str(provider.get("npi") or "")) or not provider.get("name"):
        raise HTTPException(503, "The clinic needs a valid saved NPI and name before checking coverage.")
    try:
        payer = await stedi_payers.search_payer("", [value.payer_id], api_key)
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "The insurance company could not be verified right now.") from None
    if not payer or payer.get("stediId") != value.payer_id:
        raise HTTPException(422, "Choose an insurance company from the search results.")
    if stedi_payers.enrollment_required(payer) and not await stedi_payers.eligibility_enrolled(value.payer_id, provider["npi"], api_key):
        raise HTTPException(503, "This insurer requires a live clinic enrollment before coverage can be checked.")
    details = value.model_dump(mode="json")
    body = prebooking.request_body(details, payer, provider)
    response = await stedi.Client(stedi.Settings("production", api_key)).check(body)
    checked_at = datetime.now(timezone.utc).isoformat()
    payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
    check_id = payload.get("id") if isinstance(payload.get("id"), str) and re.fullmatch(r"ec_[A-Za-z0-9-]{1,80}", payload["id"]) else None
    result = (prebooking.evaluate(payload, details, today) if response.get("outcome") == "complete"
              else {"status": "needs_review", "coverage_start": None, "coverage_end": None,
                    "message": "The payer check was not completed. Review Stedi activity before sending another inquiry."})
    return JSONResponse({**result, "checked_at": checked_at, "checked_on": today,
                         "payer": payer["displayName"], "check_id": check_id}, headers=_staff.NO_STORE)


def _contexts(practice_id: str) -> list[dict]:
    """Offer clinic provider/location choices without claiming any payer network tier."""
    seen = set()
    choices = []
    for row in commercial_store.list_networks(practice_id):
        if row.get("status") != "verified":
            continue
        key = (row.get("billing_npi"), row.get("rendering_npi"),
               row.get("location_id"), row.get("place_of_service"))
        if key in seen:
            continue
        seen.add(key)
        choices.append({"id": row["id"], "label": f"{row.get('organization_name') or 'Clinic'} · "
                        f"location {row['location_id']} · provider {row['rendering_npi']} · POS {row['place_of_service']}"})
    return choices


@router.get("/prebooking-estimate/contexts", response_model=PrebookingContextsResponse, description="List this clinic's configured provider/location choices for an unsaved prospective estimate. A listed context does not establish participation in a patient's plan.")
async def prebooking_estimate_contexts(request: Request):
    staff = await _staff.require_staff(request)
    choices = await run_in_threadpool(_contexts, staff["practice_id"])
    return JSONResponse({"contexts": choices}, headers=_staff.NO_STORE)


@router.post("/prebooking-estimate/plan", response_model=PrebookingPlanResponse, description="Suggest possible CPT/HCPCS services from a prospective visit reason, for clinician review. This makes no Stedi request and does not create a patient, case or appointment. Suggestions are never an order or a price.")
async def prebooking_estimate_plan(value: PrebookingPlanRequest, request: Request):
    await _staff.require_staff(request)
    suggestions = await run_in_threadpool(cpt_suggester.suggest_preliminary_cpt_codes,
                                           value.reason + f"\nPatient visit status: {value.visit_type}.")
    return JSONResponse(prebooking.plan(value.reason, value.visit_type, suggestions), headers=_staff.NO_STORE)


def _terms(benefits: dict) -> dict:
    return {key: benefits.get(key) for key in ("copay_cents", "coinsurance_percent",
            "deductible_annual_cents", "deductible_remaining_cents", "oop_annual_cents",
            "oop_remaining_cents", "deductible_applicability", "authorization_signal",
            "referral_signal", "resolved_service_type")}


def _estimate_from_payer(practice_id: str, details: dict, context: dict | None, payer: dict,
                         provider: dict, payload: dict, service_date: str, lines: list[dict]) -> dict:
    """Adapt a prospective scenario to the shared visit benefit/network/pricing rules."""
    from app.api.routers import commercial_estimator

    coverage = {"patient_first": details["given_name"], "patient_last": details["family_name"],
                "patient_birth_date": details["birth_date"], "member_id": details["member_id"],
                "subscriber_first": details.get("subscriber_given_name") or details["given_name"],
                "subscriber_last": details.get("subscriber_family_name") or details["family_name"],
                "relationship": "" if details.get("relationship") == "dependent" else "self",
                "payer_id": payer["stediId"]}
    comparison = identity.compare(payload, coverage)
    benefits = commercial.normalize_stedi_271(payload, "30", False, service_date=service_date,
        provider_npi=provider["npi"], place_of_service=(context or {}).get("place_of_service"),
        visit_context={"reason": details["reason"]})
    benefits["identity"] = comparison
    benefits["benefit_source"] = "Stedi"
    if ((not context or context["rendering_npi"] != provider["npi"])
            and benefits.get("network_source") == "payer_participation_message"):
        benefits["network_status"] = "unknown"
        benefits["network_source"] = None
    product = benefits.get("plan_details") or {}
    names = product.get("names") or []
    plan = names[0] if len(names) == 1 else ""
    source = {"billing_provider": {"npi": (context or {}).get("billing_npi")},
              "rendering_provider": {"npi": (context or {}).get("rendering_npi")},
              "location_id": (context or {}).get("location_id"),
              "plan": plan, "payer_plan_names": names,
              "payer_plan_bound": bool(plan and identity.patient_benefits_bound(comparison)),
              "payer_network_names": product.get("network_names") or []}
    inputs = {"reason": details["reason"], "visit_type": details["visit_type"],
              "service_date": service_date, "place_of_service": (context or {}).get("place_of_service"),
              "rendering_provider": {"npi": (context or {}).get("rendering_npi")},
              "lines": [], "payer_id": payer["stediId"]}
    result = {"payer": payer["displayName"], "codes": lines, "issues": []}
    commercial_estimator._attach_previsit_pricing(result, practice_id, inputs, source, payer["stediId"], benefits,
        payload=payload, review={"selected_ids": [row["id"] for row in lines]})
    price = result.get("price_range") or {}
    network = benefits.get("network_status") or "unknown"
    verified = bool(context) and (network_participation.verified_in_network(benefits)
                                  or network_participation.verified_out_of_network(benefits))
    priced_rows = {row["code"]: row for row in price.get("lines") or []}
    contract_rates = len(priced_rows) == len(lines) and all(
        row.get("allowed_basis") == "clinic_contract"
        and (row.get("rate_resolution") or {}).get("status") == "RESOLVED"
        for row in priced_rows.values())
    totals = (price.get("allowed_min_cents"), price.get("allowed_max_cents"),
              price.get("plan_min_cents"), price.get("plan_max_cents"),
              price.get("patient_min_cents"), price.get("patient_max_cents"))
    reconciled = (all(type(amount) is int and amount >= 0 for amount in totals)
                  and totals[0] == totals[1] and totals[2] == totals[3]
                  and totals[4] == totals[5] and totals[0] == totals[2] + totals[4])
    ready = (benefits.get("active") is True and identity.patient_benefits_bound(comparison)
             and network_participation.is_nmct_practice(practice_id)
             and verified and network == "in_network" and bool(plan)
             and contract_rates and reconciled and price.get("complete") is True)
    priced_lines = []
    for selected in lines:
        row = priced_rows.get(selected["code"]) or {}
        priced_lines.append({"code": selected["code"], "units": selected["units"],
                             "role": visit_estimate.clinical_group(selected["code"]),
                             "service_type": visit_estimate.service_type(selected["code"], {}),
                             "allowed_cents": row.get("allowed_cents") if ready else None,
                             "patient_cents": (row.get("calculation") or {}).get("patient_cents") if ready else None,
                             "rate_source": row.get("rate_source") if ready else None,
                             "benefits": _terms(row.get("benefits") or {}),
                             "issues": row.get("issues") or [], "conditions": row.get("conditions") or []})
    issues = list(dict.fromkeys([*result["issues"], *(price.get("service_issues") or []),
        *(reason for row in priced_lines for reason in row["issues"])]))
    if not identity.patient_benefits_bound(comparison):
        issues.insert(0, "The payer did not bind these benefits to the entered member and patient. Confirm the member ID and identity.")
    if not plan:
        issues.insert(0, "The payer did not identify one plan product for this person; verify the exact product before pricing.")
    if not verified:
        issues.insert(0, "Choose a clinic provider and location, then confirm participation for this member's exact plan.")
    if not network_participation.is_nmct_practice(practice_id):
        issues.insert(0, "Priced prebooking estimates currently support NMCT contracted plans only.")
    elif network != "in_network":
        issues.insert(0, "An NMCT in-network contract price is not established for this visit.")
    elif not contract_rates or not reconciled:
        issues.insert(0, "Verify contract rates and benefit calculations for every selected service before showing a total.")
    return {"network_status": network if verified else "unknown",
            "network_evidence": result.get("network_evidence"), "plan_name": plan or None,
            "benefits": _terms(benefits), "lines": priced_lines,
            "allowed_total_cents": totals[0] if ready else None,
            "plan_total_cents": totals[2] if ready else None,
            "patient_min_cents": price.get("patient_min_cents") if ready else None,
            "patient_max_cents": price.get("patient_max_cents") if ready else None,
            "estimate_status": "conditional" if ready and price.get("conditional") else "priced" if ready else "needs_review",
            "issues": issues[:20]}


@router.post("/prebooking-estimate/check", response_model=PrebookingEstimateResponse, description="Run an unsaved Stedi 270/271 check for staff-confirmed prospective services and date. Return coverage and payer terms; provide NMCT contract-allowed visit cost, estimated plan share, patient share and per-service rate sources only when member, plan, provider, service, in-network contract rate and totals agree. Unknown values are null. Future-date checks can be unsupported by a payer and never fall back silently to today. No patient, case or appointment is created; recheck after booking.")
async def prebooking_estimate_check(value: PrebookingEstimateRequest, request: Request):
    staff = await _staff.require_staff(request)
    if (os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot"
            or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true"):
        raise HTTPException(503, "Live Stedi eligibility is not enabled for this clinic deployment.")
    api_key = stedi_financial.credentials(staff["practice_id"])
    if not api_key:
        raise HTTPException(503, "The clinic's Stedi credential is not configured.")
    details = value.model_dump(mode="json")
    try:
        lines = prebooking.validate_services(details["selected_services"], details["visit_type"], details["reason"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    today = _today()
    service_date = details.get("service_date") or today
    if not date.fromisoformat(today) <= date.fromisoformat(service_date) <= date.fromisoformat(today) + timedelta(days=365):
        raise HTTPException(422, "Choose a prospective service date within one year.")
    clinic = await run_in_threadpool(clinic_store.get_for_practice, staff["practice_id"], staff["practice_name"])
    contexts = await run_in_threadpool(commercial_store.list_networks, staff["practice_id"])
    context = next((row for row in contexts if row.get("id") == details.get("network_context_id")
                    and row.get("status") == "verified"), None)
    if details.get("network_context_id") and not context:
        raise HTTPException(422, "Choose a current clinic provider and location.")
    provider = {"npi": (context or {}).get("billing_npi") or clinic.get("npi"),
                "name": (context or {}).get("organization_name") or clinic.get("name")}
    if not clinic_store.valid_npi(str(provider.get("npi") or "")) or not provider.get("name"):
        raise HTTPException(503, "The clinic needs a valid saved provider NPI and name before checking coverage.")
    try:
        payer = await stedi_payers.search_payer("", [value.payer_id], api_key)
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "The insurance company could not be verified right now.") from None
    if not payer or payer.get("stediId") != value.payer_id:
        raise HTTPException(422, "Choose an insurance company from the search results.")
    if stedi_payers.enrollment_required(payer) and not await stedi_payers.eligibility_enrolled(value.payer_id, provider["npi"], api_key):
        raise HTTPException(503, "This insurer requires a live clinic enrollment before coverage can be checked.")
    client = stedi.Client(stedi.Settings("production", api_key))
    inquiry_date = service_date if service_date != today else None
    response = await client.check(prebooking.request_body(details, payer, provider, service_date=inquiry_date))
    checked_at = datetime.now(timezone.utc).isoformat()
    payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
    check_ids = [payload["id"]] if isinstance(payload.get("id"), str) and re.fullmatch(r"ec_[A-Za-z0-9-]{1,80}", payload["id"]) else []
    coverage = (prebooking.evaluate(payload, details, service_date) if response.get("outcome") == "complete"
                else {"status": "needs_review", "message": (
                    "The payer did not complete eligibility for the selected future date. It may not support that date; review Stedi activity before another inquiry. No check for today was substituted."
                    if service_date != today else
                    "The payer check did not complete. Review Stedi activity before another inquiry."),
                      "coverage_start": None, "coverage_end": None})
    base = {"coverage": coverage, "payer": payer["displayName"], "checked_at": checked_at,
            "checked_on": service_date, "check_ids": check_ids, "network_status": "unknown",
            "network_evidence": None, "plan_name": None, "benefits": None, "lines": [],
            "allowed_total_cents": None, "plan_total_cents": None,
            "patient_min_cents": None, "patient_max_cents": None, "estimate_status": "needs_review",
            "issues": []}
    if coverage["status"] != "active_today":
        return JSONResponse({**base, "issues": [coverage["message"]]}, headers=_staff.NO_STORE)
    categories = list(dict.fromkeys(visit_estimate.service_type(row["code"], {}) for row in lines))
    issues = []
    for category in categories:
        if not category:
            issues.append("A selected procedure has no supported benefit category. Verify its procedure-specific benefits with the payer.")
            continue
        if category == "30":
            continue
        reply = await client.check(prebooking.request_body(details, payer, provider,
            service_type=category, service_date=inquiry_date))
        service_payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
        if isinstance(service_payload.get("id"), str) and re.fullmatch(r"ec_[A-Za-z0-9-]{1,80}", service_payload["id"]):
            check_ids.append(service_payload["id"])
        if reply.get("outcome") != "complete" or not isinstance(reply.get("payload"), dict):
            issues.append(f"Service category {category} was not confirmed for the selected date. Review Stedi activity before another inquiry; no check for today was substituted.")
            break
        try:
            category_active = commercial.normalize_stedi_271(service_payload, category, False,
                service_date=service_date).get("active") is True
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            category_active = False
        if not prebooking.matches_identity(service_payload, details) or not category_active:
            issues.append(f"Service category {category} did not confirm the same active patient and policy.")
            break
        payload.setdefault("_service_payloads", {})[category] = service_payload
    try:
        estimate = await run_in_threadpool(_estimate_from_payer, staff["practice_id"], details,
                                            context, payer, provider, payload, service_date, lines)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return JSONResponse({**base, "issues": ["The payer benefits could not be interpreted for these services. Review the check in Stedi."]},
                            headers=_staff.NO_STORE)
    if issues:
        estimate["estimate_status"] = "needs_review"
        estimate["allowed_total_cents"] = estimate["plan_total_cents"] = None
        estimate["patient_min_cents"] = estimate["patient_max_cents"] = None
        estimate["lines"] = [{**row, "allowed_cents": None, "patient_cents": None, "rate_source": None}
                             for row in estimate["lines"]]
    estimate["issues"] = list(dict.fromkeys([*issues, *estimate["issues"]]))[:20]
    return JSONResponse({**base, **estimate}, headers=_staff.NO_STORE)
