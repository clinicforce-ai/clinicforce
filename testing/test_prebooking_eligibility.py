"""Synthetic, unsaved eligibility checks for people who have not booked."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.api.routers import staff
from app.api.routers.staff_flow import staff_prebooking
from app.insurance import prebooking, stedi


PAYER = {"id": "payer-1", "display_name": "Example Health", "stedi_payer_id": "EXAMPLE"}
PROVIDER = {"id": "provider-1", "payer_connection_id": "payer-1", "billing_npi": "1234567890",
            "organization_name": "Example Clinic", "network_name": "Main office"}
SELF = {"payer_connection_id": "payer-1", "provider_network_id": "provider-1", "given_name": "Pat",
        "family_name": "Example", "birth_date": "1980-01-01", "member_id": "MEM123",
        "relationship": "self", "subscriber_given_name": "", "subscriber_family_name": "",
        "subscriber_birth_date": None}


def response(status="ACTIVE_COVERAGE", service="30"):
    return {"id": "ec_synthetic-123", "subscriber": {"memberId": "MEM123",
            "name": {"person": {"firstName": "Pat", "lastName": "Example"}}, "dateOfBirth": "1980-01-01"},
            "plans": [{"benefits": {"statuses": [{"status": status,
                "service": {"system": "STC", "value": service}}]}}]}


def test_request_is_general_medical_and_keeps_patient_separate_from_policyholder():
    own = prebooking.request_body(SELF, PAYER, PROVIDER)
    assert own["encounter"] == {"services": [{"system": "STC", "value": "30"}]}
    assert own["subscriber"]["memberId"] == "MEM123"
    assert "dependent" not in own
    dependent = {**SELF, "relationship": "dependent", "given_name": "Alex", "birth_date": "2010-01-01",
                 "subscriber_given_name": "Pat", "subscriber_family_name": "Example", "subscriber_birth_date": "1980-01-01"}
    sent = prebooking.request_body(dependent, PAYER, PROVIDER)
    assert sent["subscriber"]["dateOfBirth"] == "1980-01-01"
    assert sent["dependent"]["dateOfBirth"] == "2010-01-01"


def test_only_matching_current_medical_coverage_is_active():
    assert prebooking.evaluate(response(), SELF, "2026-10-27")["status"] == "active_today"
    assert prebooking.evaluate(response("INACTIVE"), SELF, "2026-10-27")["status"] == "inactive_today"
    assert prebooking.evaluate(response(service="35"), SELF, "2026-10-27")["status"] == "needs_review"
    wrong = response()
    wrong["subscriber"]["dateOfBirth"] = "1981-01-01"
    assert prebooking.evaluate(wrong, SELF, "2026-10-27")["status"] == "needs_review"
    expired = response()
    expired["subscriber"]["dates"] = {"plan": {"end": "2026-10-26"}}
    assert prebooking.evaluate(expired, SELF, "2026-10-27")["status"] == "inactive_today"
    future_benefit = response()
    future_benefit["plans"][0]["benefits"]["statuses"][0]["dates"] = {"benefit": {"start": "2026-11-01"}}
    assert prebooking.evaluate(future_benefit, SELF, "2026-10-27")["status"] == "needs_review"
    rejected = response()
    rejected["errors"] = [{"code": "72"}]
    assert prebooking.evaluate(rejected, SELF, "2026-10-27")["status"] == "needs_review"
    dependent = {**SELF, "relationship": "dependent", "given_name": "Alex", "birth_date": "2010-01-01",
                 "subscriber_given_name": "Pat", "subscriber_family_name": "Example", "subscriber_birth_date": "1980-01-01"}
    returned = response()
    returned["dependent"] = {"name": {"person": {"firstName": "Alex", "lastName": "Example"}}, "dateOfBirth": "2010-01-01"}
    assert prebooking.evaluate(returned, dependent, "2026-10-27")["status"] == "active_today"
    returned["dependent"]["dateOfBirth"] = "2011-01-01"
    assert prebooking.evaluate(returned, dependent, "2026-10-27")["status"] == "needs_review"


def test_staff_route_checks_without_creating_a_patient_or_case():
    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(side_effect=HTTPException(401, "Sign in"))):
                assert (await client.post("/api/staff/prebooking-eligibility/check", json=SELF)).status_code == 401
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1"})), \
                 patch.object(staff_prebooking, "_configured", return_value=([PAYER], [PROVIDER])), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "complete", "payload": response()})) as check, \
                 patch.object(staff.staff_store, "create_intake") as create_intake, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                options = await client.get("/api/staff/prebooking-eligibility/options")
                assert options.status_code == 200
                assert options.json()["payers"][0]["providers"][0]["id"] == "provider-1"
                invalid_dependent = await client.post("/api/staff/prebooking-eligibility/check", json={**SELF, "relationship": "dependent"})
                assert invalid_dependent.status_code == 422
                result = await client.post("/api/staff/prebooking-eligibility/check", json=SELF)
                assert result.status_code == 200
                assert result.json()["status"] == "active_today"
                assert result.headers["cache-control"] == "no-store"
                assert "member_id" not in result.json()
                assert check.await_count == 1
                create_intake.assert_not_called()
                wrong_provider = await client.post("/api/staff/prebooking-eligibility/check", json={**SELF, "provider_network_id": "other"})
                assert wrong_provider.status_code == 422
                assert check.await_count == 1
    asyncio.run(run())
