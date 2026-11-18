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
from app.insurance import (collection_guidance, commercial, commercial_store, cpt_suggester,
                           encounter_summary, identity,
                           network_participation, prebooking, prebooking_reason, stedi, stedi_financial,
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


@router.post("/prebooking-eligibility/check", response_model=PrebookingResult, description="Check today's general medical coverage (STC 30) through Stedi using prospective patient name, birth date, selected insurer and optional member ID. The clinic's saved name and NPI supply the provider identity. Returns active_today, inactive_today, payer_rejected with an actionable payer error when available, or needs_review when the response is indeterminate. No patient, intake, case, appointment, or saved eligibility result is created; repeat the check after booking for the visit date.")
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
    interpretable = (response.get("outcome") == "complete" or response.get("outcome") == "payer_error"
                     and bool(commercial.eligibility_errors(payload)))
    result = (prebooking.evaluate(payload, details, today) if interpretable
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


@router.post("/prebooking-estimate/plan", response_model=PrebookingPlanResponse, description="Build an unsaved office-visit planning baseline from new or returning patient status and ask the AI for optional CPT/HCPCS ideas from the visit reason. The baseline code is selected by default; optional ideas are not. Plain-language groups may contain no code when the clinical detail needed for a specific procedure is missing. This makes no Stedi request and creates no patient, case or appointment. A planning code is not a diagnosis, order, final billed level or price.")
async def prebooking_estimate_plan(value: PrebookingPlanRequest, request: Request):
    await _staff.require_staff(request)
    suggestions = await run_in_threadpool(cpt_suggester.suggest_preliminary_cpt_codes,
                                           value.reason + f"\nPatient visit status: {value.visit_type}.",
                                           prospective=True)
    return JSONResponse(prebooking.plan(value.reason, value.visit_type, suggestions), headers=_staff.NO_STORE)


def _terms(benefits: dict) -> dict:
    return {key: benefits.get(key) for key in ("copay_cents", "coinsurance_percent",
            "deductible_annual_cents", "deductible_remaining_cents", "oop_annual_cents",
            "oop_remaining_cents", "deductible_applicability", "authorization_signal",
            "referral_signal", "resolved_service_type")}


def _deferred_collection(practice_id: str) -> dict:
    if network_participation.is_nmct_practice(practice_id):
        return {"amount_cents": 0, "basis": "clinic_deferred_prebooking", "reason":
                "NMCT policy: collect $0 at arrival for this check; bill after coverage and claim review. This does not mean the visit costs $0.",
                "components": None}
    return {"amount_cents": None, "basis": "needs_review", "reason":
            "No clinic arrival collection policy is set for this practice.", "components": None}


def _prospective_collection(practice_id: str, payer_id: str, benefits: dict, comparison: dict,
                            price: dict, lines: list[dict], network_evidence: dict | None,
                            ready: bool) -> dict:
    missing = {"amount_cents": None, "basis": "needs_review", "reason":
               "ClinicForce did not calculate one patient share for these services or find an applicable fixed office copay in the insurer's reply.",
               "components": None}
    office_copay_candidate = (network_participation.is_nmct_practice(practice_id)
        and benefits.get("active") is True and identity.patient_benefits_bound(comparison)
        and network_participation.verified_in_network(benefits)
        and price.get("mode") == "services" and len(lines) == 1
        and len(price.get("lines") or []) == 1
        and price["lines"][0].get("id") == lines[0]["id"]
        and visit_estimate.clinical_group(lines[0]["code"]) == "visit")
    if not ready and not office_copay_candidate:
        return missing
    connections = [row for row in commercial_store.list_connections(practice_id)
                   if row.get("stedi_payer_id") == payer_id]
    policy = ((connections[0].get("rules") or {}).get("collection_policy") or {}) if len(connections) == 1 else {}
    if not (isinstance(policy, dict) and policy.get("mode") in {
            "defer_to_claim", "collect_verified_copay", "collect_supported_advance"}
            and all(policy.get(key) for key in ("approved_by", "approved_at", "reference"))):
        return {**missing, "reason":
                "NMCT has no approved check-in collection rule for this insurer."}
    evidence = {"benefits": benefits, "identity": comparison, "price_range": price,
                "codes": lines, "selected_ids": [row["id"] for row in lines],
                "network_evidence": network_evidence, "payment_mode": "insurance",
                "service_selection_status": "confirmed", "collection_policy": policy}
    try:
        guidance = collection_guidance.build(evidence)
        summary = encounter_summary.build({**evidence, "collection_guidance": guidance})
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return {**missing, "reason": "The clinic collection formula could not be completed; review the payer check and clinic policy."}
    independent_copay = guidance.get("basis") == "office_copay" and office_copay_candidate
    amount = (summary.get("collect_today_cents") if not summary.get("integrity_error")
              and (summary.get("complete") or independent_copay) else None)
    components = summary.get("components") or {}
    complete_components = (summary.get("complete") and len(components) == 5
                           and all(type(value) is int for value in components.values()))
    return {"amount_cents": amount, "basis": guidance.get("basis") or "needs_review",
            "reason": guidance.get("reason") or missing["reason"],
            "components": components if complete_components else None}


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
    returned_dependent = payload.get("dependent")
    if (details.get("relationship") != "dependent" and details.get("member_id")
            and isinstance(returned_dependent, dict) and returned_dependent
            and prebooking.matches_identity(payload, details)):
        subscriber = commercial.payer_person(payload.get("subscriber") or {})
        if subscriber.get("firstName") and subscriber.get("lastName"):
            coverage.update(subscriber_first=subscriber["firstName"], subscriber_last=subscriber["lastName"],
                            relationship=(returned_dependent.get("relationToSubscriberCode")
                                          or returned_dependent.get("relationToSubscriber") or ""))
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
    source = {"prebooking": True,
              "billing_provider": {"npi": (context or {}).get("billing_npi"),
                                   "tin": (context or {}).get("tin")},
              "rendering_provider": {"npi": (context or {}).get("rendering_npi")},
              "location_id": (context or {}).get("location_id"),
              "plan": plan, "payer_plan_names": names,
              "payer_plan_bound": bool(plan and identity.patient_benefits_bound(comparison)),
              "payer_network_names": product.get("network_names") or [],
              "payer_verification_party": product.get("verification_payer")}
    inputs = {"reason": details["reason"], "visit_type": details["visit_type"],
              "service_date": service_date, "place_of_service": (context or {}).get("place_of_service"),
              "rendering_provider": {"npi": (context or {}).get("rendering_npi")},
              "lines": [], "payer_id": payer["stediId"]}
    result = {"payer": payer["displayName"],
              "stedi_payer": {"id": payer["stediId"], "name": payer["displayName"]},
              "plan_details": {"stedi": commercial.plan_details(payload)},
              "codes": lines, "issues": []}
    commercial_estimator._attach_previsit_pricing(result, practice_id, inputs, source, payer["stediId"], benefits,
        payload=payload, review={"selected_ids": [row["id"] for row in lines]})
    price = result.get("price_range") or {}
    network = benefits.get("network_status") or "unknown"
    verified = bool(context) and identity.patient_benefits_bound(comparison) and (network_participation.verified_in_network(benefits)
                                  or network_participation.verified_out_of_network(benefits))
    priced_rows = {row["code"]: row for row in price.get("lines") or []}
    contract_rates = len(priced_rows) == len(lines) and all(
        (row := priced_rows.get(selected["code"]))
        and row.get("units") == selected["units"]
        and row.get("allowed_basis") == "clinic_contract"
        and (row.get("rate_resolution") or {}).get("status") == "RESOLVED"
        for selected in lines)
    totals = (price.get("allowed_min_cents"), price.get("allowed_max_cents"),
              price.get("plan_min_cents"), price.get("plan_max_cents"),
              price.get("patient_min_cents"), price.get("patient_max_cents"))
    priced_lines_complete = len(priced_rows) == len(lines) and all(
        type(row.get("allowed_cents")) is int
        and type((row.get("calculation") or {}).get("patient_cents")) is int
        and type((row.get("calculation") or {}).get("plan_cents")) is int
        and row["allowed_cents"] >= 0
        and row["calculation"]["patient_cents"] >= 0
        and row["calculation"]["plan_cents"] >= 0
        and row["allowed_cents"] == row["calculation"]["patient_cents"] + row["calculation"]["plan_cents"]
        for row in priced_rows.values())
    reconciled = (priced_lines_complete and all(type(amount) is int and amount >= 0 for amount in totals)
                  and totals[0] == totals[1] and totals[2] == totals[3]
                  and totals[4] == totals[5] and totals[0] == totals[2] + totals[4]
                  and totals[0] == sum(row["allowed_cents"] for row in priced_rows.values())
                  and totals[2] == sum(row["calculation"]["plan_cents"] for row in priced_rows.values())
                  and totals[4] == sum(row["calculation"]["patient_cents"] for row in priced_rows.values()))
    contract_ready = (benefits.get("active") is True and identity.patient_benefits_bound(comparison)
             and network_participation.is_nmct_practice(practice_id)
             and verified and network == "in_network" and bool(plan)
             and contract_rates and all(type(row.get("allowed_cents")) is int
                                        and row["allowed_cents"] >= 0 for row in priced_rows.values()))
    ready = contract_ready and reconciled and price.get("complete") is True
    allowed_total = sum(row["allowed_cents"] for row in priced_rows.values()) if contract_ready else None
    collection = _prospective_collection(practice_id, payer["stediId"], benefits, comparison,
                                         price, lines, result.get("network_evidence"), ready)
    collection_gap = collection["reason"] if collection["amount_cents"] is None else ""
    if collection["amount_cents"] is None:
        collection = _deferred_collection(practice_id)
    priced_lines = []
    for selected in lines:
        row = priced_rows.get(selected["code"]) or {}
        priced_lines.append({"code": selected["code"], "units": selected["units"],
                             "role": visit_estimate.clinical_group(selected["code"]),
                             "service_type": visit_estimate.service_type(selected["code"], {}),
                             "allowed_cents": row.get("allowed_cents") if contract_ready else None,
                             "patient_cents": (row.get("calculation") or {}).get("patient_cents") if ready else None,
                             "rate_source": row.get("rate_source") if contract_ready else None,
                             "benefits": _terms(row.get("benefits") or {}),
                             "issues": row.get("issues") or [], "conditions": row.get("conditions") or []})
    blockers = []
    if not identity.patient_benefits_bound(comparison):
        blockers.append("Benefits did not match the entered member and patient. Check the card details and dependent selection before estimating collection.")
    if not plan:
        blockers.append("The insurer did not identify one plan product for this person.")
    if identity.patient_benefits_bound(comparison) and not verified and network == "unknown":
        if office_note := prebooking_reason.out_of_network_office_rows(price):
            blockers.append(office_note)
    if context and not verified and identity.patient_benefits_bound(comparison):
        blockers.append("The payer response did not identify whether the requested NMCT provider is in network for this plan; no in-network clinic price was used.")
    elif not context:
        blockers.append("ClinicForce has no selected NMCT provider and office location for its contract-rate lookup.")
    if not network_participation.is_nmct_practice(practice_id):
        blockers.append("Priced prebooking estimates currently support NMCT contracted plans only.")
    elif network != "in_network":
        blockers.append("This plan did not return an in-network result for NMCT, so its in-network clinic rates were not used.")
    elif not contract_rates:
        blockers.append("ClinicForce has no exact NMCT contract rate for every selected CPT on this plan, provider, office and date. The eligibility reply does not include the clinic's contracted rates.")
    elif not priced_lines_complete:
        blockers.append("The insurer did not return enough applicable benefit terms to calculate the patient share.")
    elif not reconciled:
        blockers.append("ClinicForce's selected service amounts did not add up to the visit total, so no patient share was shown.")
    elif price.get("complete") is not True:
        blockers.append("ClinicForce did not complete the selected service price calculation, so no patient share was shown.")
    issues = list(dict.fromkeys([*blockers, *result["issues"], *(price.get("service_issues") or []),
        *(reason for row in priced_lines for reason in row["issues"])]))
    if collection["basis"] == "clinic_deferred_prebooking" and not issues:
        collection["reason"] += " " + collection_gap
    office = priced_lines[0]["benefits"] if len(priced_lines) == 1 and priced_lines[0]["role"] == "visit" else {}
    if (collection["basis"] == "clinic_deferred_prebooking" and verified and network == "in_network"
            and office.get("copay_cents") is None and office.get("coinsurance_percent") in {"0", "0.0", "0.00"}
            and office.get("deductible_applicability") == "UNKNOWN"
            and office.get("deductible_remaining_cents") != 0 and office.get("deductible_annual_cents") != 0):
        collection["reason"] += " The insurer reports 0% office coinsurance but does not say whether the office visit is subject to the deductible."
    displayed_terms = _terms(benefits)
    if (verified and network == "in_network" and identity.patient_benefits_bound(comparison)
            and len(priced_lines) == 1 and priced_lines[0]["role"] == "visit"):
        # General STC 30 can contain coverage while office STC 98 carries terms.
        office_terms = priced_lines[0]["benefits"]
        for key, value in office_terms.items():
            if displayed_terms.get(key) is None and value is not None:
                displayed_terms[key] = value
    return {"network_status": network if verified else "unknown",
            "network_evidence": result.get("network_evidence"), "plan_name": plan or None,
            "benefits": displayed_terms, "lines": priced_lines,
            "allowed_total_cents": allowed_total,
            "plan_total_cents": totals[2] if ready else None,
            "patient_min_cents": price.get("patient_min_cents") if ready else None,
            "patient_max_cents": price.get("patient_max_cents") if ready else None,
            "collection": collection,
            "estimate_status": "conditional" if ready and price.get("conditional") else "priced" if ready else "needs_review",
            "issues": issues[:20]}


@router.post("/prebooking-estimate/check", response_model=PrebookingEstimateResponse, description="Run an unsaved Stedi 270/271 check for an office-visit planning scenario and optional selected services. Member ID is required. The selected clinic rendering NPI is sent as the requesting provider without an organization name, allowing Stedi to resolve the name from NPPES. Payer AAA rejection codes return payer_rejected with the card field to correct, before identity matching or pricing. A payer-returned dependent can bind to the entered patient without eCW when the dependent name, birth date and full member ID match; the returned subscriber and relationship then supply the pricing identity. An active member-bound out-of-state Blue plan can use NMCT's signed Texas BlueCard agreement, exact provider/location scope and local contracted rates when the home payer has no clinic estimator connection. A signed, scoped NMCT Blue agreement can map a member-bound generic PPO medical label to the clinic plan for contract rate lookup; a specific payer product still needs its own mapping. A hash-verified signed group agreement can stand in for a missing direct profile document link only when its provider, network, plan, location and date exactly match the rate scope. STC 98 office terms are shown when STC 30 reports coverage but no financial terms. Out-of-network office cost-sharing rows are named when provider participation is unverified, but the rows do not establish that provider tier. Return coverage, payer terms and the matched patient address when available; report member network unknown and prioritize card correction when the payer response does not bind the entered member and patient; provide the NMCT contract-allowed visit cost and per-service rate sources when member, plan, provider, service and in-network contract rates match. Estimated patient and plan shares require complete benefit calculations and reconciled totals; they remain null if unknown. A positive prospective collection amount requires an administrator-approved payer policy and the shared visit calculation checks; a verified fixed office copay can be used without a full contract rate. If no approved positive amount is available, NMCT policy returns $0 to collect at arrival and bills after coverage and claim review, without treating patient liability as zero. The no-store response repeats entered patient identity. Unknown visit-price and liability values are null. Rejections and missing or conflicting payer terms include a specific reason or a labeled AI inference from deidentified response signals, including whether relevant cost-sharing rows were present. Future-date checks can be unsupported by a payer and never fall back silently to today. No patient, case or appointment is created; recheck after booking.")
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
    provider = {"npi": (context or {}).get("rendering_npi") or clinic.get("npi")}
    if not clinic_store.valid_npi(str(provider.get("npi") or "")):
        raise HTTPException(503, "The clinic needs a valid saved provider NPI before checking coverage.")
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
    interpretable = (response.get("outcome") == "complete" or response.get("outcome") == "payer_error"
                     and bool(commercial.eligibility_errors(payload)))
    coverage = (prebooking.evaluate(payload, details, service_date) if interpretable
                else {"status": "needs_review", "message": (
                    "The payer did not complete eligibility for the selected future date. It may not support that date; review Stedi activity before another inquiry. No check for today was substituted."
                    if service_date != today else
                    "The payer check did not complete. Review Stedi activity before another inquiry."),
                      "coverage_start": None, "coverage_end": None})
    if not interpretable:
        inferred = await run_in_threadpool(prebooking_reason.infer, "check_incomplete", {
            "payer_status": str(response.get("outcome") or "unknown")[:20],
            "future_date": service_date != today})
        coverage["message"] += " " + inferred
    elif coverage["status"] == "needs_review" and coverage["message"] == (
            "The insurer did not return a clear coverage answer for this date."):
        coverage["message"] = await run_in_threadpool(prebooking_reason.infer, "missing_coverage", {
            "payer_status": "COMPLETE", "service_types": ["30"],
            "future_date": service_date != today})
    base = {"coverage": coverage, "payer": payer["displayName"], "checked_at": checked_at,
            "checked_on": service_date, "check_ids": check_ids,
            "patient_given_name": details["given_name"], "patient_family_name": details["family_name"],
            "patient_birth_date": details["birth_date"],
            "patient_address": prebooking.patient_address(payload, details) if response.get("outcome") == "complete" else None,
            "network_status": "unknown",
            "network_evidence": None, "plan_name": None, "benefits": None, "lines": [],
            "allowed_total_cents": None, "plan_total_cents": None,
            "patient_min_cents": None, "patient_max_cents": None, "estimate_status": "needs_review",
            "collection": _deferred_collection(staff["practice_id"]),
            "issues": []}
    if coverage["status"] == "payer_rejected" and coverage["message"] in {
            "The insurer rejected the submitted member details. Check the insurance card and try again.",
            "The insurer rejected this inquiry. Check the card details and try again."}:
        codes = sorted({str(row.get(key)) for row in commercial.eligibility_errors(payload)
                        for key in ("code", "errorCode", "industryCode")
                        if re.fullmatch(r"\d{2}", str(row.get(key) or ""))})
        inferred = await run_in_threadpool(prebooking_reason.infer, "payer_rejected", {
            "payer_status": str(payload.get("status") or "REJECTED")[:20], "error_codes": codes})
        coverage["message"] = (f"Insurer rejection code {', '.join(codes)}. " if codes else
                               "The insurer rejected the inquiry without a reason code. ") + inferred
    if coverage["status"] != "active_today":
        if base["collection"]["basis"] == "clinic_deferred_prebooking":
            base["collection"]["reason"] += " " + coverage["message"]
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
            category_reason = prebooking.evaluate(service_payload, details, service_date)
            if category_reason["status"] == "payer_rejected" and category_reason["message"] not in {
                    "The insurer rejected the submitted member details. Check the insurance card and try again.",
                    "The insurer rejected this inquiry. Check the card details and try again."}:
                explanation = category_reason["message"]
            else:
                explanation = await run_in_threadpool(prebooking_reason.infer,
                    "payer_rejected" if category_reason["status"] == "payer_rejected" else "check_incomplete", {
                    "coverage": "general medical active", "payer_status": str(reply.get("outcome") or "unknown")[:20],
                    "service_types": [category]})
            issues.append(f"Service category {category} was not confirmed. {explanation}")
            break
        try:
            category_active = commercial.normalize_stedi_271(service_payload, category, False,
                service_date=service_date).get("active") is True
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            category_active = False
        if not prebooking.matches_identity(service_payload, details):
            issues.append(f"Service category {category} returned a different patient or member ID from the general-medical check.")
            break
        if not category_active:
            issues.append(f"The insurer's service category {category} reply did not show active coverage for this patient and date.")
            break
        payload.setdefault("_service_payloads", {})[category] = service_payload
    try:
        estimate = await run_in_threadpool(_estimate_from_payer, staff["practice_id"], details,
                                            context, payer, provider, payload, service_date, lines)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        reason = "ClinicForce's benefit calculator failed while reading the insurer's response for these services. The clinic must review this check in Stedi before using an insured price."
        if base["collection"]["basis"] == "clinic_deferred_prebooking":
            base["collection"]["reason"] += " " + reason
        return JSONResponse({**base, "issues": [reason]},
                            headers=_staff.NO_STORE)
    if issues:
        estimate["estimate_status"] = "needs_review"
        estimate["allowed_total_cents"] = estimate["plan_total_cents"] = None
        estimate["patient_min_cents"] = estimate["patient_max_cents"] = None
        estimate["lines"] = [{**row, "allowed_cents": None, "patient_cents": None, "rate_source": None}
                             for row in estimate["lines"]]
        estimate["collection"] = _deferred_collection(staff["practice_id"])
    estimate["issues"] = list(dict.fromkeys([*issues, *estimate["issues"]]))[:20]
    missing_benefit_issue = "The insurer did not return enough applicable benefit terms to calculate the patient share."
    missing_plan_issue = "The insurer did not identify one plan product for this person."
    if missing_benefit_issue in estimate["issues"] or missing_plan_issue in estimate["issues"]:
        terms = estimate.get("benefits") or {}
        missing = (["office copay or coinsurance"]
                   if terms.get("copay_cents") is None and terms.get("coinsurance_percent") is None else [])
        missing += [label for key, label in (("deductible_remaining_cents", "remaining deductible"),
                   ("oop_remaining_cents", "remaining out-of-pocket limit"))
                   if terms.get(key) is None]
        if missing_plan_issue in estimate["issues"]:
            missing.insert(0, "plan product")
        service_types = sorted({str(row.get("service_type")) for row in estimate["lines"]
                                if row.get("service_type")})
        inferred = await run_in_threadpool(prebooking_reason.infer, "missing_benefits", {
            "coverage": "active", "missing_terms": missing,
            "service_types": service_types,
            "cost_share_rows_present": prebooking_reason.cost_share_rows_present(payload, service_types)})
        estimate["issues"] = [inferred if issue in {missing_benefit_issue, missing_plan_issue} else issue
                              for issue in estimate["issues"]]
    if (estimate.get("collection") or {}).get("basis") == "clinic_deferred_prebooking" and estimate["issues"]:
        estimate["collection"]["reason"] += " " + estimate["issues"][0]
        inferred_issue = next((issue for issue in estimate["issues"]
                               if "Likely explanation (" in issue), None)
        if inferred_issue and inferred_issue != estimate["issues"][0]:
            estimate["collection"]["reason"] += " " + inferred_issue
    return JSONResponse({**base, **estimate}, headers=_staff.NO_STORE)
