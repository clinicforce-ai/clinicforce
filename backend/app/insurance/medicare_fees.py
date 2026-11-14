"""Clinic-approved Medicare allowed fees stored in PostgreSQL."""
from __future__ import annotations

from datetime import date



def estimate_rate(code: str, modifiers: list[str], units: int, *, service_date: str,
                  place_of_service: str, provider_npi: str, patient_binding: str) -> dict | None:
    """Read one current unmodified CPT fee; do not infer missing code or modifier prices."""
    if (modifiers or type(units) is not int or not 1 <= units <= 10000
            or not place_of_service or not provider_npi or not patient_binding):
        return None
    try:
        day = date.fromisoformat(service_date)
    except (TypeError, ValueError):
        return None
    from app.core.patient_store import _connection
    with _connection() as db:
        row = db.execute('''SELECT code,allowed_cents,source_reference,effective_from
            FROM medicare_fee_schedule WHERE code=%s AND effective_from<=%s
            ORDER BY effective_from DESC LIMIT 1''', (code, day.isoformat())).fetchone()
    if not row or day < date.fromisoformat(row['effective_from']):
        return None
    return {'id': f"medicare:{row['effective_from']}:{code}", 'code': code, 'modifier': '',
            'allowed_cents': row['allowed_cents'], 'basis': 'medicare_fee_schedule',
            'source_reference': row['source_reference'], 'effective_from': row['effective_from'],
            'service_date': service_date, 'place_of_service': place_of_service,
            'provider_npi': provider_npi, 'patient_binding': patient_binding}
