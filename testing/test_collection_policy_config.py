"""Clinic approval is the only way to attach a defer-to-claim instruction."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routers import commercial_estimator as api
from app.insurance import commercial_store
from app.core import patient_store


def test_admin_route_requires_a_policy_reference_and_admin_role(monkeypatch):
    with pytest.raises(ValidationError):
        api.CollectionPolicyInput(mode='defer_to_claim', reference='')
    assert api.CollectionPolicyInput(mode='collect_supported_advance',
        reference='Clinic policy approved 2026').mode == 'collect_supported_advance'
    assert 'collect_supported_advance' in api.CollectionPolicyInput.model_json_schema()['properties']['mode']['enum']
    value = api.CollectionPolicyInput(mode='defer_to_claim', reference='Clinic policy approved 2026')
    monkeypatch.setattr(api, '_session', AsyncMock(return_value={
        'practice_id': 'clinic', 'staff': {'id': 'clinician', 'role': 'staff'}}))
    with patch.object(commercial_store, 'set_collection_policy') as save, pytest.raises(HTTPException) as denied:
        asyncio.run(api.set_collection_policy('connection', value, MagicMock()))
    assert denied.value.status_code == 403
    save.assert_not_called()


def test_scoped_policy_update_stamps_actor_and_removal_preserves_other_rules():
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    db.execute.return_value.fetchone.return_value = {'rules_json': json.dumps({'cost_share': 'payer_returned'})}
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer'}) as read:
        commercial_store.set_collection_policy('clinic', 'payer', 'defer_to_claim',
                                               'Clinic policy approved 2026', 'administrator',
                                               allow_supported_oon_advance=True)
    queries = db.execute.call_args_list
    assert 'practice_id=%s FOR UPDATE' in queries[0].args[0]
    assert queries[0].args[1] == ('payer', 'clinic')
    stored = json.loads(queries[1].args[1][0])
    assert stored['cost_share'] == 'payer_returned'
    assert stored['collection_policy']['approved_by'] == 'administrator'
    assert stored['collection_policy']['mode'] == 'defer_to_claim'
    assert stored['collection_policy']['allow_supported_oon_advance'] is True
    read.assert_called_once_with('clinic', 'payer')
    db.reset_mock()
    db.execute.return_value.fetchone.return_value = {'rules_json': json.dumps(stored)}
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer'}):
        commercial_store.set_collection_policy('clinic', 'payer', 'disabled',
                                               'Clinic policy withdrawn 2026', 'administrator')
    removed = json.loads(db.execute.call_args_list[1].args[1][0])
    assert removed == {'cost_share': 'payer_returned'}


def test_visit_policy_requires_exact_scope_and_writes_approved_version():
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    db.execute.return_value.fetchone.side_effect = [
        {'rules_json': json.dumps({'collection_policy': {'mode': 'collect_verified_copay'}})},
        {'next_version': 1}]
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer'}):
        commercial_store.set_collection_policy('clinic', 'payer', 'collect_visit_model',
            'Synthetic clinic approval', 'administrator', product_binding='synthetic-product',
            network_tier='in_network', service_groups=['specialist_office', 'emg_ncs'],
            effective_from='2026-01-01', effective_to='2026-12-31',
            allow_benchmark_advance=True, max_cents=20000,
            disclosure='Synthetic check-in estimate.')
    calls = db.execute.call_args_list
    assert 'commercial_collection_policy_versions' in calls[2].args[0]
    assert calls[2].args[1][3] == 1
    stored = json.loads(calls[3].args[1][0])
    policy = stored['visit_collection_policies'][0]
    assert stored['collection_policy']['mode'] == 'collect_verified_copay'
    assert policy['allow_benchmark_advance'] is True
    assert policy['service_groups'] == ['emg_ncs', 'specialist_office']
    with pytest.raises(commercial_store.CommercialError):
        commercial_store.set_collection_policy('clinic', 'payer', 'collect_visit_model',
            'Synthetic clinic approval', 'administrator', product_binding='synthetic-product',
            network_tier='in_network', service_groups=['emg_ncs'],
            effective_from='2026-01-01', effective_to='2026-12-31', max_cents=None)


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_visit_policies_persist_as_separate_scoped_versions():
    url = os.environ['STAFF_TEST_DATABASE_URL']
    schema = 'collection_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        scoped = make_conninfo(url, options=f'-csearch_path={schema}')
        with patch.object(patient_store, 'DATABASE_URL', scoped), \
             patch.object(patient_store, '_schema_initialized', False):
            payer = commercial_store.create_connection('clinic', {
                'display_name': 'Synthetic Commercial', 'stedi_payer_id': 'synthetic-route'})
            for groups in (['specialist_office'], ['specialist_office', 'emg_ncs']):
                commercial_store.set_collection_policy('clinic', payer['id'], 'collect_visit_model',
                    'Synthetic test approval', 'test-admin', product_binding='synthetic-product',
                    network_tier='in_network', service_groups=groups,
                    effective_from='2026-01-01', effective_to='2026-12-31',
                    allow_benchmark_advance=True, max_cents=20000,
                    disclosure='Synthetic check-in estimate.')
            stored = commercial_store.connection('clinic', payer['id'])['rules']
            assert len(stored['visit_collection_policies']) == 2
            with commercial_store._connection() as db:
                count = db.execute('SELECT count(*) AS total FROM commercial_collection_policy_versions').fetchone()
            assert count['total'] == 2
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
