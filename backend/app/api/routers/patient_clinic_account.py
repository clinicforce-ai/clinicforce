"""Optional, explicit association of a card submission with its signed-in owner."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.routers.staff import NO_STORE
from app.api.settings import PATIENT_SESSION_COOKIE
from app.core.patient_store import get_patient_profile, get_patient_session


router = APIRouter(tags=["patient insurance upload"])


async def _profile(request: Request):
    token = request.cookies.get(PATIENT_SESSION_COOKIE, "")
    if not token:
        return None
    patient_id = await run_in_threadpool(get_patient_session, token)
    return await run_in_threadpool(get_patient_profile, patient_id) if patient_id else None


@router.get("/api/insurance-upload/account")
async def account(request: Request):
    profile = await _profile(request)
    if not profile or not all((profile.given_name, profile.family_name, profile.birth_date)):
        return JSONResponse({"available": False}, headers=NO_STORE)
    return JSONResponse({"available": True, "given_name": profile.given_name,
                         "family_name": profile.family_name, "birth_date": profile.birth_date}, headers=NO_STORE)


async def portal_patient_for_upload(request: Request, fields: dict, link_account: bool) -> str | None:
    # A browser may hold another family member's session. Cookie presence alone
    # must never associate a card with that account or reveal it to a clinic.
    if not link_account:
        return None
    profile = await _profile(request)
    if not profile:
        raise HTTPException(401, "Your patient session has expired. Uncheck account linking to continue without signing in.", headers=NO_STORE)
    for key in ("given_name", "family_name", "birth_date"):
        if not getattr(profile, key) or str(getattr(profile, key)).strip().casefold() != str(fields.get(key, "")).strip().casefold():
            raise HTTPException(409, "These details do not match your signed-in patient profile. Uncheck account linking to upload for someone else.", headers=NO_STORE)
    return profile.patient_id
