"""Synthetic, unsaved eligibility checks for people who have not booked."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.api.routers import staff
from app.api.routers.staff_flow import staff_prebooking
from app.api.schemas.staff import PrebookingEstimateResponse
from app.insurance import network_participation, prebooking, stedi, stedi_payers


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


def test_prospective_plan_never_selects_or_prices_a_suggestion():
    suggestions = {"groups": [{"label": "Possible testing", "selection_mode": "multiple",
        "codes": [{"code": "95909", "reason": "Nerve studies may be considered."},
                  {"code": "invalid", "reason": "Bad code"}]}], "notes": []}
    plan = prebooking.plan("Hand numbness; testing may be considered", "established", suggestions)
    assert plan["groups"][0]["codes"][0]["code"] == "99212"
    assert [row["code"] for row in plan["groups"][1]["codes"]] == ["95909"]
    assert all("selected" not in row for group in plan["groups"] for row in group["codes"])
    assert prebooking.validate_services([{"code": "95909", "units": 1}], "established", "Hand numbness")[0]["confirmed"]
    try:
        prebooking.validate_services([{"code": "95909", "units": 1}, {"code": "95908", "units": 1}],
                                     "established", "Hand numbness")
    except ValueError:
        pass
    else:
        assert False, "Alternative nerve study levels must be rejected"


def test_dependent_inquiry_requires_matching_policyholder_and_patient():
    details = {**PERSON, "relationship": "dependent", "subscriber_given_name": "Sam",
               "subscriber_family_name": "Example", "subscriber_birth_date": "1950-01-01"}
    body = prebooking.request_body(details, PAYER, PROVIDER, service_type="73", service_date="2026-11-05")
    assert body["encounter"] == {"services": [{"system": "STC", "value": "73"}],
                                  "dates": {"service": {"start": "2026-11-05"}}}
    assert body["dependent"]["name"]["person"]["firstName"] == "Pat"
    reply = response()
    reply["subscriber"]["name"]["person"]["firstName"] = "Sam"
    reply["subscriber"]["dateOfBirth"] = "1950-01-01"
    reply["dependent"] = {"name": {"person": {"firstName": "Pat", "lastName": "Example"}},
                          "dateOfBirth": "1980-01-01"}
    assert prebooking.matches_identity(reply, details)
    reply["dependent"]["dateOfBirth"] = "1981-01-01"
    assert not prebooking.matches_identity(reply, details)


def test_estimate_route_checks_identity_and_services_before_pricing():
    from app.api.routers import commercial_estimator
    from app.insurance import library_network_exclusions

    async def search(name, ids, key, *, candidates=None):
        return PAYER if ids == ["EXAMPLE"] else None

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        estimate = {**PERSON, "reason": "Planned office consultation", "visit_type": "established",
                    "selected_services": [{"code": "99213", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "complete", "payload": response()})) as check, \
                 patch.object(commercial_estimator.commercial_store, "list_connections", return_value=[]), \
                 patch.object(library_network_exclusions, "applicable_exclusion", return_value=None), \
                 patch.object(staff.staff_store, "create_intake") as create_intake, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                invalid = await client.post("/api/staff/prebooking-estimate/check", json={**estimate,
                    "selected_services": [{"code": "95909", "units": 1}, {"code": "95908", "units": 1}]})
                assert invalid.status_code == 422
                assert check.await_count == 0
                result = await client.post("/api/staff/prebooking-estimate/check", json=estimate)
                assert result.status_code == 200, result.text
                assert result.headers["cache-control"] == "no-store"
                assert result.json()["coverage"]["status"] == "active_today"
                assert result.json()["patient_max_cents"] is None
                assert result.json()["network_status"] == "unknown"
                PrebookingEstimateResponse.model_validate(result.json())
                assert check.await_count == 2
                assert [call.args[0]["encounter"]["services"][0]["value"] for call in check.await_args_list] == ["30", "98"]
                create_intake.assert_not_called()
    asyncio.run(run())


def test_prospective_amount_requires_member_binding_and_verified_network():
    from app.api.routers import commercial_estimator

    payload = response()
    payload["plans"][0]["name"] = "Example PPO"
    details = {**PERSON, "reason": "Planned office consultation", "visit_type": "established"}
    context = {"billing_npi": PROVIDER["npi"], "rendering_npi": PROVIDER["npi"],
               "location_id": "office-1", "place_of_service": "11"}
    lines = prebooking.validate_services([{"code": "99213", "units": 1}], "established", details["reason"])

    def priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        benefits.update(network_status="in_network", network_source="clinic_library_contract")
        result["network_evidence"] = {"decision": "confirmed_participating", "source": "clinic_library_contract",
                                      "reason": "Synthetic contract."}
        result["price_range"] = {"complete": True, "conditional": False,
            "allowed_min_cents": 10000, "allowed_max_cents": 10000,
            "plan_min_cents": 7500, "plan_max_cents": 7500,
            "patient_min_cents": 2500, "patient_max_cents": 2500,
            "lines": [{"code": "99213", "units": 1, "allowed_cents": 10000,
                       "allowed_basis": "clinic_contract", "rate_source": "Synthetic signed rate",
                       "rate_resolution": {"status": "RESOLVED"},
                       "calculation": {"patient_cents": 2500}, "benefits": benefits,
                       "issues": [], "conditions": []}]}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=priced):
        result = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, details, context, PAYER,
            PROVIDER, payload, "2026-10-27", lines)
        assert result["estimate_status"] == "priced"
        assert result["allowed_total_cents"] == 10000
        assert result["plan_total_cents"] == 7500
        assert result["patient_max_cents"] == 2500
        assert result["lines"][0]["rate_source"] == "Synthetic signed rate"
        assert result["lines"][0]["benefits"]["copay_cents"] is None
        PrebookingEstimateResponse.model_validate({"coverage": prebooking.evaluate(payload, details, "2026-10-27"),
            "payer": PAYER["displayName"], "checked_at": "2026-10-27T12:00:00Z", "checked_on": "2026-10-27",
            "check_ids": [], **result})

        no_member = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, {**details, "member_id": ""},
            context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert no_member["estimate_status"] == "needs_review"
        assert no_member["patient_max_cents"] is None
        assert no_member["allowed_total_cents"] is None

        other_clinic = staff_prebooking._estimate_from_payer("clinic-1", details, context, PAYER,
            PROVIDER, payload, "2026-10-27", lines)
        assert other_clinic["allowed_total_cents"] is None
        assert other_clinic["lines"][0]["rate_source"] is None

    def unverified(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        benefits.update(network_status="unknown", network_source=None)

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=unverified):
        no_network = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, details, context, PAYER,
            PROVIDER, payload, "2026-10-27", lines)
        assert no_network["network_status"] == "unknown"
        assert no_network["patient_max_cents"] is None
        assert no_network["network_evidence"]["reason"] == "Synthetic contract."

    def conflicting(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        benefits["network_conflict"] = True

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=conflicting):
        conflict = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert conflict["network_status"] == "unknown"
        assert conflict["allowed_total_cents"] is None

    def mismatched_total(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        result["price_range"]["plan_min_cents"] = 7600
        result["price_range"]["plan_max_cents"] = 7600

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=mismatched_total):
        mismatch = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert mismatch["estimate_status"] == "needs_review"
        assert mismatch["plan_total_cents"] is None

    def missing_rate(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        result["price_range"]["lines"][0]["rate_resolution"] = {"status": "RATE_UNAVAILABLE"}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=missing_rate):
        unpriced = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert unpriced["allowed_total_cents"] is None


def test_uncertain_service_response_stops_further_inquiries_and_withholds_amount():
    async def search(name, ids, key, *, candidates=None):
        return PAYER

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        body = {**PERSON, "reason": "Planned office and nerve testing", "visit_type": "established",
                "selected_services": [{"code": "99213", "units": 1}, {"code": "95909", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(side_effect=[
                     {"outcome": "complete", "payload": response()},
                     {"outcome": "unavailable", "payload": {"id": "ec_synthetic-uncertain"}},
                 ])) as check, \
                 patch.object(staff_prebooking, "_estimate_from_payer", return_value={
                     "network_status": "in_network", "network_evidence": None, "plan_name": "Example PPO",
                     "benefits": None, "lines": [{"code": "99213", "units": 1, "role": "visit",
                         "service_type": "98", "allowed_cents": 10000, "patient_cents": 2500,
                         "benefits": staff_prebooking._terms({}), "issues": [], "conditions": []}],
                     "patient_min_cents": 2500, "patient_max_cents": 2500,
                     "estimate_status": "priced", "issues": []}), \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                result = await client.post("/api/staff/prebooking-estimate/check", json=body)
                assert result.status_code == 200, result.text
                assert check.await_count == 2
                assert result.json()["check_ids"] == ["ec_synthetic-123", "ec_synthetic-uncertain"]
                assert result.json()["patient_max_cents"] is None
                assert result.json()["lines"][0]["patient_cents"] is None
                assert result.json()["lines"][0]["allowed_cents"] is None
                assert result.json()["estimate_status"] == "needs_review"
    asyncio.run(run())


def test_future_date_rejection_never_substitutes_today_or_creates_a_record():
    async def search(name, ids, key, *, candidates=None):
        return PAYER

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        body = {**PERSON, "reason": "Planned office consultation", "visit_type": "established",
                "service_date": "2026-11-05", "selected_services": [{"code": "99213", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "primary", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "payer_error", "payload": {"id": "ec_synthetic-future"}})) as check, \
                 patch.object(staff.staff_store, "create_intake") as create_intake, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                response = await client.post("/api/staff/prebooking-estimate/check", json=body)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["checked_on"] == "2026-11-05"
                assert result["coverage"]["status"] == "needs_review"
                assert "No check for today was substituted" in result["coverage"]["message"]
                assert result["allowed_total_cents"] is None
                assert result["plan_total_cents"] is None
                assert result["patient_max_cents"] is None
                assert check.await_count == 1
                assert check.await_args.args[0]["encounter"]["dates"]["service"]["start"] == "2026-11-05"
                create_intake.assert_not_called()
    asyncio.run(run())
