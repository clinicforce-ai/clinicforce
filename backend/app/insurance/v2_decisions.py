"""Pure V2 workflow decisions and staff-facing summaries."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.insurance import cpt_rules, cpt_suggester


def demo_inputs() -> dict:
    return {"patient": {"patient_first_name": "GEORGE", "patient_last_name": "KOVAL", "patient_birth_date": "1999-01-01", "gender": "M"},
            "subscriber": {"firstName": "GEORGE", "lastName": "KOVAL", "birthDate": "1999-01-01", "memberId": "H39522358"},
            "relationship": "18", "member_id": "H39522358", "payer_id": "BCBSF", "group_number": "",
            "provider": {"lastName": "Availity synthetic provider", "npi": "1234567893"}, "submitter_id": "G2509",
            "rendering_provider": {"lastName": "Synthetic service provider", "npi": "1234567893"}, "service_type": "1",
            "service_date": datetime.now(timezone.utc).date().isoformat(), "place_of_service": "11", "diagnoses": ["J3089"],
            "patient_phone": "5552347700", "reason": "Availity's published synthetic transaction",
            "coverage_missing": [], "review_missing": [], "lines": [{"code": "92523", "description": "Availity synthetic service",
                                                                        "units": "100", "charge": "250.00", "cash_cents": None,
                                                                        "service_status": "scheduled", "modifiers": [], "diagnosis": "J3089"},
                                                                       {"code": "95913", "description": "Possible synthetic follow-up procedure",
                                                                        "units": "1", "charge": "900.00", "cash_cents": None,
                                                                        "service_status": "possible", "modifiers": [], "diagnosis": "J3089"}]}



def canonical(resource: str) -> str:
    return "estimate" if resource == "procedure_estimate" else resource



def service_lines(case: dict, resource: str) -> list[dict]:
    status = "possible" if resource == "procedure_estimate" else "scheduled"
    return [line for line in case["inputs"].get("lines", []) if line.get("service_status", "scheduled") == status]



def refresh_missing(case: dict) -> None:
    """Recalculate service gaps after eCW, fee CSV, or a confirmed suggestion changes lines."""
    inputs = case["inputs"]
    line_gaps = ("eClinicalWorks did not return clinician-coded", "eClinicalWorks did not associate every coded service",
                 "eClinicalWorks did not return the intended units", "eClinicalWorks FHIR did not return each billed line amount",
                 "Upload the clinic fee CSV")
    review = [item for item in inputs.get("review_missing", []) if not item.startswith(line_gaps)]
    lines = inputs.get("lines", [])
    diagnoses = inputs.get("diagnoses", [])
    if not lines:
        review.append("No CPT/HCPCS service has been confirmed for this appointment.")
    if not diagnoses and case.get("source_mode") != "stedi":
        review.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if case.get("source_mode") != "stedi" and lines and any(not line.get("diagnosis") or line["diagnosis"] not in diagnoses for line in lines):
        review.append("eClinicalWorks did not associate every coded service with one of the returned ICD-10 diagnoses.")
    if any(not line.get("units") for line in lines):
        review.append("Confirm the intended units for every coded service.")
    inputs["review_missing"] = list(dict.fromkeys(review))
    estimate = list(inputs["review_missing"])
    if case.get("source_mode") == "stedi":
        if any(line.get("allowed_cents") is None for line in lines):
            estimate.append("Upload the clinic contract-rate CSV with a Contracted Allowed Amount for every selected CPT/HCPCS service.")
    else:
        if not inputs.get("patient_phone"):
            estimate.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
        if any(line.get("charge") is None for line in lines):
            estimate.append("Upload the clinic fee CSV with a Billed Charge for every selected CPT/HCPCS service.")
    case["missing"] = list(dict.fromkeys(estimate))



def preserve_ecw_snapshot(existing: dict | None, inputs: dict, source: dict) -> None:
    """Do not replace known eCW facts with blanks when an optional refresh query stalls."""
    def absent(value) -> bool:
        return not any(value.values()) if isinstance(value, dict) else not value

    if not existing:
        return
    old_source = (existing.get("sources", {}).get("ecw", {}) or {})
    if old_source.get("appointment_date") != source.get("appointment_date"):
        return
    old_inputs = existing.get("inputs", {})
    timed_out = set(source.get("timed_out_resources", []))
    preserved: set[str] = set()
    if "Coverage" in timed_out:
        for key in ("subscriber", "relationship", "member_id", "payer_id", "group_number"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("Coverage")
        if not source.get("payer") and old_source.get("payer"):
            source["payer"] = old_source["payer"]
            preserved.add("Coverage")
    if timed_out & {"ServiceRequest", "Procedure", "Condition"}:
        for key in ("lines", "diagnoses"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("coded services")
    if "Patient" in timed_out and not inputs.get("patient_phone") and old_inputs.get("patient_phone"):
        inputs["patient_phone"] = old_inputs["patient_phone"]
        preserved.add("Patient")
    if "Practitioner" in timed_out:
        for key in ("provider", "rendering_provider"):
            if absent(inputs.get(key)) and not absent(old_inputs.get(key)):
                inputs[key] = copy.deepcopy(old_inputs[key])
                preserved.add("Practitioner")
    if "Location" in timed_out:
        for key in ("place_of_service", "service_type"):
            if not inputs.get(key) and old_inputs.get(key):
                inputs[key] = old_inputs[key]
                preserved.add("Location")
        if not source.get("location") and old_source.get("location"):
            source["location"] = old_source["location"]
            preserved.add("Location")
    if preserved:
        source["preserved_resources"] = sorted(preserved)
        source["missing"] = [*source.get("missing", []),
                             "Using the last saved eClinicalWorks values for: " + ", ".join(sorted(preserved)) + "."]



def request_missing(case: dict, resource: str) -> list[str]:
    inputs = case["inputs"]
    if resource == "coverage":
        return inputs.get("coverage_missing", [])
    if case.get("source_mode") == "stedi":
        if resource not in {"estimate", "procedure_estimate"}:
            return ["Stedi temporary mode supports eligibility and local estimates only; review authorization and referral manually."]
        lines = service_lines(case, resource)
        label = "possible procedure" if resource == "procedure_estimate" else "scheduled"
        missing = list(inputs.get("coverage_missing", []))
        if not inputs.get("place_of_service"):
            missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
        if not lines:
            missing.append(f"No {label} service is confirmed for this request.")
        if any(not line.get("units") for line in lines):
            missing.append("Confirm the intended units for every requested service.")
        if any(line.get("allowed_cents") is None for line in lines):
            missing.append("Upload the clinic contract-rate CSV with a Contracted Allowed Amount for every requested CPT/HCPCS service.")
        return list(dict.fromkeys(missing))
    line_gaps = ("eClinicalWorks did not return clinician-coded", "eClinicalWorks did not associate every coded service",
                 "eClinicalWorks did not return the intended units", "Confirm the intended units",
                 "No CPT/HCPCS service", "eClinicalWorks FHIR did not return each billed line amount", "Upload the clinic fee CSV")
    if canonical(resource) == "estimate":
        missing = list(inputs.get("coverage_missing", []))
        if not inputs.get("place_of_service"):
            missing.append("Administrator: map this eClinicalWorks location to its two-digit place-of-service code.")
    else:
        missing = [item for item in inputs.get("review_missing", []) if not item.startswith(line_gaps)]
    lines = service_lines(case, resource)
    label = "possible procedure" if resource == "procedure_estimate" else "scheduled"
    if not lines:
        missing.append(f"No {label} service is confirmed for this request.")
    diagnoses = inputs.get("diagnoses", [])
    if not diagnoses:
        missing.append("eClinicalWorks did not return clinician-coded ICD-10 diagnoses for these services.")
    if lines and any(not line.get("diagnosis") or line["diagnosis"] not in diagnoses for line in lines):
        missing.append("eClinicalWorks did not associate every requested service with a returned ICD-10 diagnosis.")
    if any(not line.get("units") for line in lines):
        missing.append("Confirm the intended units for every requested service.")
    if canonical(resource) == "estimate":
        if not inputs.get("patient_phone"):
            missing.append("eClinicalWorks did not return the patient's phone number required by Availity PCE.")
        if any(line.get("charge") is None for line in lines):
            missing.append("Upload the clinic fee CSV with a Billed Charge for every requested CPT/HCPCS service.")
        missing.extend(pricing_prerequisites(case))
    return list(dict.fromkeys(missing))



def _suggestion_context(inputs: dict) -> str:
    """Make the limits of eCW appointment data explicit to coding decision support."""
    reason = str(inputs.get("reason") or "").strip()
    visit_type = inputs.get("visit_type")
    status = (f"Patient visit status from eClinicalWorks: {visit_type} patient."
              if visit_type in {"new", "established"}
              else "Patient visit status: eClinicalWorks did not explicitly label this appointment new or established; do not infer it.")
    return "\n".join((reason, status,
                       "E/M complexity: eClinicalWorks did not return documented medical decision making or qualifying time. Do not choose an E/M level without clinician confirmation."))



def _ecw_coded_groups(inputs: dict, rates: list[dict]) -> tuple[list[dict], list[str]]:
    """Present actual eCW coded services first; a fee file only adds pricing."""
    available = {str(rate.get("code") or "").upper() for rate in rates}
    coded, unavailable = [], []
    for line in inputs.get("lines", []):
        code = str(line.get("code") or "").upper()
        if not code:
            continue
        coded.append({"code": code, "description": str(line.get("description") or code),
                      "reason": "Clinician-coded service returned by eClinicalWorks for this appointment.",
                      "requires_quantity": not bool(line.get("units")), "unit_label": "unit"})
        if code not in available:
            unavailable.append(code)
    if not coded:
        return [], list(dict.fromkeys(unavailable))
    return [{"id": "ecw-clinician-coded-services", "label": "Services coded in eClinicalWorks",
             "category": "other", "selection_mode": "multiple",
             "description": "These codes came from the eClinicalWorks appointment. Confirm the intended services and units before sending any payer request.",
             "requires_groups": [], "codes": coded}], list(dict.fromkeys(unavailable))



def _without_codes(suggestions: dict, excluded: set[str]) -> dict:
    """Never show the same source code again as a model-generated alternative."""
    groups = []
    for group in suggestions.get("groups", []):
        if not isinstance(group, dict):
            continue
        codes = [candidate for candidate in group.get("codes", [])
                 if isinstance(candidate, dict) and str(candidate.get("code") or "").upper() not in excluded]
        if codes:
            groups.append({**group, "codes": codes})
    return {**suggestions, "groups": groups}



def build_service_suggestions(case: dict) -> tuple[list[dict], list[dict], list[str]]:
    """Build clinician-reviewable choices from source codes plus appointment context."""
    rates = case.get("sources", {}).get("fees", {}).get("lines", [])
    inputs = case.get("inputs", {})
    history = case.get("sources", {}).get("ecw", {}).get("recent_completed_appointments", [])
    source_groups, missing_fee_codes = _ecw_coded_groups(inputs, rates)
    source_codes = {candidate["code"] for group in source_groups for candidate in group["codes"]}
    suggested = (cpt_suggester.suggest_cpt_codes(_suggestion_context(inputs), rates, history) if rates
                 else cpt_suggester.suggest_preliminary_cpt_codes(_suggestion_context(inputs), history))
    raw = _without_codes(suggested, source_codes)
    raw["groups"] = [*source_groups, *raw.get("groups", [])]
    lines, groups = cpt_rules.grouped_candidates(raw, rates, allow_unpriced=not rates)
    groups.sort(key=lambda group: group["id"] != "ecw-clinician-coded-services")
    notes = [str(note)[:1000] for note in raw.get("notes", []) if isinstance(note, str)][:20]
    if missing_fee_codes:
        notes.append("eClinicalWorks returned coded service(s) absent from this clinic fee CSV: " + ", ".join(missing_fee_codes) + ". Add those fee rows before estimating them.")
    if not rates:
        notes.append("These are unpriced clinical review candidates. Upload the clinic fee CSV before creating a payer request or an estimate.")
    if inputs.get("visit_type") not in {"new", "established"}:
        notes.append("eClinicalWorks did not explicitly return new-versus-established status. Confirm it with the clinician; do not infer it from this appointment.")
    notes.append("CPT choices require clinician review. eCW did not provide medical decision making or qualifying time, so ClinicForce cannot establish an E/M complexity level.")
    return lines, groups, notes



def front_desk_summary(case: dict) -> dict:
    coverage_transaction = case.get("transactions", {}).get("coverage", {})
    coverage = coverage_transaction.get("result", {})
    coverage_source = coverage_transaction.get("source") or coverage.get("source")
    plans = coverage.get("plans") or []
    plan_text = " ".join(str(plan.get(key) or "") for plan in plans for key in ("name", "planName", "insuranceType"))
    hmo = bool(re.search(r"\bHMO\b", plan_text, re.I))
    benefits = [benefit for plan in plans for benefit in plan.get("benefits", [])]
    referral_required = hmo or any(benefit.get("referralNumber") or "referral required" in str(benefit.get("description", "")).lower() for benefit in benefits)
    authorization_required = any(benefit.get("authorizationRequired") is True for benefit in benefits)
    authorization_unknown = any(benefit.get("authorizationRequiredUnknown") is True for benefit in benefits)
    service_date = case.get("inputs", {}).get("service_date") or ""

    def review(kind: str) -> dict:
        result = case.get("transactions", {}).get(kind, {}).get("result", {})
        procedures = result.get("procedures") or []
        procedure = procedures[0] if len(procedures) == 1 else {}
        start = result.get("certificationEffectiveDate") or result.get("fromDate") or procedure.get("certificationEffectiveDate") or procedure.get("fromDate")
        end = result.get("certificationExpirationDate") or result.get("toDate") or procedure.get("certificationExpirationDate") or procedure.get("toDate")
        reference = result.get("certificationNumber") or procedure.get("certificationNumber")
        approved = result.get("status_code") in {"A1", "NA"} and all(item.get("statusCode") in {None, "A1", "NA"} for item in procedures)
        if kind == "referral" and referral_required and result.get("status_code") == "A1":
            approved = bool(reference and start and end and start[:10] <= service_date <= end[:10])
        return {"required": referral_required if kind == "referral" else authorization_required,
                "unknown": False if kind == "referral" else authorization_unknown,
                "approved": approved, "status": result.get("status"), "reference": reference,
                "effective": start, "expiration": end}

    referral, authorization = review("referral"), review("authorization")
    manual = case.get("manual_review") if isinstance(case.get("manual_review"), dict) else {}
    labels = {"unconfirmed": "Not verified", "not_required": "Not required", "approved": "Approved", "required": "Required / pending"}
    if coverage_source == "stedi":
        for target, status_key, reference_key in ((referral, "referral_status", "referral_reference"),
                                                  (authorization, "authorization_status", "authorization_reference")):
            status = manual.get(status_key)
            if status in labels:
                target.update(required=status in {"approved", "required"}, unknown=status == "unconfirmed",
                              approved=status in {"approved", "not_required"}, status=labels[status],
                              reference=manual.get(reference_key) or None)
    scheduled = case.get("transactions", {}).get("estimate", {}).get("result", {})
    possible = case.get("transactions", {}).get("procedure_estimate", {}).get("result", {})

    def price(kind: str, result: dict) -> dict:
        lines = service_lines(case, kind)
        cash = None
        if lines and all(line.get("cash_cents") is not None for line in lines):
            try:
                total = sum(Decimal(line["cash_cents"]) * Decimal(str(line.get("units") or 1)) for line in lines)
                cash = int(total) if total == total.to_integral_value() else None
            except (InvalidOperation, ValueError):
                pass
        return {"codes": [line["code"] for line in lines], "insurance_cents": result.get("patient_cents") if result.get("estimate_available") else None,
                "allowed_cents": result.get("allowed_cents") if result.get("estimate_available") else None,
                "cash_cents": cash, "network": result.get("provider_network"), "network_status": result.get("provider_network_status", "unconfirmed")}

    scheduled_price, possible_price = price("estimate", scheduled), price("procedure_estimate", possible)
    if coverage_source == "stedi" and manual.get("network_status") in {"in_network", "out_of_network"}:
        network = str(manual["network_status"])
        scheduled_price.update(network=network.replace("_", " ").title() + " (staff verified)", network_status=network)
        possible_price.update(network=network.replace("_", " ").title() + " (staff verified)", network_status=network)
    active = coverage.get("active")
    if active is False:
        action, heading = "self_pay", "Coverage inactive — use the clinic cash price"
    elif active is not True:
        action, heading = "verify", "Verify eligibility before scheduling"
    elif coverage_source == "stedi":
        if referral["required"] and not referral["approved"]:
            action, heading = "do_not_schedule", "Do not schedule — referral requires payer confirmation"
        elif authorization["required"] and not authorization["approved"]:
            action, heading = "do_not_schedule", "Do not schedule — authorization requires payer confirmation"
        elif manual.get("price_path") == "cash":
            action, heading = "self_pay", "Cash price selected — insurance estimate remains pending"
        else:
            action, heading = "verify", "Insurance estimate pending — verify payer details before scheduling"
    elif referral["required"] and not referral["approved"]:
        action, heading = "do_not_schedule", "Do not schedule — referral required"
    elif authorization["required"] and not authorization["approved"]:
        action, heading = "do_not_schedule", "Do not schedule — authorization required"
    elif authorization["unknown"] and not authorization["approved"]:
        action, heading = "verify", "Review the authorization requirement before scheduling"
    elif scheduled_price["network_status"] == "out_of_network":
        action, heading = "review_oon", "Out-of-network — review OON benefits before collecting"
    elif scheduled_price["insurance_cents"] is not None:
        action, heading = "collect", f"Collect ${scheduled_price['insurance_cents'] / 100:,.2f} for the scheduled visit"
    else:
        action, heading = "verify", "Complete the scheduled visit estimate"
    return {"action": action, "heading": heading, "coverage": "active" if active is True else "inactive" if active is False else "unconfirmed",
            "hmo": hmo, "referral": referral, "authorization": authorization,
            "scheduled": scheduled_price, "possible": possible_price,
            "manual_review": {"source": manual.get("source"), "network_status": manual.get("network_status"),
                              "price_path": manual.get("price_path")}}



def pricing_prerequisites(case: dict) -> list[str]:
    """Require the flowchart's payer-review decision before an insured quote."""
    summary = front_desk_summary(case)
    referral = summary["referral"]
    authorization = summary["authorization"]
    missing = []
    if referral["required"] and not referral["approved"]:
        missing.append("Complete and obtain an approved referral before requesting an insured price estimate.")
    if authorization["required"] and not authorization["approved"]:
        missing.append("Complete and obtain an approved prior authorization before requesting an insured price estimate.")
    elif authorization["unknown"] and not authorization["approved"]:
        missing.append("Confirm the prior-authorization requirement with Availity before requesting an insured price estimate.")
    return missing
