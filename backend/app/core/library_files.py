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

from app.core.library_parsing import (
    OFFICE_TYPES,
    office_preview,
    csv_check,
    structured_content,
)


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
