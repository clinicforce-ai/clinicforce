"""Synthetic Library rules: source-bound prices, dependencies and publication."""
import hashlib
import json
import os
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from app.core import library_files, patient_store, staff_store
from app.insurance import commercial_store, library_pricing as rules, practice_prices


def document(kind='self_pay', **extra):
    return {'id': 'source', 'practice_id': 'practice', 'label': 'Source schedule', 'filename': 'source.csv',
            'kind': kind, 'payer': 'Example Payer', 'created_at': '2026-10-01T12:00:00Z',
            'effective_from': '2026-01-01', 'effective_to': None, **extra}


def extracted(tables, text=''):
    return {'sha256': 'digest', 'pages': [{'number': 1, 'text': text}], 'tables': tables, 'passages': [], 'summary': {'status': 'read'}}


def table(rows, context='', locator='Section 1'):
    return {'rows': rows, 'context': context, 'locator': locator}


def quote(rows, codes, mode='services', review=None):
    result = {'available_codes': [], 'price_range': {'mode': mode, 'lines': codes}}
    practice_prices.apply(result, {'id': 'version', 'filename': 'approved.csv', 'effective_from': '2026-01-01', 'rows': rows}, review)
    return result


def test_final_session_and_named_package_cash_prices_do_not_infer_cpt_scope():
    rows, issues = rules.cash_tables([
        table([['Category', 'Status', 'Price Status'],
               ['Botox', 'Current', 'Final / in use'], ['NCS / EMG Packages', 'Current', 'Final / in use'],
               ['Biopsy', 'Pending approval', 'Suggested']]),
        table([['Component', 'CPT / HCPCS Reference', 'Cash Price', 'How to Apply'],
               ['Injection session', '64611-64616; 64642-64645', '$450 / session', 'One fee'],
               ['Drug', 'J0585', '$7.50 / unit', 'Add to session fee']], 'Botox cash pricing'),
        table([['Package / Best For', 'Cash Price', 'Likely CPT Example(s)', 'Coding / Scope Note'],
               ['One Hand\nCarpal tunnel', '$500', '95909 + 95886', 'Focused visit included'],
               ['Both Legs\nNeuropathy', '$800', '95910 + 95886 X 2', 'Focused visit included']], 'NCS / EMG Packages'),
        table([['Service', 'CPT Reference', 'Suggested Cash Price'], ['Biopsy', '11104', '$450']], 'Pending approval'),
    ], {})
    assert len(rows) == 11 and not any(issue['status'] == 'conflict' for issue in issues)
    assert not any(row['code'] == '11104' for row in rows)
    session = [{'code': '64615', 'units': 1}, {'code': 'J0585', 'units': 100}]
    assert quote(rows, session)['cash_quote']['min_cents'] == 120000
    session[1]['units'] = 155
    assert quote(rows, session)['cash_quote']['min_cents'] == 161250
    session[1]['units'] = 200
    assert quote(rows, session)['cash_quote']['min_cents'] == 195000
    assert not quote(rows, [{'code': 'J0585', 'units': 100}])['cash_quote']['complete']
    assert not quote(rows, [{'code': '95909', 'units': 1}, {'code': '95886', 'units': 1}])['cash_quote']['complete']
    package = next(row for row in rows if row.get('label') == 'One Hand')
    package['schedule_filename'] = 'named-source.docx'
    package['schedule_effective_from'] = '2026-09-04'
    selected = quote(rows, [{'code': '99203', 'units': 1}, {'code': '95909', 'units': 1},
                            {'code': '95886', 'units': 1}], review={'cash_selection': package['code']})
    assert selected['cash_quote']['min_cents'] == 50000
    assert all(line['cash_included'] for line in selected['price_range']['lines'])
    assert selected['cash_quote']['package_label'] == 'One Hand'
    assert selected['cash_quote']['source'] == 'named-source.docx'
    assert selected['cash_quote']['effective_from'] == '2026-09-04'
    assert not quote(rows, [{'code': '95909', 'units': 1}, {'code': '62270', 'units': 1}],
                     review={'cash_selection': package['code']})['cash_quote']['complete']


def test_final_sections_survive_proposals_and_conflicts_are_excluded():
    compiled = rules.compile_document(document(), extracted([
        table([['CPT', 'Description', 'Cash Price'], ['99213', 'Office', '$130'], ['99214', 'Proposed', '$190'], ['99215', 'Office', '$200'], ['99215', 'Office', '$210']]),
        table([['CPT', 'Cash Price'], ['95886', '$90']], 'Draft testing prices'),
    ]), [])
    assert [row['code'] for row in compiled['cash']] == ['99213']
    assert {issue['status'] for issue in compiled['issues']} == {'provisional', 'conflict'}
    assert compiled['cash'][0]['source']['locator'] == 'Section 1, row 2, price column 3'
    assert compiled['cash'][0]['source']['sha256'] == 'digest'


def test_plan_matrix_cannot_publish_price_network_or_payer_rules():
    source = document('plan_matrix')
    evidence = extracted([
        table([['CPT', 'Allowed Amount', 'Network', 'Plan', 'POS', 'Effective From'],
               ['99213', '200', 'Preferred', 'Example Product', '11', '2026-01-01']]),
        table([['Billing NPI', 'Rendering NPI', 'Organization Name', 'TIN', 'Location ID',
                'POS', 'Network', 'Plan', 'Effective From', 'Participation', 'Execution Status'],
               ['1234567893', '1234567893', 'Example', '123456789', 'office', '11',
                'Preferred', 'Example Product', '2026-01-01', 'participating', 'executed']]),
        table([['Stedi Payer ID', 'Payer Name', 'Payer Category'],
               ['newpayer', 'Example Payer', 'commercial_medical']]),
    ])
    compiled = rules.compile_document(source, evidence, [], [])
    assert not any(compiled.get(key) for key in
                   ('cash', 'rates', 'networks', 'schedule_terms', 'payer_connection_id', 'connection'))
    with patch.object(staff_store, 'save_library_extraction') as save, \
            patch.object(staff_store, '_connection', side_effect=AssertionError('pricing publication attempted')):
        rules.publish(source, evidence, compiled)
    save.assert_called_once()
    assert evidence['summary']['status'] == 'missing_dependency'
    assert evidence['summary']['estimator_version'] is None


def test_formulas_packages_and_alternatives_never_double_charge():
    rows, issues = rules.cash_tables([table([
        ['CPT', 'Cash Price'], ['J0585', '$100 + $12 per unit'],
        ['95910 + 95886 X 2', '$600'], ['99213', '$130'], ['99214', '$170'],
    ])], {})
    assert not issues
    result = quote(rows, [{'code': 'J0585', 'units': 25}, {'code': '95910', 'units': 1}, {'code': '95886', 'units': 2}])
    assert result['cash_quote']['min_cents'] == 100000  # 400 + 600, not 400 + per-code package copies.
    assert len(result['price_range']['cash_packages']) == 1
    assert result['price_range']['lines'][1]['cash_cents'] is None
    assert not quote(rows, [{'code': 'J0585', 'units': None}])['cash_quote']['complete']
    assert not quote(rows, [{'code': '95910', 'units': 1}])['cash_quote']['complete']
    assert not quote(rows, [{'code': '95910', 'units': 1}, {'code': '95886', 'units': 3}])['cash_quote']['complete']
    alternatives = quote(rows, [{'code': '99213', 'units': 1}, {'code': '99214', 'units': 1}], 'options')
    assert (alternatives['cash_quote']['min_cents'], alternatives['cash_quote']['max_cents']) == (13000, 17000)
    conflicting = [*rows, {**rows[2], 'cash_cents': 90000}]
    assert not quote(conflicting, [{'code': '99213', 'units': 1}])['cash_quote']['complete']


def test_package_overlap_and_ambiguous_codes_cannot_supply_cash():
    rows, issues = rules.cash_tables([table([['CPT', 'Description', 'Cash Price'],
        ['95910 + 95886', 'Package one', '$500'], ['95910 + 95885', 'Package two', '$400'],
        ['99213/99214', 'Likely codes', '$140'], ['99215', 'Package unspecified', '$200']])], {})
    assert len(rows) == 2 and len(issues) == 2
    assert not quote(rows, [{'code': code, 'units': 1} for code in ('95910', '95886', '95885')])['cash_quote']['complete']


def test_referenced_schedule_exact_payer_scope_and_lesser_of_operands():
    connection = [{'id': 'payer', 'display_name': 'Example Payer', 'stedi_payer_id': 'example'}]
    base = rules.compile_document(document('fee_schedule', id='schedule'), extracted([table([
        ['CPT', 'Allowed Amount', 'Network', 'Plan', 'POS', 'Effective From'],
        ['99213', '200', 'Preferred', 'Example Product', '11', '2026-01-01']])]), connection)
    term = rules.compile_document(document('contract'), extracted([table([
        ['Code', 'Schedule Reference', 'Percent of Schedule', 'Network', 'Plan', 'POS', 'Effective From', 'Lesser Of Billed Percent', 'Billed Charge'],
        ['99213', 'Source schedule', '120%', 'Preferred', 'Example Product', '11', '2026-01-01', '85%', '250']])]), connection)
    rules.resolve_schedules(term, [(document('fee_schedule', id='schedule'), base)])
    assert term['rates'][0]['allowed_cents'] == 21250
    assert term['rates'][0]['source']['schedule_source']['reference'] == 'library:schedule'
    missing = deepcopy(term); missing['rates'] = []; missing['issues'] = []
    missing['schedule_terms'][0]['values']['billedcharge'] = ''
    rules.resolve_schedules(missing, [(document('fee_schedule', id='schedule'), base)])
    assert not missing['rates'] and 'billed charge' in missing['issues'][0]['reason']
    wrong = deepcopy(term); wrong['rates'] = []; wrong['issues'] = []
    rules.resolve_schedules(wrong, [(document('fee_schedule', id='schedule'), {**base, 'payer_connection_id': 'other'})])
    assert not wrong['rates']
    assert not rules.compile_document(document('fee_schedule', payer='Different payer'), extracted([table([
        ['CPT', 'Allowed Amount', 'Network', 'POS', 'Effective From'], ['99213', '200', 'Preferred', '11', '2026-01-01']])]), connection)['rates']


def test_invalid_dates_do_not_activate_cash_and_bytes_are_bound_to_the_source():
    compiled = rules.compile_document(document(effective_from='2026-13-01'), extracted([table([['CPT', 'Cash Price'], ['99213', '150']])]), [])
    assert not compiled['cash']
    with patch.object(library_files.object_store, 'get_object_bytes', return_value=b'different source'):
        with pytest.raises(ValueError, match='checksum'):
            library_files.extract_document({**document(), 'object_key': 'private', 'sha256': 'wrong'})


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_automatic_publish_backfill_duplicate_retry_and_tenant_scope():
    url = os.environ['STAFF_TEST_DATABASE_URL']; schema = 'library_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with patch.object(patient_store, 'DATABASE_URL', make_conninfo(url, options=f'-csearch_path={schema}')), patch.object(patient_store, '_schema_initialized', False), patch.object(staff_store, '_schema_initialized', False):
            with staff_store._connection() as db:
                db.execute("INSERT INTO staff_members (id,username,display_name,practice_id,practice_name,role,password_hash,created_at) VALUES ('actor','test','Synthetic','practice','Example','admin','unused','2026-01-01')")
            content = b'CPT,Description,Cash Price\n99213,Office,130\n99214,Proposed,190\n'
            digest = hashlib.sha256(content).hexdigest()
            staff = {'id': 'actor', 'practice_id': 'practice'}
            doc = {**document(), 'content_type': 'text/csv', 'object_key': 'protected', 'sha256': digest}
            staff_store.create_library(staff, doc)
            with patch.object(library_files.object_store, 'get_object_bytes', return_value=content) as read:
                prepared = rules.prepare('practice')
                assert prepared[0]['extraction_summary']['status'] == 'partial'
                assert quote(practice_prices.current('practice', '2026-10-08')['rows'], [{'code': '99213', 'units': 1}])['cash_quote']['min_cents'] == 13000
                assert practice_prices.current('other-practice', '2026-10-08') is None
                rules.prepare('practice'); read.assert_called_once()
                staff_store.create_library(staff, {**doc, 'id': 'duplicate'})
                rules.prepare('practice')
                with staff_store._connection() as db:
                    assert db.execute('SELECT count(*) AS count FROM practice_cash_schedules').fetchone()['count'] == 1
                # Conflicting uploads remain visible and cannot quote by newest-wins.
                other_content = content.replace(b'Office,130', b'Office,160')
                other = {**doc, 'id': 'conflicting', 'sha256': hashlib.sha256(other_content).hexdigest(), 'object_key': 'other'}
                staff_store.create_library(staff, other)
                read.side_effect = lambda key: other_content if key == 'other' else content
                reports = rules.prepare('practice')
                assert any(row['extraction_summary']['status'] == 'conflict' for row in reports)
                assert not quote(practice_prices.current('practice', '2026-10-08')['rows'], [{'code': '99213', 'units': 1}])['cash_quote']['complete']
                with staff_store._connection() as db:
                    db.execute("DELETE FROM staff_library WHERE id='conflicting'")
                    db.execute("DELETE FROM practice_cash_schedules WHERE sha256=%s", (other['sha256'],))
                # A crashed task's status is repaired without another version.
                staff_store.save_library_extraction(doc['id'], 'practice', {'summary': {'status': 'reading'}})
                rules.prepare('practice')
                with staff_store._connection() as db:
                    assert db.execute('SELECT count(*) AS count FROM practice_cash_schedules').fetchone()['count'] == 1
            from app.api.routers import commercial_estimator as api
            from fastapi import HTTPException
            contract = {**doc, 'id': 'contract', 'kind': 'contract', 'label': 'Signed scope', 'sha256': 'contract-hash'}
            staff_store.create_library(staff, contract)
            scope = table([
                ['Payer Name', 'Stedi Payer ID', 'Payer Category', 'Billing NPI', 'Rendering NPI', 'Organization Name', 'TIN', 'Location ID', 'POS', 'Network', 'Plan', 'Effective From', 'Participation', 'Execution Status'],
                ['Example Payer', 'EXAMPLE', 'commercial_medical', '1234567890', '1234567890', 'Example Practice', '123456789', 'office', '11', 'Preferred', 'Example Product', '2026-01-01', 'participating', 'executed']])
            evidence = extracted([scope, table([['CPT', 'Allowed Amount', 'Network', 'Plan', 'POS', 'Effective From'], ['99213', '200', 'Preferred', 'Example Product', '11', '2026-01-01']])])
            evidence['sha256'] = 'contract-hash'
            compiled = rules.compile_document(contract, evidence, [])
            assert len(compiled['networks']) == 1 and len(compiled['rates']) == 1
            rules.publish(contract, evidence, compiled)
            connection = commercial_store.list_connections('practice')[0]
            inputs = {'payer_id': connection['id'], 'service_date': '2026-10-08', 'rendering_provider': {'npi': '1234567890'}, 'place_of_service': '11'}
            source = {'billing_provider': {'npi': '1234567890'}, 'location_id': 'office', 'plan': 'Example Product'}
            _, network, profile = api._practice_path('practice', inputs, source)
            assert commercial_store.matching_rates('practice', profile, network, '2026-10-08', [{'code': '99213'}])[0]['allowed_cents'] == 20000
            negative = {**contract, 'id': 'negative', 'sha256': 'negative-hash'}
            staff_store.create_library(staff, negative)
            neg_evidence = extracted([deepcopy(scope)]); neg_evidence['sha256'] = 'negative-hash'
            neg_evidence['tables'][0]['rows'][1][-2] = 'nonparticipating'
            rules.publish(negative, neg_evidence, rules.compile_document(negative, neg_evidence, [connection]))
            with pytest.raises(HTTPException, match='') as error:
                api._practice_path('practice', inputs, source)
            assert 'nonparticipation' in error.value.detail
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_practice_prefixed_header_and_discount_do_not_hide_standard_office_prices():
    compiled = rules.compile_document(document(), extracted([
        table([['Service', 'CPT Range', 'Practice Cash Price', 'Internal Use Note'],
               ['New Patient Visit', '99202-99205', '$230', 'Level follows documentation.'],
               ['Established Patient Visit', '99212-99215', '$130', 'Level follows documentation.'],
               ['Friends & Family - New Patient', '99202-99205', '$40', 'Discounted rate.']]),
        table([['Service', 'CPT Reference', 'Suggested Cash Price'], ['Biopsy', '11104 + 11105 X 2', '$490']], 'Suggested pricing'),
        table([['Package / Best For', 'Cash Price', 'Likely CPT Examples'], ['Example diagnostic package', '$700', '95910 + 95886']], 'Final packages'),
    ]), [])
    assert len(compiled['cash']) == 8
    assert {row['cash_cents'] for row in compiled['cash']} == {23000, 13000}
    assert {issue['status'] for issue in compiled['issues']} == {'missing_dependency', 'provisional'}
    assert quote(compiled['cash'], [{'code': code, 'units': 1} for code in ('99212', '99213', '99214', '99215')], 'options')['cash_quote']['max_cents'] == 13000


def test_formula_cell_cached_values_never_become_prices_and_sparse_rows_keep_coordinates():
    from io import BytesIO
    from zipfile import ZipFile
    from test_library_files import workbook_file
    original = BytesIO(workbook_file()); output = BytesIO()
    with ZipFile(original) as source, ZipFile(output, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename == 'xl/worksheets/sheet1.xml':
                content = b'<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>CPT</t></is></c><c r="B1" t="inlineStr"><is><t>Cash Price</t></is></c></row><row r="7"><c r="A7"><v>99213</v></c><c r="B7"><f>100+50</f><v>150</v></c></row></sheetData></worksheet>'
            target.writestr(entry, content)
    _, tables = library_files.structured_content(output.getvalue(), 'cash.xlsx')
    rows, issues = rules.cash_tables(tables, {})
    assert not rows and 'row 7' in issues[0]['locator']


def test_old_inactive_benefit_does_not_cancel_current_active_coverage():
    from app.insurance import commercial
    benefits = commercial.normalize_stedi_271({'benefitsInformation': [
        {'code': '6', 'serviceTypeCodes': ['30'], 'benefitsDateInformation': {'eligibilityEnd': '20201231'}},
        {'code': '1', 'serviceTypeCodes': ['30'], 'benefitsDateInformation': {'eligibilityBegin': '20260101'}},
    ]}, '30', False, service_date='2026-10-08')
    assert benefits['active'] is True
    assert benefits['coverage_evidence']['reported_status'] == 'CONFLICT'
    assert benefits['coverage_evidence']['status'] == 'ACTIVE'
    assert not benefits['coverage_status_conflict']
