"""Sharing diagnostics correlate delivery without retaining a share capability."""

import asyncio
import io
import json
import logging
from unittest.mock import patch

import httpx

from app.main import app
from app.logs.collection_audit import logger


def test_manifest_delivery_and_rejections_have_safe_correlated_events():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    secret = 's' * 43
    edge = 'synthetic-edge-request-id-1234567890=='

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch('app.main.verify_manifest_pin', return_value={'encrypted_jwe': 'private-jwe'}):
                delivered = await client.post('/manifest/' + secret, headers={'x-amz-cf-id': edge},
                                              json={'recipient': 'private-recipient'})
            invalid = await client.post('/manifest/' + secret, json={'passcode': '1234'})
            with patch('app.main.verify_manifest_pin', return_value=None):
                unavailable = await client.post('/manifest/' + secret, json={'recipient': 'private-recipient'})
            return delivered, invalid, unavailable

    try:
        responses = asyncio.run(run())
    finally:
        logger.removeHandler(handler)
    assert [r.status_code for r in responses] == [200, 422, 404]
    assert responses[0].json()['files'][0]['embedded'] == 'private-jwe'
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(events) == 3
    assert events[0]['cloudfront_request_id'] == edge
    for response, event in zip(responses, events):
        assert event['event'] == 'sharing_http_completed'
        assert event['stage'] == 'manifest_fetch'
        assert event['http_status'] == response.status_code
        assert event['request_id'] == response.headers['X-ClinicForce-Request-ID']
        assert response.headers['Cache-Control'] == 'no-store'
    assert len({event['request_id'] for event in events}) == 3
    for private in (secret, 'private-jwe', 'private-recipient', '"passcode"', '/manifest/'):
        assert private not in stream.getvalue()


def test_unhandled_sharing_failure_omits_exception_and_invalid_edge_header():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
            with patch('app.main.verify_manifest_pin', side_effect=RuntimeError('private exception')):
                return await client.post('/manifest/secret', headers={'x-amz-cf-id': 'private patient details'},
                                         json={'recipient': 'private-recipient'})
    try:
        response = asyncio.run(run())
    finally:
        logger.removeHandler(handler)
    event = json.loads(stream.getvalue())
    assert response.status_code == 500
    assert event['event'] == 'sharing_http_failed'
    assert event['reason_code'] == 'unhandled_error'
    assert 'cloudfront_request_id' not in event
    assert 'private' not in stream.getvalue()
