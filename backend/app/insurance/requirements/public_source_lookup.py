"""Check scoped workbook leads against current public payer sources.

Only fixed official URLs are fetched. Patient identifiers are never sent to
these pages. An unavailable or incomplete source is not negative evidence.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone

import fitz
import httpx
from lxml import html

from app.insurance import plan_matrix
from app.insurance.identity import generic_plan_label, payer_identity_verified

UHC_REFERRAL = 'https://www.uhcprovider.com/en/health-plans-by-state/texas-health-plans/tx-commercial-plans.html'
UHC_COMMERCIAL_PA = 'https://ams-gateway.uhcprovider.com/content/dam/provider/docs/public/prior-auth/pa-requirements/commercial/UHC-Commercial-PA-Requirements-11-2026.pdf'
UHC_MEDICARE_PA = 'https://ams-gateway.uhcprovider.com/content/dam/provider/docs/public/prior-auth/pa-requirements/medicare/Med-Adv-Dual-Eff-10-1-26.pdf'
_MAX_BYTES = 1_500_000
_CODE = re.compile(r'(?<![A-Z0-9])(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])(?![A-Z0-9])')
_CACHE: dict[tuple[str, str], tuple[str, str]] = {}


def _read(url: str, checked_day: str) -> tuple[str, str] | None:
    """Cache one day's official text and digest; never cache a failed request."""
    key = url, checked_day
    if key in _CACHE:
        return _CACHE[key]
    try:
        with httpx.Client(timeout=httpx.Timeout(5, connect=2), follow_redirects=False) as client:
            response = client.get(url)
        response.raise_for_status()
        if not 0 < len(response.content) <= _MAX_BYTES:
            return None
        media = response.headers.get('content-type', '').lower()
        if url.endswith('.pdf') and 'application/pdf' in media:
            with fitz.open(stream=response.content, filetype='pdf') as document:
                if not 1 <= len(document) <= 80:
                    return None
                if url in {UHC_COMMERCIAL_PA, UHC_MEDICARE_PA} and len(document) < 20:
                    return None
                content = ' '.join(page.get_text(sort=True) for page in document)
        elif url.endswith('.html') and 'text/html' in media:
            content = html.fromstring(response.content).text_content()
        else:
            return None
        value = ' '.join(content.split()), hashlib.sha256(response.content).hexdigest()
        if len(_CACHE) >= 12:
            _CACHE.clear()
        _CACHE[key] = value
        return value
    except (httpx.HTTPError, ValueError, fitz.FileDataError, html.etree.ParserError):
        return None


def _source(label: str, url: str, digest: str, start: str) -> dict:
    return {'label': label, 'url': url, 'source_type': 'official_live_web',
            'source_version': digest, 'verified_at': datetime.now(timezone.utc).date().isoformat(),
            'effective_from': start, 'effective_to': '2026-12-31'}


def _uhc_referral(plan: str, row: dict, code: str) -> dict | None:
    lead = str(row.get('Referral Rule Source') or '').casefold()
    detail = str(row.get('Referral Rule Detail (which CPT/services trigger it)') or '').casefold()
    matrix_answer = str(row.get('Referral Required Before NMCT Visit') or '').strip().casefold()
    if 'uhcprovider.com' not in lead or 'wellmed' in plan:
        return None
    if plan in {'choice', 'choiceplus', 'uhcchoiceplus', 'unitedhealthcarechoiceplus',
                'poschoiceplus'}:
        if matrix_answer != 'no' or 'self-refer' not in detail:
            return None
        marker = 'Choice and Choice Plus health plans allow members to choose a physician or specialist'
        decision, detail = 'Not required', 'do not need a referral to receive benefits'
    elif plan in {'navigate', 'unitedhealthcarenavigate'}:
        if matrix_answer != 'yes' or 'pcp referral is required' not in detail:
            return None
        marker = 'Navigate members must receive a referral from their PCP to see a network specialist'
        decision, detail = 'Required', 'for benefits to be covered'
    elif plan in {'nexusacooa', 'nexusacooap'}:
        marker = 'NexusACO Plans – Open Access'
        decision, detail = 'NexusACO OA plans offer'
    elif plan in {'nexusacor'}:
        marker = 'NexusACO Plans – Referral Required'
        decision, detail = 'NexusACO R plans offer'
    else:
        return None
    retrieved = _read(UHC_REFERRAL, date.today().isoformat())
    if not retrieved:
        return None
    page, digest = retrieved
    if marker.casefold() not in page.casefold() or detail.casefold() not in page.casefold():
        return None
    return {'status': decision,
            'reason': f'UHC’s current Texas plan guidance {"requires" if decision == "Required" else "does not require"} a specialist referral for this identified product; the same visit rule applies to CPT {code}.',
            'sources': [_source('UHC Texas commercial referral guidance', UHC_REFERRAL, digest, '2026-01-01')],
            'source_scope': 'identified UHC commercial product and specialist visit'}


def _uhc_pa(plan: str, row: dict, code: str, service_date: str) -> dict | None:
    lead = str(row.get('Prior Auth Source / Submission Channel') or '').casefold()
    trigger = str(row.get('Prior Authorization Needed For') or '').casefold()
    if ('uhcprovider.com' not in lead or 'emg/ncs' not in trigger
            or 'wellmed' in plan or code not in {'95908', '95886'}):
        return None
    if any(term in plan for term in ('medicare', 'advantage', 'dual')):
        url, start, marker = UHC_MEDICARE_PA, '2026-10-01', 'Effective October 1, 2026'
    elif plan in {'choice', 'choiceplus', 'uhcchoiceplus', 'unitedhealthcarechoiceplus',
                  'poschoiceplus', 'navigate', 'unitedhealthcarenavigate',
                  'nexusacooa', 'nexusacooap', 'nexusacor'}:
        url, start, marker = UHC_COMMERCIAL_PA, '2026-11-01', 'Effective November 1, 2026'
    else:
        return None
    if not start <= service_date <= '2026-12-31':
        return None
    retrieved = _read(url, date.today().isoformat())
    if not retrieved:
        return None
    page, digest = retrieved
    if (marker.casefold() not in page[:700].casefold()
            or 'prior authorization requirements' not in page[:700].casefold()):
        return None
    if code in _CODE.findall(page):
        return None  # A listed code needs its row, indication and exceptions reviewed.
    if re.search(rf'(?<!\d){code[:3]}\d{{2}}\s*[-–]\s*{code[:3]}\d{{2}}(?!\d)', page):
        return None  # An applicable range needs review before clearing a code.
    return {'status': 'Not required',
            'reason': f'CPT {code} is absent from the current UHC prior authorization requirements list for this identified product type and visit date. Check delegated rules if the member is assigned to a delegate.',
            'sources': [_source('UHC prior authorization requirements list', url, digest, start)],
            'source_scope': 'participating UHC plan, published standard list',
            'member_specific_check': False}


def lookup(result: dict, context: dict, data: dict) -> dict[str, dict]:
    """Return a decision only when one matrix row and its official source match."""
    matched = plan_matrix.matched_row(context, data)
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (not matched or benefits.get('active') is not True
            or benefits.get('service_date_matches') is not True
            or benefits.get('network_status') != 'in_network'
            or not payer_identity_verified(identity)
            or not context.get('member_id')
            or str(context.get('pos') or '') != '11'):
        return {}
    _, row = matched
    if row['Payer'] != 'UnitedHealthcare':
        return {}
    plans = {plan_matrix._key(name) for name in context.get('plans') or []
             if name and not generic_plan_label(name)}
    plans = {'choiceplus' if plan in {'unitedhealthcarechoiceplus', 'uhcchoiceplus',
                                    'poschoiceplus'} else plan for plan in plans}
    if len(plans) != 1:
        return {}
    plan = next(iter(plans))
    code_rows = result.get('codes') or []
    if not code_rows or any(item.get('needs_confirmation') for item in code_rows):
        return {}
    codes = {str(item.get('code') or '') for item in code_rows}
    if not codes <= {'95908', '95886'}:
        return {}
    service_date = str(context.get('service_date') or '')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', service_date):
        return {}
    referral = [_uhc_referral(plan, row, code) for code in sorted(codes)]
    authorization = [_uhc_pa(plan, row, code, service_date) for code in sorted(codes)]
    output = {}
    for kind, decisions in (('referral', referral), ('authorization', authorization)):
        if all(decisions) and len({item['status'] for item in decisions}) == 1:
            output[kind] = {**decisions[0],
                            'reason': decisions[0]['reason'].replace(f'CPT {sorted(codes)[0]}',
                                f'CPTs {", ".join(sorted(codes))}' if len(codes) > 1 else f'CPT {sorted(codes)[0]}')}
    return output
