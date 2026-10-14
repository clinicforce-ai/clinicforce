"""Compile explicit Library cash-price table cells."""
from __future__ import annotations

import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from app.insurance import commercial


PROVISIONAL = re.compile(r'\b(?:proposed|pending|suggested|draft|unapproved)\b', re.I)
CODE = r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])'
DOLLARS = r'\$?\s*\d[\d,]*(?:\.\d{1,2})?'


def stable_id(*values: str) -> str:
    return str(uuid5(NAMESPACE_URL, ':'.join(values)))


def dollars(value: str) -> int:
    return commercial._money(value.replace('$', '').replace(',', '').strip(), 'Document price')


def _final_cash_categories(tables: list[dict]) -> set[str]:
    """A suggested price in a different table cannot become an active rate."""
    final = set()
    for table in tables:
        rows = table.get('rows') or []
        if not rows or [commercial._key(cell) for cell in rows[0]][:3] != ['category', 'status', 'pricestatus']:
            continue
        for row in rows[1:]:
            if len(row) >= 3 and 'final / in use' in str(row[2]).casefold() and not PROVISIONAL.search(str(row[1]) + ' ' + str(row[2])):
                final.add(commercial._key(row[0]))
    return final


def _named_cash_rules(table: dict, source: dict, final: set[str]) -> tuple[list[dict], list[dict]] | None:
    """Read final service packages without treating likely CPTs as inclusions."""
    rows = table.get('rows') or []
    if not rows:
        return None
    headers = [commercial._key(cell) for cell in rows[0]]
    context = str(table.get('context') or '')
    if headers[:4] == ['component', 'cpthcpcsreference', 'cashprice', 'howtoapply'] and 'botox' in context.casefold():
        if 'botox' not in final:
            return [], [{'status': 'provisional', 'reason': 'Botox price is not marked final in the source status table.', **source, 'locator': table['locator']}]
        if len(rows) != 3:
            return [], [{'status': 'missing_dependency', 'reason': 'Botox session and drug prices must both be stated.', **source, 'locator': table['locator']}]
        session, drug = rows[1:]
        session_price = re.fullmatch(rf'({DOLLARS})\s*/\s*session', str(session[2]).strip(), re.I)
        drug_price = re.fullmatch(rf'({DOLLARS})\s*/\s*unit', str(drug[2]).strip(), re.I)
        ranges = re.findall(r'(\d{5})\s*[-–]\s*(\d{5})', str(session[1]))
        if (not session_price or not drug_price or 'J0585' not in str(drug[1]).upper()
                or len(ranges) != 2 or 'injection' not in str(session[0]).casefold()):
            return [], [{'status': 'missing_dependency', 'reason': 'Botox session CPT scope or per-unit drug price is incomplete.', **source, 'locator': table['locator']}]
        codes = set()
        for first, last in ranges:
            if int(first) > int(last) or int(last) - int(first) > 10:
                return [], [{'status': 'missing_dependency', 'reason': 'Botox CPT range is too broad.', **source, 'locator': table['locator']}]
            codes.update(str(code) for code in range(int(first), int(last) + 1))
        codes -= {'64613', '64614'}  # Already excluded from the current visit planner.
        def evidence(index: int) -> dict:
            number = (table.get('row_numbers') or list(range(1, len(rows) + 1)))[index]
            return {**source, 'locator': f"{table['locator']}, row {number}, price column 3"}
        session_rows = [{'code': code, 'cash_cents': dollars(session_price[1]), 'price_kind': 'fixed',
                         'session_group': 'botox', 'description': 'Botox injection session', 'modifier': '',
                         'effective_to': source.get('effective_to'), 'source': evidence(1)} for code in sorted(codes)]
        return [*session_rows, {'code': 'J0585', 'cash_cents': dollars(drug_price[1]), 'price_kind': 'per_unit',
                'requires_session': 'botox', 'description': 'Botox drug per unit', 'modifier': '',
                'effective_to': source.get('effective_to'), 'source': evidence(2)}], []
    if headers[:4] == ['packagebestfor', 'cashprice', 'likelycptexamples', 'codingscopenote']:
        if 'ncsemgpackages' not in final or PROVISIONAL.search(context):
            return [], [{'status': 'missing_dependency', 'reason': 'Named NCS/EMG packages need explicit final status and clinical package selection.', **source, 'locator': table['locator']}]
        packages, issues = [], []
        for index, row in enumerate(rows[1:], 1):
            label, _, indication = str(row[0] or '').partition('\n')
            label = label.strip(); indication = indication.strip()
            amount = str(row[1] or '').strip()
            number = (table.get('row_numbers') or list(range(1, len(rows) + 1)))[index]
            evidence = {**source, 'locator': f"{table['locator']}, row {number}, price column 2"}
            if not label or PROVISIONAL.search(' '.join(str(cell or '') for cell in row)) or not re.fullmatch(DOLLARS, amount):
                issues.append({'status': 'missing_dependency', 'reason': 'Named package needs a final fixed price and a clear name.', **evidence})
                continue
            identifier = 'named:' + hashlib.sha256(label.casefold().encode()).hexdigest()[:20]
            packages.append({'code': identifier, 'cash_cents': dollars(amount), 'price_kind': 'named_package',
                             'label': label, 'indication': indication, 'scope_note': str(row[3] or '').strip(), 'description': label,
                             'modifier': '', 'effective_to': source.get('effective_to'), 'source': evidence})
        return packages, issues
    return None


def cash_tables(tables: list[dict], source: dict) -> tuple[list[dict], list[dict]]:
    rows, issues = [], []
    final = _final_cash_categories(tables)
    for table in tables:
        named = _named_cash_rules(table, source, final)
        if named is not None:
            rows.extend(named[0]); issues.extend(named[1])
            continue
        cells = table['rows']
        if not cells:
            continue
        headers = [commercial._key(cell) for cell in cells[0]]
        code_cols = [i for i, name in enumerate(headers) if name in {'cpt', 'hcpcs', 'code', 'procedurecode', 'cptcode', 'cptrange', 'includedcodes', 'cptreference', 'cpthcpcsreference'}]
        price_cols = [i for i, name in enumerate(headers) if name in {'amount', 'price', 'cash', 'selfpay'} or name.endswith(('cashprice', 'cashrate', 'selfpayprice', 'selfpayrate'))]
        if len(code_cols) != 1 or len(price_cols) != 1:
            if price_cols and any('likelycpt' in header or 'package' in header for header in headers):
                issues.append({'status': 'missing_dependency', 'reason': 'Named package prices require the exact ordered package scope; likely CPT examples do not establish included services or units.', **source, 'locator': table['locator']})
            continue
        for number, cells_row in enumerate(cells[1:], 2):
            source_row = table.get('row_numbers', list(range(1, len(cells) + 1)))[number - 1]
            locator = f"{table['locator']}, row {source_row}, price column {price_cols[0] + 1}"
            values = {header: str(cells_row[i] or '').strip() if i < len(cells_row) else '' for i, header in enumerate(headers)}
            evidence = {**source, 'locator': locator}
            text = ' '.join(values.values())
            if not text.strip():
                continue
            if PROVISIONAL.search(table.get('context', '')) or PROVISIONAL.search(text):
                issues.append({'status': 'provisional', 'reason': 'Provisional price excluded.', **evidence})
                continue
            if re.search(r'\bfriends\s*(?:&|and)\s*family\b|\bdiscount(?:ed)?\b', text, re.I):
                issues.append({'status': 'missing_dependency', 'reason': 'Discounted price requires recorded discount eligibility; excluded from the standard cash quote.', **evidence})
                continue
            token = values[headers[code_cols[0]]].upper()
            price = values[headers[price_cols[0]]]
            description = next((values[key] for key in ('description', 'service', 'procedure') if values.get(key)), '')
            base = {'description': description, 'source': evidence, 'effective_to': source.get('effective_to'),
                    'modifier': values.get('modifier', ''), 'price_kind': 'per_unit'}
            try:
                if re.search(r'\badd to\b.*\bsession fee\b', text, re.I):
                    raise ValueError('This component requires a separate session fee; exact procedure scope and drug units are needed for the combined quote.')
                formula = re.fullmatch(rf'({DOLLARS})\s*\+\s*({DOLLARS})\s*(?:/|per\s+)(?:unit|units)', price, re.I)
                per_unit = re.fullmatch(rf'({DOLLARS})\s*(?:/|per\s+)(?:unit|units)', price, re.I)
                fixed = re.fullmatch(rf'({DOLLARS})\s*(?:/|per\s+)(?:visit|session)', price, re.I)
                if formula:
                    base.update(price_kind='fixed_plus_unit', base_cents=dollars(formula[1]), cash_cents=dollars(formula[2]))
                elif per_unit or fixed:
                    base.update(cash_cents=dollars((per_unit or fixed)[1]), price_kind='fixed' if fixed else 'per_unit')
                elif re.fullmatch(DOLLARS, price):
                    base['cash_cents'] = dollars(price)
                    unit_rule = commercial._key(values.get('unitrule', 'per_unit'))
                    if unit_rule in {'fixed', 'pervisit', 'persession'}:
                        base['price_kind'] = 'fixed'
                    elif unit_rule != 'perunit':
                        raise ValueError('Cash price has an unsupported unit rule.')
                else:
                    raise ValueError('Price needs an explicit amount or supported fixed-plus-unit formula.')
                if re.fullmatch(CODE, token):
                    expanded = [token]
                elif re.fullmatch(r'\d{5}\s*[-–]\s*\d{5}', token):
                    start, end = map(int, re.split(r'\s*[-–]\s*', token))
                    if not start <= end <= start + 20:
                        raise ValueError('CPT range exceeds 21 alternatives.')
                    expanded = [str(n) for n in range(start, end + 1)]
                elif '+' in token:
                    components = []
                    for component in token.split('+'):
                        match = re.fullmatch(rf'\s*({CODE})(?:\s*[X×]\s*(\d+))?\s*', component)
                        if not match:
                            raise ValueError('Package inclusions or units are not explicit.')
                        components.append({'code': match[1], 'units': int(match[2] or 1)})
                    if len({row['code'] for row in components}) != len(components) or any(row['units'] < 1 for row in components):
                        raise ValueError('Package components must be unique with positive units.')
                    if base['price_kind'] != 'per_unit' or base['modifier']:
                        raise ValueError('A package needs one fixed total without unspecified modifiers.')
                    base.update(price_kind='package', components=components)
                    expanded = ['package:' + hashlib.sha256(json.dumps(components, sort_keys=True).encode()).hexdigest()[:20]]
                else:
                    raise ValueError('Service code or complete package inclusions are missing.')
                if re.search(r'\b(?:likely|approximately|starting|discount|depending|minimum|maximum)\b', text, re.I):
                    raise ValueError('Conditional service scope or price cannot supply a fixed quote.')
                if re.search(r'\b(?:package|bundle)\b', text, re.I) and base['price_kind'] != 'package':
                    raise ValueError('Package total requires its full included CPTs and units.')
                if re.search(r'\bsession\b', description, re.I) and base['price_kind'] == 'per_unit' and not values.get('unitrule') and not per_unit:
                    raise ValueError('Session price needs explicit per-session or per-unit semantics.')
                for code in expanded:
                    rows.append({**base, 'code': code})
            except (ValueError, commercial.CommercialError) as exc:
                issues.append({'status': 'missing_dependency', 'reason': str(exc), **evidence})
    # Identical evidence is harmless; conflicting final rows must not use last-wins.
    grouped = {}
    for row in rows:
        grouped.setdefault((row['code'], row['modifier']), []).append(row)
    usable = []
    for group in grouped.values():
        signatures = {json.dumps({k: v for k, v in row.items() if k not in {'source', 'description'}}, sort_keys=True) for row in group}
        if len(signatures) == 1:
            usable.append(group[0])
        else:
            issues.append({'status': 'conflict', 'reason': f"Conflicting final prices for {group[0]['code']}.", **group[0]['source']})
    return usable, issues
