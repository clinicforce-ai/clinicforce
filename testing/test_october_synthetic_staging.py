"""October-inspired fake patients through PostgreSQL and the saved FastAPI route."""
import json
import os
from datetime import date
from uuid import uuid4
from unittest.mock import patch

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.core import patient_store, staff_store
from app.insurance import collection_guidance, store, visit_collection_model
from app.insurance.staging_synthetic import SCENARIOS, journey, visit
from app.api.routers.staging_collection_fixtures import STAGING_NMCT_PRACTICE_ID


@pytest.mark.parametrize('name,share,collect,decision', SCENARIOS)
def test_october_inspired_fake_visit(name, share, collect, decision):
    result = visit(name)
    if result['payment_mode'] == 'cash':
        guidance = collection_guidance.build(result, result['visit_review'])
        actual = (None, guidance['amount_cents'], guidance['basis'])
    else:
        modeled = visit_collection_model.build(result)
        actual = (modeled['estimated_patient_responsibility_cents'], modeled['collect_now_cents'],
                  modeled['collection_decision_type'])
    assert actual == (share, collect, decision)
    assert result['benefits']['identity']['binding'].startswith('synthetic')
    if name == 'missing_benchmark_cpt':
        assert [row['code'] for row in modeled['price_acquisition']] == ['95886']
    if name == 'specialist_271_conflict':
        assert modeled['service_groups'][0]['benefit_evidence'] == ['synthetic-specialist-98']
        assert modeled['service_groups'][0]['estimated_patient_share_cents'] == 8000
    if name == 'aetna_new_office_date_mismatch':
        assert result['service_date'] == '2026-11-03'
        assert result['price_range']['lines'][0]['code'] == '99203'
        assert result['benefits']['service_date_matches'] is False
        assert result['benefits']['network_status'] == 'unknown'
        assert result['temporary_rate_reference']['lines'] == []


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_fake_patients_seed_save_reload_and_fastapi(monkeypatch):
    import asyncio
    asyncio.run(_run_fake_patients_seed_save_reload_and_fastapi(monkeypatch))


async def _run_fake_patients_seed_save_reload_and_fastapi(monkeypatch):
    """The staging-only admin action saves each fake journey and HTTP reads its final state."""
    from app.api.application import app
    from app.api.routers import commercial_estimator as api, staff as staff_api
    from app.api.routers import staging_collection_fixtures as fixtures_api

    url = os.environ['STAFF_TEST_DATABASE_URL']
    schema = 'october_fake_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        scoped = make_conninfo(url, options=f'-csearch_path={schema}')
        with patch.object(patient_store, 'DATABASE_URL', scoped), \
             patch.object(patient_store, '_schema_initialized', False), \
             patch.object(staff_store, '_schema_initialized', False):
            monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
            monkeypatch.setenv('PUBLIC_BASE_URL', 'https://staging.clinicforce.ai')
            monkeypatch.setenv('INSURANCE_BETA_ENABLED', 'true')
            monkeypatch.setattr(staff_api, 'PUBLIC_BASE_URL', 'http://testserver')
            monkeypatch.setattr(fixtures_api, 'date', type('FixtureDate', (), {
                'today': staticmethod(lambda: date(2026, 11, 2))}))
            password = 'Synthetic-staging-only-123'
            with staff_store._connection() as db:
                db.execute("INSERT INTO staff_members (id,username,display_name,practice_id,practice_name,role,password_hash,created_at) VALUES ('staff','fake-october','Fake Test Staff',%s,'NMCT Staging','admin',%s,'2026-10-01')", (STAGING_NMCT_PRACTICE_ID, staff_store.hash_password(password)))
            def no_external_request(*_args, **_kwargs):
                raise AssertionError('A synthetic visit must not contact eCW or a payer')
            monkeypatch.setattr(api.ecw_backend, 'fetch_inputs', no_external_request)
            monkeypatch.setattr(api, '_post_stedi_eligibility', no_external_request)
            def fake_research(facts):
                new_office = facts.get('fixture_scenario') == 'aetna_new_office_date_mismatch'
                return {'status': 'complete', 'model_id': 'synthetic-sonnet-check',
                    'findings': [{'code': '99203' if new_office else '99204',
                        'finding': 'Verify the visit-date benefit and provider tier',
                        'evidence_ids': ['synthetic-benefit-prior-date'] if new_office else []}]}
            monkeypatch.setattr(fixtures_api.insurance_research_agent, 'investigate', fake_research)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://testserver') as client:
                unsigned = await client.post('/api/staff/testing/collection-engine-scenarios',
                    headers={'Origin': 'http://testserver'})
                assert unsigned.status_code == 401
                login = await client.post('/api/staff/login',
                    headers={'Origin': 'http://testserver'},
                    json={'username': 'fake-october', 'password': password})
                assert login.status_code == 200
                research = await client.post('/api/staff/testing/collection-engine-research-check',
                                             headers={'Origin': 'http://testserver'})
                assert research.status_code == 200
                assert research.json() == {'synthetic_only': True, 'status': 'complete',
                    'finding_count': 1, 'model_id': 'synthetic-sonnet-check', 'reason': None}
                new_office_research = await client.post(
                    '/api/staff/testing/collection-engine-research-check?scenario=aetna_new_office_date_mismatch',
                    headers={'Origin': 'http://testserver'})
                assert new_office_research.status_code == 200
                assert new_office_research.json()['findings'][0]['code'] == '99203'
                assert new_office_research.json()['collect_now_cents'] is None
                assert new_office_research.json()['decision'] == 'EVIDENCE_REQUIRED'
                requirement = await client.get('/api/staff/testing/collection-engine-requirement-check')
                assert requirement.status_code == 200
                assert requirement.json()['procedures'] == [{
                    'code': '99204', 'authorization_status': 'Verification required',
                    'referral_status': 'Verification required',
                    'authorization_contact_available': True,
                    'referral_contact_available': True}]
                seeded = await client.post('/api/staff/testing/collection-engine-scenarios',
                    headers={'Origin': 'http://testserver'})
                assert seeded.status_code == 200
                cases = seeded.json()['cases']
                assert len(cases) == len(SCENARIOS)
                for row, (_, share, collect, decision) in zip(cases, SCENARIOS):
                    case_id = row['case_id']
                    response = await client.get(f'/api/doctor/insurance/commercial/cases/{case_id}/previsit',
                                                headers={'X-ClinicForce-Intake': row['intake_id']})
                    assert response.status_code == 200, (row['scenario'], response.status_code)
                    body = response.json()
                    guidance = body['collection_guidance']
                    model = guidance.get('visit_model') or {}
                    assert body['staging_synthetic_fixture'] is True
                    assert (model.get('estimated_patient_responsibility_cents'),
                            guidance['amount_cents'],
                            model.get('collection_decision_type', guidance['basis'])) == (
                                share, collect, decision), row['scenario']
                    assert row['iterations'] == len(journey(row['scenario']))
                    with patient_store._connection() as db:
                        events = db.execute("SELECT stage,snapshot_json FROM insurance_case_events WHERE case_id=%s AND stage LIKE 'synthetic-%%' ORDER BY id", (case_id,)).fetchall()
                    assert len(events) == row['iterations']
                    if row['iterations'] > 1:
                        first = json.loads(events[0]['snapshot_json'])['result']
                        assert first['fixture_iteration'] == 'eligibility_pending'
                        assert first['collection_guidance']['amount_cents'] is None
                    snapshot = json.loads(events[-1]['snapshot_json'])
                    assert snapshot['result']['collection_guidance']['amount_cents'] == collect
                repeated = await client.post('/api/staff/testing/collection-engine-scenarios',
                    headers={'Origin': 'http://testserver'})
                assert [row['case_id'] for row in repeated.json()['cases']] == [row['case_id'] for row in cases]
                assert [row['iterations'] for row in repeated.json()['cases']] == [
                    row['iterations'] for row in cases]
                with patient_store._connection() as db:
                    event_count = db.execute("SELECT count(*) AS n FROM insurance_case_events WHERE stage LIKE 'synthetic-%%'").fetchone()['n']
                assert event_count == sum(row['iterations'] for row in cases)
                due = await client.get('/api/staff/testing/collection-engine-due?days=30')
                assert due.status_code == 200
                assert due.json()['checked_count'] == len(SCENARIOS) - 8
                assert due.json()['payer_requests'] == 0
                assert {row['service_date'] for row in due.json()['visits']} == {
                    '2026-11-03', '2026-11-10'}
                due_again = await client.get('/api/staff/testing/collection-engine-due?days=30')
                assert [row['evidence_fingerprint'] for row in due_again.json()['visits']] == [
                    row['evidence_fingerprint'] for row in due.json()['visits']]
                with patient_store._connection() as db:
                    after_due = db.execute("SELECT count(*) AS n FROM insurance_case_events WHERE stage LIKE 'synthetic-%%'").fetchone()['n']
                assert after_due == event_count
                # Recreate the first expanded release's October date in one fake
                # case; reseeding must add exactly one upcoming-date revision.
                older = cases[8]
                with patient_store._connection() as db:
                    row = db.execute('SELECT workflow_json FROM insurance_cases WHERE id=%s',
                                     (older['case_id'],)).fetchone()
                    workflow = json.loads(row['workflow_json'])
                    workflow['previsit_context']['result']['service_date'] = '2026-10-01'
                    workflow['previsit_context']['inputs']['service_date'] = '2026-10-01'
                    db.execute('UPDATE insurance_cases SET workflow_json=%s WHERE id=%s',
                               (json.dumps(workflow), older['case_id']))
                migrated = await client.post('/api/staff/testing/collection-engine-scenarios',
                                             headers={'Origin': 'http://testserver'})
                assert migrated.status_code == 200
                migrated_row = migrated.json()['cases'][8]
                assert migrated_row['iterations'] == older['iterations'] + 1
                with patient_store._connection() as db:
                    events_after_migration = db.execute("SELECT count(*) AS n FROM insurance_case_events WHERE stage LIKE 'synthetic-%%'").fetchone()['n']
                assert events_after_migration == event_count + 1
                migrated_again = await client.post('/api/staff/testing/collection-engine-scenarios',
                                                   headers={'Origin': 'http://testserver'})
                assert migrated_again.json()['cases'][8]['iterations'] == migrated_row['iterations']
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.parametrize('host,flag,practice,role,code', [
    ('https://app.clinicforce.ai', 'true', STAGING_NMCT_PRACTICE_ID, 'admin', 404),
    ('https://staging.clinicforce.ai', 'false', STAGING_NMCT_PRACTICE_ID, 'admin', 404),
    ('https://staging.clinicforce.ai', 'true', 'other', 'admin', 403),
    ('https://staging.clinicforce.ai', 'true', STAGING_NMCT_PRACTICE_ID, 'staff', 403),
])
def test_fixture_route_is_scoped_to_staging_nmct_admin(monkeypatch, host, flag, practice, role, code):
    from app.api.routers.staging_collection_fixtures import _staging_only
    from fastapi import HTTPException
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', flag)
    monkeypatch.setenv('PUBLIC_BASE_URL', host)
    with pytest.raises(HTTPException) as error:
        _staging_only({'practice_id': practice, 'role': role})
    assert error.value.status_code == code
