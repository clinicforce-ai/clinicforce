"""Clinic-approved Medicare allowed fees stored in PostgreSQL."""
from __future__ import annotations

from datetime import date


EFFECTIVE_FROM = '2026-11-13'
SOURCE_REFERENCE = 'Clinic-approved Medicare allowed fee schedule, 2026-11-13'
FEE_ROWS = (
    ('99203', 'Office o/p new low 30 min', 11960),
    ('99204', 'Office o/p new mod 45 min', 18049),
    ('99205', 'Office o/p new hi 60 min', 24151),
    ('99213', 'Office o/p est low 20 min', 9633),
    ('99214', 'Office o/p est mod 30 min', 13741),
    ('99215', 'Office o/p est hi 40 min', 19513),
    ('64612', 'Chemodenervation of muscle(s); facial/trigeminal/cervical (as applicable)', 14502),
    ('J0585', 'Injection, OnabotulinumtoxinA (Botox), per unit', 651),
    ('95911', 'Nerve Conduction Studies (9–10 studies)', 22107),
    ('95886', 'Needle EMG, each extremity with related paraspinal areas', 9997),
    ('98006', 'Synchronous telemedicine E/M service', 10555),
    ('11104', 'Punch bx skin single lesion', 12195),
    ('11105', 'Punch bx skin ea sep/addl', 6115),
    ('96372', 'Ther/proph/diag inj sc/im', 1547),
    ('95983', 'Alys brn npgt prgrmg 15 min', 5289),
)


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
