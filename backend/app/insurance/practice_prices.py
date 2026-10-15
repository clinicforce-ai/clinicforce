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


from app.insurance.pricing.cash import apply
