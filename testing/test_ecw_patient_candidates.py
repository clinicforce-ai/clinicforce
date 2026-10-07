"""Similar names require staff review and a fresh, constrained eCW search."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import staff
from app.insurance import ecw_backend as ecw

PROFILE = {"patient_first_name": "Marry Ann", "patient_last_name": "Example",
           "patient_birth_date": "1980-01-15", "patient_middle_initial": "B"}
DETAILS = {"given_name": "Marry Ann", "family_name": "Example", "birth_date": "1980-01-15", "middle_initial": "B"}
PATIENT = {"resourceType": "Patient", "id": "synthetic-1", "birthDate": "1980-01-15",
           "name": [{"use": "official", "given": ["Mary Ann", "Beth"], "family": "Example"}],
           "identifier": [{"use": "secondary", "value": "SYNTHETIC-ACCT"}]}


def transport(rows, calls, failure=None):
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/Patient'):
            assert set(request.url.params) != {'given', 'birthdate'}
            if failure:
                return httpx.Response(failure, json={"resourceType": "OperationOutcome"})
            values = rows
        else:
            assert request.url.path.endswith('/Encounter')
            values = [{"resourceType": "Encounter", "id": "visit-1", "status": "planned",
                       "subject": {"reference": "Patient/synthetic-1"},
                       "period": {"start": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}]
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": row} for row in values]})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize('left,right,expected', [
    ('jmaes', 'james', True), ('marryann', 'maryann', True), ('james', 'jamez', True),
    ('james', 'jammes', True), ('john', 'jon', False), ('alice', 'alicia', False),
    ('james', 'james', False), ('abcdef', 'abcfed', False), ('mary', 'maryann', False),
])
def test_name_edit_boundary(left, right, expected):
    assert ecw._one_name_edit(left, right) is expected
    assert ecw._one_name_edit(right, left) is expected


def test_candidate_is_review_only_then_rechecked_before_appointments():
    async def run():
        calls = []
        wire = transport([PATIENT], calls)
        with patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
                patch.object(ecw, '_access_token', new=AsyncMock(return_value='synthetic')):
            with pytest.raises(ecw.EcwPatientCandidates) as review:
                await ecw.lookup_patient_and_appointments(PROFILE, {}, transport=wire, suggest_matches=True)
            candidate = review.value.candidates[0]
            assert candidate['given_name'] == 'Mary Ann'
            assert candidate['match_kind'] == 'similar_first_name'
            assert candidate['ecw_account_number'] == 'SYNTHETIC-ACCT'
            assert all(req.url.path.endswith('/Patient') for req in calls)
            before = len(calls)
            result = await ecw.lookup_patient_and_appointments(PROFILE, {}, transport=wire,
                suggest_matches=True, candidate_id=candidate['id'], candidate_given_name=candidate['given_name'])
            assert len(calls) > before
            assert result['patient']['given_name'] == 'Mary Ann'
            assert result['patient']['id'] == candidate['id']
            assert [v['id'] for v in result['appointments']] == ['visit-1']
    asyncio.run(run())


@pytest.mark.parametrize('changes', [
    {'patient_birth_date': '1980-01-16'}, {'patient_last_name': 'Other'},
    {'patient_middle_initial': 'C'}, {'patient_first_name': 'Someone Else'},
])
def test_conflicting_identity_is_never_suggested(changes):
    async def run():
        async with httpx.AsyncClient(transport=transport([PATIENT], [])) as client:
            with pytest.raises(ecw.EcwBackendError) as error:
                await ecw._patient_for_profile(client, 'https://ehr.example.test/fhir', 'synthetic',
                                              {**PROFILE, **changes}, suggest_matches=True)
            assert error.value.reason_code == 'no_match'
    asyncio.run(run())


def test_similarity_does_not_enable_automatic_or_other_reader_matching():
    async def run():
        async with httpx.AsyncClient(transport=transport([PATIENT], [])) as client:
            with pytest.raises(ecw.EcwBackendError) as error:
                await ecw._patient_for_profile(client, 'https://ehr.example.test/fhir', 'synthetic', PROFILE)
            assert error.value.reason_code == 'no_match'
    asyncio.run(run())


@pytest.mark.parametrize('rows,profile,expected', [
    ([PATIENT, {**PATIENT, 'id': 'synthetic-2'}], PROFILE, 'confirmation_required'),
    ([PATIENT, {**PATIENT, 'id': 'synthetic-2'}], {**PROFILE, 'patient_first_name': 'Mary Ann'}, 'confirmation_required'),
    ([{**PATIENT, 'id': f'synthetic-{i}'} for i in range(6)], PROFILE, 'ambiguous_match'),
])
def test_multiple_candidates_never_auto_select(rows, profile, expected):
    async def run():
        async with httpx.AsyncClient(transport=transport(rows, [])) as client:
            with pytest.raises(ecw.EcwBackendError) as error:
                await ecw._patient_for_profile(client, 'https://ehr.example.test/fhir', 'synthetic', profile, suggest_matches=True)
            assert error.value.reason_code == expected
    asyncio.run(run())


def test_real_upstream_failure_is_not_a_candidate_or_no_match():
    async def run():
        async with httpx.AsyncClient(transport=transport([], [], failure=408)) as client:
            with pytest.raises(ecw.EcwBackendError) as error:
                await ecw._patient_for_profile(client, 'https://ehr.example.test/fhir', 'synthetic', PROFILE, suggest_matches=True)
            assert error.value.reason_code == 'upstream_http_error'
    asyncio.run(run())


def test_staff_confirmation_contract_persistence_and_stale_selection():
    async def run():
        app = FastAPI(); app.include_router(staff.router)
        calls = []
        rows = [PATIENT]
        actual_lookup = ecw.lookup_patient_and_appointments
        async def lookup(*args, **kwargs):
            return await actual_lookup(*args, **kwargs, transport=transport(rows, calls))
        actor = {'id': 'synthetic-staff', 'practice_id': 'synthetic-practice'}
        def create(intake_id, staff_member, documents, source, patient_id, fields):
            return {'id': intake_id, 'practice_id': staff_member['practice_id'], 'documents': [], 'fields': fields}
        with patch.object(staff, 'PUBLIC_BASE_URL', 'http://test'), \
                patch.object(staff.staff_store, 'get_session', return_value=actor), \
                patch.object(ecw, 'configured', return_value=True), \
                patch.object(ecw, 'settings', return_value=('client', 'https://ehr.example.test/fhir')), \
                patch.object(ecw, '_access_token', new=AsyncMock(return_value='synthetic')), \
                patch.object(ecw, 'lookup_patient_and_appointments', side_effect=lookup), \
                patch.object(staff.staff_store, 'find_ecw_intake', return_value=None) as find, \
                patch.object(staff.staff_store, 'create_intake', side_effect=create) as save:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Origin': 'http://test'}) as client:
                path = '/api/staff/intakes/ecw-lookup'
                review = await client.post(path, json=DETAILS)
                assert review.status_code == 200
                assert review.headers['cache-control'] == 'no-store'
                assert review.json()['requires_confirmation'] is True
                assert len(review.json()['candidates']) == 1
                find.assert_not_called(); save.assert_not_called()
                confirmation = {**DETAILS, 'candidate_id': 'synthetic-1', 'candidate_given_name': 'Mary Ann', 'identity_confirmed': True}
                for missing in ['candidate_id', 'candidate_given_name', 'identity_confirmed']:
                    invalid = await client.post(path, json={k: v for k, v in confirmation.items() if k != missing})
                    assert invalid.status_code == 422
                for alteration in [{'candidate_id': 'other'}, {'candidate_given_name': 'Different'}, {'birth_date': '1980-01-16'}]:
                    rejected = await client.post(path, json={**confirmation, **alteration})
                    assert rejected.status_code == 409
                save.assert_not_called()
                accepted = await client.post(path, json=confirmation)
                assert accepted.status_code == 201
                assert accepted.json()['fields']['given_name'] == 'Mary Ann'
                assert accepted.json()['fields']['ecw_patient_id'] == 'synthetic-1'
                assert accepted.json()['ecw_match']['appointments'][0]['id'] == 'visit-1'
                assert save.call_count == 1
                rows.clear()
                stale = await client.post(path, json=confirmation)
                assert stale.status_code == 409
                assert save.call_count == 1
                with patch.object(staff.staff_store, 'get_session', return_value=None):
                    assert (await client.post(path, json=DETAILS)).status_code == 401
                assert (await client.post(path, json=DETAILS, headers={'Origin': 'https://untrusted.example'})).status_code == 403
    asyncio.run(run())
