"""A single date read is complete and exposes only confirmed clinic links."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import Request
import pytest

from app.api.routers import staff as _staff
from app.api.routers.staff_flow import staff_appointments
from app.api.routers import commercial_estimator
from app.core import staff_store
from app.insurance import ecw_backend


def test_practice_date_search_filters_invalid_and_cancelled_encounters():
    day = '2026-11-10'
    async def token(*_args):
        return 'synthetic-token'
    async def search(_client, _issuer, _token, resource, params, **_kwargs):
        assert resource == 'Encounter'
        assert params == {'date': day}
        return [
            {'resourceType': 'Encounter', 'id': 'valid', 'subject': {'reference': 'Patient/ecw-a'},
             'period': {'start': f'{day}T09:00:00-05:00'}, 'status': 'booked'},
            {'resourceType': 'Encounter', 'id': 'cancelled', 'subject': {'reference': 'Patient/ecw-a'},
             'period': {'start': f'{day}T10:00:00-05:00'}, 'status': 'cancelled'},
            {'resourceType': 'Encounter', 'id': 'wrong-day', 'subject': {'reference': 'Patient/ecw-b'},
             'period': {'start': '2026-11-09T10:00:00-05:00'}, 'status': 'booked'},
            {'resourceType': 'Encounter', 'id': 'bad-ref', 'subject': {'reference': 'Group/other'},
             'period': {'start': f'{day}T11:00:00-05:00'}, 'status': 'booked'},
        ], None
    with patch.object(ecw_backend, 'settings', return_value=('synthetic', 'https://example.test/fhir')), \
         patch.object(ecw_backend, '_access_token', side_effect=token), \
         patch.object(ecw_backend, '_bounded_search', side_effect=search):
        visits = asyncio.run(ecw_backend.practice_appointment_date(day))
    assert visits == [{'patient_id': 'ecw-a', 'date': day,
                       'start': f'{day}T09:00:00-05:00', 'status': 'booked'}]


def test_incomplete_date_search_cannot_be_presented_as_today():
    async def token(*_args):
        return 'synthetic-token'
    async def search(*_args, **_kwargs):
        return [{'resourceType': 'Encounter'}], 'Encounter'
    with patch.object(ecw_backend, 'settings', return_value=('synthetic', 'https://example.test/fhir')), \
         patch.object(ecw_backend, '_access_token', side_effect=token), \
         patch.object(ecw_backend, '_bounded_search', side_effect=search):
        with pytest.raises(ecw_backend.EcwBackendError):
            asyncio.run(ecw_backend.practice_appointment_date('2026-11-10'))


def test_today_route_maps_only_confirmed_practice_links(monkeypatch):
    async def actor(_request):
        return {'practice_id': 'clinic-a'}
    async def provider(day):
        return [
            {'patient_id': 'ecw-a', 'date': day, 'start': f'{day}T09:00:00-05:00', 'status': 'booked'},
            {'patient_id': 'other-clinic', 'date': day, 'start': f'{day}T11:00:00-05:00', 'status': 'booked'},
        ]
    monkeypatch.setattr(_staff, 'require_staff', actor)
    monkeypatch.setattr(commercial_estimator, '_ecw_practice_config', lambda _id: {'timezone': 'UTC'})
    monkeypatch.setattr(staff_store, 'linked_ecw_patient_ids', lambda practice_id: {'ecw-a': ['patient-a']} if practice_id == 'clinic-a' else {})
    monkeypatch.setattr(ecw_backend, 'practice_appointment_date', provider)
    response = asyncio.run(staff_appointments.today_appointments(Request({'type': 'http'})))
    body = json.loads(response.body)
    assert body['date'] == datetime.now(timezone.utc).date().isoformat()
    assert body['visits'] == [{'clinic_patient_id': 'patient-a', 'date': body['date'],
                               'start': f"{body['date']}T09:00:00-05:00", 'status': 'booked'}]
    assert response.headers['cache-control'] == 'no-store'


def test_linked_patient_lookup_is_practice_scoped_and_confirmed():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchall.return_value = [
        {'id': 'patient-a', 'ecw_patient_id': 'ecw-a'},
    ]
    with patch.object(staff_store, '_connection', return_value=connection):
        assert staff_store.linked_ecw_patient_ids('clinic-a') == {'ecw-a': ['patient-a']}
    query, params = connection.execute.call_args.args
    assert 'practice_id=%s' in query
    assert "identity_status='confirmed'" in query
    assert params == ('clinic-a',)
