"""Synthetic patient identity/selection boundaries; no real provider calls."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import staff, commercial_estimator
from app.insurance import ecw_backend as ecw


PROFILE = {"patient_first_name": "Sample", "patient_last_name": "Vale", "patient_birth_date": "1980-01-01"}
PATIENT = {"resourceType": "Patient", "id": "patient-1", "name": [{"given": ["Sample"], "family": "North Vale"}],
           "birthDate": "1980-01-01", "identifier": [{"use": "secondary", "value": "SYNTHETIC-001"}]}
DETAILS = {"given_name": "Sample", "family_name": "Vale", "birth_date": "1980-01-01"}


def test_saved_link_preserves_initial_surname_rule_and_still_rejects_wrong_identity():
    async def run():
        with patch.object(ecw, "_cached_patient", AsyncMock(return_value=(PATIENT, None))):
            resolved, identifier, _ = await ecw._patient_for_profile(None, "", "", {**PROFILE, "ecw_patient_id": "patient-1"})
            assert resolved == PATIENT and identifier == "patient-1"
            for changed in ({"patient_birth_date": "1980-01-02"}, {"patient_first_name": "Other"},
                            {"patient_last_name": "Other"}, {"ecw_patient_id": "wrong-id"}, {"ecw_account_number": "wrong-account"}):
                try:
                    await ecw._patient_for_profile(None, "", "", {**PROFILE, "ecw_patient_id": "patient-1", **changed})
                except ecw.EcwBackendError:
                    pass
                else:
                    raise AssertionError("A saved ID cannot override contradictory identity")
    asyncio.run(run())


def test_multiword_given_names_are_compared_in_order_before_middle_initial():
    profile = {**PROFILE, "patient_first_name": "Mary Anne", "patient_middle_initial": "T"}
    for given in (["Mary Anne", "Taylor"], ["Mary", "Anne", "Taylor"]):
        row = {**PATIENT, "name": [{"given": given, "family": "Vale"}]}
        assert ecw._patient_matches(row, profile)
        assert not ecw._patient_matches(row, {**profile, "patient_first_name": "Anne Mary"})
        assert not ecw._patient_matches(row, {**profile, "patient_middle_initial": "Q"})


def test_name_suggestion_fallback_does_not_follow_pagination():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": PATIENT}],
                "link": [{"relation": "next", "url": "https://ehr.test/fhir/Patient?page=2"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await ecw._search(client, 'https://ehr.test/fhir', 'synthetic', 'Patient', {'name': 'Vale'}, first_page_only=True) == [PATIENT]
        assert len(calls) == 1
    asyncio.run(run())


def test_suggestions_are_bounded_deduplicated_and_require_identity_anchors():
    rows = [deepcopy(PATIENT) for _ in range(12)]
    for i, row in enumerate(rows):
        row["id"] = f"patient-{i:02}"
    assert len(ecw._patient_suggestions(rows + rows, {**PROFILE, "patient_last_name": "Vlae"})) == 8
    assert ecw._patient_suggestions([PATIENT], {**PROFILE, "patient_first_name": "Smaple"})[0]["differences"] == ["First name", "Last name"]
    assert ecw._patient_suggestions([PATIENT], {**PROFILE, "patient_birth_date": "1980-01-02"})[0]["differences"] == ["Last name", "Date of birth"]
    assert not ecw._patient_suggestions([PATIENT], {**PROFILE, "patient_first_name": "Unrelated"})
    with_middle = {**PATIENT, "name": [{"given": ["Sample", "Taylor"], "family": "North Vale"}]}
    assert not ecw._patient_suggestions([with_middle], {**PROFILE, "patient_first_name": "Smaple", "patient_middle_initial": "Q"})
    assert not ecw._patient_suggestions([{**PATIENT, "active": False}, {**PATIENT, "resourceType": "Practitioner"}], PROFILE)
    # An exact account can recover a DOB typo, but still returns a suggestion.
    result = ecw._patient_suggestions([PATIENT], {**PROFILE, "patient_birth_date": "1980-01-02", "ecw_account_number": "SYNTHETIC-001"})
    assert result[0]["differences"] == ["Last name", "Date of birth"]


def test_live_reader_contract_suggestions_do_not_read_visits_and_confirmation_rechecks():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith('/Patient/patient-1'):
                return httpx.Response(200, json=PATIENT)
            if request.url.path.endswith('/Patient'):
                params = dict(request.url.params)
                assert set(params) in ({'given', 'family', 'birthdate'}, {'family', 'birthdate'}, {'name', 'birthdate'}, {'identifier', 'birthdate'}, {'name'})
                return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': [{'resource': PATIENT}]})
            if request.url.path.endswith('/Encounter'):
                return httpx.Response(200, json={'resourceType': 'Bundle', 'entry': []})
            raise AssertionError("Unexpected provider resource")
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.test/fhir')), \
             patch.object(ecw, '_access_token', AsyncMock(return_value='synthetic-token')):
            transport = httpx.MockTransport(handler)
            try:
                await ecw.lookup_patient_and_appointments({**PROFILE, 'patient_last_name': 'Vlae'}, {}, allow_suggestions=True, transport=transport)
            except ecw.EcwPatientSelectionRequired as exc:
                assert len(exc.candidates) == 1
            else:
                raise AssertionError("Typo must require selection")
            assert not any(r.url.path.endswith('/Encounter') for r in calls)
            canonical = {**PROFILE, 'patient_last_name': 'North Vale', 'ecw_patient_id': 'patient-1'}
            result = await ecw.lookup_patient_and_appointments(canonical, {}, transport=transport)
            assert result['patient']['family_name'] == 'North Vale'
            assert any(r.url.path.endswith('/Encounter') for r in calls)
            # Wrong account results cannot become suggestions even if eCW ignores the filter.
            try:
                await ecw.lookup_patient_and_appointments({**PROFILE, 'ecw_account_number': 'OTHER'}, {}, allow_suggestions=True, transport=transport)
            except ecw.EcwPatientSelectionRequired as exc:
                assert exc.candidates == []
            encounter_reads = sum(request.url.path.endswith('/Encounter') for request in calls)
            try:
                await ecw.lookup_patient_and_appointments({'patient_first_name': '', 'patient_last_name': '',
                    'patient_birth_date': '1980-01-01', 'ecw_account_number': 'SYNTHETIC-001'}, {},
                    allow_suggestions=True, transport=transport)
            except ecw.EcwPatientSelectionRequired as exc:
                assert len(exc.candidates) == 1 and exc.candidates[0]['given_name'] == 'Sample'
            else:
                raise AssertionError('Account plus DOB without names must require selection')
            assert sum(request.url.path.endswith('/Encounter') for request in calls) == encounter_reads
            confirmed = await ecw.lookup_patient_and_appointments({**canonical, 'ecw_account_number': 'SYNTHETIC-001'}, {}, transport=transport)
            assert confirmed['patient']['ecw_account_number'] == 'SYNTHETIC-001'
            assert sum(request.url.path.endswith('/Encounter') for request in calls) == encounter_reads + 1
    asyncio.run(run())


def test_selection_api_auth_confirmation_no_writes_and_canonical_storage():
    app = FastAPI()
    app.include_router(staff.router)
    client = TestClient(app)
    actor = {'id': 'staff', 'practice_id': 'practice'}
    path = '/api/staff/intakes/ecw-lookup'
    with patch.object(staff, 'PUBLIC_BASE_URL', 'http://testserver'), \
         patch.object(staff.staff_store, 'get_session', return_value=None), \
         patch.object(ecw, 'lookup_patient_and_appointments', AsyncMock()) as read:
        assert client.post(path, json=DETAILS, headers={'Origin': 'http://testserver'}).status_code == 401
        assert client.post(path, json=DETAILS, headers={'Origin': 'https://other.test'}).status_code == 403
        read.assert_not_awaited()
    with patch.object(staff, 'require_staff', AsyncMock(return_value=actor)), \
         patch.object(ecw, 'configured', return_value=True), \
         patch.object(staff.staff_store, 'find_ecw_intake', return_value=None) as find, \
         patch.object(staff.staff_store, 'create_intake', return_value={'id': 'intake'}) as create, \
         patch.object(staff, 'public', side_effect=lambda value: value), \
         patch.object(ecw, 'lookup_patient_and_appointments', AsyncMock(side_effect=ecw.EcwPatientSelectionRequired([]))) as read:
        response = client.post(path, json=DETAILS)
        assert response.status_code == 200 and response.json() == {'status': 'selection_required', 'candidates': []}
        assert response.headers['cache-control'] == 'no-store'
        find.assert_not_called(); create.assert_not_called()
        assert client.post(path, json={**DETAILS, 'selected_patient_id': 'patient-1'}).status_code == 422
        match = {'patient': {'id': 'patient-1', 'given_name': 'Sample', 'family_name': 'North Vale', 'birth_date': '1980-01-01'}, 'appointments': []}
        read.side_effect = None; read.return_value = match
        response = client.post(path, json={**DETAILS, 'family_name': 'North Vale', 'selected_patient_id': 'patient-1', 'identity_confirmed': True})
        assert response.status_code == 201
        assert create.call_args.args[-1]['family_name'] == 'North Vale'
        assert read.call_args.args[0]['ecw_patient_id'] == 'patient-1'
        assert read.call_args.kwargs['allow_suggestions'] is False
        find.return_value = {'id': 'existing', 'patient': {'given_name': 'Smaple', 'family_name': 'Vale', 'birth_date': '1980-01-01'}}
        create.reset_mock()
        with patch.object(staff.staff_store, 'confirm_ecw_demographics') as update, \
             patch.object(staff.staff_store, 'get_intake', return_value={'id': 'existing'}):
            response = client.post(path, json=DETAILS)
            assert response.status_code == 200 and response.json()['status'] == 'selection_required'
            update.assert_not_called(); create.assert_not_called()
            response = client.post(path, json={**DETAILS, 'family_name': 'North Vale', 'selected_patient_id': 'patient-1', 'identity_confirmed': True})
            assert response.status_code == 201
            assert update.call_args.args[0] == 'existing'
            assert update.call_args.args[2]['given_name'] == 'Sample'
            assert update.call_args.args[2]['ecw_patient_id'] == 'patient-1'
        find.return_value = None
        read.side_effect = ecw.EcwBackendError('Provider timed out', reason_code='read_timeout')
        assert client.post(path, json=DETAILS).status_code == 502


def test_account_and_birth_date_api_needs_no_name():
    app = FastAPI()
    app.include_router(staff.router)
    client = TestClient(app)
    actor = {'id': 'staff', 'practice_id': 'practice'}
    path = '/api/staff/intakes/ecw-lookup'
    with patch.object(staff, 'require_staff', AsyncMock(return_value=actor)), \
         patch.object(ecw, 'configured', return_value=True), \
         patch.object(ecw, 'lookup_patient_and_appointments', AsyncMock(side_effect=ecw.EcwPatientSelectionRequired([{
             'id': 'patient-1', 'given_name': 'Sample', 'family_name': 'North Vale',
             'birth_date': '1980-01-01', 'ecw_account_number': 'SYNTHETIC-001', 'differences': [],
         }]))) as read:
        response = client.post(path, json={'ecw_account_number': ' SYNTHETIC-001 ', 'birth_date': '1980-01-01'})
        assert response.status_code == 200 and len(response.json()['candidates']) == 1
        assert read.call_args.args[0]['ecw_account_number'] == 'SYNTHETIC-001'
        assert read.call_args.args[0]['patient_birth_date'] == '1980-01-01'
        assert 'ecw_patient_id' not in read.call_args.args[0]
        assert read.call_args.kwargs['allow_suggestions'] is True
        read.reset_mock()
        response = client.post(path, json={'ecw_account_number': 'SYNTHETIC-NEW'})
        assert response.status_code == 422
        read.assert_not_awaited()
        assert client.post(path, json={}).status_code == 422


def test_estimator_identity_failure_returns_actionable_code_without_benefits():
    app = FastAPI()
    app.include_router(commercial_estimator.router)
    client = TestClient(app)
    with patch.object(commercial_estimator, '_session', AsyncMock(return_value={'practice_id': 'practice'})), \
         patch.object(commercial_estimator.library_pricing, 'prepare', return_value=[]), \
         patch.object(commercial_estimator, '_ecw_identity', return_value=PROFILE), \
         patch.object(commercial_estimator, '_ecw_practice_config', return_value={}), \
         patch.object(ecw, 'configured', return_value=True), \
         patch.object(ecw, 'list_appointments', AsyncMock(side_effect=ecw.EcwBackendError('No match', reason_code='no_match'))), \
         patch.object(ecw, 'fetch_inputs', AsyncMock(side_effect=ecw.EcwBackendError('No match', reason_code='no_match'))), \
         patch.object(commercial_estimator, '_post_stedi_eligibility', AsyncMock()) as payer:
        response = client.get('/api/doctor/insurance/commercial/appointments')
        assert response.status_code == 409
        assert response.json()['code'] == 'ecw_patient_selection_required'
        response = client.post('/api/doctor/insurance/commercial/appointments/previsit', json={'appointment_id': 'visit-1'})
        assert response.status_code == 409
        assert response.json()['code'] == 'ecw_patient_selection_required'
        payer.assert_not_awaited()
