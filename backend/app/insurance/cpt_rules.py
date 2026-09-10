"""Validate case-specific suggested groups against uploaded rates, never a CPT catalog."""
from app.insurance.golden import GoldenCaseError


def grouped_candidates(suggestions: dict, rates: list[dict]) -> tuple[list, list]:
    if not isinstance(suggestions, dict) or not isinstance(suggestions.get("groups"), list):
        raise GoldenCaseError("The suggested code grouping could not be read. Generate suggestions again.")
    allowed = {line["code"]: line for line in rates}
    lines, groups, seen = [], [], set()
    for entry in suggestions.get("groups", []):
        if not isinstance(entry, dict) or entry.get("selection_mode") not in {"single", "multiple"}:
            raise GoldenCaseError("The suggested code grouping could not be read. Generate suggestions again.")
        group_id = str(entry.get("id", ""))[:80]
        if not group_id or any(group["id"] == group_id for group in groups):
            raise GoldenCaseError("The suggested code groups are ambiguous. Generate suggestions again.")
        group_codes = []
        if not isinstance(entry.get("codes"), list):
            raise GoldenCaseError("The suggested code list could not be read. Generate suggestions again.")
        for candidate in entry["codes"]:
            if not isinstance(candidate, dict):
                continue
            code = str(candidate.get("code", "")).upper()
            reason = str(candidate.get("reason", "")).strip()
            if code not in allowed or code in seen or not reason:
                continue
            seen.add(code)
            group_codes.append(code)
            lines.append({**allowed[code], "group_id": group_id, "recommendation_reason": reason[:1000],
                          "requires_quantity": candidate.get("requires_quantity") is True,
                          "unit_label": str(candidate.get("unit_label") or "service")[:80],
                          "selected": False, "confirmed": False, "available": True, "suggested_by": "ClinicForce"})
        if group_codes:
            dependencies = entry.get("requires_groups", [])
            if not isinstance(dependencies, list) or any(not isinstance(key, str) for key in dependencies):
                raise GoldenCaseError("The suggested companion-code relationship could not be read. Try again.")
            groups.append({"id": group_id, "label": str(entry.get("label") or "Suggested services")[:160],
                           "category": str(entry.get("category") or "other"),
                           "selection_mode": "single" if entry.get("category") == "visit" else entry["selection_mode"], "description": str(entry.get("description") or "")[:2000],
                           "requires_groups": dependencies, "codes": group_codes})
    ids = {group["id"] for group in groups}
    for group in groups:
        if set(group["requires_groups"]) - (ids - {group["id"]}):
            raise GoldenCaseError("A suggested companion service is missing from the uploaded schedule. Review the schedule and retry.")
    groups.sort(key=lambda group: group["category"] != "visit")
    return lines, groups


def validate_selection(codes: list[str], groups: list[dict], lines: list[dict], quantities: dict[str, int]) -> None:
    selected = set(codes)
    if len(selected) != len(codes) or set(quantities) - selected:
        raise GoldenCaseError("Choose each code once and provide quantities only for selected codes.")
    active_groups = {group["id"] for group in groups if selected.intersection(group["codes"])}
    visit_codes = {code for group in groups if group.get("category") == "visit" for code in group["codes"]}
    if len(selected.intersection(visit_codes)) > 1:
        raise GoldenCaseError("Choose one alternative for the anticipated office visit, not multiple complexity levels.")
    for group in groups:
        if group["id"] not in active_groups:
            continue
        if group["selection_mode"] == "single" and len(selected.intersection(group["codes"])) > 1:
            raise GoldenCaseError(f"Choose one alternative in {group['label']}.")
        missing = set(group.get("requires_groups", [])) - active_groups
        if missing:
            labels = [item["label"] for item in groups if item["id"] in missing]
            raise GoldenCaseError(f"{group['label']} needs a companion selection: {', '.join(labels)}.")
    for line in lines:
        if line["code"] not in selected:
            continue
        quantity = quantities.get(line["code"])
        if line.get("requires_quantity") and quantity is None:
            raise GoldenCaseError(f"Confirm the number of {line.get('unit_label', 'units')} for {line['code']}; units cannot be guessed.")
        if quantity is not None and (type(quantity) is not int or not 1 <= quantity <= 10000):
            raise GoldenCaseError("Quantities must be whole numbers between 1 and 10000.")
