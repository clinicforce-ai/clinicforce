"""A linked appointment's structured service may supply one planned code."""

from app.insurance.ecw.ecw_visit_coding import coded_visit
from app.api.routers import commercial_estimator


def _lines(appointments, *, orders=None, visit_type=None):
    visit = {"resourceType": "Encounter", "id": "visit", "status": "planned"}
    if visit_type:
        visit["type"] = [{"text": visit_type}]
    return coded_visit(visit, orders or [], [], [], [], "Encounter/visit", set(), appointments)[2]


def _appointment(code, *, status="booked", system="http://www.ama-assn.org/go/cpt"):
    return {"resourceType": "Appointment", "id": "scheduled", "status": status,
            "serviceType": [{"coding": [{"system": system, "code": code}]}]}


def test_single_structured_appointment_cpt_is_scheduled():
    lines = _lines([_appointment("99203")])
    assert lines == [{
        "id": "Appointment:scheduled:99203", "code": "99203", "units": 1,
        "charge": None, "modifiers": [], "diagnosis": None,
        "service_status": "scheduled", "source": "eCW Appointment.serviceType",
        "needs_confirmation": False,
    }]
    selected = commercial_estimator._previsit_codes({"lines": lines})
    assert [(row["code"], row["units"], row["source_detail"]) for row in selected] == [
        ("99203", 1, "eCW Appointment.serviceType")]


def test_single_structured_encounter_office_cpt_supplies_one_unit():
    visit = {"resourceType": "Encounter", "id": "visit", "status": "planned",
             "serviceType": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99203"}]}}
    lines = coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2]
    assert [(row["code"], row["units"], row.get("source")) for row in lines] == [
        ("99203", 1, "eCW Encounter.serviceType")]
    selected = commercial_estimator._previsit_codes({"lines": lines})
    assert [(row["code"], row["units"], row["source_detail"]) for row in selected] == [
        ("99203", 1, "eCW Encounter.serviceType")]


def test_encounter_office_cpt_requires_one_explicit_compatible_service_code():
    visit = {"resourceType": "Encounter", "id": "visit", "status": "planned",
             "serviceType": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99203"}]}}
    visit["type"] = [{"text": "Established patient visit"}]
    assert coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2][0]["units"] is None
    visit.pop("type")
    visit["serviceType"]["coding"].append({"system": "http://www.ama-assn.org/go/cpt", "code": "99204"})
    assert all(row["units"] is None for row in coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2])
    visit["serviceType"]["coding"] = [{"system": "urn:local", "code": "99203"}]
    assert coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2] == []
    visit["serviceType"]["coding"] = [{"code": "99203"}]
    assert coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2][0]["units"] is None
    visit["serviceType"]["coding"] = [{"system": "http://www.ama-assn.org/go/cpt", "code": "99203"}]
    visit["status"] = "cancelled"
    assert coded_visit(visit, [], [], [], [], "Encounter/visit", set())[2][0]["units"] is None


def test_appointment_hint_cannot_override_visit_coding():
    order = {"resourceType": "ServiceRequest", "id": "order", "encounter": {"reference": "Encounter/visit"},
             "code": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204"}]}}
    lines = _lines([_appointment("99203")], orders=[order])
    assert [line["code"] for line in lines] == ["99204"]


def test_ambiguous_cancelled_or_text_only_appointment_does_not_select_cpt():
    ambiguous = _appointment("99203")
    ambiguous["serviceType"].append({"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99204"}]})
    assert _lines([ambiguous]) == []
    assert _lines([_appointment("99203", status="cancelled")]) == []
    assert _lines([_appointment("99203", system="urn:ecw:service")]) == []
    assert _lines([_appointment("95910")]) == []  # Units are not supplied by Appointment.serviceType.
    assert _lines([_appointment("99211")]) == []  # Not a consultation alternative in this estimator.
    assert _lines([_appointment("99203")], visit_type="Established patient visit") == []
    assert _lines([{"resourceType": "Appointment", "id": "scheduled", "status": "booked",
                    "comment": "Possible 99203", "serviceType": [{"text": "New patient"}]}]) == []
