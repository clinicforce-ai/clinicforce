"""Synthetic, unsaved eligibility checks for people who have not booked."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.api.routers import staff
from app.api.routers.staff_flow import staff_prebooking
from app.api.schemas.staff import PrebookingEstimateResponse
from app.insurance import commercial, network_participation, prebooking, prebooking_reason, stedi, stedi_payers, visit_estimate


PAYER = {"stediId": "EXAMPLE", "displayName": "Example Health", "transactionSupport": {"eligibilityCheck": "SUPPORTED"}}
PROVIDER = {"npi": "1234567893", "name": "Example Clinic"}
PERSON = {"payer_id": "EXAMPLE", "given_name": "Pat", "family_name": "Example",
          "birth_date": "1980-01-01", "member_id": "MEM123"}


def test_ai_reason_uses_only_structured_evidence_and_labels_inference():
    reply = {"output": {"message": {"content": [{"toolUse": {
        "name": "choose_prebooking_cause", "input": {"reason_code": "eligibility_terms_omitted"}}}]}}}
    with patch.object(prebooking_reason.boto3, "client") as client:
        client.return_value.converse.return_value = reply
        reason = prebooking_reason.infer("missing_benefits", {
            "coverage": "active", "payer_status": "MEM123",
            "missing_terms": ["office copay", "coinsurance", "Pat"]})
    assert reason.startswith("The insurer's response did not include these visit-specific items: office copay and coinsurance. ")
    assert "Likely explanation (AI inference): " in reason
    assert "cost-sharing terms" in reason
    sent = client.return_value.converse.call_args.kwargs["messages"]
    assert "MEM123" not in str(sent) and "Pat" not in str(sent)


def test_ai_reason_failure_still_explains_what_the_evidence_suggests():
    with patch.object(prebooking_reason.boto3, "client", side_effect=RuntimeError("offline")):
        reason = prebooking_reason.infer("payer_rejected", {"payer_status": "REJECTED"})
    assert reason.startswith("Likely explanation")
    assert "member" in reason.lower()
    assert "did not identify" in reason


def test_ai_reason_discards_unstructured_model_text():
    with patch.object(prebooking_reason.boto3, "client") as client:
        client.return_value.converse.return_value = {"output": {"message": {"content": [
            {"text": "The payer omitted a $50 copay."}]}}}
        reason = prebooking_reason.infer("missing_benefits", {
            "coverage": "active", "missing_terms": ["office copay"]})
    assert "$50" not in reason
    assert "Likely explanation (inference)" in reason


def test_ai_reason_can_name_a_nonapplicable_benefit_without_assuming_zero_cost():
    with patch.object(prebooking_reason.boto3, "client") as client:
        client.return_value.converse.return_value = {"output": {"message": {"content": [
            {"toolUse": {"name": "choose_prebooking_cause",
                         "input": {"reason_code": "category_not_applicable"}}}]}}}
        reason = prebooking_reason.infer("missing_benefits", {
            "coverage": "active", "missing_terms": ["office copay"]})
    assert "does not apply to this plan" in reason
    assert "did not say the visit costs $0" in reason


def test_ai_reason_does_not_call_present_office_rows_omitted():
    payload = {"plans": [{"benefits": {"coInsurance": [{
        "service": {"system": "STC", "value": "98"}, "percent": 0,
        "messages": ["synthetic office condition"]}]}}]}
    assert prebooking_reason.cost_share_rows_present(payload, ["98"])
    assert not prebooking_reason.cost_share_rows_present(payload, ["73"])
    reply = {"output": {"message": {"content": [{"toolUse": {
        "name": "choose_prebooking_cause", "input": {"reason_code": "visit_conditions_unclear"}}}]}}}
    with patch.object(prebooking_reason.boto3, "client") as client:
        client.return_value.converse.return_value = reply
        reason = prebooking_reason.infer("missing_benefits", {
            "coverage": "active", "missing_terms": ["office copay or coinsurance"],
            "service_types": ["98"], "cost_share_rows_present": True})
    assert "returned cost-sharing rows" in reason
    assert "different type of visit or location" in reason
    choices = client.return_value.converse.call_args.kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"]["reason_code"]["enum"]
    assert "eligibility_terms_omitted" not in choices


def test_conflicting_payer_coinsurance_rows_explain_why_no_term_was_selected():
    from app.insurance.commercial import normalize_stedi_271

    payload = response(status="ACTIVE_COVERAGE", service="98")
    payload["benefitsInformation"] = [{"code": "A", "benefitPercent": 0,
        "coverageLevelCode": "IND", "inPlanNetworkIndicatorCode": "Y",
        "serviceTypeCodes": ["98"], "messages": [label]}
        for label in ("Specialist", "Other office condition")]
    terms = normalize_stedi_271(payload, "98", True, office_visit=True,
                                 place_of_service="11")
    assert terms["coinsurance_percent"] is None
    assert any("different coinsurance rows" in reason for reason in terms["review_reasons"])


def test_prebooking_uses_verified_office_copay_when_rate_is_missing():
    from copy import deepcopy
    from app.api.routers import commercial_estimator
    from test_collection_guidance import scenario
    from testing.synthetic_policy import COPAY_POLICY

    case = scenario(1500)
    comparison = {"status": "matched", "binding": "synthetic-binding", "match_version": 2}
    case["identity"] = comparison
    case["benefits"].update(identity=comparison, service_date_matches=True,
                            oop_remaining_cents=331000, coinsurance_percent="0")
    case["price_range"].update(complete=False, patient_min_cents=None, patient_max_cents=None)
    line = case["price_range"]["lines"][0]
    line.update(allowed_cents=None, allowed_basis="clinic_contract", calculation=None,
                rate_resolution={"status": "RATE_UNAVAILABLE"})
    line["benefits"].update(copay_cents=1500, coinsurance_percent="0",
        oop_remaining_cents=331000, copay_period="VISIT", copay_id="synthetic-office-copay",
        reported_options=[{"id": "synthetic-office-copay", "kind": "copay",
            "service_type_code": "98", "amount_cents": 1500, "network": "IN_NETWORK",
            "time_period": "VISIT", "coverage_level": "INDIVIDUAL",
            "payer_note": "PHYSICIAN OFFICE VISIT SPECIALIST"}])
    selected = [{"id": "office", "code": "99203", "units": 1}]
    with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[
            {"stedi_payer_id": "EXAMPLE", "rules": {"collection_policy": COPAY_POLICY}}]):
        result = staff_prebooking._prospective_collection(network_participation.NMCT_PRACTICE_ID,
            "EXAMPLE", case["benefits"], comparison, case["price_range"], selected, None, False)
    assert result["amount_cents"] == 1500
    assert result["basis"] == "office_copay"
    assert result["components"] is None

    with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[]):
        no_rule = staff_prebooking._prospective_collection(network_participation.NMCT_PRACTICE_ID,
            "EXAMPLE", case["benefits"], comparison, case["price_range"], selected, None, False)
    assert no_rule["amount_cents"] is None
    assert no_rule["reason"] == "NMCT has no approved check-in collection rule for this insurer."

    details = {**PERSON, "reason": "New patient office consultation", "visit_type": "new"}
    selected = prebooking.validate_services([{"code": "99203", "units": 1}], "new", details["reason"])
    payload = response()
    payload["plans"][0]["name"] = "Example PPO"
    context = {"billing_npi": PROVIDER["npi"], "rendering_npi": PROVIDER["npi"],
               "location_id": "office-1", "place_of_service": "11"}

    def office_copay_without_rate(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        benefits.update({key: value for key, value in case["benefits"].items() if key != "identity"})
        result["network_evidence"] = {"source": "clinic_library_contract",
                                      "decision": "confirmed_participating"}
        price = deepcopy(case["price_range"])
        price["lines"][0]["id"] = selected[0]["id"]
        result["price_range"] = price

    with patch.object(commercial_estimator, "_attach_previsit_pricing",
                      side_effect=office_copay_without_rate), \
         patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[
             {"stedi_payer_id": "EXAMPLE", "rules": {"collection_policy": COPAY_POLICY}}]):
        quote = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", selected)
    assert quote["collection"]["amount_cents"] == 1500
    assert quote["allowed_total_cents"] is None
    assert quote["patient_max_cents"] is None


def test_raw_office_271_copay_reaches_arrival_collection_without_a_clinic_rate():
    from testing.synthetic_policy import COPAY_POLICY

    payload = response()
    payload["plans"][0]["name"] = "Example PPO"
    rows = payload["plans"][0]["benefits"]
    rows["statuses"].append({"status": "ACTIVE_COVERAGE",
                             "service": {"system": "STC", "value": "98"}})
    office = {"service": {"system": "STC", "value": "98"},
              "coverageLevel": "INDIVIDUAL", "network": {"indicator": "IN_NETWORK"},
              "messages": ["PHYSICIAN OFFICE VISIT SPECIALIST"]}
    rows["coPayment"] = [{**office, "amount": "15", "timePeriod": "VISIT"}]
    rows["coInsurance"] = [{**office, "percent": "0"}]
    rows["outOfPocket"] = [{"service": {"system": "STC", "value": "30"},
                            "coverageLevel": "INDIVIDUAL", "network": {"indicator": "IN_NETWORK"},
                            "amount": "3000", "timePeriod": "REMAINING"}]
    benefits = commercial.normalize_stedi_271(payload, "30", True,
        service_date="2026-10-27", place_of_service="11", provider_npi=PROVIDER["npi"])
    comparison = {"status": "matched", "binding": "synthetic", "match_version": 2}
    benefits.update(identity=comparison, network_status="in_network",
                    network_source="clinic_library_contract")
    selected = prebooking.validate_services([{"code": "99203", "units": 1}], "new", "Office visit")
    price = visit_estimate.price_lines(selected, [], benefits,
        {"cost_share": "payer_returned", "place_of_service": "11", "provider_npi": PROVIDER["npi"]},
        payload=payload, service_date="2026-10-27")
    assert benefits["copay_cents"] is None
    assert price["lines"][0]["benefits"]["copay_cents"] == 1500
    with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[
            {"stedi_payer_id": "EXAMPLE", "rules": {"collection_policy": COPAY_POLICY}}]):
        amount = staff_prebooking._prospective_collection(network_participation.NMCT_PRACTICE_ID,
            "EXAMPLE", benefits, comparison, price, selected,
            {"decision": "confirmed_participating", "source": "clinic_library_contract"}, False)
    assert amount["amount_cents"] == 1500
    assert amount["basis"] == "office_copay"

    del rows["coPayment"]
    without_copay = visit_estimate.price_lines(selected, [], benefits,
        {"cost_share": "payer_returned", "place_of_service": "11", "provider_npi": PROVIDER["npi"]},
        payload=payload, service_date="2026-10-27")
    with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[
            {"stedi_payer_id": "EXAMPLE", "rules": {"collection_policy": COPAY_POLICY}}]):
        no_amount = staff_prebooking._prospective_collection(network_participation.NMCT_PRACTICE_ID,
            "EXAMPLE", benefits, comparison, without_copay, selected,
            {"decision": "confirmed_participating", "source": "clinic_library_contract"}, False)
    assert no_amount["amount_cents"] is None
    assert staff_prebooking._deferred_collection(network_participation.NMCT_PRACTICE_ID)["amount_cents"] == 0


def test_prebooking_pdf_pattern_gives_zero_arrival_and_names_missing_clinic_rates():
    from app.api.routers import commercial_estimator

    details = {**PERSON, "reason": "Office visit and possible two-extremity needle EMG",
               "visit_type": "new"}
    lines = prebooking.validate_services([{"code": "99203", "units": 1},
        {"code": "95861", "units": 1}], "new", details["reason"])
    payload = response()
    payload["plans"][0]["name"] = "Example PPO"
    context = {"billing_npi": PROVIDER["npi"], "rendering_npi": PROVIDER["npi"],
               "location_id": "office-1", "place_of_service": "11"}

    def no_rates(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        benefits.update(network_status="in_network", network_source="clinic_library_contract")
        result["network_evidence"] = {"decision": "confirmed_participating",
                                      "source": "clinic_library_contract"}
        result["price_range"] = {"complete": False, "mode": "services", "lines": [
            {"id": row["id"], "code": row["code"], "units": 1, "allowed_cents": None,
             "rate_resolution": {"status": "RATE_UNAVAILABLE"}, "benefits": {},
             "issues": [], "conditions": []} for row in lines]}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=no_rates), \
         patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[]):
        result = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
    assert result["collection"]["amount_cents"] == 0
    assert result["collection"]["basis"] == "clinic_deferred_prebooking"
    assert result["patient_max_cents"] is None
    assert result["allowed_total_cents"] is None
    assert any("no exact NMCT contract rate" in issue for issue in result["issues"])


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


def test_estimate_request_uses_rendering_npi_without_an_organization_name():
    body = prebooking.request_body(PERSON, PAYER, {"npi": "1720615453"})
    assert body["provider"] == {"npi": "1720615453"}


def test_generic_blue_product_uses_signed_scoped_contract_plan_for_prebooking():
    from app.insurance.group_contract_support import prebooking_pricing_source, pricing_agreement_matches

    source = {"prebooking": True, "plan": "PREFERRED PROVIDER OPTION MEDICAL",
              "payer_plan_names": ["PREFERRED PROVIDER OPTION MEDICAL"], "payer_plan_bound": True}
    evidence = {"decision": "in_network_contract_supported", "document_id": "synthetic-contract",
                "product": "PREFERRED PROVIDER OPTION MEDICAL", "contract_plan_matcher": "CI"}
    assert prebooking_pricing_source(source, evidence)["plan"] == "CI"
    assert prebooking_pricing_source(source, evidence)["payer_plan_names"] == []
    assert prebooking_pricing_source({**source, "prebooking": False}, evidence)["plan"] == source["plan"]
    assert prebooking_pricing_source({**source, "plan": "Specific PPO",
        "payer_plan_names": ["Specific PPO"]}, evidence)["plan"] == "Specific PPO"
    assert prebooking_pricing_source(source, {**evidence, "document_id": None})["plan"] == source["plan"]
    network = {"network_name": "Blue Choice PPO", "plan_matcher": "CI",
               "billing_npi": "1234567893", "rendering_npi": "1720615453",
               "tin": "123456789", "location_id": "office-1", "place_of_service": "11"}
    scoped = {**evidence, "document_sha256": "synthetic-hash", "network_name": network["network_name"],
              "billing_npi": network["billing_npi"], "rendering_npi": network["rendering_npi"],
              "tin": network["tin"], "location_id": network["location_id"],
              "place_of_service": network["place_of_service"], "service_date": "2026-10-27"}
    inputs = {"service_date": "2026-10-27", "rendering_provider": {"npi": network["rendering_npi"]}}
    assert pricing_agreement_matches(scoped, network, inputs)
    assert not pricing_agreement_matches({**scoped, "document_sha256": None}, network, inputs)
    assert not pricing_agreement_matches(scoped, {**network, "location_id": "other-office"}, inputs)
    assert not pricing_agreement_matches(scoped, network, {**inputs, "service_date": "2026-10-28"})


def test_signed_prebooking_agreement_unlocks_exact_rate_lookup_without_profile_document_link():
    from app.api.routers import commercial_estimator as api
    from app.insurance.group_contract_support import prebooking_pricing_source

    network = {"network_name": "Blue Choice PPO", "plan_matcher": "CI",
               "billing_npi": "1234567893", "rendering_npi": "1720615453",
               "tin": "123456789", "location_id": "office-1", "place_of_service": "11"}
    evidence = {"decision": "in_network_contract_supported", "document_id": "synthetic-contract",
                "document_sha256": "synthetic-hash", "product": "PREFERRED PROVIDER OPTION MEDICAL",
                "contract_plan_matcher": "CI", "service_date": "2026-10-27",
                **{key: network[key] for key in ("network_name", "billing_npi", "rendering_npi",
                                               "tin", "location_id", "place_of_service")}}
    source = prebooking_pricing_source({"prebooking": True, "plan": evidence["product"],
        "payer_plan_names": [evidence["product"]], "payer_plan_bound": True,
        "rendering_provider": {"npi": network["rendering_npi"]}}, evidence)
    inputs = {"service_date": "2026-10-27", "place_of_service": "11", "reason": "Office visit",
              "rendering_provider": {"npi": network["rendering_npi"]}}
    lines = [{"id": "prospective:99213", "code": "99213", "units": 1, "source": "Prospective planning scenario"}]
    benefits = {"network_status": "in_network", "active": True}
    with patch.object(api, "_previsit_practice_path", return_value=({}, network, {"rules": {}})), \
         patch.object(api, "_network_contract_evidence", return_value={}), \
         patch.object(api.commercial_store, "matching_rates", return_value=[{"code": "99213", "allowed_cents": 7615}]) as rates, \
         patch.object(api.visit_estimate, "price_lines", return_value={"lines": []}):
        _, issues = api._previsit_range("clinic", inputs, source, "blue-route", lines, benefits,
                                       review={"selected_ids": [lines[0]["id"]]})
        assert issues == []
        rates.assert_called_once()
        _, issues = api._previsit_range("clinic", inputs, {**source, "_prebooking_contract_evidence": None},
                                       "blue-route", lines, benefits, review={"selected_ids": [lines[0]["id"]]})
        assert "no contract linked" in issues[0]


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
    assert prebooking.evaluate(rejected, PERSON, "2026-10-27")["status"] == "payer_rejected"
    birth_rejected = {"errors": [{"code": "71"}]}
    birth_result = prebooking.evaluate(birth_rejected, PERSON, "2026-10-27")
    assert birth_result["status"] == "payer_rejected"
    assert "subscriber birth date" in birth_result["message"]
    assert "temporarily unavailable" in prebooking.evaluate(
        {"errors": [{"code": "42"}]}, PERSON, "2026-10-27")["message"]
    assert "clinic administrator" in prebooking.evaluate(
        {"errors": [{"code": "41"}]}, PERSON, "2026-10-27")["message"]
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


def test_prospective_plan_selects_only_office_baseline_and_never_prices_ai_ideas():
    suggestions = {"groups": [{"label": "Possible testing", "selection_mode": "multiple",
        "codes": [{"code": "95909", "reason": "Nerve studies may be considered."},
                  {"code": "invalid", "reason": "Bad code"}]}], "notes": []}
    plan = prebooking.plan("Hand numbness; testing may be considered", "established", suggestions)
    assert plan["groups"][0]["codes"][0]["code"] == "99213"
    assert plan["groups"][0]["codes"][0]["selected_by_default"] is True
    assert plan["groups"][1]["codes"] == []
    assert "Confirm the exact planned service" in plan["groups"][1]["description"]
    assert prebooking.validate_services([{"code": "95909", "units": 1}], "established", "Hand numbness")[0]["confirmed"]
    try:
        prebooking.validate_services([{"code": "95909", "units": 1}, {"code": "95908", "units": 1}],
                                     "established", "Hand numbness")
    except ValueError:
        pass
    else:
        assert False, "Alternative nerve study levels must be rejected"


def test_prebooking_headache_reason_produces_a_usable_office_visit_baseline():
    plan = prebooking.plan("headache", "new", {"groups": [], "notes": ["No separate test is established."]})
    assert len(plan["groups"]) == 1
    assert plan["groups"][0]["codes"] == [{
        "code": "99203", "label": "Office visit",
        "reason": "Planning baseline for a new-patient office visit; the billed level can differ.",
        "role": "consultation", "service_type": "98", "selected_by_default": True,
    }]
    assert "No separate test" in plan["notes"][0]


def test_plan_api_keeps_office_baseline_when_ai_has_no_procedure_code():
    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1"})), \
                 patch.object(staff_prebooking.cpt_suggester, "suggest_preliminary_cpt_codes",
                              return_value={"groups": [], "notes": ["No separate test is established."]}) as suggest, \
                 patch.object(stedi.Client, "check", AsyncMock()) as check:
                result = await client.post("/api/staff/prebooking-estimate/plan",
                                           json={"reason": "headache", "visit_type": "new"})
                assert result.status_code == 200
                assert result.json()["groups"][0]["codes"][0]["code"] == "99203"
                assert result.json()["groups"][0]["codes"][0]["selected_by_default"] is True
                assert suggest.call_count == 1
                check.assert_not_called()
    asyncio.run(run())


def test_estimate_requires_member_id_before_a_payer_inquiry():
    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1"})), \
                 patch.object(stedi.Client, "check", AsyncMock()) as check:
                for member_id in ("", "   "):
                    result = await client.post("/api/staff/prebooking-estimate/check", json={
                        **PERSON, "member_id": member_id, "reason": "headache", "visit_type": "new",
                        "selected_services": [{"code": "99203", "units": 1}],
                    })
                    assert result.status_code == 422
                check.assert_not_called()
    asyncio.run(run())


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


def test_patient_address_comes_only_from_the_matching_person():
    reply = response()
    reply["subscriber"]["address"] = {"addressLine1": "100 Example Ave", "city": "Albany",
                                      "state": "NY", "postalCode": "12207"}
    assert prebooking.patient_address(reply, PERSON) == {
        "line1": "100 Example Ave", "city": "Albany", "state": "NY", "postal_code": "12207"}
    assert prebooking.patient_address(reply, {**PERSON, "birth_date": "1981-01-01"}) is None

    dependent = {**PERSON, "relationship": "dependent", "subscriber_given_name": "Sam",
                 "subscriber_family_name": "Example", "subscriber_birth_date": "1950-01-01"}
    reply["subscriber"]["name"]["person"]["firstName"] = "Sam"
    reply["subscriber"]["dateOfBirth"] = "1950-01-01"
    reply["dependent"] = {"name": {"person": {"firstName": "Pat", "lastName": "Example"}},
                          "dateOfBirth": "1980-01-01"}
    assert prebooking.patient_address(reply, dependent) is None  # policyholder address is not the patient's
    reply["dependent"]["address"] = {"addressLine1": "200 Example St", "city": "Troy"}
    assert prebooking.patient_address(reply, dependent) == {"line1": "200 Example St", "city": "Troy"}


def test_estimate_route_checks_identity_and_services_before_pricing():
    from app.api.routers import commercial_estimator
    from app.insurance import library_network_exclusions

    async def search(name, ids, key, *, candidates=None):
        return PAYER if ids == ["EXAMPLE"] else None

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        payer_response = response()
        payer_response["subscriber"]["address"] = {"addressLine1": "100 Example Ave", "city": "Albany"}
        estimate = {**PERSON, "reason": "Planned office consultation", "visit_type": "established",
                    "selected_services": [{"code": "99213", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": network_participation.NMCT_PRACTICE_ID, "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "complete", "payload": payer_response})) as check, \
                 patch.object(staff_prebooking.prebooking_reason, "infer", return_value=(
                     "Likely explanation (AI inference): the insurer returned general coverage without naming one plan product.")) as infer, \
                 patch.object(commercial_estimator.commercial_store, "list_connections", return_value=[]), \
                 patch.object(commercial_estimator.staff_store, "list_library", return_value=[]), \
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
                assert result.json()["patient_given_name"] == "Pat"
                assert result.json()["patient_birth_date"] == "1980-01-01"
                assert result.json()["patient_address"]["line1"] == "100 Example Ave"
                assert result.json()["patient_max_cents"] is None
                assert result.json()["collection"]["amount_cents"] == 0
                assert result.json()["network_status"] == "unknown"
                assert any("Likely explanation (AI inference)" in issue for issue in result.json()["issues"])
                assert "Likely explanation (AI inference)" in result.json()["collection"]["reason"]
                assert infer.call_args.args[0] == "missing_benefits"
                PrebookingEstimateResponse.model_validate(result.json())
                assert check.await_count == 2
                assert [call.args[0]["encounter"]["services"][0]["value"] for call in check.await_args_list] == ["30", "98"]
                create_intake.assert_not_called()
    asyncio.run(run())


def test_estimate_route_requests_benefits_for_selected_rendering_clinician():
    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        context = {"id": "network-1", "status": "verified", "billing_npi": PROVIDER["npi"],
                   "rendering_npi": "1720615453", "location_id": "office-1", "place_of_service": "11"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "clinic-1", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[context]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", AsyncMock(return_value=PAYER)), \
                 patch.object(stedi.Client, "check", AsyncMock(side_effect=[
                     {"outcome": "complete", "payload": response()},
                     {"outcome": "complete", "payload": response(service="98")},
                 ])) as check, \
                 patch.object(staff_prebooking, "_estimate_from_payer", return_value={"issues": [], "lines": []}) as estimate, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                reply = await client.post("/api/staff/prebooking-estimate/check", json={**PERSON,
                    "reason": "Office consultation", "visit_type": "established",
                    "network_context_id": context["id"],
                    "selected_services": [{"code": "99213", "units": 1}]})
                assert reply.status_code == 200, reply.text
                assert check.await_count == 2
                assert all(call.args[0]["provider"] == {"npi": context["rendering_npi"]}
                           for call in check.await_args_list)
                assert estimate.call_args.args[2] == context
    asyncio.run(run())


def test_payer_birth_date_rejection_is_explicit_and_stops_before_pricing():
    async def search(name, ids, key, *, candidates=None):
        return PAYER

    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        body = {**PERSON, "reason": "Planned office consultation", "visit_type": "new",
                "selected_services": [{"code": "99203", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={"practice_id": "primary", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", side_effect=search), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "payer_error", "payload": {
                     "id": "ec_synthetic-rejected", "errors": [{"code": "71"}]}})) as check, \
                 patch.object(staff_prebooking, "_estimate_from_payer") as price, \
                 patch.object(staff.staff_store, "create_intake") as create_intake, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                reply = await client.post("/api/staff/prebooking-estimate/check", json=body)
                assert reply.status_code == 200, reply.text
                result = reply.json()
                assert result["coverage"]["status"] == "payer_rejected"
                assert "subscriber birth date" in result["coverage"]["message"]
                assert result["network_status"] == "unknown"
                assert result["allowed_total_cents"] is None
                assert result["patient_max_cents"] is None
                assert check.await_count == 1
                price.assert_not_called()
                create_intake.assert_not_called()
    asyncio.run(run())


def test_unknown_payer_rejection_includes_code_and_labeled_ai_hypothesis():
    async def run():
        app = FastAPI()
        app.include_router(staff.router)
        body = {**PERSON, "reason": "Office consultation", "visit_type": "new",
                "selected_services": [{"code": "99203", "units": 1}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test", headers={"Origin": "http://test"}) as client:
            with patch.object(staff, "require_staff", AsyncMock(return_value={
                    "practice_id": "primary", "practice_name": "Example Clinic"})), \
                 patch.object(staff_prebooking.clinic_store, "get_for_practice", return_value=PROVIDER), \
                 patch.object(staff_prebooking.commercial_store, "list_networks", return_value=[]), \
                 patch.object(staff_prebooking, "_today", return_value="2026-10-27"), \
                 patch.object(staff_prebooking.stedi_financial, "credentials", return_value="synthetic-key"), \
                 patch.object(staff_prebooking.stedi_payers, "search_payer", AsyncMock(return_value=PAYER)), \
                 patch.object(stedi.Client, "check", AsyncMock(return_value={"outcome": "payer_error",
                     "payload": {"errors": [{"code": "99"}]}})), \
                 patch.object(staff_prebooking.prebooking_reason, "infer", return_value=(
                     "Likely explanation (AI inference): the payer could not process the submitted policy.")) as infer, \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot",
                                      "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                reply = await client.post("/api/staff/prebooking-estimate/check", json=body)
        assert reply.status_code == 200, reply.text
        result = reply.json()
        assert result["coverage"]["status"] == "payer_rejected"
        assert "Insurer rejection code 99" in result["coverage"]["message"]
        assert "Likely explanation (AI inference)" in result["coverage"]["message"]
        assert result["collection"]["amount_cents"] == 0
        assert "Likely explanation (AI inference)" in result["collection"]["reason"]
        assert result["patient_max_cents"] is None
        assert infer.call_args.args[1]["error_codes"] == ["99"]
    asyncio.run(run())


@patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[])
def test_prospective_amount_requires_member_binding_and_verified_network(_connections):
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
        result["price_range"] = {"complete": True, "conditional": False, "mode": "services",
            "allowed_min_cents": 10000, "allowed_max_cents": 10000,
            "plan_min_cents": 7500, "plan_max_cents": 7500,
            "patient_min_cents": 2500, "patient_max_cents": 2500,
            "lines": [{"id": "prospective:99213", "code": "99213", "units": 1, "allowed_cents": 10000,
                       "allowed_basis": "clinic_contract", "rate_source": "Synthetic signed rate",
                       "rate_resolution": {"status": "RESOLVED"},
                       "calculation": {"patient_cents": 2500, "plan_cents": 7500,
                                       "copay_cents": 2500, "deductible_cents": 0,
                                       "coinsurance_cents": 0, "oop_adjustment_cents": 0,
                                       "allowed_cap_adjustment_cents": 0}, "benefits": benefits,
                       "issues": [], "conditions": []}]}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=priced), \
         patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[]):
        result = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, details, context, PAYER,
            PROVIDER, payload, "2026-10-27", lines)
        assert result["estimate_status"] == "priced"
        assert result["allowed_total_cents"] == 10000
        assert result["plan_total_cents"] == 7500
        assert result["patient_max_cents"] == 2500
        assert result["lines"][0]["rate_source"] == "Synthetic signed rate"
        assert result["lines"][0]["benefits"]["copay_cents"] is None
        policy = {"mode": "collect_supported_advance", "approved_by": "synthetic-admin",
                  "approved_at": "2026-01-01T00:00:00Z", "reference": "synthetic-policy"}
        with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[
                {"stedi_payer_id": "EXAMPLE", "rules": {"collection_policy": policy}}]):
            approved = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
                details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
            assert approved["collection"]["amount_cents"] == 2500
            payer_dependent = response()
            payer_dependent["plans"][0]["name"] = "Example PPO"
            payer_dependent["subscriber"]["name"]["person"] = {"firstName": "Parent", "lastName": "Example"}
            payer_dependent["subscriber"]["dateOfBirth"] = "1950-01-01"
            payer_dependent["dependent"] = {"name": {"person": {"firstName": "Pat", "lastName": "Example"}},
                "dateOfBirth": "1980-01-01", "relationToSubscriberCode": "19"}
            assert prebooking.evaluate(payer_dependent, details, "2026-10-27")["status"] == "active_today"
            dependent_estimate = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
                details, context, PAYER, PROVIDER, payer_dependent, "2026-10-27", lines)
            assert dependent_estimate["collection"]["amount_cents"] == 2500
            assert dependent_estimate["network_status"] == "in_network"
            payer_dependent["dependent"]["dateOfBirth"] = "1981-01-01"
            mismatch = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
                details, context, PAYER, PROVIDER, payer_dependent, "2026-10-27", lines)
            assert mismatch["collection"]["amount_cents"] == 0
            assert mismatch["patient_max_cents"] is None
            assert mismatch["collection"]["basis"] == "clinic_deferred_prebooking"
        with patch.object(staff_prebooking.commercial_store, "list_connections", return_value=[]):
            no_policy = staff_prebooking._estimate_from_payer("clinic-1", details, context, PAYER,
                PROVIDER, payload, "2026-10-27", lines)
            assert no_policy["patient_max_cents"] is None
            assert no_policy["collection"]["amount_cents"] is None
        PrebookingEstimateResponse.model_validate({"coverage": prebooking.evaluate(payload, details, "2026-10-27"),
            "payer": PAYER["displayName"], "checked_at": "2026-10-27T12:00:00Z", "checked_on": "2026-10-27",
            "check_ids": [], "patient_given_name": "Pat", "patient_family_name": "Example",
            "patient_birth_date": "1980-01-01", "patient_address": None, **result})

    def office_terms(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        result["price_range"]["lines"][0]["benefits"] = {**benefits, "copay_cents": 2500,
            "coinsurance_percent": 20, "deductible_remaining_cents": 10000}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=office_terms):
        result = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, details, context, PAYER,
            PROVIDER, payload, "2026-10-27", lines)
        assert result["benefits"]["copay_cents"] == 2500
        assert result["benefits"]["coinsurance_percent"] == 20
        assert result["benefits"]["deductible_remaining_cents"] == 10000

        no_member = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID, {**details, "member_id": ""},
            context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert no_member["estimate_status"] == "needs_review"
        assert no_member["network_status"] == "unknown"
        assert no_member["issues"][0].startswith("Benefits did not match")
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

    def out_of_network(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        benefits.update(network_status="out_of_network", network_source="clinic_nonparticipation_rule")

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=out_of_network):
        outside = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert outside["network_status"] == "out_of_network"
        assert outside["allowed_total_cents"] is None

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
        assert any("did not add up to the visit total" in issue for issue in mismatch["issues"])

    def missing_rate(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        result["price_range"]["lines"][0]["rate_resolution"] = {"status": "RATE_UNAVAILABLE"}

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=missing_rate):
        unpriced = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert unpriced["allowed_total_cents"] is None

    def missing_benefit(result, practice_id, inputs, source, payer_id, benefits, **kwargs):
        priced(result, practice_id, inputs, source, payer_id, benefits, **kwargs)
        result["price_range"]["complete"] = False
        result["price_range"]["lines"][0]["calculation"] = None

    with patch.object(commercial_estimator, "_attach_previsit_pricing", side_effect=missing_benefit):
        unresolved = staff_prebooking._estimate_from_payer(network_participation.NMCT_PRACTICE_ID,
            details, context, PAYER, PROVIDER, payload, "2026-10-27", lines)
        assert unresolved["patient_max_cents"] is None
        assert unresolved["allowed_total_cents"] == 10000
        assert unresolved["lines"][0]["allowed_cents"] == 10000
        assert unresolved["lines"][0]["rate_source"] == "Synthetic signed rate"
        assert unresolved["lines"][0]["patient_cents"] is None


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
                     "collection": {"amount_cents": 2500, "basis": "anticipated_services",
                                    "reason": "Synthetic advance.", "components": None},
                     "estimate_status": "priced", "issues": []}), \
                 patch.dict(os.environ, {"STEDI_EXECUTION_MODE": "commercial_pilot", "STEDI_LIVE_ELIGIBILITY_ENABLED": "true"}):
                result = await client.post("/api/staff/prebooking-estimate/check", json=body)
                assert result.status_code == 200, result.text
                assert check.await_count == 2
                assert result.json()["check_ids"] == ["ec_synthetic-123", "ec_synthetic-uncertain"]
                assert result.json()["patient_max_cents"] is None
                assert result.json()["collection"]["amount_cents"] is None
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
