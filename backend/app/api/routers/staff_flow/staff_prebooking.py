"""Staff-only eligibility screening before a clinic patient or eCW visit exists."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.routers import staff as _staff
from app.api.schemas.staff import PrebookingEligibility, PrebookingOptions, PrebookingResult
from app.core import clinic_store
from app.insurance import prebooking, stedi, stedi_financial, stedi_payers


router = _staff.router


def _today() -> str:
    try:
        return datetime.now(ZoneInfo(os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date().isoformat()
    except ZoneInfoNotFoundError:
        raise HTTPException(503, "The clinic timezone is not configured correctly.") from None


@router.get("/prebooking-eligibility/options", response_model=PrebookingOptions, description="Search Stedi's current medical eligibility payer directory by insurance company name. Staff select the exact insurer; no patient or appointment is created.")
async def prebooking_options(request: Request, query: str = Query(min_length=2, max_length=100)):
    staff = await _staff.require_staff(request)
    api_key = stedi_financial.credentials(staff["practice_id"])
    if not api_key:
        raise HTTPException(503, "The clinic's Stedi credential is not configured.")
    candidates: list[dict] = []
    try:
        await stedi_payers.search_payer(query, [], api_key, candidates=candidates)
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(503, "Insurance companies are temporarily unavailable.") from None
    payers = {row["stediId"]: {"id": row["stediId"], "name": row["displayName"]}
              for row in candidates if isinstance(row.get("stediId"), str)
              and isinstance(row.get("displayName"), str)}
    return JSONResponse({"payers": list(payers.values())}, headers=_staff.NO_STORE)


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
