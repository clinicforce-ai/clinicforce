"""Read explicit collection instructions from returned, visit-linked billing notes.

These are EHR collection instructions, not a replacement payer eligibility response.
Unknown or conflicting prose stays visible for review rather than supplying money.
"""
import re
from datetime import datetime, timezone

from app.insurance import commercial


def collection_note(fields: list[dict]) -> dict | None:
    evidence = []
    for field in fields:
        label = re.sub(r'[^a-z]', '', str(field.get('label') or '').lower())
        if label not in {'billingnote', 'billingnotes', 'billinginstruction', 'billinginstructions'}:
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
        text = ' '.join(str(field.get('text') or '').lower().split()).strip(' .')
        if re.fullmatch(r'(?:no need to (?:take|collect) (?:any )?money from (?:the )?patient|do not collect(?: (?:any )?money)?(?: from (?:the )?patient)?)(?:[.; ]+secondary will take\s*care)?', text):
            parsed = {'kind': 'no_collection', 'amount_cents': 0}
        elif re.fullmatch(r'self[ -]?pay', text):
            parsed = {'kind': 'self_pay'}
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
