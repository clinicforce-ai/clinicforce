"""Opening dates for commercial previsit insurance checks."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api


def _eligibility_timing(service_date: str, check_from_date: str | None = None) -> dict | None:
    service_day = _api.date.fromisoformat(service_date)
    today = _api.datetime.now(_api.ZoneInfo(_api.os.getenv("COMMERCIAL_PRACTICE_TIMEZONE", "America/New_York"))).date()
    check_from = max(service_day - _api.timedelta(days=31), _api.date.fromisoformat(check_from_date) if check_from_date else _api.date.min)
    if today >= check_from:
        return None
    return {'status': 'too_early', 'appointment_date': service_date, 'check_from': check_from.isoformat(),
        'message': f"Appointment found on {service_day.strftime('%B %d, %Y')}. Insurance checking opens {check_from.strftime('%B %d, %Y')}. No Stedi request is made before then."}


def _insurance_check_from(case: dict) -> str | None:
    """Keep staff holds, but discard an opening date saved by the former seven-day rule."""
    hold = (case.get('visit_review') or {}).get('insurance_check_from')
    context = case.get('previsit_context') or {}
    service_date = (context.get('inputs') or {}).get('service_date')
    timing = (context.get('result') or {}).get('eligibility_timing') or {}
    if (hold and service_date and timing.get('status') == 'too_early'
            and timing.get('check_from') == hold
            and hold == (_api.date.fromisoformat(service_date) - _api.timedelta(days=7)).isoformat()):
        return None
    return hold
