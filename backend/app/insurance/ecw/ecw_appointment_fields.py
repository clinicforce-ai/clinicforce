"""Read appointment dates, labels, notes and visit history."""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

from app.insurance.ecw.ecw_fields import _coding, _concept_labels, _reference


def _appointment_date(resource: dict[str, Any]) -> str:
    return str((resource.get("period") or {}).get("start") or "")[:10]

def _is_upcoming_appointment(resource: dict[str, Any], now: datetime) -> bool:
    """An estimate is for a visit that has not started, even if eCW has not finalized it yet."""
    if str(resource.get("status") or "").casefold() in {"cancelled", "entered-in-error", "finished", "completed"}:
        return False
    start = str((resource.get("period") or {}).get("start") or "")
    try:
        started_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=now.tzinfo)
    return started_at > now

def _visit_context(visit: dict[str, Any], orders: list[dict[str, Any]], procedures: list[dict[str, Any]],
                   conditions: list[dict[str, Any]]) -> str | None:
    """Keep exact eCW visit details together for clinician-reviewed CPT suggestions."""
    details: list[str] = []
    fields = (("Reason", visit.get("reasonCode")), ("Visit type", visit.get("type")),
              ("Service", visit.get("serviceType")))
    for label, value in fields:
        details.extend(f"{label}: {text}" for text in _concept_labels(value))
    for order in orders:
        details.extend(f"Planned service: {text}" for text in _concept_labels(order.get("code")))
        details.extend(f"Order reason: {text}" for text in _concept_labels(order.get("reasonCode")))
    for procedure in procedures:
        details.extend(f"Procedure: {text}" for text in _concept_labels(procedure.get("code")))
        details.extend(f"Procedure reason: {text}" for text in _concept_labels(procedure.get("reasonCode")))
    for condition in conditions:
        details.extend(f"Linked condition: {text}" for text in _concept_labels(condition.get("code")))
    unique = list(dict.fromkeys(" ".join(detail.split()) for detail in details if detail.strip()))
    return "; ".join(unique)[:2000] or None

def appointment_fields(visit: dict, linked: list[dict] | None = None) -> list[dict]:
    """Verbatim source context, deliberately kept out of the coding prompt."""
    fields = []
    for resource in [visit, *(linked or [])]:
        reference = f"{resource.get('resourceType', 'Encounter')}/{resource.get('id', '')}"
        def add(label, value, path):
            if isinstance(value, str) and value.strip():
                fields.append({'label': label, 'text': value, 'source': reference + '.' + path,
                               'updated_at': resource.get('meta', {}).get('lastUpdated'), 'status': 'returned'})
        for key, label in (('description', 'Appointment description'), ('comment', 'General comments'),
                           ('patientInstruction', 'Patient instructions')):
            add(label, resource.get(key), key)
        for i, note in enumerate(resource.get('note') or []):
            if isinstance(note, dict):
                add('Appointment note', note.get('text'), f'note[{i}].text')
        def extensions(rows, prefix='extension'):
            for i, extension in enumerate(rows):
                if not isinstance(extension, dict):
                    continue
                url = str(extension.get('url') or '')
                label = re.sub(r'[-_]', ' ', url.rsplit('/', 1)[-1]) or 'eCW appointment field'
                for key in ('valueString', 'valueMarkdown'):
                    add(label, extension.get(key), f'{prefix}[{i}]({url}).{key}')
                for key in ('valueBoolean', 'valueInteger', 'valueDecimal', 'valueCode', 'valueDate', 'valueDateTime'):
                    if extension.get(key) is not None:
                        add(label, str(extension[key]), f'{prefix}[{i}]({url}).{key}')
                for key in ('valueMoney', 'valueQuantity'):
                    amount = extension.get(key) or {}
                    if amount.get('value') is not None:
                        add(label, f"{amount['value']} {amount.get('currency') or amount.get('unit') or ''}".strip(), f'{prefix}[{i}]({url}).{key}')
                annotation = extension.get('valueAnnotation') or {}
                add(label, annotation.get('text'), f'{prefix}[{i}]({url}).valueAnnotation.text')
                extensions(extension.get('extension') or [], f'{prefix}[{i}].extension')
        extensions(resource.get('extension') or [])
        class Narrative(HTMLParser):
            def __init__(self):
                super().__init__(); self.parts = []; self.hidden = 0
            def handle_starttag(self, tag, attrs):
                if tag in {'script', 'style'}: self.hidden += 1
                if tag in {'p', 'div', 'br', 'tr', 'li'}: self.parts.append('\n')
            def handle_endtag(self, tag):
                if tag in {'script', 'style'}: self.hidden = max(0, self.hidden - 1)
            def handle_data(self, data):
                if not self.hidden: self.parts.append(data)
        narrative = Narrative()
        narrative.feed(str((resource.get('text') or {}).get('div') or ''))
        add('eCW appointment narrative', ''.join(narrative.parts).strip(), 'text.div')
    return fields

def _visit_type(visit: dict[str, Any]) -> str | None:
    """Use only an explicit eCW appointment label; history is not a safe proxy."""
    labels = _concept_labels(visit.get("type")) + _concept_labels(visit.get("serviceType"))
    text = " ".join(labels).casefold()
    if re.search(r"\bnew\s+(?:patient|visit)\b", text):
        return "new"
    if re.search(r"\b(?:established\s+(?:patient|visit)|follow[ -]?up|f/u|fu)\b", text):
        return "established"
    return None

def _history_summary(visit: dict[str, Any], procedures: list[dict[str, Any]], orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a compact history card; never make a past service part of the new request."""
    encounter_ref = f"Encounter/{visit.get('id')}"
    related = [*([row for row in procedures if _reference(row.get("encounter")) == encounter_ref]),
               *([row for row in orders if _reference(row.get("encounter")) == encounter_ref])]
    labels = []
    for field in (visit.get("reasonCode"), visit.get("type"), visit.get("serviceType")):
        labels.extend(_concept_labels(field))
    # Some eCW builds keep a completed visit's code on the Encounter rather
    # than on a linked Procedure or ServiceRequest.
    codes = list(dict.fromkeys([*_coding(visit, "procedure"),
                                *(code for row in related for code in _coding(row, "procedure"))]))[:12]
    return {"date": _appointment_date(visit), "status": str(visit.get("status") or "not_returned"),
            "visit_type": _visit_type(visit) or "not_returned",
            "reason": "; ".join(dict.fromkeys(" ".join(label.split()) for label in labels if label.strip()))[:500] or None,
            "coded_services": codes}
