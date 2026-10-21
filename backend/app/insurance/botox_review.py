"""Clinical evidence required before pricing a botulinum-toxin visit."""
from __future__ import annotations

import re
from app.insurance import cpt_rules

DRUG_CODES = cpt_rules.SERVICE_FAMILIES['botulinum_drug']['codes']
PROCEDURE_CODES = cpt_rules.SERVICE_FAMILIES['chemodenervation']['codes']
SELECTABLE_CODES = DRUG_CODES | PROCEDURE_CODES


def is_botox_visit(inputs: dict) -> bool:
    current = ' '.join(str(inputs.get(key) or '') for key in ('reason', 'appointment_type'))
    current += ' ' + ' '.join(str(row.get('description') or '') for row in inputs.get('lines') or [])
    return bool(re.search(r'\b(?:botox|onabotulinumtoxin\w*|chemodenerv\w*)\b', current, re.I))


def clinical_issues(codes: list[dict], review: dict, *, botox_visit: bool = False,
                    completed: bool = False) -> list[str]:
    """A booking label, suggestion or shipment cannot establish performed care."""
    selected = {str(row.get('code') or '').upper(): row for row in codes}
    if not selected.keys() & SELECTABLE_CODES and not botox_visit:
        return []
    facts = review.get('botox') or {}
    issues = []
    for code in SELECTABLE_CODES:
        if sum(str(row.get('code') or '').upper() == code for row in codes) > 1:
            issues.append(f'Consolidate or separately document duplicate {code} lines and units before pricing.')
    if completed and facts.get('encounter_status') != 'performed':
        issues.append('Use documented performed care for this completed encounter; a planned dose is insufficient.')
    if selected.keys() & SELECTABLE_CODES:
        required = (('evidence_reference', 'chart evidence reference'),
                         ('modifier_reference', 'modifier coding review reference'),
                         ('indication', 'medical indication'), ('anatomical_region', 'injection anatomy'),
                         ('encounter_status', 'planned or performed status'),
                         ('supply_source', 'drug supply source'))
    else:
        required = (('evidence_reference', 'chart evidence reference'),
                    ('encounter_status', 'planned or performed status'))
    for field, label in required:
        if not facts.get(field):
            issues.append(f'Confirm the Botox {label} before pricing the selected care.')
    if selected.keys() & DRUG_CODES:
        if not selected.keys() & PROCEDURE_CODES:
            issues.append('Select the independently documented injection procedure before pricing Botox drug units.')
        if facts.get('brand') != cpt_rules.service_definition('J0585')['brand']:
            issues.append('Confirm the planned or administered brand before using HCPCS J0585.')
        if facts.get('supply_source') != 'clinic':
            issues.append('Do not price clinic drug reimbursement unless the clinic supplied the Botox.')
        billed = selected['J0585'].get('units')
        planned = facts.get('planned_units')
        administered = facts.get('administered_units')
        discarded = facts.get('discarded_units')
        if facts.get('encounter_status') == 'planned':
            if type(billed) is not int or type(planned) is not int or planned < 1 or billed != planned:
                issues.append('Confirm planned Botox dose and proposed HCPCS units; received stock is not the dose.')
        elif facts.get('encounter_status') == 'performed':
            if type(billed) is not int or type(administered) is not int or administered < 1:
                issues.append('Confirm administered and billable Botox units; received stock is not the dose.')
            elif discarded:
                # Waste may require a separate claim line and payer-specific
                # modifiers. Do not infer reimbursable units from vial contents.
                issues.append('Review payer waste policy and separately documented drug lines before pricing discarded Botox units.')
            elif billed != administered:
                issues.append('Confirm administered and billable Botox units; received stock is not the dose.')
    for row in codes:
        if row.get('code') in SELECTABLE_CODES and row.get('id') not in (review.get('confirmed_modifiers') or {}):
            issues.append(f"Confirm the modifiers, including none, for selected {row['code']} before pricing.")
    if selected.keys() & PROCEDURE_CODES and len(selected.keys() & PROCEDURE_CODES) > 1:
        issues.append('Confirm each separately documented anatomical injection procedure before pricing both codes.')
    if any(code.isdigit() and 99202 <= int(code) <= 99215 for code in selected):
        if not facts.get('separate_em_documented'):
            issues.append('A separate office E/M service needs independently documented work and coding review.')
    return issues
