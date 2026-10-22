"""Compile explicit Library tables into existing estimator stores.

Document prose is evidence, never executable instructions. Unsupported or
incomplete terms remain dependencies. No model confidence can authorize a price.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

from app.core import library_files, staff_store
from app.insurance import (commercial, commercial_store, group_contract_support,
                           library_network_exclusions, plan_matrix)
from app.insurance.identity import normalized

VERSION = 'library-rules-4'
from app.insurance.pricing.library_cash_tables import (
    PROVISIONAL,
    CODE,
    DOLLARS,
    stable_id,
    dollars,
    _final_cash_categories,
    _named_cash_rules,
    cash_tables,
)


def compile_document(document: dict, extracted: dict, connections: list[dict], networks: list[dict] | None = None) -> dict:
    source = {'document_id': document['id'], 'sha256': extracted['sha256'],
              'effective_to': document.get('effective_to'), 'reference': 'library:' + document['id']}
    compiled = {'version': VERSION, 'source': source, 'cash': [], 'rates': [], 'networks': [],
                'exclusions': [], 'issues': [], 'schedule_terms': []}
    if document['kind'] == 'plan_matrix':
        # This workbook supplies referral/auth research only. Its tables must
        # never become payer connections, participation, or price rules.
        return compiled
    tables = extracted.get('tables') or []
    text = '\n'.join(page['text'] for page in extracted['pages'])
    compiled['exclusions'] = library_network_exclusions.compile_exclusions(document, extracted)
    effective = document.get('effective_from')
    if not effective:
        matches = set(re.findall(r'(?i)effective(?:\s+(?:date|from))?\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})', text))
        effective = next(iter(matches)) if len(matches) == 1 else None
    if document['kind'] == 'self_pay':
        # Undated operational cash lists begin at upload, never retroactively.
        effective = effective or document['created_at'][:10]
        compiled['cash'], compiled['issues'] = cash_tables(tables, source)
    else:
        # A label alone may map an existing exact payer identity; it never
        # establishes provider participation, product scope or a new payer ID.
        payer = commercial._key(document.get('payer'))
        matches = [row for row in connections if payer and payer in {
            commercial._key(row.get('stedi_payer_id')), commercial._key(row.get('display_name')),
            *(commercial._key(alias) for alias in row.get('aliases', []))}]
        explicit = []
        for table in tables:
            if not table['rows'] or PROVISIONAL.search(table.get('context', '')):
                continue
            headers = [commercial._key(cell) for cell in table['rows'][0]]
            for row in table['rows'][1:]:
                values = {key: str(row[i] or '').strip() if i < len(row) else '' for i, key in enumerate(headers)}
                if (values.get('stedipayerid') and values.get('payername') and values.get('payercategory') == 'commercial_medical'
                        and not PROVISIONAL.search(' '.join(values.values()))
                        and payer in {commercial._key(values['payername']), commercial._key(values['stedipayerid'])}):
                    explicit.append((values['stedipayerid'], values['payername']))
        if not matches and len(set(explicit)) == 1:
            identifier, name = explicit[0]
            existing = [row for row in connections if row['stedi_payer_id'] == identifier]
            connection = existing[0] if existing else {'id': stable_id(document['practice_id'], 'payer', identifier),
                'display_name': name, 'stedi_payer_id': identifier, 'category': 'commercial_medical'}
            matches = [connection]
            if not existing:
                compiled['connection'] = connection
        if len(matches) == 1:
            compiled['payer_connection_id'] = matches[0]['id']
        for table in tables:
            rows = table['rows']
            if not rows:
                continue
            headers = [commercial._key(cell) for cell in rows[0]]
            if PROVISIONAL.search(table.get('context', '')):
                compiled['issues'].append({'status': 'provisional', 'reason': 'Provisional section excluded.', 'locator': table['locator']})
                continue
            for number, cells in enumerate(rows[1:], 2):
                values = {key: str(cells[i] or '').strip() if i < len(cells) else '' for i, key in enumerate(headers)}
                if PROVISIONAL.search(' '.join(values.values())):
                    continue
                source_row = table.get('row_numbers', list(range(1, len(rows) + 1)))[number - 1]
                locator = f"{table['locator']}, row {source_row}"
                if 'schedulereference' in values and 'percentofschedule' in values:
                    compiled['schedule_terms'].append({'values': values, 'source': {**source, 'locator': locator}})
                if 'participation' in values:
                    required = ('billingnpi', 'renderingnpi', 'organizationname', 'tin', 'locationid', 'pos', 'network', 'plan', 'effectivefrom')
                    uhc = commercial.is_united_healthcare_payer({'payer': document.get('payer')}) or (
                        len(matches) == 1 and commercial.is_united_healthcare_payer({'payer': matches[0].get('display_name')}))
                    if uhc and commercial._key(values.get('plan')) in {
                            '', 'uhc', 'unitedhealthcare', 'ppo', 'hmo', 'commercial', 'medicareadvantage'}:
                        compiled['issues'].append({'status': 'missing_dependency',
                            'reason': 'Participation needs the exact UHC product, not a generic payer or PPO label.',
                            'locator': locator})
                        continue
                    if (len(matches) == 1 and values['participation'].lower() in {'participating', 'nonparticipating'}
                            and all(values.get(key) for key in required)
                            and all(re.fullmatch(r'\d{10}', values[key]) for key in ('billingnpi', 'renderingnpi'))
                            and values.get('executionstatus', '').lower() in {'executed', 'signed'}):
                        try:
                            date.fromisoformat(values['effectivefrom'])
                            if values.get('effectiveto'):
                                date.fromisoformat(values['effectiveto'])
                                if values['effectiveto'] < values['effectivefrom']:
                                    raise ValueError('reversed dates')
                            values['effectivefrom'] = max(values['effectivefrom'], effective or '')
                            ends = [end for end in (values.get('effectiveto'), document.get('effective_to')) if end]
                            values['effectiveto'] = min(ends) if ends else ''
                            if values['effectiveto'] and values['effectiveto'] < values['effectivefrom']:
                                raise ValueError('no overlapping dates')
                            compiled['networks'].append({'values': values, 'source': {**source, 'locator': locator}})
                        except ValueError:
                            compiled['issues'].append({'status': 'missing_dependency', 'reason': 'Participation dates are invalid.', 'locator': locator})
                    else:
                        compiled['issues'].append({'status': 'missing_dependency', 'reason': 'Participation needs an executed, exact payer/product/provider/location/date scope.', 'locator': locator})
            allowance_table = any(key in headers for key in ('allowedamount', 'contractedallowedamount'))
            export_table = document['kind'] == 'fee_schedule' and 'amount' in headers and 'network' in headers
            if (allowance_table or export_table) and 'schedulereference' not in headers:
                supported = {'cpt', 'hcpcs', 'procedurecode', 'code', 'description', 'allowedamount', 'contractedallowedamount',
                    'cashprice', 'modifier', 'unitrule', 'network', 'plan', 'planmatcher', 'pos', 'placeofservice',
                    'providernpi', 'renderingnpi', 'tin', 'taxid', 'effectivestart', 'effectivefrom', 'effectiveend', 'effectiveto',
                    'amount', 'status', 'notes'}
                note_columns = [i for i, key in enumerate(headers) if key == 'notes']
                terms = table.get('context', '') + ' '.join(str(row[i] or '') for row in rows[1:] for i in note_columns if i < len(row))
                if set(headers) - supported or re.search(r'lesser of|subject to|\d\s*%|\blocality\b|\b(?:minimum|maximum)\b', terms, re.I):
                    compiled['issues'].append({'status': 'missing_dependency', 'reason': 'Allowance table contains additional scope or calculation conditions that are not represented by the supported rate fields.', 'locator': table['locator']})
                    continue
                included = [(number, row) for number, row in enumerate(rows[1:], 2)
                            if any(str(cell or '').strip() for cell in row)
                            and not (export_table and len(row) == 2 and str(row[0]).strip().casefold() in {'state', 'time stamp'})
                            and not PROVISIONAL.search(' '.join(str(cell or '') for cell in row))]
                final = [list(rows[0]), *[list(row) for _, row in included]]
                if export_table and not allowance_table:
                    final[0][headers.index('amount')] = 'Allowed Amount'
                if effective and not {'effectivefrom', 'effectivestart'} & set(headers):
                    final[0].append('Effective From')
                    for row in final[1:]:
                        row.append(effective)
                # Join an exact, already sourced participation record. A payer
                # label alone cannot supply the missing product/provider scope.
                if len(matches) == 1 and 'network' in headers and not {'pos', 'placeofservice'} & set(headers):
                    scopes = []
                    for row in final[1:]:
                        matched = [network for network in networks or [] if network['payer_connection_id'] == matches[0]['id']
                                   and network['status'] == 'verified' and network['network_name'] == row[headers.index('network')]
                                   and effective and network['effective_from'] <= effective
                                   and (not network.get('effective_to') or effective <= network['effective_to'])]
                        scopes.append(matched[0] if len(matched) == 1 else None)
                    if scopes and all(scopes):
                        fields = [('POS', 'place_of_service'), ('Plan', 'plan_matcher'), ('Provider NPI', 'rendering_npi'), ('TIN', 'tin')]
                        fields = [(label, key) for label, key in fields if commercial._key(label) not in headers]
                        final[0].extend(label for label, _ in fields)
                        for row, scope in zip(final[1:], scopes):
                            row.extend(scope.get(key, '') for _, key in fields)
                stream = io.StringIO(); csv.writer(stream).writerows(final)
                try:
                    rates = commercial.parse_rate_csv(stream.getvalue().encode(), 'document.csv')
                    for rate in rates:
                        original = included[rate['row_number'] - 2][0]
                        rate['row_number'] = table.get('row_numbers', list(range(1, len(rows) + 1)))[original - 1]
                        date.fromisoformat(rate['effective_from'])
                        if rate.get('effective_to'):
                            date.fromisoformat(rate['effective_to'])
                    if len(matches) != 1:
                        raise ValueError('Exact practice payer mapping is unavailable for this fee schedule.')
                    compiled['rates'].extend({**rate, 'source': {**source, 'locator': f"{table['locator']}, row {rate['row_number']}"}} for rate in rates)
                except (ValueError, commercial.CommercialError) as exc:
                    compiled['issues'].append({'status': 'missing_dependency', 'reason': str(exc), 'locator': table['locator']})
        for passage in extracted.get('passages', []):
            if re.search(r'\d\s*%.*(?:fee|rate) schedule|(?:fee|rate) schedule.*\d\s*%', passage['text'], re.I | re.S):
                references = sorted(set(re.findall(r'\b(?:[A-Z][A-Za-z0-9-]*\s+){1,5}(?:Fee|Rate)\s+Schedule\b', passage['text'])))
                reference = ' (' + '; '.join(references) + ')' if references else ''
                compiled['issues'].append({'status': 'missing_dependency', 'reason': 'Percentage-of-schedule term' + reference + ': exact schedule values, product/provider/date scope and calculation operands are required.', 'locator': f"Page {passage['page']}"})
    compiled['effective_from'] = effective
    try:
        if effective:
            date.fromisoformat(effective)
        if source['effective_to']:
            date.fromisoformat(source['effective_to'])
            if effective and source['effective_to'] < effective:
                raise ValueError('reversed dates')
    except ValueError:
        compiled['cash'] = []; compiled['rates'] = []; compiled['networks'] = []; compiled['exclusions'] = []; compiled['schedule_terms'] = []
        compiled.pop('connection', None)
        compiled['issues'].append({'status': 'missing_dependency', 'reason': 'Document effective dates are invalid.'})
    excluded_kinds = {row['product_kind'] for row in compiled['exclusions']}
    if excluded_kinds:
        participating = [row for row in compiled['networks'] if row['values']['participation'].lower() == 'participating'
                         and library_network_exclusions.product_kind(row['values'].get('plan', '')) in excluded_kinds]
        if participating:
            compiled['networks'] = [row for row in compiled['networks'] if row not in participating]
            compiled['issues'].append({'status': 'conflict', 'reason':
                'An explicit product exclusion in this agreement overrides its participation rows.',
                'locator': participating[0]['source']['locator']})
        compiled['rates'] = [row for row in compiled['rates']
                             if library_network_exclusions.product_kind(row.get('plan_matcher', '')) not in excluded_kinds]
    if not compiled['cash'] and not compiled['rates'] and not compiled['networks'] and not compiled['exclusions'] and not compiled['schedule_terms'] and not compiled['issues']:
        compiled['issues'].append({'status': 'missing_dependency', 'reason': 'No explicit supported price table with complete service and pricing scope was found. This source remains reference evidence.'})
    return compiled


def resolve_schedules(compiled: dict, documents: list[tuple[dict, dict]]) -> None:
    """Resolve only explicitly linked schedules within the same practice/payer.

    An opaque schedule reference, unmatched scope, lesser-of without charges,
    conflicting schedules or missing locality cannot produce a numeric allowance.
    """
    for term in compiled['schedule_terms']:
        values, evidence = term['values'], term['source']
        matches = [(document, rules) for document, rules in documents
                   if values['schedulereference'] in {document['label'], document['id'], 'library:' + document['id']}
                   and rules.get('payer_connection_id') == compiled.get('payer_connection_id')]
        matches = list({(rules['source']['sha256'], rules.get('effective_from'), document.get('effective_to')): (document, rules)
                        for document, rules in matches}.values())
        try:
            if len(matches) != 1 or not compiled.get('payer_connection_id'):
                raise ValueError('Referenced schedule is missing or does not identify exactly one schedule for this payer.')
            factor = Decimal(values['percentofschedule'].rstrip('%')) / 100
            if not factor.is_finite() or not 0 < factor <= 10:
                raise ValueError('Schedule percentage is invalid.')
            required = ('code', 'network', 'plan', 'pos', 'effectivefrom')
            if not all(values.get(key) for key in required):
                raise ValueError('Schedule term is missing CPT, network, product, POS or effective date.')
            date.fromisoformat(values['effectivefrom'])
            rates = [row for row in matches[0][1]['rates'] if all(
                row.get(key, '') == values.get(column, '') for key, column in (
                    ('code', 'code'), ('network', 'network'), ('plan_matcher', 'plan'), ('pos', 'pos'),
                    ('modifier', 'modifier'), ('provider_npi', 'providernpi'), ('tin', 'tin')))
                and row['effective_from'] <= values['effectivefrom']
                and (not row.get('effective_to') or row['effective_to'] >= values['effectivefrom'])]
            if len(rates) != 1:
                raise ValueError('Referenced schedule has no unique rate for the exact service, product, provider and date scope.')
            rate = rates[0]
            amount = int((Decimal(rate['allowed_cents']) * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if not 0 <= amount <= 100_000_000:
                raise ValueError('Derived allowance is outside the supported range.')
            if values.get('lesserofbilledpercent'):
                if not values.get('billedcharge'):
                    raise ValueError('Lesser-of term is missing the applicable billed charge.')
                ceiling = Decimal(values['lesserofbilledpercent'].rstrip('%')) / 100
                if not ceiling.is_finite() or not 0 < ceiling <= 1:
                    raise ValueError('Lesser-of percentage is invalid.')
                amount = min(amount, int((Decimal(dollars(values['billedcharge'])) * ceiling).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
            ends = [value for value in (rate.get('effective_to'), values.get('effectiveto'), compiled['source'].get('effective_to')) if value]
            for end in ends:
                date.fromisoformat(end)
                if end < values['effectivefrom']:
                    raise ValueError('Referenced schedule term has no overlapping effective dates.')
            compiled['rates'].append({**rate, 'allowed_cents': amount, 'effective_from': values['effectivefrom'],
                'effective_to': min(ends) if ends else None, 'source': {**evidence, 'schedule_source': rate.get('source'), 'percent': str(factor * 100)}})
        except (ValueError, ArithmeticError) as exc:
            compiled['issues'].append({'status': 'missing_dependency', 'reason': str(exc), **evidence})


def source_conflicts(prepared: list[tuple[dict, dict, dict]]) -> None:
    """Make cross-document contradictions visible; pricing retains its own gate."""
    indexed = {}
    for document, _, compiled in prepared:
        for kind in ('cash', 'rates'):
            for row in compiled[kind]:
                key = (kind, compiled.get('payer_connection_id') if kind == 'rates' else None,
                       *(row.get(field, '') for field in ('code', 'modifier', 'network', 'pos', 'provider_npi', 'tin', 'plan_matcher')))
                start = row.get('effective_from') or compiled.get('effective_from') or ''
                end = row.get('effective_to') or document.get('effective_to') or '9999-12-31'
                amount = tuple(row.get(field) for field in ('allowed_cents', 'cash_cents', 'base_cents', 'price_kind'))
                for other, other_row, other_start, other_end, other_amount in indexed.get(key, []):
                    if start <= other_end and other_start <= end and amount != other_amount:
                        for target, evidence_row in ((compiled, row), (other, other_row)):
                            issue = {'status': 'conflict', 'code': row['code'], 'reason': f"Overlapping source documents disagree on the price for {row['code']}.", **evidence_row.get('source', {})}
                            if issue not in target['issues']:
                                target['issues'].append(issue)
                indexed.setdefault(key, []).append((compiled, row, start, end, amount))


def publish(document: dict, extracted: dict, compiled: dict) -> None:
    """Publish prices and their source manifest atomically, including on retry."""
    if document['kind'] == 'plan_matrix':
        try:
            plan_matrix.parse(extracted.get('tables') or [], document['filename'])
            status, issues = 'usable', []
        except ValueError:
            status = 'missing_dependency'
            issues = [{'status': status, 'reason': 'A complete three-sheet payer plan matrix is required.'}]
        extracted['compiled'] = compiled
        extracted['summary'].update(status=status, compiler_version=VERSION,
            processed_at=staff_store.now(), usable_rules=0, issues=issues, estimator_version=None)
        staff_store.save_library_extraction(document['id'], document['practice_id'], extracted)
        return
    practice = document['practice_id']
    rate_digest = hashlib.sha256(json.dumps([{k: v for k, v in row.items() if k != 'source'} for row in compiled['rates']], sort_keys=True).encode()).hexdigest()
    version = stable_id(practice, extracted['sha256'], document['kind'], document.get('payer') or '', compiled.get('effective_from') or '', document.get('effective_to') or '', VERSION, rate_digest)
    timestamp = staff_store.now()
    issues = compiled['issues']
    conflicting = {issue.get('code') for issue in issues if issue['status'] == 'conflict'}
    usable_count = (sum(row['code'] not in conflicting for row in compiled['cash'] + compiled['rates'])
                    + len(compiled['networks']) + len(compiled['exclusions']))
    usable = bool(usable_count)
    status = 'partial' if usable and issues else 'usable' if usable else 'conflict' if any(i['status'] == 'conflict' for i in issues) else 'provisional' if all(i['status'] == 'provisional' for i in issues) else 'missing_dependency'
    extracted['compiled'] = compiled
    extracted['summary'].update(status=status, compiler_version=VERSION, processed_at=timestamp,
        usable_rules=usable_count, issues=issues,
        estimator_version=version if compiled['cash'] or compiled['rates'] else None)
    with staff_store._connection() as db:
        previous = ((document.get('extraction') or {}).get('summary') or {}).get('estimator_version')
        if previous and previous != version:
            # Only recompiled evidence from this same immutable document can
            # supersede its own derived version. A newer upload is not precedence.
            db.execute("UPDATE commercial_rate_versions SET status='superseded' WHERE id=%s AND practice_id=%s AND status='active' AND source_reference=%s", (previous, practice, 'library:' + document['id']))
            db.execute("UPDATE practice_cash_schedules SET status='superseded' WHERE id=%s AND practice_id=%s AND status='active' AND actor_id='system:library'", (previous, practice))
        if compiled.get('connection'):
            connection = compiled['connection']
            connection_status = ('staff_guidance_enabled' if any(
                row['values']['participation'].lower() == 'participating' for row in compiled['networks']) else 'draft')
            db.execute('''INSERT INTO commercial_payer_connections (id,practice_id,display_name,stedi_payer_id,category,status,aliases_json,rules_json,created_at,updated_at)
                VALUES (%s,%s,%s,%s,'commercial_medical',%s,'[]',%s,%s,%s) ON CONFLICT (practice_id,stedi_payer_id) DO NOTHING''',
                (connection['id'], practice, connection['display_name'], connection['stedi_payer_id'], connection_status,
                 json.dumps({'library_source': compiled['source']}), timestamp, timestamp))
        for network in compiled['networks']:
            values = network['values']
            connection = compiled['payer_connection_id']
            fields = (practice, connection, values['billingnpi'], values['renderingnpi'], values['tin'], values['locationid'], values['pos'], values['network'], values['plan'], values['effectivefrom'], values.get('effectiveto') or None)
            status = 'verified' if values['participation'].lower() == 'participating' else 'nonparticipating'
            found = db.execute('''SELECT id FROM commercial_provider_networks WHERE practice_id=%s AND payer_connection_id=%s AND billing_npi=%s AND rendering_npi=%s AND tin=%s AND location_id=%s AND place_of_service=%s AND network_name=%s AND plan_matcher=%s AND effective_from=%s AND effective_to IS NOT DISTINCT FROM %s AND status=%s''', (*fields, status)).fetchone()
            network_id = found['id'] if found else stable_id(version, 'network', json.dumps(values, sort_keys=True))
            if not found:
                db.execute('''INSERT INTO commercial_provider_networks (id,practice_id,payer_connection_id,billing_npi,rendering_npi,tin,location_id,place_of_service,network_name,plan_matcher,effective_from,effective_to,organization_name,status,evidence_reference,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING''',
                    (network_id, *fields, values['organizationname'], status, network['source']['reference'], timestamp, timestamp))
            if status == 'verified' and not db.execute('SELECT 1 FROM commercial_estimation_profiles WHERE practice_id=%s AND provider_network_id=%s', (practice, network_id)).fetchone():
                db.execute('''INSERT INTO commercial_estimation_profiles (id,practice_id,payer_connection_id,provider_network_id,service_category,plan_matcher,status,rules_json,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,'office',%s,'staff_guidance_enabled',%s,%s,%s) ON CONFLICT (id) DO NOTHING''',
                    (stable_id(network_id, 'profile'), practice, connection, network_id, values['plan'], json.dumps({'cost_share': 'payer_returned', 'plan_product_names': [values['plan']], 'library_source': network['source']}), timestamp, timestamp))
        if compiled['cash']:
            db.execute('''INSERT INTO practice_cash_schedules (id,practice_id,actor_id,filename,sha256,effective_from,rows_json,status,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'active',%s) ON CONFLICT (id) DO NOTHING''',
                (version, practice, 'system:library', document['filename'], extracted['sha256'], compiled['effective_from'], json.dumps(compiled['cash']), timestamp))
        if compiled['rates']:
            rates = compiled['rates']
            db.execute('''INSERT INTO commercial_rate_versions (id,practice_id,payer_connection_id,source_type,status,source_reference,sha256,effective_from,effective_to,change_ceiling_bps,created_at,activated_at)
                VALUES (%s,%s,%s,'contract_export','active',%s,%s,%s,%s,0,%s,%s) ON CONFLICT (id) DO NOTHING''',
                (version, practice, compiled['payer_connection_id'], 'library:' + document['id'], extracted['sha256'],
                 compiled['effective_from'] or min(row['effective_from'] for row in rates), document.get('effective_to'), timestamp, timestamp))
            if not db.execute('SELECT 1 FROM commercial_rate_lines WHERE version_id=%s', (version,)).fetchone():
                db.cursor().executemany('''INSERT INTO commercial_rate_lines (version_id,row_number,code,description,modifier,unit_rule,allowed_cents,cash_cents,network_name,plan_matcher,place_of_service,provider_npi,tin,effective_from,effective_to)
                    VALUES (%(version_id)s,%(row_number)s,%(code)s,%(description)s,%(modifier)s,%(unit_rule)s,%(allowed_cents)s,%(cash_cents)s,%(network)s,%(plan_matcher)s,%(pos)s,%(provider_npi)s,%(tin)s,%(effective_from)s,%(effective_to)s)''',
                    [{**row, 'version_id': version} for row in rates])
        db.execute('UPDATE staff_library SET extraction=%s::jsonb,sha256=COALESCE(sha256,%s) WHERE id=%s AND practice_id=%s',
                   (json.dumps(extracted), extracted['sha256'], document['id'], practice))


def link_signed_group_agreements(practice_id: str, documents: list[dict],
                                 connections: list[dict], networks: list[dict]) -> None:
    """Replace a setup-manifest reference with one current signed Library network attachment."""
    payers = {row['id']: row for row in connections}
    profiles = commercial_store.list_profiles(practice_id)
    contracts = []
    for metadata in documents:
        if metadata.get('kind') != 'contract' or metadata.get('content_type') != 'application/pdf':
            continue
        document = staff_store.get_library(metadata['id'], practice_id)
        extracted = (document or {}).get('extraction') or {}
        if not document or not document.get('sha256') or extracted.get('sha256') != document['sha256']:
            continue
        text = '\n'.join(str(page.get('text') or '') for page in extracted.get('pages') or [])
        signed_date = group_contract_support._signed_agreement_effective_date(text)
        metadata_date = document.get('effective_from') or ''
        if (not signed_date and not metadata_date) or (signed_date and metadata_date and signed_date != metadata_date):
            continue
        if not re.search(r'\b(?:executed|signed|signature)\b', text, re.I):
            continue
        contracts.append((document, extracted, metadata_date or signed_date))
    for network in networks:
        if network.get('status') != 'verified' or not str(network.get('evidence_reference') or '').startswith('s3://'):
            continue
        payer = payers.get(network.get('payer_connection_id'))
        matching_profiles = [row for row in profiles if row.get('provider_network_id') == network.get('id')
                             and row.get('payer_connection_id') == network.get('payer_connection_id')
                             and row.get('status') == 'staff_guidance_enabled'
                             and row.get('plan_matcher') == network.get('plan_matcher')
                             and not (row.get('rules') or {}).get('library_source')]
        if not payer or len(matching_profiles) != 1:
            continue
        matches = []
        for document, extracted, effective in contracts:
            if (normalized(document.get('payer')) != normalized(payer.get('display_name'))
                    or not effective or network.get('effective_to') and effective > network['effective_to']
                    or document.get('effective_to') and document['effective_to'] < network.get('effective_from', '')):
                continue
            locator = group_contract_support.signed_network_attachment(
                extracted, str(network.get('network_name') or ''))
            if locator:
                matches.append((document, effective, locator))
        if len(matches) != 1:
            continue
        document, effective, locator = matches[0]
        reference = 'library:' + document['id']
        rules = {**matching_profiles[0]['rules'], 'library_source': {
            'document_id': document['id'], 'sha256': document['sha256'], 'reference': reference,
            'effective_from': effective, 'locator': locator, 'origin': 'signed_group_agreement'}}
        timestamp = staff_store.now()
        with staff_store._connection() as db:
            current_network = db.execute('''SELECT evidence_reference,status FROM commercial_provider_networks
                WHERE id=%s AND practice_id=%s FOR UPDATE''', (network['id'], practice_id)).fetchone()
            current_profile = db.execute('''SELECT rules_json,status FROM commercial_estimation_profiles
                WHERE id=%s AND practice_id=%s FOR UPDATE''',
                (matching_profiles[0]['id'], practice_id)).fetchone()
            if (not current_network or current_network['status'] != 'verified'
                    or current_network['evidence_reference'] != network['evidence_reference']
                    or not current_profile or current_profile['status'] != 'staff_guidance_enabled'
                    or json.loads(current_profile['rules_json']) != matching_profiles[0]['rules']):
                continue
            db.execute('''UPDATE commercial_provider_networks SET evidence_reference=%s,updated_at=%s
                WHERE id=%s AND practice_id=%s''', (reference, timestamp, network['id'], practice_id))
            db.execute('''UPDATE commercial_estimation_profiles SET rules_json=%s,updated_at=%s
                WHERE id=%s AND practice_id=%s''',
                (json.dumps(rules), timestamp, matching_profiles[0]['id'], practice_id))


def prepare(practice_id: str) -> list[dict]:
    """Backfill/retry on use; serialize per practice, cache unchanged sources."""
    documents = staff_store.list_library(practice_id)
    if not documents:
        return []
    with staff_store._connection() as lock:
        lock.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('library:' + practice_id,))
        documents = staff_store.list_library(practice_id)
        connections = commercial_store.list_connections(practice_id)
        networks = commercial_store.list_networks(practice_id)
        fingerprint = hashlib.sha256(json.dumps({
            'documents': [{key: row.get(key) for key in ('id', 'sha256', 'kind', 'payer', 'effective_from', 'effective_to')} for row in documents],
            'connections': [{key: row.get(key) for key in ('id', 'display_name', 'stedi_payer_id', 'aliases')} for row in connections],
            'networks': networks,
        }, sort_keys=True).encode()).hexdigest()
        if all(row['kind'] == 'era_835' or ((row.get('extraction_summary') or {}).get('compiler_version') == VERSION
                and (row.get('extraction_summary') or {}).get('dependency_fingerprint') == fingerprint) for row in documents):
            link_signed_group_agreements(practice_id, documents, connections, networks)
            return documents
        prepared = []
        for metadata in documents:
            if metadata['kind'] == 'era_835':
                continue
            document = staff_store.get_library(metadata['id'], practice_id)
            saved = document.get('extraction') or {}
            try:
                staff_store.save_library_extraction(document['id'], practice_id, {**saved, 'summary': {'status': 'reading', 'started_at': staff_store.now()}})
                extracted = library_files.extract_document(document)
                compiled = compile_document(document, extracted, connections, networks)
                prepared.append((document, extracted, compiled))
            except Exception:
                # Raw source text and infrastructure errors are never public.
                staff_store.save_library_extraction(document['id'], practice_id, {**saved, 'summary': {'status': 'needs_retry', 'reason': 'Document processing did not complete; it will retry automatically.'}})
        for document, extracted, compiled in prepared:
            resolve_schedules(compiled, [(doc, rules) for doc, _, rules in prepared if rules is not compiled and not rules['schedule_terms']])
        source_conflicts(prepared)
        for document, extracted, compiled in prepared:
            try:
                extracted['summary']['dependency_fingerprint'] = fingerprint
                publish(document, extracted, compiled)
            except Exception:
                staff_store.save_library_extraction(document['id'], practice_id, {**extracted, 'summary': {'status': 'needs_retry', 'reason': 'Rule publication did not complete; it will retry automatically.'}})
        link_signed_group_agreements(practice_id, staff_store.list_library(practice_id),
                                     connections, commercial_store.list_networks(practice_id))
    return staff_store.list_library(practice_id)
