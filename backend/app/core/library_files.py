"""Keep original clinic references and render Office documents without active content."""
import csv
import hashlib
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree

from app.core import object_store, staff_store

OFFICE_TYPES = {
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


def office_preview(content: bytes, filename: str) -> str:
    """Render text/tables only; never follow relationships, execute macros or formulas."""
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 30 * 1024 * 1024:
            raise ValueError('Office documents must expand to 30 MB or less.')
        if any('vbaproject' in entry.filename.lower() for entry in entries):
            raise ValueError('Upload a document without macros.')

        def xml(name):
            data = archive.read(name)
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('Unsupported XML declarations.')
            return etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))

        def text(node):
            return ''.join(node.xpath('.//*[local-name()="t"]/text()'))

        def table(rows):
            return '<table>' + ''.join('<tr>' + ''.join('<td>' + escape(str(cell)) + '</td>' for cell in row) + '</tr>' for row in rows) + '</table>'

        blocks = []
        if Path(filename).suffix.lower() == '.docx':
            root = xml('word/document.xml')
            bodies = root.xpath('//*[local-name()="body"]')
            if not bodies:
                raise ValueError('The Word document has no body.')
            for node in bodies[0]:
                name = etree.QName(node).localname
                if name == 'p':
                    blocks.append('<p>' + escape(text(node)) + '</p>')
                elif name == 'tbl':
                    blocks.append(table([[text(cell) for cell in row.xpath('./*[local-name()="tc"]')]
                                         for row in node.xpath('./*[local-name()="tr"]')]))
        else:
            workbook = xml('xl/workbook.xml')
            relations = {item.get('Id'): item.get('Target') for item in xml('xl/_rels/workbook.xml.rels')
                         if item.get('TargetMode') != 'External'}
            shared = [text(node) for node in xml('xl/sharedStrings.xml')] if 'xl/sharedStrings.xml' in archive.namelist() else []
            for sheet in workbook.xpath('//*[local-name()="sheet"]'):
                target = relations.get(sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'), '')
                name = target.lstrip('/') if target.startswith('/xl/') else 'xl/' + target
                if not name.startswith('xl/worksheets/') or '..' in name:
                    raise ValueError('Unsupported worksheet location.')
                rows = []
                for row in xml(name).xpath('//*[local-name()="sheetData"]/*'):
                    cells = []
                    for cell in row:
                        column = 0
                        for letter in cell.get('r', ''):
                            if not letter.isalpha():
                                break
                            column = column * 26 + ord(letter.upper()) - ord('A') + 1
                        if not 1 <= column <= 100:
                            raise ValueError('Worksheets may have up to 100 columns.')
                        cells.extend([''] * max(0, column - len(cells)))
                        values = cell.xpath('./*[local-name()="v"]/text()')
                        value = values[0] if values else ''
                        if cell.get('t') == 's':
                            value = shared[int(value)]
                        elif cell.get('t') == 'inlineStr':
                            value = text(cell)
                        cells[column - 1] = value
                    rows.append(cells)
                    if len(rows) > 10000:
                        raise ValueError('Worksheets may have up to 10,000 rows.')
                blocks.append('<h2>' + escape(sheet.get('name', 'Worksheet')) + '</h2>' + table(rows))
    if not blocks:
        raise ValueError('No readable document content found.')
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(filename) + '</title><style>body{font:16px/1.5 system-ui;margin:24px;color:#172b4d}'
            'table{border-collapse:collapse;display:block;overflow:auto;margin:20px 0}td{border:1px solid #ccd5df;padding:10px;min-width:120px;vertical-align:top}'
            'tr:first-child{font-weight:600;background:#f3f6fa}p{white-space:pre-wrap}</style></head><body><h1>'
            + escape(filename) + '</h1>' + ''.join(blocks) + '</body></html>')


def csv_check(content: bytes) -> None:
    rows = list(csv.reader(StringIO(content.decode('utf-8-sig')), strict=True))
    if not 2 <= len(rows) <= 20001 or any(len(row) > 100 for row in rows):
        raise ValueError('Upload a CSV with a header and 1–20,000 rows, up to 100 columns.')


def save(staff: dict, content: bytes, filename: str, content_type: str, kind: str,
         label: str = '', payer: str = '', effective_from: str | None = None, effective_to: str | None = None) -> dict:
    """Save the original once the calling upload route has validated its contents."""
    document_id = str(uuid4())
    filename = Path(filename).name[:200]
    key = object_store.put_staff_document(staff['practice_id'], document_id, 'source' + Path(filename).suffix.lower(), content, content_type)
    try:
        return staff_store.create_library(staff, {'id': document_id, 'filename': filename,
            'content_type': content_type, 'kind': kind, 'label': label or filename, 'payer': payer,
            'effective_from': effective_from, 'effective_to': effective_to, 'object_key': key,
            'sha256': hashlib.sha256(content).hexdigest()})
    except Exception:
        object_store.delete_object(key)
        raise


def estimator_links(practice_id: str, documents: list[dict]) -> dict:
    from app.insurance import commercial_store, nmct_cash
    payers = commercial_store.list_connections(practice_id)
    versions = commercial_store.list_rate_versions(practice_id)
    with commercial_store._connection() as db:
        cash_versions = [dict(row) for row in db.execute(
            'SELECT id,sha256,status,effective_from FROM practice_cash_schedules WHERE practice_id=%s', (practice_id,)).fetchall()]
    names = {row['id']: row['display_name'] for row in payers}
    links = {}
    for document in documents:
        digest = document.get('sha256')
        uses = []
        for version in versions:
            if version['source_reference'] == 'library:' + document['id'] or digest and digest == version['sha256']:
                uses.append({'id': version['id'], 'type': 'contract_rates', 'status': version['status'],
                             'payer': names.get(version['payer_connection_id'], ''), 'effective_from': version['effective_from'],
                             'effective_to': version.get('effective_to')})
        for version in cash_versions:
            if digest and digest == version['sha256']:
                uses.append({**version, 'type': 'cash_rates', 'payer': ''})
        if practice_id == nmct_cash.PRACTICE_ID and digest == nmct_cash.SOURCE_SHA256:
            uses.append({'id': 'nmct-cash', 'type': 'cash_rates', 'status': 'active', 'payer': '',
                         'effective_from': nmct_cash.EFFECTIVE_FROM,
                         'note': 'Office visits, Botox and NCS/EMG packages. Proposed biopsy and EEG prices remain excluded.'})
        links[document['id']] = uses
    return {'links': links}
