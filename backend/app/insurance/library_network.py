"""Match clinic-uploaded participation evidence without inventing contract rates."""
import hashlib
import re
from datetime import datetime

from lxml import html

from app.core import library_files, object_store, staff_store


def key(value):
    words = re.sub(r'\b(and|of|the|llc|pllc|inc)\b', '', str(value).casefold())
    return re.sub(r'[^a-z0-9]', '', words)


def product_keys(text, payer):
    """Expand explicit shared-prefix names, e.g. Example POS / POS II."""
    values = set()
    for item in text.split(';'):
        previous = ''
        for part in item.split('/'):
            part = part.strip()
            if not part:
                continue
            first = part.split()[0]
            prefix = re.split(r'\b' + re.escape(first) + r'\b', previous, maxsplit=1, flags=re.I)
            value = prefix[0] + part if len(prefix) == 2 else part
            values.add(key(value).removeprefix(key(payer)))
            previous = value
    return values - {''}


def match(documents, matrices, payer, products, provider, service_date):
    """Require the group, payer, exact product and effective date to match."""
    wanted = {key(value).removeprefix(key(payer)) for value in products if value}
    group = key(provider.get('name', ''))
    if not wanted or not key(payer) or len(group) < 12:
        return None
    matrix_refs = []
    accepted = set()
    for matrix in matrices:
        for table in matrix['tables']:
            header = None
            for number, row in enumerate(table, 1):
                labels = [str(value).casefold() for value in row]
                if 'payer' in labels:
                    columns = [i for i, value in enumerate(labels) if 'names' in value and 'accepted' in value]
                    header = (labels.index('payer'), columns[0]) if len(columns) == 1 else None
                    continue
                if header and len(row) > max(header) and key(row[header[0]]) == key(payer):
                    accepted.update(product_keys(row[header[1]], payer))
                    matrix_refs.append(f"library:{matrix['id']}:row:{number}")
    matches = []
    for document in documents:
        pages = document.get('pages') or []
        if not pages:
            continue
        cover = pages[0]['text']
        if key(payer) not in key(cover[:1800]) or group not in key(cover):
            continue
        start = document.get('effective_from')
        if not start:
            date = re.search(r'\bEFFECTIVE DATE\s*[:\-].{0,100}?([A-Za-z]+ \d{1,2},? +\d{4}|\d{4}-\d{2}-\d{2})', re.sub(r'\s+', ' ', cover), re.I)
            if date:
                for fmt in ('%B %d %Y', '%b %d %Y', '%Y-%m-%d'):
                    try:
                        start = datetime.strptime(date[1].replace(',', ''), fmt).date().isoformat()
                        break
                    except ValueError:
                        pass
        if not start or start > service_date or document.get('effective_to') and document['effective_to'] < service_date:
            continue
        # A payer-brand contract is insufficient for an arbitrary product. Use
        # the clinic's explicit accepted-product matrix or names in the contract.
        contract_text = key(' '.join(page['text'] for page in pages))
        if not wanted <= accepted and not all(key(product) in contract_text for product in products):
            continue
        matches.append({'network_name': 'Clinic contracted plan', 'network_source': 'clinic_library',
                        'evidence_reference': 'library:' + document['id'], 'filename': document['filename'],
                        'effective_from': start, 'effective_to': document.get('effective_to'),
                        'product_references': sorted(set(matrix_refs)) if wanted <= accepted else [],
                        'products': products, 'provider_scope': 'contracted billing group'})
    # Amendments and overlapping contracts require a deliberate selection.
    return matches[0] if len(matches) == 1 else None


def network(practice_id, payer, products, provider, service_date):
    if not payer or not products or len(key(provider.get('name', ''))) < 12:
        return None
    documents, matrices = [], []
    for summary in staff_store.list_library(practice_id):
        if summary['kind'] == 'contract':
            document = staff_store.get_library(summary['id'], practice_id)
            saved = (document or {}).get('extraction') or {}
            if document and saved.get('sha256') and saved['sha256'] == document.get('sha256') and saved.get('pages'):
                documents.append({**document, 'pages': saved['pages']})
        elif summary['kind'] == 'plan_matrix' and summary['filename'].lower().endswith('.xlsx'):
            content = object_store.get_object_bytes(summary['object_key'])
            if hashlib.sha256(content).hexdigest() != summary.get('sha256'):
                continue
            rendered = html.fromstring(library_files.office_preview(content, summary['filename']))
            matrices.append({'id': summary['id'], 'tables': [
                [[cell.text_content() for cell in row] for row in table.xpath('./tr')]
                for table in rendered.xpath('//table')]})
    return match(documents, matrices, payer, products, provider, service_date)
