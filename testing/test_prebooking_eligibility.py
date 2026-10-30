"""Synthetic, unsaved eligibility checks for people who have not booked."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.api.routers import staff
from app.api.routers.staff_flow import staff_prebooking
from app.insurance import prebooking, stedi, stedi_payers


PAYER = {"stediId": "EXAMPLE", "displayName": "Example Health", "transactionSupport": {"eligibilityCheck": "SUPPORTED"}}
PROVIDER = {"npi": "1234567893", "name": "Example Clinic"}
PERSON = {"payer_id": "EXAMPLE", "given_name": "Pat", "family_name": "Example",
          "birth_date": "1980-01-01", "member_id": "MEM123"}


def test_payer_suggestions_keep_stedi_ranked_medical_matches():
    def respond(request):
        assert request.url.params["query"] == "Example Health"
        assert request.headers["Authorization"] == "synthetic-key"
        payers = [
            {"stediId": "EXACT", "displayName": "Example Health", "coverageTypes": ["medical"],
             "transactionSupport": {"eligibilityCheck": "SUPPORTED"}},
            {"stediId": "CLOSE", "displayName": "Example Health East", "coverageTypes": ["medical"],
             "transactionSupport": {"eligibilityCheck": "ENROLLMENT_REQUIRED"}},
            {"stediId": "DENTAL", "displayName": "Example Dental", "coverageTypes": ["dental"],
             "transactionSupport": {"eligibilityCheck": "SUPPORTED"}},
            {"stediId": "UNSUPPORTED", "displayName": "Example Unsupported", "coverageTypes": ["medical"],
             "transactionSupport": {"eligibilityCheck": "NOT_SUPPORTED"}},
        ]
        return httpx.Response(200, json={"items": [{"payer": payer} for payer in [*payers, payers[0]]]})

    found = asyncio.run(stedi_payers.payer_suggestions(
        "Example Health", "synthetic-key", transport=httpx.MockTransport(respond)))
    assert found == [{"id": "EXACT", "name": "Example Health"},
                     {"id": "CLOSE", "name": "Example Health East"}]


def response(status="ACTIVE_COVERAGE", service="30"):
    return {"id": "ec_synthetic-123", "subscriber": {"memberId": "MEM123",
            "name": {"person": {"firstName": "Pat", "lastName": "Example"}}, "dateOfBirth": "1980-01-01"},
            "plans": [{"benefits": {"statuses": [{"status": status,
                "service": {"system": "STC", "value": service}}]}}]}


def test_request_uses_only_five_fields_and_omits_missing_member_id():
    own = prebooking.request_body(PERSON, PAYER, PROVIDER)
    assert own["encounter"] == {"services": [{"system": "STC", "value": "30"}]}
    assert own["provider"]["npi"] == PROVIDER["npi"]
    assert own["subscriber"]["memberId"] == "MEM123"
    assert "memberId" not in prebooking.request_body({**PERSON, "member_id": ""}, PAYER, PROVIDER)["subscriber"]
    assert "dependent" not in own


def test_only_matching_current_medical_coverage_is_active():
    assert prebooking.evaluate(response(), PERSON, "2026-10-27")["status"] == "active_today"
    assert prebooking.evaluate(response("INACTIVE"), PERSON, "2026-10-27")["status"] == "inactive_today"
    assert prebooking.evaluate(response(service="35"), PERSON, "2026-10-27")["status"] == "needs_review"
    without_id = {**PERSON, "member_id": ""}
    assert prebooking.evaluate(response(), without_id, "2026-10-27")["status"] == "active_today"
    no_returned_id = response()
    no_returned_id["subscriber"].pop("memberId")
    assert prebooking.evaluate(no_returned_id, without_id, "2026-10-27")["status"] == "needs_review"
    wrong = response()
    wrong["subscriber"]["dateOfBirth"] = "1981-01-01"
    assert prebooking.evaluate(wrong, PERSON, "2026-10-27")["status"] == "needs_review"
    expired = response()
    expired["subscriber"]["dates"] = {"plan": {"end": "2026-10-26"}}
    assert prebooking.evaluate(expired, PERSON, "2026-10-27")["status"] == "inactive_today"
    future_benefit = response()
    future_benefit["plans"][0]["benefits"]["statuses"][0]["dates"] = {"benefit": {"start": "2026-11-01"}}
    assert prebooking.evaluate(future_benefit, PERSON, "2026-10-27")["status"] == "needs_review"
    rejected = response()
    rejected["errors"] = [{"code": "72"}]
    assert prebooking.evaluate(rejected, PERSON, "2026-10-27")["status"] == "needs_review"
    dependent = response()
    dependent["dependent"] = {"name": {"person": {"firstName": "Pat", "lastName": "Example"}},
                              "dateOfBirth": "1980-01-01"}
    assert prebooking.evaluate(dependent, PERSON, "2026-10-27")["status"] == "active_today"
    dependent["dependent"]["dateOfBirth"] = "2011-01-01"
    assert prebooking.evaluate(dependent, PERSON, "2026-10-27")["status"] == "needs_review"


def test_staff_route_checks_without_creating_a_patient_or_case():
    async def search(name, ids, key, *, candidates=None):
        return PAYER if ids == ["EXAMPLE"] else None

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(side_effect=HTTPException(401, "Sign in"))):
                assert (await client.post("/api/staff/prebooking-eligibility/check", json=PERSON)).status_code == 401
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "payer_suggestions", AsyncMock(return_value=[{"id": "EXAMPLE", "name": "Example Health"}])), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "complete", "payload": response()})) as check, \
                 patch.object(staff.staff_store, "create_intake") as create_intake, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                options = await client.get("/api/staff/prebooking-eligibility/options?query=Example")
                assert options.status_code == 200
                assert options.json()["payers"] == [{"id": "EXAMPLE", "name": "Example Health"}]
                result = await client.post("/api/staff/prebooking-eligibility/check", json={**PERSON, "member_id": ""})
                assert result.status_code == 200
                assert result.json()["status"] == "active_today"
                assert result.headers["cache-control"] == "no-store"
                assert "member_id" not in result.json()
                assert "memberId" not in check.await_args.args[0]["subscriber"]
                create_intake.assert_not_called()
                invalid = await client.post("/api/staff/prebooking-eligibility/check", json={**PERSON, "payer_id": "OTHER"})
                assert invalid.status_code == 422
                assert check.await_count == 1
    asyncio.run(run())
