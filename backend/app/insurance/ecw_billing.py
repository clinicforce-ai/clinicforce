"""Read explicit collection instructions from returned, visit-linked eCW notes.

These are EHR collection instructions, not a replacement payer eligibility response.
Unknown or conflicting prose stays visible for review rather than supplying money.
"""
import re
from datetime import datetime, timezone

from app.insurance import commercial


def cash_without_coverage(source: dict) -> bool:
    """Use cash when the completed eCW coverage read has no policy or payer."""
    return (source.get('coverage_retrieval', {}).get('status') == 'empty'
            and source.get('active_coverages') == 0
            and (source.get('saved_eligibility') or {}).get('status') != 'verified_active'
            and not source.get('payer') and not source.get('insurance_candidates'))


def collection_note(fields: list[dict]) -> dict | None:
    evidence = []
    for field in fields:
        label = re.sub(r'[^a-z]', '', str(field.get('label') or '').lower())
        billing_field = label in {'billingnote', 'billingnotes', 'billinginstruction', 'billinginstructions'}
        if not billing_field and label not in {'generalcomments', 'appointmentnote', 'generalnotes'}:
            continue
        reference = str(field.get('source') or '')
        if not re.match(r'^(?:Encounter|Appointment)/[A-Za-z0-9.-]+\.', reference):
            return None
        try:
            updated = datetime.fromisoformat(str(field['updated_at']).replace('Z', '+00:00'))
            if updated.tzinfo is None or updated > datetime.now(timezone.utc):
                return None
        except (KeyError, ValueError, TypeError):
            return None
        text = str(field.get('text') or '')
        if label == 'generalnotes':
            header = re.split(r'Patient entered comments on healow\s*:', text, flags=re.I)[0]
            preferences = re.findall(r'(?im)^\s*Patient payment preference\s*:\s*([^,;\r\n]+)', header)
            if not preferences:
                continue
            if len(preferences) != 1 or not re.fullmatch(r'self[ -]?pay', preferences[0].strip(), re.I):
                return None
            text = preferences[0]
        text = ' '.join(text.lower().split()).strip(' .')
        patient_cash_choice = re.fullmatch(
            r'(?:patient|pt)(?: (?:states?|stated|says?|said|confirmed|reported))?'
            r'(?: (?:he|she|they))? (?:will|wants to|elects? to|elected to) self[ -]?pay', text)
        if patient_cash_choice or re.fullmatch(r'self[ -]?pay', text):
            parsed = {'kind': 'self_pay'}
        elif not billing_field:
            if re.search(r'\bself[ -]?pay\b', text):
                return None
            continue
        elif re.fullmatch(r'(?:no need to (?:take|collect) (?:any )?money from (?:the )?patient|do not collect(?: (?:any )?money)?(?: from (?:the )?patient)?)(?:[.; ]+secondary will take\s*care)?', text):
            parsed = {'kind': 'no_collection', 'amount_cents': 0}
        else:
            # Accept complete, explicit clauses only; no partial matches inside
            # conditional, negated, historical or patient-entered instructions.
            clauses = re.split(r'\s*[,;.]\s*(?!\d)', text)
            active = met = no_coins = False
            copays = set()
            for clause in clauses:
                if re.fullmatch(r'(?:(?:pt|patient) (?:is )?)?active', clause):
                    active = True
                elif re.fullmatch(r'ded(?:uctible)?(?: has been)? met', clause):
                    met = True
                elif re.fullmatch(r'no coins(?:urance)?', clause):
                    no_coins = True
                elif re.fullmatch(r'no co[ -]?pay', clause):
                    copays.add(0)
                elif match := re.fullmatch(r'\$(\d+(?:\.\d{1,2})?) co[ -]?pay', clause):
                    try:
                        copays.add(commercial._money(match[1], 'eCW billing copay'))
                    except commercial.CommercialError:
                        return None
                else:
                    return None
            if not (active and met and len(copays) == 1):
                return None
            amount = copays.pop()
            if amount == 0 and not no_coins:
                return None
            parsed = {'kind': 'office_copay', 'amount_cents': amount}
        evidence.append({**parsed, 'source': reference, 'updated_at': field['updated_at'], 'text': field['text']})
    if not evidence or len({(row['kind'], row.get('amount_cents')) for row in evidence}) != 1:
        return None
    return max(evidence, key=lambda row: datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00')))
