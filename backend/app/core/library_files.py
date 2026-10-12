"""Keep original clinic references and render Office documents without active content."""
import csv
import hashlib
import re
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree

from app.core import object_store, staff_store

EXTRACTION_VERSION = 'clinic-pages-3'

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

        def docx_cell_text(node):
            parts = []
            for paragraph in node.xpath('./*[local-name()="p"]'):
                if parts:
                    parts.append('\n')
                for item in paragraph.iter():
                    name = etree.QName(item).localname
                    if name == 't' and item.text:
                        parts.append(item.text)
                    elif name in {'br', 'cr'}:
                        parts.append('\n')
                    elif name == 'tab':
                        parts.append('\t')
            return ''.join(parts) if parts else text(node)

        def table(rows, row_numbers=None):
            return '<table>' + ''.join(f'<tr data-row="{row_numbers[index] if row_numbers else index + 1}">' + ''.join('<td>' + escape(str(cell)) + '</td>' for cell in row) + '</tr>' for index, row in enumerate(rows)) + '</table>'

        blocks = []
        if Path(filename).suffix.lower() == '.docx':
            root = xml('word/document.xml')
            bodies = root.xpath('//*[local-name()="body"]')
            if not bodies:
                raise ValueError('The Word document has no body.')
            for node in bodies[0]:
                name = etree.QName(node).localname
                if name == 'p':
                    styles = node.xpath('./*[local-name()="pPr"]/*[local-name()="pStyle"]/@*[local-name()="val"]')
                    heading = bool(styles and re.match(r'heading\d|title', styles[0], re.I))
                    blocks.append(('<h3>' if heading else '<p>') + escape(text(node)) + ('</h3>' if heading else '</p>'))
                elif name == 'tbl':
                    blocks.append(table([[docx_cell_text(cell) for cell in row.xpath('./*[local-name()="tc"]')]
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
                rows, row_numbers = [], []
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
                        if cell.xpath('./*[local-name()="f"]'):
                            value = '[Formula cell; cached value: ' + value + ']'
                        cells[column - 1] = value
                    rows.append(cells)
                    row_numbers.append(int(row.get('r') or len(rows)))
                    if len(rows) > 10000:
                        raise ValueError('Worksheets may have up to 10,000 rows.')
                blocks.append('<h2>' + escape(sheet.get('name', 'Worksheet')) + '</h2>' + table(rows, row_numbers))
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


def structured_content(content: bytes, filename: str) -> tuple[list[dict], list[dict]]:
    """Inert source blocks and tables with page/section/sheet/row coordinates."""
    from app.insurance import v1_sources
    suffix = Path(filename).suffix.lower()
    pages, tables = [], []
    if suffix in OFFICE_TYPES:
        root = etree.HTML(office_preview(content, filename))
        context, heading, sheet, number = '', '', '', 0
        for node in root.find('body'):
            if node.tag == 'h1':
                continue
            number += 1
            text = '\n'.join(node.itertext())
            if node.tag == 'h2':
                sheet = text
            if node.tag == 'h3':
                heading, context = text, ''
            if node.tag == 'p':
                if re.match(r'^\s*\d+[.)]\s', text):
                    heading, context = text, ''
                else:
                    context += '\n' + text
            locator = f'Sheet {sheet}' if sheet else f'Section {number}'
            pages.append({'number': number, 'text': text, 'locator': locator})
            if node.tag == 'table':
                tables.append({'locator': locator, 'context': heading + '\n' + context,
                               'row_numbers': [int(row.get('data-row') or index + 1) for index, row in enumerate(node)],
                               'rows': [[''.join(cell.itertext()) for cell in row] for row in node]})
                context = ''
    elif suffix == '.csv':
        csv_check(content)
        rows = list(csv.reader(StringIO(content.decode('utf-8-sig'))))
        pages = [{'number': 1, 'text': '\n'.join(' | '.join(row) for row in rows), 'locator': 'CSV'}]
        tables = [{'locator': 'CSV', 'context': '', 'rows': rows}]
    else:
        pages = [{'number': number, 'text': text, 'locator': f'Page {number}'}
                 for number, text in v1_sources.read_pages(content, detect_outlined_text=True)]
        if suffix == '.pdf':
            import fitz
            with fitz.open(stream=content, filetype='pdf') as pdf:
                for number, page in enumerate(pdf, 1):
                    for table in page.find_tables().tables:
                        tables.append({'locator': f'Page {number}',
                                       'context': page.get_text(clip=fitz.Rect(0, 0, page.rect.width, table.bbox[1])),
                                       'rows': table.extract()})
    return pages, tables


def extract_document(document: dict) -> dict:
    """Read every page, including scanned exhibits; retain evidence without inventing rates."""
    saved = document.get('extraction') or {}
    if saved.get('version') == EXTRACTION_VERSION and saved.get('sha256') == document.get('sha256') and saved.get('pages'):
        return saved
    content = object_store.get_object_bytes(document['object_key'])
    if document.get('sha256') and hashlib.sha256(content).hexdigest() != document['sha256']:
        raise ValueError('The stored document does not match its saved checksum.')
    pages, tables = structured_content(content, document.get('filename', 'source.pdf'))
    result = {'version': EXTRACTION_VERSION, 'sha256': hashlib.sha256(content).hexdigest(),
              'pages': pages, 'tables': tables,
              'summary': {'status': 'read', 'page_count': len(pages),
                          'empty_pages': [page['number'] for page in pages if not page['text'].strip()]}}
    # Index source passages, not inferred terms. One PDF can contain several
    # products, provider types, dates and fee schedules on different pages.
    result['passages'] = []
    for page in pages:
        number, text = page['number'], page['text']
        lines = text.splitlines()
        indices = set()
        for index, line in enumerate(lines):
            if re.search(r'fee schedule|rate schedule|reimbursement|payment appendix|compensation|effective date|\b99[2-4]\d{2}\b|\d\s*%', line, re.I):
                indices.update(range(max(0, index - 1), min(len(lines), index + 2)))
        if indices:
            result['passages'].append({'page': number, 'text': '\n'.join(lines[index] for index in sorted(indices))})
    staff_store.save_library_extraction(document['id'], document['practice_id'], result)
    return result


def extract_after_upload(document: dict) -> None:
    try:
        from app.insurance import library_pricing
        library_pricing.prepare(document['practice_id'])
    except Exception:
        # The original remains saved. Opening extracted text retries and reports
        # the failure instead of silently using a partial document for pricing.
        staff_store.save_library_extraction(document['id'], document['practice_id'],
            {'summary': {'status': 'needs_retry'}})


def extraction_preview(document: dict, result: dict) -> str:
    title = escape(document['label'])
    passages = ''.join(f'<li><a href="#page-{row["page"]}">Page {row["page"]}</a><pre>{escape(row["text"])}</pre></li>'
                       for row in result['passages'])
    pages = ''.join(f'<section id="page-{page["number"]}"><h2>Page {page["number"]}</h2>'
                    f'<a href="document?preview=true#page={page["number"]}" target="_blank" rel="noopener noreferrer">Open original page</a>'
                    f'<pre>{escape(page["text"])}</pre></section>' for page in result['pages'])
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{title} · Extracted text</title><style>body{{font:16px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#172b4d}}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 monospace}section{border-top:1px solid #ccd5df;margin-top:32px}a{color:#1458a6}</style></head>'
            f'<body><h1>{title}</h1><p>{len(result["pages"])} pages read. Search this page to find terms across the full document.</p>'
            '<p>Compare extracted figures with the original page. A contract can contain several schedules. '
            'Its payer, product, provider type, location and effective date must match the visit before a rate is used. '
            'Supported final rules are processed automatically. Missing schedules, incomplete scope and conflicting terms remain unavailable for pricing.</p>'
            f'<details><summary>Payment and rate passages</summary><ul>{passages}</ul></details>{pages}</body></html>')


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
    from app.insurance import commercial_store
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
        links[document['id']] = uses
    return {'links': links}
