"""Clinical evidence required before pricing a botulinum-toxin visit."""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from app.insurance import cpt_rules
from app.insurance.identity import payer_identity_verified

DRUG_CODES = cpt_rules.SERVICE_FAMILIES['botulinum_drug']['codes']
PROCEDURE_CODES = cpt_rules.SERVICE_FAMILIES['chemodenervation']['codes']
SELECTABLE_CODES = DRUG_CODES | PROCEDURE_CODES


def is_botox_visit(inputs: dict) -> bool:
    current = ' '.join(str(inputs.get(key) or '') for key in ('reason', 'appointment_type'))
    current += ' ' + ' '.join(str(row.get('description') or '') for row in inputs.get('lines') or [])
    return bool(re.search(r'\b(?:botox|onabotulinumtoxin\w*|chemodenerv\w*)\b', current, re.I))


def provisional_office_injection(result: dict, options: list[dict], review: dict | None = None) -> dict | None:
    """Show one priced coding candidate as a planning amount, never a billed selection."""
    benefits = result.get('benefits') or {}
    if (result.get('payment_mode') != 'insurance' or result.get('service_selection_status') != 'preview' or result.get('codes')
            or (review or {}).get('payments')
            or (result.get('service_plan') or {}).get('kind') != 'botox'
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or (result.get('requested_service_types') or [None])[0] != '98'
            or benefits.get('active') is not True or benefits.get('network_status') != 'in_network'
            or benefits.get('network_source') not in {'clinic_group_agreement', 'payer_participation_message'}
            or not payer_identity_verified(benefits.get('identity')) or benefits.get('has_secondary')
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('service_date_matches') is False
            or result.get('separate_em_planned')
            or (result.get('collection_policy') or {}).get('mode') != 'collect_supported_advance'):
        return None
    suggested = {str(code.get('code') or '').upper()
                 for group in (result.get('suggestions') or {}).get('groups') or []
                 for code in group.get('codes') or []} & PROCEDURE_CODES
    if len(suggested) != 1:
        return None
    candidate = [row for row in result.get('available_codes') or []
                 if row.get('code') in suggested and row.get('unit_allowed_cents') is not None
                 and row.get('rate_source') and not row.get('modifiers') and row.get('units') in (None, 1)]
    if len(candidate) != 1:
        return None
    def injection(row: dict) -> bool:
        return (row.get('network') == 'IN_NETWORK' and
                bool(re.search(r'\bINJECTIONS\s*-\s*PROFESSIONAL\b', row.get('payer_note') or '', re.I)))
    coinsurance = [row for row in options if injection(row) and row.get('kind') == 'coinsurance'
                   and row.get('service_type_code') == 'BY' and row.get('coverage_level') == 'INDIVIDUAL'
                   and row.get('places_of_service') == ['11'] and row.get('percent') is not None]
    copays = [row for row in options if injection(row) and row.get('kind') == 'copay'
              and row.get('service_type_code') == 'BY' and '11' in (row.get('places_of_service') or [])]
    def family_remaining(kind: str) -> list[dict]:
        return [row for row in options if injection(row) and row.get('kind') == kind
                and row.get('service_type_code') == '30' and row.get('coverage_level') == 'FAMILY'
                and row.get('time_period') == 'REMAINING' and row.get('amount_cents') is not None]
    deductible, oop = family_remaining('deductible'), family_remaining('out_of_pocket')
    individual = [row for row in options if injection(row) and row.get('coverage_level') == 'INDIVIDUAL'
                  and row.get('time_period') == 'REMAINING' and row.get('kind') in {'deductible', 'out_of_pocket'}
                  and row.get('service_type_code') in {'30', 'BY'} and row.get('amount_cents') is not None]
    individual_deductible = [row['amount_cents'] for row in individual if row['kind'] == 'deductible']
    individual_oop = [row['amount_cents'] for row in individual if row['kind'] == 'out_of_pocket']
    if (len(coinsurance) != 1 or copays or len(deductible) != 1 or len(oop) != 1
            or deductible[0]['amount_cents'] != 0 or oop[0]['amount_cents'] < 0
            or len(set(individual_deductible)) > 1 or any(value > 0 for value in individual_deductible)
            or len(set(individual_oop)) > 1 or any(value < 0 for value in individual_oop)):
        return None
    try:
        percent = Decimal(coinsurance[0]['percent'])
        if not Decimal('0') <= percent <= Decimal('100'):
            return None
        allowed = candidate[0]['unit_allowed_cents']
        share = min(int((Decimal(allowed) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)),
                    oop[0]['amount_cents'], *(individual_oop or [oop[0]['amount_cents']]))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return {'status': 'provisional', 'code': candidate[0]['code'], 'units': 1,
            'allowed_cents': allowed, 'coinsurance_percent': str(percent),
            'family_deductible_remaining_cents': 0, 'family_oop_remaining_cents': oop[0]['amount_cents'],
            'patient_share_cents': share, 'rate_source': candidate[0]['rate_source'],
            'benefit_source': 'Stedi office-injection benefit'}


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
