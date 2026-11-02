"""Daily staging worker reads only saved fake visits and emits aggregate counts."""
from datetime import date

import pytest

from app.insurance import previsit_cached_tasks, store
from app.insurance.staging_synthetic import visit


def test_schedule_is_staging_only_and_deduplicated(monkeypatch):
    with pytest.raises(RuntimeError, match='staging-only'):
        previsit_cached_tasks.staging_schedule_summary(date(2026, 11, 2))
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://staging.clinicforce.ai')
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
    result = visit('missing_benchmark_cpt')
    case = {'id': 'invented-case', 'visit_review': result['visit_review'],
            'previsit_context': {'result': result}}
    calls = []
    def saved_only(practice_id, start, end):
        calls.append((practice_id, start, end))
        return [case, case]
    monkeypatch.setattr(store, 'staging_synthetic_due', saved_only)
    first = previsit_cached_tasks.staging_schedule_summary(date(2026, 11, 2))
    second = previsit_cached_tasks.staging_schedule_summary(date(2026, 11, 2))
    assert first == second
    assert first['synthetic_only'] is True
    assert first['checked_count'] == 2
    assert first['payer_requests'] == 0
    assert first['clinic_price_task_count'] <= 1
    assert all(start == '2026-11-02' and end == '2026-12-02'
               for _, start, end in calls)
    assert 'invented-case' not in str(first)
