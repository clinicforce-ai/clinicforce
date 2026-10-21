"""Plan-specific research from NMCT's three-sheet workbook, never payer approval."""
from __future__ import annotations

import re
import hashlib
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app.core.library_parsing import structured_content
from app.insurance import cpt_rules

WORKBOOK = Path(__file__).with_name('NMCT_Payer_Plan_Matrix.xlsx')
SHEETS = ('Payer Plan Matrix', 'ClinicForce Data Checklist', 'Portal & Contact Reference')
PAYER_ALIASES = {
    'Traditional Medicare': {'traditionalmedicare', 'medicarepartb', 'originalmedicare'},
    'Cigna': {'cigna'}, 'Aetna': {'aetna'},
    'UnitedHealthcare': {'unitedhealthcare', 'uhc', 'wellmed'},
    'Wellpoint (Texas)': {'wellpoint', 'wellpointtexas'},
    'HealthSmart': {'healthsmart'},
    'MultiPlan / PHCS (Claritev)': {'multiplan', 'phcs', 'claritev'},
    'Humana Medicare (Pending)': {'humana', 'humanamedicare'},
    'Blue Cross Blue Shield of Texas': {'bluecrossblueshieldoftexas', 'bluecrossandblueshieldoftexas',
                                        'bluecrossblueshieldtx', 'bcbstx', 'bcbstexas'},
    'Blue Cross Blue Shield (out-of-state BlueCard)': {'bluecard', 'outofstatebluecard'},
}
CODE = re.compile(r'\b(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])\b')


def _key(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _urls(value: object) -> list[str]:
    urls = re.findall(r'https://[^\s,;]+', str(value or ''))
    return [url for url in urls if urlparse(url).hostname]


def _names(value: str) -> set[str]:
    names = set()
    for item in value.split(';'):
        item = item.strip()
        if item:
            names.add(_key(item))
        if ' / ' in item:
            for part in item.split(' / '):
                if len(part.strip()) >= 5 and (len(part.split()) > 1 or part.strip() == 'Choice'):
                    names.add(_key(part))
    return names


def _sheet(tables: list[dict], name: str, header: str) -> list[tuple[int, dict]]:
    table = next((row for row in tables if row.get('locator') == 'Sheet ' + name), None)
    if not table:
        raise ValueError(f'Missing {name} sheet')
    rows = table.get('rows') or []
    numbers = table.get('row_numbers') or list(range(1, len(rows) + 1))
    header_index = next((i for i, cells in enumerate(rows) if cells and cells[0] == header), None)
    if header_index is None:
        raise ValueError(f'Missing {name} header')
    headings = rows[header_index]
    return [(numbers[i], dict(zip(headings, cells))) for i, cells in enumerate(rows[header_index + 1:], header_index + 1)
            if cells and cells[0]]


def parse(tables: list[dict], filename: str) -> dict:
    """Reject partial or unrelated workbooks before any rule is used."""
    if any('[Formula cell;' in str(cell) for table in tables for row in table.get('rows') or [] for cell in row):
        raise ValueError('Formula cells cannot supply payer rules')
    matrix = _sheet(tables, SHEETS[0], 'Payer')
    checklist = _sheet(tables, SHEETS[1], 'Workflow Step')
    portals = _sheet(tables, SHEETS[2], 'Payer')
    if not matrix or not portals or len(checklist) != 4:
        raise ValueError('Incomplete payer workbook')
    required = {'Payer', 'Plan Group (shared rule set)', "Specific Plan / Network Names on NMCT's Accepted List",
                'Referral Required Before NMCT Visit', 'Referral Rule Detail (which CPT/services trigger it)',
                'Referral Rule Source', 'Prior Authorization Needed For',
                'Prior Auth Source / Submission Channel', 'Verification Notes',
                'Eligibility Portal URL'}
    if any(not required <= row.keys() for _, row in matrix):
        raise ValueError('Unsupported payer matrix columns')
    steps = {row['Workflow Step'].split('. ', 1)[-1]: row for _, row in checklist}
    if set(steps) != {'Eligibility check', 'Referral check', 'Prior authorization check', 'Claim-ready confirmation'}:
        raise ValueError('Incomplete verification checklist')
    if any('Exact Fields ClinicForce Must Pull' not in row for row in steps.values()):
        raise ValueError('Missing verification fields')
    portal_by_payer = {row['Payer']: {**row, '_row_number': number} for number, row in portals}
    if any('Primary Portal' not in row or 'Portal URL' not in row for row in portal_by_payer.values()):
        raise ValueError('Missing portal reference columns')
    return {'filename': filename, 'rows': matrix, 'steps': steps, 'portals': portal_by_payer}


@lru_cache(maxsize=1)
def bundled() -> dict:
    raw = WORKBOOK.read_bytes()
    _, tables = structured_content(raw, WORKBOOK.name)
    return {**parse(tables, WORKBOOK.name), 'sha256': hashlib.sha256(raw).hexdigest(),
            'source_type': 'bundled_research'}


def uploaded(documents: list[dict], service_date: str | None = None) -> dict:
    """Use the newest complete, current Clinical Library plan_matrix; otherwise bundled research."""
    applicable_date = service_date or date.today().isoformat()
    for document in documents:
        if document.get('kind') != 'plan_matrix' or (document.get('extraction') or {}).get('sha256') != document.get('sha256'):
            continue
        if document.get('effective_from') and str(document['effective_from']) > applicable_date:
            continue
        if document.get('effective_to') and str(document['effective_to']) < applicable_date:
            continue
        try:
            return {**parse((document['extraction'] or {}).get('tables') or [], document['filename']),
                    'sha256': document['sha256'], 'source_type': 'clinical_library_research',
                    **({'document_id': document['id']} if document.get('id') else {})}
        except ValueError:
            continue
    return bundled()
