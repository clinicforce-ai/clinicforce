"""Collection events remain useful for tracing without recording patient data."""

import io
import json
import logging
import asyncio
from uuid import uuid4

import httpx

from app.main import app
from app.logs.collection_audit import collection_event, logger, request_id


def test_collection_event_keeps_only_safe_fields():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    trace = str(uuid4())
    case = str(uuid4())
    token = request_id.set(trace)
    try:
        collection_event('collection_calculated', case_id=case, status='estimated',
                         basis='office_copay', amount_cents=2500, source='Pat Example 1990-01-01',
                         practice_id='member-123')
    finally:
        request_id.reset(token)
        logger.removeHandler(handler)
    event = json.loads(stream.getvalue())
    assert event['request_id'] == trace
    assert event['case_id'] == case
    assert event['amount_cents'] == 2500
    assert event['status'] == 'estimated'
    assert 'source' not in event and 'practice_id' not in event
    assert 'Pat Example' not in stream.getvalue()


def test_collection_error_exposes_its_trace_without_logging_demographics():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.post('/api/staff/intakes/ecw-lookup', json={
                'given_name': 'Pat', 'family_name': 'Example', 'birth_date': '1990-01-01'})
    try:
        response = asyncio.run(request())
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 403
    trace = response.headers['X-ClinicForce-Request-ID']
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert events[-1]['event'] == 'collection_http_completed'
    assert events[-1]['request_id'] == trace
    assert events[-1]['http_status'] == 403
    assert 'Pat' not in stream.getvalue() and '1990-01-01' not in stream.getvalue()
