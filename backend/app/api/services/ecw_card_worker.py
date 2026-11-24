"""Run recoverable eCW card scans outside staff HTTP requests."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.concurrency import run_in_threadpool

from app.core import ecw_card_refresh, object_store, staff_store
from app.insurance import ecw_backend
from app.logs.logger import log_warning


async def _refresh(job: dict) -> None:
    saved: list[dict] = []
    try:
        rows = await asyncio.wait_for(
            ecw_backend.insurance_cards(job['ecw_patient_id'], include_bytes=True), timeout=55,
        )
        partition = hashlib.sha256((job['practice_id'] + ':' + job['ecw_patient_id']).encode()).hexdigest()
        for row in rows:
            content = row.get('_content')
            if not isinstance(content, bytes):
                raise ValueError('eCW card bytes are missing')
            mime = row['content_type']
            extension = '.pdf' if mime == 'application/pdf' else '.png' if mime == 'image/png' else '.jpg'
            object_key = await run_in_threadpool(
                object_store.put_insurance_evidence, partition, 'card' + extension, content, mime,
            )
            saved.append({'id': row['id'], 'title': row['title'], 'content_type': mime, 'object_key': object_key})
        old = await run_in_threadpool(ecw_card_refresh.complete, job, saved)
    except Exception as exc:
        log_warning('eCW card refresh', 'Card scan incomplete: %s', type(exc).__name__)
        old = await run_in_threadpool(ecw_card_refresh.complete, job, saved, failed=True)
    for key in old:
        try:
            await run_in_threadpool(object_store.delete_object, key)
        except Exception:
            log_warning('eCW card refresh', 'Superseded card cleanup failed')


async def refresh_loop() -> None:
    while True:
        try:
            job = await run_in_threadpool(ecw_card_refresh.claim)
            if job:
                await _refresh(job)
                continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_warning('eCW card refresh', 'Queue check failed: %s', type(exc).__name__)
        await asyncio.sleep(2)


async def prefetch_loop() -> None:
    """Warm confirmed patients booked today/tomorrow, even before the UI opens."""
    while True:
        try:
            from app.api.routers.commercial_estimator import _ecw_practice_config
            linked = await run_in_threadpool(staff_store.linked_ecw_practices)
            if linked:
                practice_days = {}
                for practice_id in linked:
                    try:
                        zone = ZoneInfo(_ecw_practice_config(practice_id)['timezone'])
                        local_today = datetime.now(zone).date()
                        practice_days[practice_id] = {local_today.isoformat(),
                            (local_today + timedelta(days=1)).isoformat()}
                    except (KeyError, ValueError):
                        continue
                for day in sorted(set().union(*practice_days.values())) if practice_days else []:
                    encounters = await asyncio.wait_for(ecw_backend.practice_appointment_date(day), timeout=15)
                    booked = {item['patient_id'] for item in encounters}
                    for practice_id, patient_ids in linked.items():
                        if day in practice_days.get(practice_id, ()):
                            await run_in_threadpool(ecw_card_refresh.enqueue_many, practice_id, booked & patient_ids)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_warning('eCW card prefetch', 'Upcoming appointment read failed: %s', type(exc).__name__)
        await asyncio.sleep(4 * 60 * 60)
