"""Create an isolated clinic or accept an administrator's staff invitation."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, field_validator

from app.api.routers.staff import Model, NO_STORE, STAFF_COOKIE, check_origin, require_staff
from app.api.settings import PUBLIC_BASE_URL
from app.core import clinic_email, clinic_store, staff_store
from app.core.request_address import request_address

router = APIRouter(prefix="/api/staff", tags=["staff-registration"])


class Account(Model):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._+\-]*$")
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email", mode="before")
    @classmethod
    def valid_email(cls, value):
        return clinic_email._address(value.strip().lower()) if isinstance(value, str) else value

    @field_validator("display_name")
    @classmethod
    def printable_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a name without control characters")
        return value


class Register(Account):
    clinic_name: str = Field(min_length=2, max_length=100)
    clinic_code: str = Field(min_length=3, max_length=40)

    @field_validator("clinic_code")
    @classmethod
    def code(cls, value):
        value = value.strip().lower()
        if not clinic_store.valid_code(value):
            raise ValueError("Choose 3–40 lowercase letters, numbers or hyphens, starting with a letter. This code must be unique.")
        return value

    @field_validator("clinic_name")
    @classmethod
    def clinic_name_characters(cls, value):
        return cls.printable_name(value)


class Invitation(Model):
    role: Literal["staff", "admin"] = "staff"


class SendEmail(Model):
    email: str = Field(min_length=3, max_length=254)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return clinic_email._address(value)


async def limited(action: str, key: str, limit: int, seconds: int = 3600):
    if not await run_in_threadpool(clinic_store.allow_action, action, key, limit, seconds):
        raise HTTPException(429, "Too many attempts. Please try again later.", headers={**NO_STORE, "Retry-After": str(seconds)})


def signed_in(member: dict, token: str):
    response = JSONResponse({"staff": member}, status_code=201, headers=NO_STORE)
    response.set_cookie(STAFF_COOKIE, token, httponly=True, secure=PUBLIC_BASE_URL.startswith("https://"),
                        samesite="lax", path="/api", max_age=staff_store.SESSION_SECONDS)
    return response


async def admin(request: Request) -> dict:
    member = await require_staff(request)
    if member["role"] != "admin":
        raise HTTPException(403, "Only a clinic administrator can invite staff", headers=NO_STORE)
    return member


@router.post("/register")
async def register(body: Register, request: Request):
    check_origin(request)
    await limited("clinic-registration", request_address(request), 10)
    try:
        member = await run_in_threadpool(clinic_store.register_clinic, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc), headers=NO_STORE) from None
    token = await run_in_threadpool(staff_store.create_session, member)
    return signed_in(member, token)


@router.get("/clinic")
async def current_clinic(request: Request):
    member = await require_staff(request)
    clinic = await run_in_threadpool(clinic_store.get_for_practice, member["practice_id"], member["practice_name"])
    return JSONResponse({**clinic, "upload_url": f"{PUBLIC_BASE_URL}/{clinic['code']}"}, headers=NO_STORE)


@router.post("/invitations")
async def create_invitation(body: Invitation, request: Request):
    member = await admin(request)
    await limited("staff-invitations", member["id"], 30)
    invitation, token = await run_in_threadpool(clinic_store.create_invitation, member, body.role)
    return JSONResponse({**invitation, "url": f"{PUBLIC_BASE_URL}/join/{token}"}, status_code=201, headers=NO_STORE)


@router.get("/invitations")
async def invitations(request: Request):
    member = await admin(request)
    values = await run_in_threadpool(clinic_store.list_invitations, member["practice_id"])
    return JSONResponse({"invitations": values}, headers=NO_STORE)


@router.post("/invitations/{invitation_id}/revoke")
async def revoke_invitation(invitation_id: str, request: Request):
    member = await admin(request)
    changed = await run_in_threadpool(clinic_store.revoke_invitation, invitation_id, member["practice_id"])
    if not changed:
        raise HTTPException(404, "Open staff invitation not found", headers=NO_STORE)
    return Response(status_code=204, headers=NO_STORE)


@router.get("/invitations/{token}")
async def invitation_metadata(token: str):
    invitation = await run_in_threadpool(clinic_store.get_invitation, token)
    if not invitation or clinic_store.invitation_status(invitation) != "open":
        raise HTTPException(410, "This invitation is expired, revoked or already used. Ask your clinic administrator for a new invitation.", headers=NO_STORE)
    return JSONResponse({"clinic_name": invitation["clinic_name"], "expires_at": invitation["expires_at"], "role": invitation["role"]}, headers=NO_STORE)


@router.post("/invitations/{token}")
async def accept_invitation(token: str, body: Account, request: Request):
    check_origin(request)
    await limited("staff-invitation-accept", request_address(request), 20)
    try:
        member = await run_in_threadpool(clinic_store.accept_invitation, token, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(409, str(exc), headers=NO_STORE) from None
    session = await run_in_threadpool(staff_store.create_session, member)
    return signed_in(member, session)


@router.get("/email/configuration")
async def email_configuration(request: Request):
    await require_staff(request)
    return JSONResponse(clinic_email.configuration(), headers=NO_STORE)


async def email_link(body: SendEmail, member: dict, clinic_name: str, link_id: str, purpose: str):
    await limited("clinic-email", member["id"], 20)
    await limited("clinic-email-link", link_id, 3)
    result = await run_in_threadpool(clinic_email.send_link, body.email, clinic_name=clinic_name, link_url=body.url, purpose=purpose)
    return JSONResponse(result, headers=NO_STORE)


@router.post("/invitations/{invitation_id}/email")
async def email_invitation(invitation_id: str, body: SendEmail, request: Request):
    member = await admin(request)
    invitation = await run_in_threadpool(clinic_store.validate_invitation_url, invitation_id, member["practice_id"], body.url, PUBLIC_BASE_URL)
    if not invitation:
        raise HTTPException(404, "Open staff invitation not found", headers=NO_STORE)
    return await email_link(body, member, invitation["clinic_name"], invitation_id, "staff_invite")


@router.post("/upload-links/{link_id}/email")
async def email_patient_link(link_id: str, body: SendEmail, request: Request):
    from app.core import intake_links_store
    member = await require_staff(request)
    invitation = await run_in_threadpool(intake_links_store.validate_owned_url, link_id, member["practice_id"], body.url)
    if not invitation:
        raise HTTPException(404, "Open patient upload request not found", headers=NO_STORE)
    return await email_link(body, member, member["practice_name"], link_id, "patient_upload")
