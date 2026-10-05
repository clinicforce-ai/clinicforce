"""Trace stalled eCW reads without disclosing identity, URLs, or credentials."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


@pytest.mark.parametrize("saved_patient", [False, True])
def test_lookup_allows_slow_authorization_patient_and_appointment_reads(saved_patient):
    # Scale the real deadlines and provider delays together so this exercises
    # cancellation without adding a minute to every test run.
    wait_for = asyncio.wait_for
    async def scaled_wait_for(awaitable, timeout):
        return await wait_for(awaitable, timeout / 100)

    patient = {"resourceType": "Patient", "id": "synthetic-patient",
               "name": [{"given": ["Pat"], "family": "Example"}], "birthDate": "1980-01-01"}
    profile = {"patient_first_name": "Pat", "patient_last_name": "Example",
               "patient_birth_date": "1980-01-01"}
    if saved_patient:
        profile["ecw_patient_id"] = patient["id"]
    visit = {"resourceType": "Encounter", "id": "synthetic-visit", "status": "planned",
             "subject": {"reference": "Patient/synthetic-patient"},
             "period": {"start": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}

    async def handler(request):
        if request.url.path.endswith("/metadata"):
            await asyncio.sleep(0.11)  # Longer than the former 10-second stage budget.
            return httpx.Response(200, json={"rest": [{"security": {"extension": [
                {"url": "oauth-uris", "extension": [{"url": "token", "valueUri": "https://ehr.example.test/token"}]}]}}]})
        if request.url.path == "/token":
            await asyncio.sleep(0.11)
            return httpx.Response(200, json={"access_token": "synthetic-token"})
        assert request.extensions["timeout"]["read"] == 75
        if "/Patient" in request.url.path:
            await asyncio.sleep(0.31)
            return httpx.Response(200, json=patient if saved_patient else {"entry": [{"resource": patient}]})
        assert request.url.path.endswith("/Encounter")
        await asyncio.sleep(0.07)  # Longer than the former 6-second search budget.
        return httpx.Response(200, json={"entry": [{"resource": visit}]})

    async def run():
        with patch.object(ecw, "settings", return_value=("client", "https://ehr.example.test/fhir")), \
                patch.object(ecw.ecw, "_client_assertion", return_value="synthetic-assertion"), \
                patch.object(ecw.asyncio, "wait_for", side_effect=scaled_wait_for):
            return await ecw.lookup_patient_and_appointments(profile, {}, transport=httpx.MockTransport(handler))

    result = asyncio.run(run())
    assert result["patient"]["id"] == patient["id"]
    assert [row["id"] for row in result["appointments"]] == [visit["id"]]


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
