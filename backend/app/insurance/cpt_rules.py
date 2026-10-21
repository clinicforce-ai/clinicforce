"""Classify planned services and validate clinician-selected code groups."""
import re

from app.insurance.golden import GoldenCaseError


EMG_ADD_ONS = {'95885', '95886', '95887'}
NCS_CODES = {str(code) for code in range(95907, 95914)}
NCS_STUDY_RANGES = {
    '95907': '1–2', '95908': '3–4', '95909': '5–6', '95910': '7–8',
    '95911': '9–10', '95912': '11–12', '95913': '13 or more',
}


def documented_ncs_code(reason: str) -> str | None:
    """Use only an explicit current study count, never an anatomical guess."""
    match = re.search(r'\b(\d{1,2})\s+(?:nerve[ -]?conduction|ncs)\s+stud(?:y|ies)\b', reason, re.I)
    if not match or re.search(r'\b(?:possible|consider|history|prior|previous)\b', reason[:match.start()].split(';')[-1], re.I):
        return None
    count = int(match[1])
    if count < 1:
        return None
    return str(95907 + min((count - 1) // 2, 6))

# Versioned clinical planning metadata. This classifies services; it never
# supplies a payer benefit, covered status, allowance, or billable quantity.
SERVICE_CATALOGUE_VERSION = 1
SERVICE_FAMILIES = {
    'office_consultation': {'codes': {str(n) for n in (*range(99202, 99206), *range(99212, 99216))},
                            'group': 'visit', 'role': 'consultation', 'service_type': '98'},
    'nerve_conduction': {'codes': NCS_CODES, 'group': 'nerve', 'role': 'diagnostic_test', 'service_type': '73'},
    'needle_emg': {'codes': EMG_ADD_ONS, 'group': 'muscle', 'role': 'diagnostic_test', 'service_type': '73'},
    'diagnostic_procedure': {'codes': {'11104', '11105', '62270', '62328'},
                             'group': 'other', 'role': 'procedure', 'service_type': '2'},
    'botulinum_drug': {'codes': {'J0585'}, 'group': 'other', 'role': 'drug',
                       'service_type': None, 'associated_families': ('chemodenervation',),
                       'brand': 'Botox', 'eligibility_service_types': ('1',),
                       'benefit_service_types': ('1',),
                       'candidate_descriptions': {'J0585': 'Botox drug · proposed HCPCS units; confirm dose and supply'}},
    'chemodenervation': {'codes': {'64612', '64615'}, 'group': 'other', 'role': 'procedure',
                         'service_type': None, 'eligibility_service_types': ('2',),
                         'benefit_service_types': ('1', '2'),
                         'candidate_descriptions': {
                             '64612': 'Facial chemodenervation candidate · confirm anatomy',
                             '64615': 'Head and neck chemodenervation candidate · confirm anatomy'}},
}


def service_definition(code: str) -> dict:
    """Return catalogue classification without inferring clinical approval."""
    for family, definition in SERVICE_FAMILIES.items():
        if code in definition['codes']:
            return {**definition, 'family': family, 'catalogue_version': SERVICE_CATALOGUE_VERSION}
    return {'family': 'uncategorized', 'group': 'other', 'role': 'procedure',
            'service_type': None, 'catalogue_version': SERVICE_CATALOGUE_VERSION}


def benefit_category_compatible(code: str, category: str, *, procedure_specific: bool = False) -> bool:
    """A broad STC must describe this service; an exact procedure row can be narrower."""
    allowed = service_definition(code).get('benefit_service_types')
    return bool(category) and (procedure_specific or not allowed or category in allowed)


def service_selection_issues(codes: list[str]) -> list[str]:
    # CPT add-on definitions, independent of the clinic or payer. These checks
    # validate the planned bundle, not whether the payer covers the procedure.
    selected = set(codes)
    issues = []
    if selected & EMG_ADD_ONS and not selected & NCS_CODES:
        issues.append('Add the anticipated nerve-conduction service (95907–95913) for these EMG add-ons. Choose its level from the planned study count; the add-ons alone are not a complete visit estimate.')
    if sum(code in NCS_CODES for code in codes) > 1:
        issues.append('Choose one nerve-conduction level (95907–95913) for the total planned study count, rather than adding alternative levels together.')
    return issues


def grouped_candidates(suggestions: dict, rates: list[dict], *, allow_unpriced: bool = False) -> tuple[list, list]:
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
            if code not in allowed and allow_unpriced and re.fullmatch(r"(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])", code):
                allowed[code] = {"code": code, "description": str(candidate.get("description") or code)[:240],
                                 "charge": None, "cash_cents": None, "service_status": "scheduled"}
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
