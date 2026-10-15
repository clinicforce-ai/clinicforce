"""Display payer evidence without recreating adjudication from benefit tables."""
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from app.insurance.availity_response import unwrap


def obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def cents(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value)) * 100
        if number.is_finite() and 0 <= number <= 100_000_000 and number == number.to_integral_value():
            return int(number)
    except InvalidOperation:
        pass
    return None


def same_quantity(actual: Any, expected: Any) -> bool:
    if actual is None:
        return True  # Some valid PCE responses omit quantities; resource ID binds the request.
    try:
        return Decimal(str(actual)).is_finite() and Decimal(str(actual)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return False


def notes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value[:2000]]
    if isinstance(value, list):
        return list(dict.fromkeys(text for item in value for text in notes(item)))[:50]
    if isinstance(value, dict):
        return notes([value[k] for k in ("message", "description", "text", "errors", "validationMessages", "payerNotes", "statusReasons", "code") if k in value])
    return []


def provider_network_status(value: Any) -> str:
    text = re.sub(r"[^a-z]", "", str(value or "").casefold())
    if text in {"oon", "outofnetwork", "outnetwork", "nonparticipating"}:
        return "out_of_network"
    if text in {"innetwork", "participating", "preferred"}:
        return "in_network"
    return "unconfirmed"


def coverage(payload: dict) -> dict:
    data = unwrap(payload, "coverage")
    plans = []
    plan_statuses = []
    for plan in rows(data.get("plans")):
        plan_statuses.extend(str(plan.get(key, "")) for key in ("status", "statusCode") if plan.get(key) is not None)
        benefits = []
        for benefit in rows(plan.get("benefits")):
            for category, networks in {**obj(benefit.get("amounts")), **{k: benefit[k] for k in ("statusDetails", "nonCovered", "limitations", "exclusions", "benefitDescriptions") if k in benefit}}.items():
                if not isinstance(networks, dict):
                    continue
                for network, details in networks.items():
                    for detail in rows(details):
                        benefits.append({"category": category, "network": network,
                                         "service": benefit.get("name") or benefit.get("type") or benefit.get("code"),
                                         **{k: detail.get(k) for k in ("amount", "remaining", "total", "amountTimePeriod", "remainingTimePeriod", "totalTimePeriod", "level", "levelCode", "description", "authorizationRequired", "authorizationRequiredUnknown", "referralNumber", "placeOfServiceCode")}})
        plans.append({**{k: plan.get(k) for k in ("name", "planName", "planNumber", "groupNumber", "status", "statusCode", "insuranceType", "effectiveDate", "expirationDate")},
                      "benefits": benefits, "notes": notes(plan)})
    status_text = " ".join(plan_statuses).casefold()
    inactive = any(word in status_text for word in ("inactive", "ineligible", "terminated", "cancelled", "canceled", "expired"))
    active = True if not inactive and any(word in status_text for word in ("active", "eligible")) else False if inactive else None
    return {"payer": obj(data.get("payer")).get("name"), "status": data.get("status"), "active": active,
            "as_of_date": data.get("asOfDate"), "plans": plans,
            "pce_available": obj(data.get("supplementalInformation")).get("professionalPatientCostEstimator"),
            "provider_network": "Not established by eligibility benefit categories",
            "notes": notes(data)}


def estimate(payload: dict, expected_lines: list[dict], demo: bool) -> dict:
    data = obj(payload.get("result"))
    returned = rows(obj(data.get("claimInformation")).get("serviceLines"))
    lines = []
    for row in returned:
        lines.append({"code": row.get("procedureCode"), "description": row.get("procedure"),
                      "quantity": row.get("quantity"), "billed_cents": cents(row.get("amount")),
                      "patient_cents": cents(row.get("estimatedPatientResponsibility")),
                      **{f"{key}_cents": cents(obj(row.get(api_key)).get("amount")) for key, api_key in
                         (("allowed", "allowed"), ("copay", "coPay"), ("coinsurance", "coInsurance"), ("deductible", "deductible"), ("not_covered", "notCovered"))},
                      "notes": notes(row)})
    match = Counter(str(r.get("procedureCode")) for r in returned) == Counter(r["code"] for r in expected_lines)
    # Amount/modifier/quantity mismatches must not become a total for the wrong encounter.
    unmatched = list(expected_lines)
    for row in returned:
        candidates = [x for x in unmatched if x["code"] == row.get("procedureCode")
                      and (row.get("amount") is None or cents(row["amount"]) == cents(x["charge"]))
                      and same_quantity(row.get("quantity"), x["units"])
                      and [row.get(f"modifierCode{i}") for i in range(1, 5) if row.get(f"modifierCode{i}")] == x.get("modifiers", [])]
        if candidates:
            unmatched.remove(candidates[0])
        else:
            match = False
    available = bool(lines) and all(x["patient_cents"] is not None for x in lines) and (demo or match) and not data.get("errors") and not payload.get("errors")
    return {"estimate_available": available, "lines": lines if demo or match else [],
            "patient_cents": sum(x["patient_cents"] for x in lines) if available else None,
            "allowed_cents": sum(x["allowed_cents"] for x in lines) if available and all(x["allowed_cents"] is not None for x in lines) else None,
            "provider_network": obj(data.get("billingProvider")).get("networkStatus"),
            "provider_network_status": provider_network_status(obj(data.get("billingProvider")).get("networkStatus")),
            "message": "Synthetic Availity example; returned codes may differ from the example request." if demo else
                       "Patient total is the sum of Availity's returned line estimates." if available else
                       "Availity did not return a complete estimate matching the requested services. No patient total is available.",
            "notes": notes(payload) + notes(data),
            "disclaimer": "Estimate only. This is not a guarantee of payment, authorization, or a final bill."}


def review(payload: dict) -> dict:
    data = unwrap(payload, "authorization")
    code = str(data.get("statusCode", ""))
    labels = {"A1": "Certified in total", "A2": "Partially certified — review each procedure",
              "A3": "Not certified", "A4": "Pending", "A6": "Modified — check again", "CT": "Contact payer",
              "NA": "No authorization required for this request", "C": "Canceled", "51": "Complete — review payer details", "71": "Authorization term expired"}
    return {"status_code": code, "status": labels.get(code, data.get("status") or "No determination returned"),
            **{k: data.get(k) for k in ("certificationNumber", "certificationEffectiveDate", "certificationExpirationDate", "fromDate", "toDate")},
            "procedures": [{**{k: r.get(k) for k in ("code", "status", "statusCode", "certificationNumber", "certificationEffectiveDate", "certificationExpirationDate", "quantity", "quantityType", "fromDate", "toDate")},
                            "notes": notes(r)} for r in rows(data.get("procedures"))],
            "notes": notes(data), "disclaimer": "Check approved procedures, units, dates and payer instructions before scheduling. An estimate does not establish authorization."}
