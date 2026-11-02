"""October-inspired fake patients through PostgreSQL and the saved FastAPI route."""
import json
import os
from uuid import uuid4
from unittest.mock import patch

import httpx
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.core import patient_store, staff_store
from app.insurance import collection_guidance, store, visit_collection_model
from app.insurance.staging_synthetic import SCENARIOS, visit
from app.api.routers.staging_collection_fixtures import STAGING_NMCT_PRACTICE_ID


@pytest.mark.parametrize('name,share,collect,decision', SCENARIOS)
def test_october_inspired_fake_visit(name, share, collect, decision):
    result = visit(name)
    modeled = visit_collection_model.build(result)
    assert (modeled['estimated_patient_responsibility_cents'], modeled['collect_now_cents'],
            modeled['collection_decision_type']) == (share, collect, decision)
    assert result['benefits']['identity']['binding'].startswith('synthetic')
    if name == 'missing_benchmark_cpt':
        assert [row['code'] for row in modeled['price_acquisition']] == ['95886']
    if name == 'specialist_271_conflict':
        assert modeled['service_groups'][0]['benefit_evidence'] == ['synthetic-specialist-98']
        assert modeled['service_groups'][0]['estimated_patient_share_cents'] == 8000


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_fake_patients_seed_save_reload_and_fastapi(monkeypatch):
    import asyncio
    asyncio.run(_run_fake_patients_seed_save_reload_and_fastapi(monkeypatch))


async def _run_fake_patients_seed_save_reload_and_fastapi(monkeypatch):
    """The staging-only admin action creates eight fake NMCT cases and HTTP reads."""
    from app.api.application import app
    from app.api.routers import commercial_estimator as api, staff as staff_api

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
            password = 'Synthetic-staging-only-123'
            with staff_store._connection() as db:
                db.execute("INSERT INTO staff_members (id,username,display_name,practice_id,practice_name,role,password_hash,created_at) VALUES ('staff','fake-october','Fake Test Staff',%s,'NMCT Staging','admin',%s,'2026-10-01')", (STAGING_NMCT_PRACTICE_ID, staff_store.hash_password(password)))
            def no_external_request(*_args, **_kwargs):
                raise AssertionError('A synthetic visit must not contact eCW or a payer')
            monkeypatch.setattr(api.ecw_backend, 'fetch_inputs', no_external_request)
            monkeypatch.setattr(api, '_post_stedi_eligibility', no_external_request)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://testserver') as client:
                unsigned = await client.post('/api/staff/testing/collection-engine-scenarios',
                    headers={'Origin': 'http://testserver'})
                assert unsigned.status_code == 401
                login = await client.post('/api/staff/login',
                    headers={'Origin': 'http://testserver'},
                    json={'username': 'fake-october', 'password': password})
                assert login.status_code == 200
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
                    model = body['collection_guidance']['visit_model']
                    assert body['staging_synthetic_fixture'] is True
                    assert (model['estimated_patient_responsibility_cents'], model['collect_now_cents'],
                            model['collection_decision_type']) == (share, collect, decision), (
                                row['scenario'], model['remaining_actions'])
                    assert body['collection_guidance']['amount_cents'] == collect
                    with patient_store._connection() as db:
                        event = db.execute("SELECT snapshot_json FROM insurance_case_events WHERE case_id=%s AND stage='synthetic-visit-estimated'", (case_id,)).fetchone()
                    snapshot = json.loads(event['snapshot_json'])
                    assert snapshot['result']['collection_guidance']['visit_model']['collect_now_cents'] == collect
                repeated = await client.post('/api/staff/testing/collection-engine-scenarios',
                    headers={'Origin': 'http://testserver'})
                assert [row['case_id'] for row in repeated.json()['cases']] == [row['case_id'] for row in cases]
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
