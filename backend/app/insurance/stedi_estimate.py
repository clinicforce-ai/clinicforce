"""Conservative local estimate using Stedi benefits and clinic-owned allowed rates."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.insurance.identity import patient_benefits_bound


def _benefits(coverage: dict, category: str) -> list[dict]:
    return [benefit for plan in coverage.get("plans", []) for benefit in plan.get("benefits", [])
            if str(benefit.get("category") or "").casefold() == category.casefold()
            and benefit.get("network") == "in_network"
            and str(benefit.get("level") or "individual").casefold() == "individual"]


def _unique(coverage: dict, category: str, key: str) -> tuple[int | None, bool]:
    values = {benefit[key] for benefit in _benefits(coverage, category) if benefit.get(key) is not None}
    return (next(iter(values)), False) if len(values) == 1 else (None, len(values) > 1)


def unavailable(lines: list[dict], reason: str, *, coverage: dict) -> dict:
    return {"source": "stedi_local", "estimate_available": False, "lines": [], "patient_cents": None, "allowed_cents": None,
            "provider_network": coverage.get("provider_network"), "provider_network_status": coverage.get("provider_network_status", "unconfirmed"),
            "message": reason, "disclaimer": "No patient amount is shown when Stedi eligibility or clinic contract-rate inputs are incomplete. Final responsibility depends on adjudication."}


def calculate(coverage: dict, lines: list[dict]) -> dict:
    """Return a price only for a confirmed in-network, unambiguous benefit design."""
    if coverage.get("active") is not True:
        return unavailable(lines, "Stedi did not confirm active coverage for this appointment.", coverage=coverage)
    binding = coverage.get("identity") or {}
    if not patient_benefits_bound(binding):
        fields = list(dict.fromkeys([*(binding.get("differences") or []),
                                    *(binding.get("policy_differences") or []),
                                    *(binding.get("missing_fields") or [])]))
        detail = ", ".join(field.replace("_", " ") for field in fields)
        reason = (f"Reconcile the payer and eCW {detail} before using these member benefits." if detail else
                  "Match the payer-returned patient and policyholder to eCW before using these member benefits.")
        return unavailable(lines, reason, coverage=coverage)
    if coverage.get("provider_network_status") != "in_network":
        return unavailable(lines, "The clinic contract matrix did not confirm this provider as in-network for the returned plan. Review OON or unknown-network benefits.", coverage=coverage)
    if not lines or any(line.get("allowed_cents") is None for line in lines):
        return unavailable(lines, "Upload the clinic contract-rate CSV with an Allowed Amount for every selected CPT/HCPCS code.", coverage=coverage)
    if any(benefit.get("authorizationRequired") or benefit.get("authorizationRequiredUnknown") for plan in coverage.get("plans", []) for benefit in plan.get("benefits", [])):
        return unavailable(lines, "Stedi returned an authorization requirement or an unknown authorization indicator. Verify before quoting or collecting.", coverage=coverage)
    allowed_lines = []
    for line in lines:
        try:
            total = Decimal(int(line["allowed_cents"])) * Decimal(str(line.get("units") or 1))
            if total <= 0 or total != total.to_integral_value():
                raise ValueError
        except (ArithmeticError, ValueError):
            return unavailable(lines, "Each selected service needs a positive whole-number unit count and a clinic contract allowed amount.", coverage=coverage)
        allowed_lines.append({**line, "line_allowed_cents": int(total)})
    allowed = sum(line["line_allowed_cents"] for line in allowed_lines)
    terms = {name: _unique(coverage, category, field) for name, category, field in (
        ("copay", "Copayment", "amount_cents"), ("coinsurance", "Coinsurance", "percent"),
        ("individual out-of-pocket remaining", "Out-of-pocket maximum", "remaining_cents"),
        ("individual deductible remaining", "Deductible", "remaining_cents"))}
    conflicts = [name for name, (_, conflict) in terms.items() if conflict]
    if conflicts:
        return unavailable(lines, "Stedi returned conflicting in-network " + ", ".join(conflicts) +
                           " rows; ask the payer which amount applies to the planned service.", coverage=coverage)
    copay, coinsurance = terms["copay"][0], terms["coinsurance"][0]
    oop, deductible = terms["individual out-of-pocket remaining"][0], terms["individual deductible remaining"][0]
    if oop is None:
        return unavailable(lines, "Stedi did not return the remaining individual out-of-pocket amount needed for a safe estimate.", coverage=coverage)
    if copay is not None and coinsurance is not None:
        return unavailable(lines, "Stedi returned both copay and coinsurance; the plan’s ordering is ambiguous. Review before quoting.", coverage=coverage)
    if copay is not None:
        patient = min(allowed, int(copay), int(oop))
        return {"source": "stedi_local", "estimate_available": True, "lines": [{"code": line["code"], "description": line.get("description"), "allowed_cents": line["line_allowed_cents"]} for line in allowed_lines],
                "patient_cents": patient, "allowed_cents": allowed, "copay_cents": int(copay), "provider_network": coverage.get("provider_network"), "provider_network_status": "in_network",
                "message": "Clinic contract allowed amount plus Stedi in-network copay estimate.", "disclaimer": "Pre-visit estimate only; final responsibility depends on coding, benefit applicability, and claim adjudication."}
    if deductible is None or coinsurance is None:
        return unavailable(lines, "Stedi did not return the remaining deductible and coinsurance needed for a safe estimate.", coverage=coverage)
    remaining_deductible, remaining_oop, patient, output = int(deductible), int(oop), 0, []
    for line in allowed_lines:
        line_deductible = min(line["line_allowed_cents"], remaining_deductible)
        coinsurance_cents = int((Decimal(line["line_allowed_cents"] - line_deductible) * Decimal(int(coinsurance)) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        line_patient = min(line["line_allowed_cents"], line_deductible + coinsurance_cents, remaining_oop)
        output.append({"code": line["code"], "description": line.get("description"), "allowed_cents": line["line_allowed_cents"], "deductible_cents": line_deductible, "coinsurance_cents": coinsurance_cents, "patient_cents": line_patient})
        remaining_deductible, remaining_oop, patient = remaining_deductible - line_deductible, remaining_oop - line_patient, patient + line_patient
    return {"source": "stedi_local", "estimate_available": True, "lines": output, "patient_cents": patient, "allowed_cents": allowed,
            "deductible_cents": int(deductible) - remaining_deductible, "coinsurance_cents": sum(line["coinsurance_cents"] for line in output),
            "provider_network": coverage.get("provider_network"), "provider_network_status": "in_network",
            "message": "Clinic contract allowed amount plus Stedi in-network deductible and coinsurance estimate.", "disclaimer": "Pre-visit estimate only; final responsibility depends on coding, benefit applicability, and claim adjudication."}
