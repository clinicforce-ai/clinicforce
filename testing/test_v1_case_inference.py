"""Case-specific coding suggestions and collection decisions, with no live model calls."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from app.insurance import cpt_rules, cpt_suggester, documented, v1_requirements, v1_sources
from app.insurance.golden import GoldenCaseError


def response(payload):
    return {"output": {"message": {"content": [{"text": json.dumps(payload)}]}}}


def tool_response(payload):
    return {"output": {"message": {"content": [{"toolUse": {"name": "submit_code_suggestions", "input": payload}}]}}}


def group(key, codes, mode="multiple", **extra):
    return {"id": key, "label": key, "selection_mode": mode, "codes": [{"code": code, "reason": "Candidate for the uploaded planned care"} for code in codes], **extra}


def test_cpt_suggestions_use_bedrock_tool_output_and_only_schedule_codes():
    rates = [{"code": "70450", "description": "CT head or brain without contrast", "allowed_cents": 1234}, {"code": "76856", "description": "Pelvic ultrasound", "allowed_cents": 1234}]
    payload = {"groups": [group("head-imaging", ["70450"], category="diagnostic")], "notes": []}
    with patch.object(cpt_suggester.boto3, "client") as client:
        client.return_value.converse.return_value = tool_response(payload)
        suggestions = cpt_suggester.suggest_cpt_codes("Head is aching; CT scan is needed", rates)
    assert suggestions == payload
    call = client.return_value.converse.call_args.kwargs
    assert call["toolConfig"]["toolChoice"] == {"tool": {"name": "submit_code_suggestions"}}
    candidates = json.loads(call["messages"][0]["content"][0]["text"])["available_codes"]
    assert candidates == [{"code": "70450", "description": "CT head or brain without contrast"}]


def test_cpt_suggestions_passes_the_last_completed_visit_to_bedrock():
    rates = [{"code": "95913", "description": "Nerve conduction studies", "allowed_cents": 1234}, {"code": "70450", "description": "CT head", "allowed_cents": 1234}]
    payload = {"groups": [group("nerve-study", ["95913"], category="diagnostic")], "notes": []}
    history = [{"date": "2026-08-19", "reason": "Neuropathy study", "coded_services": ["95913"]}]
    with patch.object(cpt_suggester.boto3, "client") as client:
        client.return_value.converse.return_value = tool_response(payload)
        cpt_suggester.suggest_cpt_codes("New neuropathy follow-up", rates, history)
    request = json.loads(client.return_value.converse.call_args.kwargs["messages"][0]["content"][0]["text"])
    assert request["prior_completed_appointments"] == history
    assert request["available_codes"][0]["code"] == "95913"


def test_cpt_suggestions_fall_back_to_uploaded_description_matches():
    rates = [{"code": "70450", "description": "CT head or brain without contrast", "allowed_cents": 1234}, {"code": "76856", "description": "Pelvic ultrasound", "allowed_cents": 1234}]
    with patch.object(cpt_suggester.boto3, "client") as client:
        client.return_value.converse.side_effect = RuntimeError("Bedrock down")
        suggestions = cpt_suggester.suggest_cpt_codes("Head is aching; CT scan is needed", rates)
    assert [candidate["code"] for candidate in suggestions["groups"][0]["codes"]] == ["70450"]
    assert "Bedrock coding review was unavailable" in suggestions["notes"][0]


def test_cpt_fallback_ignores_generic_words_in_other_schedule_descriptions():
    rates = [
        {"code": "70450", "description": "Computed tomography, head or brain; without contrast material", "allowed_cents": 1234},
        {"code": "95816", "description": "Electroencephalogram, routine, awake and drowsy", "allowed_cents": 1234},
        {"code": "97110", "description": "Therapeutic exercises to develop strength and endurance, each 15 minutes", "allowed_cents": 1234},
    ]
    with patch.object(cpt_suggester.boto3, "client") as client:
        client.return_value.converse.side_effect = RuntimeError("Bedrock down")
        suggestions = cpt_suggester.suggest_cpt_codes("Headache with nausea; CT head without contrast is planned", rates)
    assert [candidate["code"] for candidate in suggestions["groups"][0]["codes"]] == ["70450"]


def test_cpt_suggestions_retry_a_transient_bedrock_error():
    rates = [{"code": "70450", "description": "CT head or brain without contrast", "allowed_cents": 1234}]
    payload = {"groups": [group("head-imaging", ["70450"], category="diagnostic")], "notes": []}
    unavailable = ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "busy"}}, "Converse")
    with patch.object(cpt_suggester.boto3, "client") as client, patch.object(cpt_suggester.time, "sleep"):
        client.return_value.converse.side_effect = [unavailable, tool_response(payload)]
        assert cpt_suggester.suggest_cpt_codes("CT head is planned", rates) == payload
    assert client.return_value.converse.call_count == 2


def test_clinical_excerpts_are_literal_private_and_do_not_invent_diagnoses():
    pages = [(1, "From: someone@example.test\nPassword: TEST-SECRET\nNew patient\nPatient reports knee pain.\nNo surgery is planned."), (2, "A generic insurance discussion.")]
    payload = {"excerpts": [{"page": 1, "quote": quote, "category": category} for quote, category in [("New patient", "visit_type"), ("Patient reports knee pain.", "symptoms"), ("No surgery is planned.", "planned_care"), ("Severe osteoarthritis", "condition"), ("Password: TEST-SECRET", "condition")]]}
    with patch.object(cpt_suggester.boto3, "client") as client:
        client.return_value.converse.return_value = response(payload)
        findings = cpt_suggester.clinical_context_from_pages(pages)
        sent = client.return_value.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
    assert "TEST-SECRET" not in sent
    assert "someone@example.test" not in sent
    assert findings["clinical_context"] == "New patient\n\nPatient reports knee pain.\n\nNo surgery is planned."
    assert findings["visit_type"] == "new"
    case = documented.pending_case({})
    case.update(service_lines=[{"code": "OLD"}], suggestions_reviewed=True)
    documented.add_document(case, "clinical", {"findings": findings})
    assert case["clinical_context"] == findings["clinical_context"]
    assert case["service_lines"] == case["service_groups"] == []
    assert not case["suggestions_reviewed"]


def test_non_neurology_case_uses_its_own_groups_and_uploaded_prices():
    rates = [{"code": code, "description": "Uploaded description", "allowed_cents": 1234} for code in ("99213", "99214", "20610", "J3301")]
    groups = [group("Injection procedure", ["20610"], "single"), group("Medication", ["J3301"], requires_groups=["Injection procedure"]), group("Office visit alternatives", ["99213", "99214", "99999"], "single", category="visit")]
    groups[1]["codes"][0].update(requires_quantity=True, unit_label="documented billing units")
    lines, normalized = cpt_rules.grouped_candidates({"groups": groups}, rates)
    assert [g["id"] for g in normalized] == ["Office visit alternatives", "Injection procedure", "Medication"]
    assert {line["code"] for line in lines} == {line["code"] for line in rates}
    assert all(line["allowed_cents"] == 1234 for line in lines)
    with pytest.raises(GoldenCaseError, match="one alternative"):
        cpt_rules.validate_selection(["99213", "99214"], normalized, lines, {})
    with pytest.raises(GoldenCaseError, match="companion"):
        cpt_rules.validate_selection(["J3301"], normalized, lines, {"J3301": 2})
    with pytest.raises(GoldenCaseError, match="cannot be guessed"):
        cpt_rules.validate_selection(["20610", "J3301"], normalized, lines, {})
    case = documented.pending_case({})
    case.update(next_stage="service", service_lines=lines, service_groups=normalized)
    documented.select_services(case, ["20610", "J3301", "99213"], {"J3301": 2})
    selected = [line for line in case["service_lines"] if line["selected"]]
    assert [line["code"] for line in selected] == ["99213", "20610", "J3301"]
    assert selected[-1]["allowed_cents"] == 2468
    assert selected[-1]["unit_allowed_cents"] == 1234


def test_missing_companion_and_invalid_quantities_fail_closed():
    rates = [{"code": "20610", "allowed_cents": 100}]
    with pytest.raises(GoldenCaseError, match="companion"):
        cpt_rules.grouped_candidates({"groups": [group("procedure", ["20610"], requires_groups=["missing"])]}, rates)
    for value in (0, -1, 1.5, True, 10001):
        with pytest.raises(GoldenCaseError):
            cpt_rules.validate_selection(["20610"], [], rates, {"20610": value})


def test_authorization_stays_with_code_page_and_network():
    pages = [(2, "95885 - Auth Info Available"), (3, "In Network No Auth Required\n95907 - Auth Info Available\nIn Network Authorization required\nOut of Network No Auth Required\n99204 - Auth Info Available\nReferral required if the plan requires a PCP\nCPT 99214\nNo authorization required")]
    rows = v1_requirements.code_requirements(pages)
    assert rows[0]["code"] == "95885" and rows[0]["reference"] == "pages 2–3"
    assert len(rows) == 4
    assert rows[-1]["status"] == "conditional"
    case = documented.pending_case({})
    case.update(coverage={"network_status": "in_network"}, service_lines=[{"code": code} for code in ("95885", "95907", "99204", "99214", "95886")])
    source = {"filename": "coverage.pdf", "observed_at": "2026-09-10T12:00:00Z", "findings": {"requirements": rows}}
    case["documents"]["coverage"] = source
    documented._apply_authorizations(case)
    assert [line["authorization_status"] for line in case["service_lines"]] == ["not_required", "required", "not_stated", "not_stated", "not_stated"]
    assert case["service_lines"][2]["referral_status"] == "conditional"
    terms = v1_requirements.contract_terms([(18, "2.13.2 PCP plans require referral. Other plans depend on the Coverage Agreement.")])
    assert terms[0]["page"] == 18
    assert v1_requirements.code_requirements([(18, terms[0]["quote"])]) == []


def test_collection_distinguishes_normal_share_limit_and_missing_evidence():
    case = documented.pending_case({})
    case["coverage"] = {"network_status": "in_network", "deductible_remaining_cents": 0, "oop_remaining_cents": 0, "coinsurance_percent": 20}
    case["service_lines"] = [{"code": "93000", "description": "Uploaded test", "allowed_cents": 13500, "selected": True, "confirmed": True, "authorization_status": "not_required", "referral_status": "not_required"}]
    case["estimate"] = documented.calculate(case)
    documented._collection_decision(case)
    estimate = case["estimate"]
    assert (estimate["normal_patient_share_cents"], estimate["oop_cap_adjustment_cents"], estimate["patient_cents"]) == (2700, -2700, 0)
    assert estimate["collect_today_cents"] == 0 and estimate["collection_action"] == "do_not_collect"
    assert not estimate["self_pay"]["available"]
    case["service_lines"][0]["referral_status"] = "not_stated"
    documented._collection_decision(case)
    assert estimate["collection_action"] == "verify_before_collecting"
    assert estimate["collect_today_cents"] is None and estimate["patient_cents"] == 0
    del case["coverage"]["coinsurance_percent"]
    case["estimate"] = documented.calculate(case)
    documented._collection_decision(case)
    assert case["estimate"]["normal_patient_share_cents"] is None
    assert case["estimate"]["nominal_coinsurance_cents"] is None
    missing = deepcopy(case)
    del missing["coverage"]["oop_remaining_cents"]
    assert documented.calculate(missing)["patient_cents"] is None
    case["documents"]["self_pay"] = documented.inspect_csv(b"CPT,Amount\n93000,89.00", "cash.csv")
    documented._collection_decision(case)
    assert case["estimate"]["self_pay"]["total_cents"] == 8900


def test_document_dates_are_not_upload_dates():
    dates = v1_sources.document_dates([(1, "Time Stamp: 2025-11-03 14:25 CST\nEffective Date: 2025-01-01")])
    assert [date["value"] for date in dates] == ["2025-11-03 14:25 CST", "2025-01-01"]
    assert v1_sources.document_dates([(1, "A document with no date.")]) == []
