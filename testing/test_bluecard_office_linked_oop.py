"""Synthetic office and general-medical responses; no patient records."""
from copy import deepcopy

from app.api.routers import commercial_estimator as api
from app.insurance import visit_estimate
from app.insurance.stedi_benefits.linked import merge_linked_medical_oop


DAY = "2026-11-10"
PERSON = {"memberId": "SYNTHETIC-1", "name": {"person": {"firstName": "Alex", "lastName": "Example"}}}
PLAN = [{"name": "Preferred Provider Organization"}]


def row(code, stc, amount=None, period=None, note=None):
    value = {"code": code, "serviceTypeCodes": [stc], "coverageLevelCode": "IND",
             "inPlanNetworkIndicatorCode": "Y", "planCoverageDescription": PLAN[0]["name"]}
    if amount is not None:
        value["benefitAmount"] = amount
    if period:
        value["timeQualifierCode"] = period
    if note:
        value["messages"] = [note]
    return value


def responses():
    primary = {"payerId": "QDTRP", "subscriber": PERSON, "plans": PLAN,
               "benefitsInformation": [row("1", "98"), row("B", "98", "60", "27", "Specialist"),
                                      row("C", "98", "0", "29"),
                                      {**row("A", "98", note="Specialist"), "benefitPercent": "0"}]}
    general = {"payerId": "QDTRP", "subscriber": PERSON, "plans": PLAN,
               "benefitsInformation": [row("1", "30"), row("C", "30", "1250", "23"),
                                      row("C", "30", "1111.12", "29"),
                                      row("G", "30", "5000", "23"),
                                      row("G", "30", "4290.53", "29")]}
    primary["_service_payloads"] = {"30": general}
    return primary


def test_linked_general_oop_supports_office_copay_without_using_general_deductible():
    payload = responses()
    coverage = {"active_coverages": 1}
    benefits = api._previsit_benefits(payload, coverage, DAY, ["98", "30"],
        inputs={"service_date": DAY, "lines": [{"code": "99203", "units": 1, "service_status": "scheduled"}]}, source={})
    assert benefits["copay_cents"] == 6000
    assert benefits["deductible_remaining_cents"] == 0
    assert benefits["oop_annual_cents"] == 500000
    assert benefits["oop_remaining_cents"] == 429053
    price = visit_estimate.price_lines([{"code": "99203", "units": 1}], [], benefits,
        {"cost_share": "payer_returned"}, payload=payload, service_date=DAY)
    assert price["lines"][0]["benefits"]["deductible_remaining_cents"] == 0
    assert price["lines"][0]["benefits"]["oop_remaining_cents"] == 429053


def test_mismatched_general_response_cannot_supply_office_limit():
    payload = responses()
    payload["_service_payloads"]["30"] = deepcopy(payload["_service_payloads"]["30"])
    payload["_service_payloads"]["30"]["subscriber"] = {**PERSON, "memberId": "SYNTHETIC-OTHER"}
    benefits = api._previsit_benefits(payload, {"active_coverages": 1}, DAY, ["98", "30"],
        inputs={"service_date": DAY, "lines": [{"code": "99203", "units": 1, "service_status": "scheduled"}]}, source={})
    assert benefits["oop_remaining_cents"] is None


def test_conflicting_office_and_general_oop_requires_review():
    office = {"oop_remaining_cents": 10000, "review_reasons": [], "requires_review": False}
    merged = merge_linked_medical_oop(responses(), office, DAY)
    assert merged["oop_remaining_cents"] == 10000
    assert merged["requires_review"] is True
    assert "disagree" in merged["review_reasons"][0]
