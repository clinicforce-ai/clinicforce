"""Saved-case pricing gap inventory stays practice-scoped and patient-free."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routers import staff
from app.insurance import missing_rates, store


def test_missing_rate_inventory_deduplicates_per_case_and_separates_cash():
    cases = [
        {'payer': 'Example Health', 'calculated_at': '2026-10-13T10:00:00Z', 'lines': [
            {'code': '99204', 'allowed_cents': None, 'cash_cents': 10000},
            {'code': '99204', 'allowed_cents': None, 'cash_cents': 10000},
            {'code': '95886', 'allowed_cents': 5000, 'cash_cents': None}]},
        {'payer': 'Example Health', 'calculated_at': '2026-10-14T10:00:00Z', 'lines': [
            {'code': '99204', 'allowed_cents': None, 'cash_cents': 10000}]},
        {'payer': 'Example Health', 'mode': 'alternatives', 'lines': [
            {'code': '99205', 'allowed_cents': None, 'cash_cents': None}]},
    ]
    result = missing_rates.summarize(cases, 500, [{'display_name': 'Example Health', 'aliases': []}])
    assert result['gaps'] == [
        {'payer': 'Example Health', 'code': '99204', 'kind': 'insured_allowed',
         'estimate_count': 2, 'last_observed_at': '2026-10-14T10:00:00Z'},
        {'payer': 'Example Health', 'code': '95886', 'kind': 'clinic_cash',
         'estimate_count': 1, 'last_observed_at': '2026-10-13T10:00:00Z'},
    ]
    assert result['scan_limit_reached'] is False
    assert 'patient' not in str(result).lower()


def test_unmapped_free_text_payer_is_not_returned():
    value = missing_rates.summarize([{'payer': 'Private patient note', 'lines': [
        {'code': '99204', 'allowed_cents': None}]}], 500)
    assert value['gaps'][0]['payer'] == 'Unmapped payer'
    assert 'Private patient note' not in str(value)


def test_cash_visit_does_not_create_an_insured_rate_gap():
    result = missing_rates.summarize([{'payment_mode': 'cash', 'lines': [
        {'code': '99204', 'allowed_cents': None, 'cash_cents': 9000}]}], 500)
    assert result['gaps'] == []


def test_missing_rate_query_reads_only_practice_scoped_price_fields():
    with patch.object(store, '_connection') as connection:
        connection.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
        assert store.recent_missing_rate_inputs('clinic-a', 25) == []
        query, arguments = connection.return_value.__enter__.return_value.execute.call_args.args
    assert 'WHERE practice_id=%s' in query
    assert 'price_range,lines' in query
    assert 'SELECT workflow_json' not in query
    assert arguments == ('clinic-a', 25)


def test_missing_rate_inventory_is_admin_only_and_queries_the_staff_practice():
    with patch.object(staff, 'require_staff', new=AsyncMock(return_value={'role': 'staff', 'practice_id': 'clinic-a'})):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(staff.library_missing_rates(object()))
        assert exc.value.status_code == 403
    with patch.object(staff, 'require_staff', new=AsyncMock(return_value={'role': 'admin', 'practice_id': 'clinic-a'})), \
         patch.object(staff.store, 'recent_missing_rate_inputs', return_value=[]) as query, \
         patch.object(staff.commercial_store, 'list_connections', return_value=[]):
        response = asyncio.run(staff.library_missing_rates(object()))
    query.assert_called_once_with('clinic-a')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
