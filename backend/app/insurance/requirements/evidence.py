"""Read source-bound contract rules and policy page citations."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from app.insurance import plan_matrix

KINDS = ('referral', 'authorization')
CODE = re.compile(r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])')
PAGE = re.compile(r'^Page (\d+)\b', re.I)
PROVISIONAL = re.compile(r'\b(?:draft|proposed|pending|suggested|unapproved)\b', re.I)
OTHER_POLICY = re.compile(r'\b(?:provider manual|policies and procedures|coverage agreement|payer policy|medical policy|see policy|member handbook|plan benefits)\b', re.I)
CONDITIONAL = re.compile(r'\b(?:if|unless|except|subject to|depending on|as applicable|where required)\b', re.I)
HEADERS = {
    'referral': {'referral', 'referralrequired', 'referralrequirement'},
    'authorization': {'priorauthorization', 'priorauthorizationrequired', 'priorauth', 'priorauthrequired',
                      'preauthorization', 'preauthorizationrequired', 'authorization',
                      'authorizationrequired', 'precertification'},
}


def _key(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())

def _codes(value: object) -> set[str]:
    found = set()
    for part in re.split(r'[,;/+]', str(value or '').upper()):
        part = part.strip()
        if CODE.fullmatch(part):
            found.add(part)
        elif re.fullmatch(r'\d{5}\s*[-–]\s*\d{5}', part):
            start, end = map(int, re.split(r'\s*[-–]\s*', part))
            if start <= end <= start + 20:
                found.update(str(code) for code in range(start, end + 1))
    return found

def _status(value: object) -> str | None:
    text = re.sub(r'\s+', ' ', str(value or '').strip()).casefold()
    if text in {'yes', 'y', 'required', 'referral required', 'authorization required',
                'prior authorization required', 'preauthorization required'}:
        return 'Required'
    if text in {'no', 'n', 'not required', 'no referral required', 'referral not required',
                'no authorization required', 'prior authorization not required',
                'preauthorization not required'}:
        return 'Not required'
    return None

def _source(document: dict, page: int | None = None) -> dict:
    return {'filename': document['filename'], 'document_id': document['id'], **({'page': page} if page else {})}

def _date(value: object) -> date | None:
    if not value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))

def _matches_scope(values: dict, context: dict, document: dict, linked: bool) -> bool:
    service_date = context['service_date']
    start = values.get('effectivefrom') or document.get('effective_from') or ''
    end = values.get('effectiveto') or document.get('effective_to') or ''
    try:
        visit_date, start_date, end_date = _date(service_date), _date(start), _date(end)
        if (start_date and start_date > visit_date) or (end_date and end_date < visit_date):
            return False
    except (ValueError, TypeError):
        return False
    matches = {
        'plan': lambda value: _key(value) in context['plans'],
        'product': lambda value: _key(value) in context['plans'],
        'payer': lambda value: _key(value) in context['payer_names'],
        'payername': lambda value: _key(value) in context['payer_names'],
        'network': lambda value: _key(value) == _key(context.get('network')),
        'renderingnpi': lambda value: value == context.get('rendering_npi'),
        'providernpi': lambda value: value == context.get('rendering_npi'),
        'billingnpi': lambda value: value == context.get('billing_npi'),
        'tin': lambda value: value == context.get('tin'),
        'groupnumber': lambda value: _key(value) == _key(context.get('group_number')),
        'plannumber': lambda value: _key(value) == _key(context.get('plan_number')),
        'locationid': lambda value: value == context.get('location_id'),
        'pos': lambda value: value == context.get('pos'),
        'placeofservice': lambda value: value == context.get('pos'),
    }
    known = {*matches, 'effectivefrom', 'effectiveto', 'cpt', 'hcpcs', 'code', 'procedurecode', 'cptcode',
             *HEADERS['referral'], *HEADERS['authorization'], 'notes', 'note', 'description', 'service'}
    if any(value and key not in known for key, value in values.items()):
        return False
    if any(value and key in matches and not matches[key](value) for key, value in values.items()):
        return False
    if linked:
        return True
    # An unlinked PDF must carry enough exact qualifiers to establish the
    # provider, product, network, setting and date for its own table row.
    return (context.get('payer_verified') and bool(start)
            and any(values.get(key) for key in ('plan', 'product'))
            and bool(values.get('network')) and bool(values.get('renderingnpi') or values.get('providernpi'))
            and bool(values.get('billingnpi')) and bool(values.get('pos') or values.get('placeofservice')))

def _table_rules(document: dict, context: dict, linked: bool) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = defaultdict(list)
    for table in (document.get('extraction') or {}).get('tables') or []:
        page = PAGE.match(table.get('locator') or '')
        rows = table.get('rows') or []
        if not page or len(rows) < 2 or PROVISIONAL.search(table.get('context') or ''):
            continue
        headers = [_key(cell) for cell in rows[0]]
        code_index = next((index for index, header in enumerate(headers)
                           if header in {'cpt', 'hcpcs', 'code', 'procedurecode', 'cptcode'}), None)
        if code_index is None:
            continue
        kind_columns = {kind: next((index for index, header in enumerate(headers) if header in HEADERS[kind]), None)
                        for kind in KINDS}
        if all(index is None for index in kind_columns.values()):
            continue
        for row in rows[1:]:
            values = {header: str(row[index] or '').strip() if index < len(row) else ''
                      for index, header in enumerate(headers)}
            codes = _codes(row[code_index] if code_index < len(row) else '') & context['codes']
            if not codes or PROVISIONAL.search(' '.join(values.values())) or not _matches_scope(values, context, document, linked):
                continue
            note = ' '.join(values.get(key, '') for key in ('notes', 'note', 'description', 'service'))
            for kind, index in kind_columns.items():
                if index is None:
                    continue
                cell = str(row[index] or '').strip() if index < len(row) else ''
                status = (_status(cell) if not (OTHER_POLICY.search(cell + ' ' + note)
                                              or CONDITIONAL.search(cell + ' ' + note)) else None)
                if not cell:
                    continue
                for code in codes:
                    found[kind].append({'code': code, 'status': status, 'source': _source(document, int(page[1])),
                                        'reason': (f'Contract row marks CPT {code} {kind} {status.lower()}.' if status else
                                                   f'Contract row for CPT {code} needs payer-policy or condition review.')})
    return found

def _policy_page(document: dict, kind: str) -> int | None:
    term = r'referral' if kind == 'referral' else r'(?:preauthoriz\w*|prior authoriz\w*|precertif\w*|authorization)'
    pages = (document.get('extraction') or {}).get('pages') or []
    for pattern in (rf'provider (?:will|must) comply with all .{{0,70}}{term}.{{0,180}}'
                    rf'(?:provider manual|policies and procedures|coverage agreement)',
                    rf'{term}.{{0,100}}(?:per|under|according to|set forth in).{{0,100}}'
                    rf'(?:provider manual|policies and procedures|coverage agreement)'):
        for page in pages:
            if re.search(pattern, re.sub(r'\s+', ' ', page.get('text') or ''), re.I):
                return page.get('number')
    return None
