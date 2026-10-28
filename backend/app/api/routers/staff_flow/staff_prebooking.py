"""Staff-only eligibility screening before a clinic patient or eCW visit exists."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.routers import staff as _staff
from app.api.schemas.staff import PrebookingEligibility, PrebookingOptions, PrebookingResult
from app.insurance import commercial_store, prebooking, stedi, stedi_financial


router = _staff.router


def _today() -> str:
    try:
        return datetime.now(ZoneInfo(os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date().isoformat()
    except ZoneInfoNotFoundError:
        raise HTTPException(503, "The clinic timezone is not configured correctly.") from None


def _configured(practice_id: str, today: str) -> tuple[list[dict], list[dict]]:
    payers = [row for row in commercial_store.list_connections(practice_id)
              if row.get("category") == "commercial_medical" and row.get("status") == "staff_guidance_enabled"
              and row.get("stedi_payer_id")]
    payer_ids = {row["id"] for row in payers}
    providers = [row for row in commercial_store.list_networks(practice_id)
                 if row.get("payer_connection_id") in payer_ids and row.get("status") == "verified"
                 and row.get("effective_from", "") <= today
                 and (not row.get("effective_to") or row["effective_to"] >= today)
                 and re.fullmatch(r"\d{10}", str(row.get("billing_npi") or ""))
                 and row.get("organization_name")]
    return payers, providers


@router.get("/prebooking-eligibility/options", response_model=PrebookingOptions, description="List this clinic's approved Stedi medical payer routes and provider identities for an unsaved pre-booking inquiry. No patient or eCW record is needed.")
async def prebooking_options(request: Request):
    staff = await _staff.require_staff(request)
    payers, providers = await run_in_threadpool(_configured, staff["practice_id"], _today())
    return JSONResponse({"payers": [{"id": payer["id"], "name": payer["display_name"],
        "providers": [{"id": row["id"], "name": row["network_name"], "npi": row["billing_npi"]}
                      for row in providers if row["payer_connection_id"] == payer["id"]]}
        for payer in payers]}, headers=_staff.NO_STORE)


@router.post("/prebooking-eligibility/check", response_model=PrebookingResult, description="Check today's general medical coverage (STC 30) through Stedi for a prospective person. Requires a clinic staff session and approved payer/provider configuration. Returns active_today, inactive_today, or needs_review. ClinicForce does not create a patient, intake, case, appointment, or saved eligibility result; repeat the check after booking for the visit date.")
async def prebooking_check(value: PrebookingEligibility, request: Request):
    staff = await _staff.require_staff(request)
    if (os.getenv("STEDI_EXECUTION_MODE", "off") != "commercial_pilot"
            or os.getenv("STEDI_LIVE_ELIGIBILITY_ENABLED", "false").lower() != "true"):
        raise HTTPException(503, "Live Stedi eligibility is not enabled for this clinic deployment.")
    api_key = stedi_financial.credentials(staff["practice_id"])
    if not api_key:
        raise HTTPException(503, "The clinic's Stedi credential is not configured.")
    today = _today()
    payers, providers = await run_in_threadpool(_configured, staff["practice_id"], today)
    payer = next((row for row in payers if row["id"] == value.payer_connection_id), None)
    provider = next((row for row in providers if row["id"] == value.provider_network_id
                     and row["payer_connection_id"] == value.payer_connection_id), None)
    if not payer or not provider:
        raise HTTPException(422, "Choose an approved payer and clinic provider for this check.")
    details = value.model_dump(mode="json")
    try:
        body = prebooking.request_body(details, payer, provider)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response = await stedi.Client(stedi.Settings("production", api_key)).check(body)
    checked_at = datetime.now(timezone.utc).isoformat()
    payload = response.get("payload") if isinstance(response.get("payload"), dict) else {}
    check_id = payload.get("id") if isinstance(payload.get("id"), str) and re.fullmatch(r"ec_[A-Za-z0-9-]{1,80}", payload["id"]) else None
    result = (prebooking.evaluate(payload, details, today) if response.get("outcome") == "complete"
              else {"status": "needs_review", "coverage_start": None, "coverage_end": None,
                    "message": "The payer check was not completed. Review Stedi activity before sending another inquiry."})
    return JSONResponse({**result, "checked_at": checked_at, "checked_on": today,
                         "payer": payer["display_name"], "check_id": check_id}, headers=_staff.NO_STORE)
