"""Tenant-scoped, versioned cash schedules from explicit document table cells."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile
from xml.etree import ElementTree
import fitz
from app.insurance import commercial, commercial_store


def read_rows(content: bytes, filename: str) -> list[dict]:
    suffix = Path(filename).suffix.lower()
    tables = []
    document_text = ''
    try:
        if suffix == '.csv':
            document_text = content.decode('utf-8-sig')
            tables = [list(csv.reader(io.StringIO(content.decode('utf-8-sig')), strict=True))]
        elif suffix == '.docx':
            with ZipFile(io.BytesIO(content)) as archive:
                item = archive.getinfo('word/document.xml')
                if item.file_size > 20 * 1024 * 1024:
                    raise ValueError('oversized document')
                root = ElementTree.fromstring(archive.read(item))
            document_text = ' '.join(root.itertext())
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            tables = [[[' '.join(node.text or '' for node in cell.findall('.//w:t', ns)) for cell in row.findall('w:tc', ns)]
                       for row in table.findall('w:tr', ns)] for table in root.findall('.//w:tbl', ns)]
        elif suffix == '.pdf':
            with fitz.open(stream=content, filetype='pdf') as document:
                if len(document) > 100:
                    raise ValueError('too many pages')
                for page in document:
                    document_text += page.get_text()
                    tables.extend(table.extract() for table in page.find_tables().tables)
        else:
            raise commercial.CommercialError('Upload a PDF, DOCX or UTF-8 CSV price schedule.')
    except commercial.CommercialError:
        raise
    except Exception as exc:
        raise commercial.CommercialError('The schedule could not be read. Use a readable table with CPT, Description and Cash Price columns.') from exc
    if re.search(r'\b(pending|suggested|proposed|draft)\b', document_text, re.I):
        raise commercial.CommercialError('This document contains provisional pricing. Upload the approved schedule.')
    result = []
    seen = set()
    for table in tables:
        if not table:
            continue
        headers = [re.sub(r'[^a-z]', '', str(x or '').lower()) for x in table[0]]
        codes = [i for i, h in enumerate(headers) if h in {'cpt','hcpcs','code','procedurecode','cptcode','cptrange'}]
        prices = [i for i,h in enumerate(headers) if 'cash' in h or 'selfpay' in h]
        if not prices:
            prices = [i for i,h in enumerate(headers) if h in {'amount','price'}]
        if len(codes) != 1 or len(prices) != 1:
            continue
        descriptions = [i for i,h in enumerate(headers) if h in {'description','service','procedure'}]
        for number, row in enumerate(table[1:],2):
            if not any(row):
                continue
            if max(codes[0], prices[0]) >= len(row):
                raise commercial.CommercialError(f'Incomplete price row {number}.')
            token = str(row[codes[0]] or '').strip().upper()
            if re.fullmatch(r'\d{5}\s*[-–]\s*\d{5}',token):
                start,end=map(int,re.split(r'\s*[-–]\s*',token))
                if not start <= end <= start + 20:
                    raise commercial.CommercialError('CPT ranges may contain up to 21 alternative codes.')
                expanded=[str(x) for x in range(start,end+1)]
            elif re.fullmatch(r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])',token):
                expanded=[token]
            else:
                raise commercial.CommercialError(f'Row {number} is a package or has no single CPT. Use one per-service price or alternative CPT range per row; bundled totals must not be split into per-code prices.')
            text=str(row[prices[0]] or '').strip()
            if not re.fullmatch(r'\$?\s*\d[\d,]*(?:\.\d{1,2})?',text):
                raise commercial.CommercialError(f'Row {number} needs one explicit dollar amount; formulas and proposed prices cannot be activated.')
            amount=commercial._stedi_cents(text.replace('$','').replace(',','').strip())
            if amount is None:
                raise commercial.CommercialError(f'Invalid price in row {number}.')
            description=str(row[descriptions[0]] or '')[:500] if descriptions else ''
            if re.search(r'pending|suggested|proposed|package|session', ' '.join(str(x or '') for x in row), re.I):
                raise commercial.CommercialError(f'Row {number} is conditional or bundled. Upload an approved per-service schedule.')
            for code in expanded:
                if code in seen:
                    raise commercial.CommercialError(f'Multiple prices for {code}; use a separate schedule for each pricing scope.')
                seen.add(code)
                result.append({'code':code,'description':description,'cash_cents':amount})
    if not result or len(result)>5000:
        raise commercial.CommercialError('No unambiguous prices found. Include a table headed CPT, Description and Cash Price.')
    return result


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
        row=db.execute("SELECT * FROM practice_cash_schedules WHERE practice_id=%s AND status='active' AND effective_from<=%s ORDER BY effective_from DESC,created_at DESC,id DESC LIMIT 1",(practice_id,service_date)).fetchone()
    if not row:
        return None
    return {**dict(row),'rows':json.loads(row['rows_json'])}


def apply(result: dict, config: dict) -> None:
    priced=result['price_range'];rates={r['code']:r for r in config['rows']}
    for row in result['available_codes']:
        row['unit_cash_cents']=rates.get(row['code'],{}).get('cash_cents') if not row.get('modifiers') else None
    amounts=[]
    for line in priced['lines']:
        unit=rates.get(line['code'],{}).get('cash_cents') if not line.get('modifiers') else None
        amount=unit*line['units'] if unit is not None and line.get('units') else None
        line.update(unit_cash_cents=unit,cash_cents=amount,cash_source=config['filename'])
        amounts.append(amount)
    complete=bool(amounts) and all(v is not None for v in amounts) and not priced.get('service_issues')
    priced.update(cash_complete=complete);priced.pop('cash_min_cents',None);priced.pop('cash_max_cents',None)
    if complete:
        priced.update(cash_min_cents=min(amounts) if priced['mode']!='services' else sum(amounts),cash_max_cents=max(amounts) if priced['mode']!='services' else sum(amounts))
    result['cash_quote']={'complete':complete,'source':config['filename'],'effective_from':config['effective_from'],'selection':'',
                          'message':'Practice cash schedule for the selected services.' if complete else 'The practice cash schedule does not cover all selected services.'}
    result['practice_cash_source']={'filename':config['filename'],'effective_from':config['effective_from'],'version':config['id']}
    if complete:
        result['cash_quote'].update(min_cents=priced['cash_min_cents'], max_cents=priced['cash_max_cents'])
