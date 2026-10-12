"""Plan-specific research from NMCT's three-sheet workbook, never payer approval."""
from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app.core import library_files
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
                                        'bcbstx', 'bcbstexas'},
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
                'Prior Authorization Needed For', 'Eligibility Portal URL'}
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
    _, tables = library_files.structured_content(WORKBOOK.read_bytes(), WORKBOOK.name)
    return parse(tables, WORKBOOK.name)


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
            return parse((document['extraction'] or {}).get('tables') or [], document['filename'])
        except ValueError:
            continue
    return bundled()


def _payer(row: dict, names: set[str]) -> bool:
    payer = row['Payer']
    aliases = PAYER_ALIASES.get(payer) or {_key(payer)}
    return any(name == alias or len(alias) >= 6 and name.startswith(alias)
               for name in names for alias in aliases)


def _portal_reference(data: dict, row: dict, plan: str) -> dict:
    payer = row['Payer']
    return data['portals'].get('UnitedHealthcare (WellMed)' if 'wellmed' in plan and payer == 'UnitedHealthcare' else payer) or {}


def _portal(data: dict, row: dict, plan: str, kind: str) -> str | None:
    payer = row['Payer']
    reference = _portal_reference(data, row, plan)
    # The contact sheet identifies the portal; the matrix supplies a fully qualified URL.
    portal_name = _key(reference.get('Primary Portal'))
    matrix_urls = _urls(row.get('Eligibility Portal URL'))
    reference_urls = _urls(' '.join(url if url.startswith('https://') else 'https://' + url
                                    for url in re.split(r'\s+and\s+', str(reference.get('Portal URL') or '')) if url))
    urls = reference_urls or matrix_urls
    if kind == 'authorization' and payer == 'Humana Medicare (Pending)':
        return next((url for url in matrix_urls if 'humana.com' in urlparse(url).netloc), None) or (urls[0] if urls else None)
    if 'wellmed' in plan and 'wellmed' in portal_name:
        return next((url for url in urls if 'wellmed' in urlparse(url).netloc), None)
    return urls[0] if urls else None


def _patient_evidence(result: dict, context: dict, steps: dict) -> list[str]:
    description = steps['Eligibility check']['Exact Fields ClinicForce Must Pull'].casefold()
    benefits = result.get('benefits') or {}
    identity = benefits.get('identity') or result.get('identity') or {}
    gaps = []
    if 'coverage status' in description and benefits.get('active') is not True:
        gaps.append('active coverage')
    period = context.get('coverage_period') or {}
    visit = str(context.get('service_date') or '')[:10]
    start, end = str(period.get('start') or '')[:10], str(period.get('end') or '')[:10]
    if 'effective date' in description and (benefits.get('service_date_matches') is False or
            start and start > visit or end and end < visit or not
            (benefits.get('service_date_matches') is True or start or end)):
        gaps.append('coverage dates')
    if 'member id' in description and not context.get('member_id'):
        gaps.append('member ID')
    if 'group number' in description and not context.get('group_number') and context.get('payer') != 'Traditional Medicare':
        gaps.append('group number')
    if not context.get('rendering_npi'):
        gaps.append('rendering provider NPI')
    if context.get('payer') != 'Traditional Medicare' and benefits.get('network_status') != 'in_network':
        gaps.append('provider network')
    if identity.get('status') not in {'matched', 'reconciled'}:
        gaps.append('patient identity')
    if not result.get('benefit_source'):
        gaps.append('current payer eligibility source')
    return gaps


def evaluate(result: dict, context: dict, data: dict) -> dict | None:
    """Return independent referral/auth research for an exact plan, or cautious lookup guidance."""
    payer_names = {_key(value) for value in context.get('payer_names') or [] if value}
    payer_rows = [(number, row) for number, row in data['rows'] if _payer(row, payer_names)]
    if not payer_rows:
        return None
    plans = {_key(value) for value in context.get('plans') or [] if _key(value) not in {'', 'ci', 'commercial', 'ppo', 'hmo'}}
    matched = [(number, row) for number, row in payer_rows
               if plans & _names(row["Specific Plan / Network Names on NMCT's Accepted List"])]
    if len(matched) != 1:
        row = payer_rows[0][1]
        label = f"{data['filename']} · Payer Plan Matrix, rows {', '.join(str(n) for n, _ in payer_rows)} (Excel research)"
        reason = ('Patient plan matches multiple matrix groups; confirm the exact product.' if matched else
                  'Exact patient plan/product is not identified in the matrix; confirm it with the payer.')
        return {kind: {'status': 'Verification required', 'reason': reason, 'sources': [{'label': label}],
                       'verify_url': _portal(data, row, '', kind), 'contact': None} for kind in ('referral', 'authorization')}
    number, row = matched[0]
    plan = next((value for value in context.get('plans') or [] if _key(value) in _names(row["Specific Plan / Network Names on NMCT's Accepted List"])), '')
    source = {'label': f"{data['filename']} · Payer Plan Matrix, row {number} · {plan} (Excel research)"}
    gaps = _patient_evidence(result, {**context, 'payer': row['Payer']}, data['steps'])
    uncertain = any(code.get('needs_confirmation') or code.get('source') == 'conditional office E/M alternative'
                    for code in result.get('codes') or [])
    rental = row['Payer'] in {'HealthSmart', 'MultiPlan / PHCS (Claritev)', 'Blue Cross Blue Shield (out-of-state BlueCard)'}
    referral = str(row['Referral Required Before NMCT Visit'] or '').strip().casefold()
    if referral.startswith('yes for hmo'):
        referral = 'yes' if 'hmo' in _key(plan) else 'no' if any(term in _key(plan) for term in ('ppo', 'pffs')) else 'varies'
    referral_status = 'Required' if referral == 'yes' else 'Not required' if referral == 'no' else 'Verification required'
    conditional_hmo = row['Payer'] == 'Blue Cross Blue Shield of Texas' and _key(plan) in {'blueessentials', 'blueadvantageplus'}
    pending_contract = 'pending' in str(row['Plan Group (shared rule set)']).casefold()
    if rental or gaps or uncertain or conditional_hmo or not result.get('codes') or pending_contract and referral == 'no':
        referral_status = 'Verification required'
    detail = str(row['Referral Rule Detail (which CPT/services trigger it)'] or '').strip()
    detail_key = detail.casefold()
    contradictory_referral = (row['Payer'] != 'Humana Medicare (Pending)' and
        (referral == 'yes' and any(term in detail_key for term in ('no referral', 'without a referral')) or
         referral == 'no' and re.search(r'(?:requires?|must submit).{0,35}referral', detail_key)))
    if rental:
        referral_reason = 'The underlying payer or employer plan, not this rental network, controls referrals.'
    elif contradictory_referral:
        referral_status = 'Verification required'
        referral_reason = 'The matrix referral answer conflicts with its explanation; confirm with the payer.'
    elif conditional_hmo:
        referral_reason = 'This HMO usually needs a PCP referral, but its same-group or self-directed exception needs patient-specific review.'
    elif pending_contract and referral == 'no':
        referral_reason = 'This plan type usually lets members self-refer, but NMCT must confirm its contract is active and the patient’s network.'
    elif gaps:
        referral_reason = f"Matrix says {'referral required' if referral == 'yes' else 'no referral generally' if referral == 'no' else 'plan-dependent referral'}; verify {', '.join(gaps)} for this patient."
    elif uncertain:
        referral_reason = 'Confirm the planned service before applying this plan’s referral rule.'
    else:
        referral_reason = detail.split(';')[0][:160] or 'Confirm this plan’s referral rule.'
    referral_fields = data['steps']['Referral check']['Exact Fields ClinicForce Must Pull'].casefold()
    if referral_status == 'Required' and all(field in referral_fields for field in ('referring pcp', 'number of visits', 'start and end date')):
        referral_reason += ' Referral on file, PCP, visit count and dates still need confirmation.'
    if row['Payer'] == 'Humana Medicare (Pending)':
        referral_reason += ' Confirm NMCT contract effective date separately.'
    codes = {str(code.get('code') or '').upper() for code in result.get('codes') or [] if CODE.fullmatch(str(code.get('code') or '').upper())}
    trigger_text = str(row['Prior Authorization Needed For'] or '')
    explicit_codes = set(CODE.findall(trigger_text.upper()))
    triggered = codes & explicit_codes
    emg_codes = codes & (cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS) if 'EMG/NCS' in trigger_text else set()
    auth_status = 'Verification required'
    if triggered and not (rental or gaps or uncertain):
        # Research names these CPTs directly; approval remains a separate payer check.
        auth_status = 'Required' if row['Payer'] in {'Aetna', 'Blue Cross Blue Shield of Texas'} else 'Verification required'
    if rental:
        auth_reason = 'The underlying payer or employer plan controls authorization; check its ID card and plan document.'
    elif not codes or uncertain:
        auth_reason = 'Confirm the planned CPT codes before checking authorization.'
    elif gaps:
        auth_reason = f"Verify {', '.join(gaps)} before applying CPT-specific authorization research."
    elif triggered:
        auth_reason = f"Matrix flags CPT {', '.join(sorted(triggered))} for authorization review."
    elif emg_codes:
        auth_reason = f"Matrix flags EMG/NCS code {', '.join(sorted(emg_codes))} for a current payer lookup; the exact requirement is unconfirmed."
    else:
        auth_reason = 'Matrix does not settle authorization for every planned CPT; check the payer’s current code lookup.'
    auth_fields = data['steps']['Prior authorization check']['Exact Fields ClinicForce Must Pull'].casefold()
    claim_fields = data['steps']['Claim-ready confirmation']['Exact Fields ClinicForce Must Pull'].casefold()
    if triggered and all(field in auth_fields for field in ('pa status', 'tracking number', 'approved unit', 'expiration date')):
        auth_reason += ' Approval status, number, units and expiry need a separate check.'
    if triggered and 'cpt codes actually performed' in claim_fields:
        auth_reason += ' Match final billed CPTs before claim submission.'
    reference = _portal_reference(data, row, _key(plan))
    contact_source = ({'label': f"{data['filename']} · Portal & Contact Reference, row {reference['_row_number']}"}
                      if reference.get('_row_number') else None)
    def evidence(kind: str, status: str, reason: str) -> dict:
        contact = str(reference.get('Precert / PA Phone' if kind == 'authorization' else 'Provider Phone') or '').strip()
        if contact.casefold().startswith('same number'):
            contact = f"{reference.get('Provider Phone') or ''} ({contact})".strip()
        return {'status': status, 'reason': reason,
                'sources': [source, *([contact_source] if contact and contact_source else [])],
                'verify_url': _portal(data, row, _key(plan), kind),
                'verification': (str(row.get('Prior Auth Source / Submission Channel') or '').strip()
                                 if kind == 'authorization' else
                                 str(data['steps']['Referral check'].get('Where It Comes From') or '').strip()),
                'contact': contact or None}
    return {
        'referral': evidence('referral', referral_status, referral_reason),
        'authorization': evidence('authorization', auth_status, auth_reason),
    }
