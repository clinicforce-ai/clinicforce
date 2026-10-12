"""Tenant-scoped, versioned cash schedules from explicit document table cells."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.insurance import commercial, commercial_store


def read_rows(content: bytes, filename: str) -> list[dict]:
    from app.core.library_files import structured_content
    from app.insurance.library_pricing import cash_tables
    try:
        _, tables = structured_content(content, filename)
        rows, issues = cash_tables(tables, {})
    except Exception as exc:
        raise commercial.CommercialError('The schedule could not be read. Include an explicit CPT and Cash Price table.') from exc
    if not rows or any(issue['status'] == 'conflict' for issue in issues):
        raise commercial.CommercialError('No unambiguous final prices found. ' + ' '.join(issue['reason'] for issue in issues))
    # Keep the established import representation for ordinary per-CPT prices.
    return [{key: value for key, value in row.items() if key not in {'source', 'effective_to', 'modifier', 'price_kind'}}
            if row['price_kind'] == 'per_unit' and not row.get('modifier') else row for row in rows]


def create(practice_id: str, actor_id: str, content: bytes, filename: str, effective_from: str) -> dict:
    rows=read_rows(content,filename)
    record={'id':str(uuid4()),'filename':Path(filename).name[:200], 'sha256':hashlib.sha256(content).hexdigest(),
            'effective_from':effective_from,'rows':rows,'status':'draft','created_at':datetime.now(timezone.utc).isoformat()}
    with commercial_store._connection() as db:
        db.execute('''INSERT INTO practice_cash_schedules (id,practice_id,actor_id,filename,sha256,effective_from,rows_json,status,created_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(record['id'],practice_id,actor_id,record['filename'],record['sha256'],effective_from,json.dumps(rows),'draft',record['created_at']))
    return record


def activate(practice_id: str, version_id: str) -> dict:
    with commercial_store._connection() as db:
        row=db.execute("UPDATE practice_cash_schedules SET status='active' WHERE id=%s AND practice_id=%s RETURNING id,filename,effective_from,status",(version_id,practice_id)).fetchone()
        if not row:
            raise commercial.CommercialError('Schedule not found in this practice.')
    return dict(row)


def current(practice_id: str, service_date: str) -> dict | None:
    with commercial_store._connection() as db:
        records = [dict(row) for row in db.execute("SELECT * FROM practice_cash_schedules WHERE practice_id=%s AND status='active' AND effective_from<=%s ORDER BY effective_from DESC,created_at DESC,id DESC", (practice_id,service_date)).fetchall()]
    if not records:
        return None
    # Keep the existing administrator schedule revision behavior. Library sources
    # coexist by explicit scope; upload recency cannot resolve a contradiction.
    manual = next((row for row in records if row['actor_id'] != 'system:library'), None)
    records = [row for row in records if row['actor_id'] == 'system:library'] + ([manual] if manual else [])
    rows = []
    for record in records:
        for row in json.loads(record['rows_json']):
            if not row.get('effective_to') or service_date <= row['effective_to']:
                rows.append({**row, 'schedule_id': record['id'], 'schedule_filename': record['filename'],
                             'schedule_effective_from': record['effective_from']})
    return {**records[0], 'rows': rows, 'versions': [row['id'] for row in records]}


def _cash_rule(row: dict) -> tuple:
    return (row.get('price_kind', 'per_unit'), row.get('cash_cents'), row.get('base_cents', 0),
            json.dumps(row.get('components'), sort_keys=True), row.get('session_group'),
            row.get('requires_session'), row.get('label'), row.get('indication'), row.get('scope_note'))


def apply(result: dict, config: dict, review: dict | None = None) -> None:
    priced = result['price_range']
    result.pop('cash_schedule', None)
    rates = {}
    for rule in config['rows']:
        rates.setdefault((rule['code'], rule.get('modifier', '')), []).append(rule)
    conflicts = {key for key, rows in rates.items() if len({_cash_rule(row) for row in rows}) > 1}
    rates = {key: rows[0] for key, rows in rates.items() if key not in conflicts}
    named = [rule for rule in rates.values() if rule.get('price_kind') == 'named_package']
    if named:
        sources = {rule.get('schedule_filename', config['filename']) for rule in named}
        result['cash_schedule'] = {'source': next(iter(sources)) if len(sources) == 1 else 'Multiple cash sources',
            'effective_from': config['effective_from'],
            'packages': [{'id': rule['code'], 'label': rule['label'], 'amount_cents': rule['cash_cents'],
                          'indication': rule.get('indication'), 'scope_note': rule.get('scope_note'),
                          'filename': rule.get('schedule_filename', config['filename']),
                          'effective_from': rule.get('schedule_effective_from', config['effective_from']),
                          'source': rule.get('source')} for rule in named]}
    for row in result['available_codes']:
        rule = rates.get((row['code'], ','.join(row.get('modifiers') or [])), {})
        row['unit_cash_cents'] = rule.get('cash_cents') if rule.get('price_kind', 'per_unit') == 'per_unit' else None
    amounts = []
    session_indices = []
    session_prices = set()
    for line in priced['lines']:
        line.pop('cash_included', None)
        rule = rates.get((line['code'], ','.join(line.get('modifiers') or [])), {})
        unit = rule.get('cash_cents')
        kind = rule.get('price_kind', 'per_unit')
        units = line.get('units')
        amount = None
        if unit is not None and isinstance(units, int) and units > 0:
            amount = unit if kind == 'fixed' else unit * units + rule.get('base_cents', 0)
        line.update(unit_cash_cents=unit if kind == 'per_unit' else None, cash_cents=amount,
                    cash_source=rule.get('schedule_filename', config['filename']), cash_rule=rule.get('source'), cash_price_kind=kind)
        if rule.get('session_group') == 'botox':
            session_indices.append(len(amounts))
            session_prices.add(unit)
        amounts.append(amount)
    if session_indices:
        if len(session_prices) != 1:
            amounts = [None]
        else:
            for index in session_indices[1:]:
                amounts[index] = 0
                priced['lines'][index].update(cash_cents=None, cash_included=True)
    if not session_indices:
        for index, line in enumerate(priced['lines']):
            rule = rates.get((line['code'], ','.join(line.get('modifiers') or [])), {})
            if rule.get('requires_session'):
                amounts[index] = None
                line['cash_cents'] = None
    packages = []
    if priced['mode'] == 'services':
        # Match full explicit inclusions and units; never split a package price or
        # bill included lines again. Overlapping packages are ambiguous.
        lines = priced['lines']
        for rule in rates.values():
            if rule.get('price_kind') != 'package':
                continue
            indices = []
            for component in rule['components']:
                matches = [i for i, line in enumerate(lines) if line['code'] == component['code']
                           and line.get('units') == component['units'] and not line.get('modifiers')]
                if len(matches) != 1:
                    break
                indices.append(matches[0])
            else:
                packages.append((rule, indices))
        covered = [index for _, indices in packages for index in indices]
        if len(covered) != len(set(covered)):
            amounts = [None]  # Competing package totals, no arbitrary cheapest pick.
        else:
            for rule, indices in packages:
                for index in indices:
                    amounts[index] = 0
                    lines[index].update(cash_cents=None, unit_cash_cents=None, cash_package=rule['code'],
                                        cash_source=rule.get('schedule_filename', config['filename']), cash_rule=rule.get('source'))
                amounts.append(rule['cash_cents'])
    selection = (review or {}).get('cash_selection') or ''
    if selection:
        package = rates.get((selection, ''), {})
        codes = [line['code'] for line in priced['lines']]
        nerve_codes = {str(code) for code in range(95907, 95914)}
        clinical_codes = {*nerve_codes, '95885', '95886',
                          *(str(code) for code in (*range(99202, 99206), *range(99212, 99216)))}
        if (package.get('price_kind') == 'named_package' and priced.get('mode') == 'services'
                and any(code in nerve_codes for code in codes)
                and all(code in clinical_codes for code in codes)):
            for line in priced['lines']:
                line.update(cash_cents=None, unit_cash_cents=None, cash_included=True,
                            cash_package=selection, cash_source=package.get('schedule_filename', config['filename']),
                            cash_rule=package.get('source'))
            amounts = [package['cash_cents']]
            packages = [(package, list(range(len(priced['lines']))))]
        else:
            amounts = [None]
    complete = bool(amounts) and all(value is not None for value in amounts) and not priced.get('service_issues')
    priced.update(cash_complete=complete, cash_packages=[{'code': rule['code'], 'label': rule.get('label'), 'amount_cents': rule['cash_cents'], 'source': rule.get('source')} for rule, _ in packages])
    priced.pop('cash_min_cents', None); priced.pop('cash_max_cents', None)
    if complete:
        priced.update(cash_min_cents=min(amounts) if priced['mode'] != 'services' else sum(amounts), cash_max_cents=max(amounts) if priced['mode'] != 'services' else sum(amounts))
    result['cash_quote'] = {'complete': complete, 'source': package.get('schedule_filename', config['filename']) if selection else config['filename'],
        'effective_from': package.get('schedule_effective_from', config['effective_from']) if selection else config['effective_from'],
        'selection': selection,
        'package_label': package.get('label') if selection else None,
        'message': ('Selected NCS/EMG package includes the focused visit and planned testing.' if selection and complete else
                    'Practice cash schedule for the selected services.' if complete else
                    'The cash schedule has missing or conflicting prices for the selected services.')}
    result['practice_cash_source'] = {'filename': config['filename'], 'effective_from': config['effective_from'], 'version': config['id'], 'versions': config.get('versions', [config['id']])}
    if complete:
        result['cash_quote'].update(min_cents=priced['cash_min_cents'], max_cents=priced['cash_max_cents'])
