"""Classify planned services and validate clinician-selected code groups."""
import re

from app.insurance.golden import GoldenCaseError


EMG_ADD_ONS = {'95885', '95886', '95887'}
STANDALONE_EMG_CODES = {'95860', '95861', '95862', '95863', '95864', '95867', '95868', '95869', '95870'}
EMG_CODES = EMG_ADD_ONS | STANDALONE_EMG_CODES
NCS_CODES = {str(code) for code in range(95907, 95914)}
NCS_STUDY_RANGES = {
    '95907': '1–2', '95908': '3–4', '95909': '5–6', '95910': '7–8',
    '95911': '9–10', '95912': '11–12', '95913': '13 or more',
}


def tia_cta_office_scenario(inputs: dict, choices: list[dict], scheduled_office: list[dict], available: dict) -> bool:
    """Use moderate E/M only for the established TIA and planned CTA cost scenario."""
    return (inputs.get('visit_type') == 'established' and not scheduled_office and '99214' in available
            and re.search(r'\b(?:transient cerebral ischemia|TIA)\b', str(inputs.get('reason') or ''), re.I)
            and any(row['code'] in {'70496', '70498'} for row in choices)
            and any(row['code'] == '99213' for row in choices))


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
SERVICE_CATALOGUE_VERSION = 3
SERVICE_FAMILIES = {
    'office_consultation': {'codes': {str(n) for n in (*range(99202, 99206), *range(99212, 99216))},
                            'group': 'visit', 'role': 'consultation', 'service_type': '98'},
    'nerve_conduction': {'codes': NCS_CODES, 'group': 'nerve', 'role': 'diagnostic_test', 'service_type': '73'},
    'needle_emg': {'codes': EMG_ADD_ONS, 'group': 'muscle', 'role': 'diagnostic_test', 'service_type': '73'},
    'standalone_emg': {'codes': STANDALONE_EMG_CODES, 'group': 'muscle', 'role': 'diagnostic_test', 'service_type': '73'},
    'diagnostic_procedure': {'codes': {'11104', '11105', '62270', '62328'},
                             'group': 'other', 'role': 'procedure', 'service_type': '2'},
    'dbs_analysis': {'codes': {'95970'}, 'group': 'other', 'role': 'procedure',
                     'service_type': '98', 'exact_cpt_only': True},
    'dbs_programming': {'codes': {'95983', '95984'}, 'group': 'other', 'role': 'procedure',
                        'service_type': '98', 'exact_cpt_only': True},
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
    definition = service_definition(code)
    allowed = definition.get('benefit_service_types')
    return bool(category) and (procedure_specific or not definition.get('exact_cpt_only')
                               and (not allowed or category in allowed))


def service_selection_issues(codes: list[str]) -> list[str]:
    # CPT add-on definitions, independent of the clinic or payer. These checks
    # validate the planned bundle, not whether the payer covers the procedure.
    selected = set(codes)
    issues = []
    if selected & EMG_ADD_ONS and not selected & NCS_CODES:
        issues.append('Add the anticipated nerve-conduction service (95907–95913) for these EMG add-ons. Choose its level from the planned study count; the add-ons alone are not a complete visit estimate.')
    if selected & STANDALONE_EMG_CODES and selected & NCS_CODES:
        issues.append('Use 95885–95887 for same-day needle EMG with nerve-conduction testing; 95860–95864 and 95867–95870 are standalone EMG codes.')
    if sum(code in NCS_CODES for code in codes) > 1:
        issues.append('Choose one nerve-conduction level (95907–95913) for the total planned study count, rather than adding alternative levels together.')
    if '95984' in selected and '95983' not in selected:
        issues.append('DBS programming add-on 95984 requires the first programming service 95983.')
    if '95970' in selected and selected & {'95983', '95984'}:
        issues.append('Choose DBS analysis without programming or DBS programming for the same device encounter.')
    return issues


def dbs_plan_issue(codes: list[str], inputs: dict) -> str | None:
    context = ' '.join(str(inputs.get(key) or '') for key in ('reason', 'appointment_type'))
    analysis = re.search(r'\b(?:interrogat\w*|analy\w*|device check|battery check)\b', context, re.I)
    programming = re.search(r'\b(?:reprogram\w*|program\w*)\b', context, re.I)
    additional = re.search(r'\b(?:additional\s+15|(?:[3-9]\d|\d{3,})\s*(?:minutes?|min))\b', context, re.I)
    if ('95970' in codes and (not analysis or programming)
            or set(codes) & {'95983', '95984'} and not programming
            or '95984' in codes and not additional):
        return 'DBS device service and programming time must be explicitly planned.'
    return None


def dbs_planning_note(reason: str, codes: list[str]) -> str | None:
    if re.search(r'\b(?:DBS|deep brain stimulation)\b', reason, re.I) and not set(codes) & {'95970', '95983', '95984'}:
        return ('DBS means deep brain stimulation. Consider 95970 for documented device analysis without programming, '
                'or 95983 and 95984 for documented programming time; none is established by a follow-up label alone.')
    return None


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
