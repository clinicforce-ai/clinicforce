"""Trace stalled eCW reads without disclosing identity, URLs, or credentials."""

import asyncio
import base64
from contextlib import contextmanager
import io
import json
import logging
from uuid import uuid4
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.insurance import ecw_backend as ecw
from app.logs.collection_audit import logger, request_id
from app.main import app


def test_inline_insurance_card_does_not_request_binary_scope():
    async def run():
        document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current',
            'subject': {'reference': 'Patient/synthetic'}, 'description': 'Insurance card',
            'content': [{'attachment': {'contentType': 'image/png', 'data': base64.b64encode(b'synthetic-card').decode()}}]}
        def handler(request):
            assert request.url.path == '/fhir/DocumentReference'
            return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': document}]})
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')) as token:
            cards = await ecw.insurance_cards('synthetic', transport=httpx.MockTransport(handler))
            data, mime = await ecw.insurance_cards('synthetic', document_id=cards[0]['id'], transport=httpx.MockTransport(handler))
        assert data == b'synthetic-card' and mime == 'image/png'
        assert all('system/Binary.read' not in call.args[3] for call in token.call_args_list)
    asyncio.run(run())


def test_card_bytes_require_same_issuer_binary_and_clinical_documents_are_not_cards():
    async def run():
        document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current',
            'subject': {'reference': 'Patient/synthetic'},
            'type': {'coding': [{'system': 'http://loinc.org', 'code': '64290-0'}]},
            'content': [{'attachment': {'contentType': 'image/png', 'url': 'Binary/image'}}]}
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == '/fhir/Binary/image':
                return httpx.Response(200, json={'resourceType': 'Binary', 'id': 'image', 'contentType': 'image/png',
                                                'data': base64.b64encode(b'synthetic-card-bytes').decode()})
            return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': document}]})
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')) as token:
            data, _ = await ecw.insurance_cards('synthetic', document_id='card:0', transport=httpx.MockTransport(handler))
            assert data == b'synthetic-card-bytes'
            assert 'system/Binary.read' in token.call_args.args[3]
            assert sum(r.url.path == '/fhir/Binary/image' for r in requests) == 1
            requests.clear()
            document['type']['coding'][0]['code'] = '34133-9'
            document['content'][0]['attachment']['contentType'] = 'application/xml'
            assert await ecw.insurance_cards('synthetic', transport=httpx.MockTransport(handler)) == []
            assert not any('/Binary/' in r.url.path for r in requests)
    asyncio.run(run())


def test_card_lookup_uses_ecw_type_search_and_patient_bound_included_binary():
    async def run():
        document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current',
            'subject': {'reference': 'Patient/synthetic'},
            'type': {'coding': [{'system': 'http://loinc.org', 'code': '64290-0'}]},
            'content': [{'attachment': {'contentType': 'image/png', 'url': 'Binary/image'}}]}
        binary = {'resourceType': 'Binary', 'id': 'image', 'contentType': 'image/png',
                  'data': base64.b64encode(b'synthetic-card').decode()}
        def handler(request):
            assert request.url.path == '/fhir/DocumentReference'
            assert request.url.params['patient'] == 'synthetic'
            rows = [document, binary] if request.url.params.get('type') == '64290-0' else []
            return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': row} for row in rows]})
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')) as token:
            cards = await ecw.insurance_cards('synthetic', transport=httpx.MockTransport(handler))
            data, _ = await ecw.insurance_cards('synthetic', document_id=cards[0]['id'], transport=httpx.MockTransport(handler))
            assert data == b'synthetic-card'
            assert all('system/Binary.read' not in call.args[3] for call in token.call_args_list)
            document['subject']['reference'] = 'Patient/someone-else'
            assert not await ecw.insurance_cards('synthetic', transport=httpx.MockTransport(handler))
    asyncio.run(run())


def test_enabled_card_read_survives_denied_optional_documents_and_uses_narrow_scope():
    async def run():
        document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current',
            'subject': {'reference': 'Patient/synthetic'},
            'type': {'coding': [{'system': 'http://loinc.org', 'code': '64290-0'}]},
            'content': [{'attachment': {'contentType': 'image/png', 'data': base64.b64encode(b'card').decode()}}]}
        def handler(request):
            if request.url.params.get('type') == '103140-0':
                return httpx.Response(403)
            return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': document}]})
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')) as token:
            data, mime = await ecw.insurance_cards('synthetic', document_id='card:0', transport=httpx.MockTransport(handler))
        assert (data, mime) == (b'card', 'image/png')
        assert token.call_args.args[3] == ('system/DocumentReference.read',)
    asyncio.run(run())


@pytest.mark.parametrize('document_type,status', [('53245-7', 'current'), ('64290-0', 'superseded')])
def test_government_ids_and_superseded_cards_are_not_current_insurance(document_type, status):
    async def run():
        document = {'resourceType': 'DocumentReference', 'id': 'doc', 'status': status,
            'subject': {'reference': 'Patient/synthetic'}, 'description': 'Insurance card',
            'type': {'coding': [{'system': 'http://loinc.org', 'code': document_type}]},
            'content': [{'attachment': {'contentType': 'image/png', 'url': 'Binary/image'}}]}
        def handler(request):
            assert request.url.path == '/fhir/DocumentReference'
            return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': document}]})
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')):
            assert await ecw.insurance_cards('synthetic', transport=httpx.MockTransport(handler)) == []
    asyncio.run(run())


@contextmanager
def captured():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    token = request_id.set(str(uuid4()))
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        request_id.reset(token)


@pytest.mark.parametrize("failure,reason,status", [
    ("http", "upstream_http_error", 408),
    ("read", "read_timeout", None),
    ("cancel", "cancelled", None),
])
def test_resource_failures_record_cause_and_query_shape_without_values(failure, reason, status):
    async def handler(request):
        if failure == "read":
            raise httpx.ReadTimeout("Pat Example 1990-01-01 secret-token", request=request)
        if failure == "cancel":
            await asyncio.sleep(1)
        return httpx.Response(408, json={"resourceType": "OperationOutcome", "issue": [
            {"diagnostics": "Pat Example 1990-01-01 secret-token"}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises((ecw.EcwBackendError, httpx.ReadTimeout, asyncio.TimeoutError)):
                await asyncio.wait_for(ecw._search(client, "https://ehr.example.test/fhir", "secret-token", "Patient",
                    {"given": "Pat", "family": "Example", "birthdate": "1990-01-01"}), 0.01)

    with captured() as stream:
        asyncio.run(run())
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["event"] for row in events] == ["ecw_stage_started", "ecw_stage_failed"]
    failed = events[-1]
    assert failed["reason_code"] == reason
    assert failed.get("http_status") == status
    assert failed["resource"] == "patient" and failed["query_kind"] == "given_family_birthdate"
    assert failed["page"] == 1 and failed["duration_ms"] >= 0
    assert failed["request_id"] == events[0]["request_id"]
    for value in ("Pat", "Example", "1990-01-01", "secret-token", "https://"):
        assert value not in stream.getvalue()


def test_overall_deadline_identifies_patient_stage_after_successful_authorization():
    async def handler(request):
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json={"rest": [{"security": {"extension": [
                {"url": "oauth-uris", "extension": [{"url": "token", "valueUri": "https://ehr.example.test/token"}]}]}}]})
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "secret-token"})
        await asyncio.sleep(1)
        return httpx.Response(200, json={"entry": []})

    async def run():
        with patch.object(ecw, "settings", return_value=("client", "https://ehr.example.test/fhir")), \
                patch.object(ecw.ecw, "_client_assertion", return_value="secret-assertion"), \
                patch.object(ecw, "LOOKUP_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(ecw.EcwBackendError, match="patient verification") as error:
                await ecw.lookup_patient_and_appointments({}, {}, transport=httpx.MockTransport(handler))
            assert error.value.reason_code == "lookup_deadline"
            assert error.value.stage == "patient_identity"

    with captured() as stream:
        asyncio.run(run())
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    completed = [row["stage"] for row in events if row["event"] == "ecw_stage_completed"]
    assert completed == ["metadata", "assertion_signing", "token_exchange"]
    assert events[-2]["reason_code"] == "cancelled"
    assert events[-2]["resource"] == "patient"
    assert events[-1]["event"] == "ecw_lookup_deadline"
    assert events[-1]["stage"] == "patient_identity"
    assert "secret-token" not in stream.getvalue() and "secret-assertion" not in stream.getvalue()


def test_commercial_appointment_support_reference_is_logged_on_failure():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/doctor/insurance/commercial/appointments")

    with captured() as stream, patch("app.api.routers.commercial_estimator._session",
                                    new=AsyncMock(side_effect=HTTPException(502, "Synthetic read failure"))):
        response = asyncio.run(run())
    assert response.status_code == 502
    completed = json.loads(stream.getvalue().splitlines()[-1])
    assert completed["stage"] == "appointment_list"
    assert completed["http_status"] == response.status_code
    assert completed["request_id"] == response.headers["X-ClinicForce-Request-ID"]
