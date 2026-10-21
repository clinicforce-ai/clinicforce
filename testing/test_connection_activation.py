"""Draft payer activation needs scoped participation evidence, not a duplicate route."""
import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import psycopg
from fastapi import HTTPException
from pydantic import ValidationError
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api.routers import commercial_estimator as api
from app.core import patient_store
from app.insurance import commercial_store
from app.insurance.commercial import CommercialError


def test_activation_route_requires_admin_and_reference(monkeypatch):
    with pytest.raises(ValidationError):
        api.ConnectionActivationInput(reference='', service_type_code='98')
    value = api.ConnectionActivationInput(reference='Signed product scope reviewed', service_type_code='98')
    monkeypatch.setattr(api, '_session', AsyncMock(return_value={
        'practice_id': 'clinic', 'staff': {'id': 'clinician', 'role': 'staff'}}))
    with patch.object(commercial_store, 'activate_connection') as activate, pytest.raises(HTTPException) as denied:
        asyncio.run(api.activate_connection('payer', value, MagicMock()))
    assert denied.value.status_code == 403
    activate.assert_not_called()


def test_alias_update_route_requires_admin_and_reference(monkeypatch):
    with pytest.raises(ValidationError):
        api.ConnectionAliasInput(reference='short', aliases=['United Healthcare'])
    value = api.ConnectionAliasInput(reference='Approved eCW payer mapping', aliases=['United Healthcare'])
    monkeypatch.setattr(api, '_session', AsyncMock(return_value={
        'practice_id': 'clinic', 'staff': {'id': 'clinician', 'role': 'staff'}}))
    with patch.object(commercial_store, 'add_connection_aliases') as update, pytest.raises(HTTPException) as denied:
        asyncio.run(api.add_connection_aliases('payer', value, MagicMock()))
    assert denied.value.status_code == 403
    update.assert_not_called()


def test_activation_can_add_unique_ecw_payer_aliases_without_guessing_product():
    row = {'status': 'draft', 'category': 'commercial_medical', 'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
           'aliases_json': json.dumps([]), 'rules_json': json.dumps({})}
    scoped = {'plan_matcher': 'Choice Plus', 'profile_plan_matcher': 'Choice Plus',
              'evidence_reference': 'payer-portal:synthetic-product-scope'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    second = MagicMock(); second.fetchall.return_value = [scoped]
    third = MagicMock(); third.fetchall.return_value = []
    db.execute.side_effect = [first, second, MagicMock(), third, MagicMock()]
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer', 'status': 'staff_guidance_enabled'}):
        commercial_store.activate_connection('clinic', 'payer', '98',
            'Signed product scope reviewed', 'administrator', aliases=['United Healthcare'])
    update = db.execute.call_args_list[-1]
    assert 'aliases_json' in update.args[0]
    assert json.loads(update.args[1][1]) == ['United Healthcare']


def test_activation_rejects_alias_owned_by_another_enabled_route():
    row = {'status': 'draft', 'category': 'commercial_medical', 'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
           'aliases_json': '[]', 'rules_json': '{}'}
    scoped = {'plan_matcher': 'Choice Plus', 'profile_plan_matcher': 'Choice Plus',
              'evidence_reference': 'payer-portal:synthetic-product-scope'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    second = MagicMock(); second.fetchall.return_value = [scoped]
    third = MagicMock(); third.fetchall.return_value = [
        {'display_name': 'Example Payer', 'aliases_json': '["United Health Care"]'}]
    db.execute.side_effect = [first, second, MagicMock(), third]
    with patch.object(commercial_store, '_connection', return_value=db), \
         pytest.raises(CommercialError, match='another enabled payer route'):
        commercial_store.activate_connection('clinic', 'payer', '98',
            'Signed product scope reviewed', 'administrator', aliases=['United Healthcare'])
    assert db.execute.call_count == 4


def test_enabled_route_accepts_later_alias_without_changing_activation():
    row = {'status': 'staff_guidance_enabled', 'category': 'commercial_medical',
           'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
           'aliases_json': '["United Healthcare"]',
           'rules_json': '{"activation":{"approved_by":"first-admin"}}'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    others = MagicMock(); others.fetchall.return_value = []
    db.execute.side_effect = [first, MagicMock(), others, MagicMock()]
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer'}):
        commercial_store.add_connection_aliases('clinic', 'payer',
            ['UNITED HEALTH CARE', 'UHC Choice Plus'], 'Payer label reviewed in eCW', 'second-admin')
    update = db.execute.call_args_list[-1]
    assert json.loads(update.args[1][0]) == ['United Healthcare', 'UHC Choice Plus']
    rules = json.loads(update.args[1][1])
    assert rules['activation']['approved_by'] == 'first-admin'
    assert rules['alias_update']['approved_by'] == 'second-admin'


def test_enabled_route_rejects_alias_owned_by_another_enabled_route():
    row = {'status': 'staff_guidance_enabled', 'category': 'commercial_medical',
           'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
           'aliases_json': '[]', 'rules_json': '{}'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    others = MagicMock(); others.fetchall.return_value = [
        {'display_name': 'Other Payer', 'aliases_json': '["UHC Choice Plus"]'}]
    db.execute.side_effect = [first, MagicMock(), others]
    with patch.object(commercial_store, '_connection', return_value=db), \
         pytest.raises(CommercialError, match='another enabled payer route'):
        commercial_store.add_connection_aliases('clinic', 'payer',
            ['UHC Choice Plus'], 'Payer label reviewed in eCW', 'administrator')
    assert db.execute.call_count == 3


def test_enabled_route_rejects_alias_without_a_mappable_name():
    row = {'status': 'staff_guidance_enabled', 'category': 'commercial_medical',
           'display_name': 'UnitedHealthcare', 'aliases_json': '[]', 'rules_json': '{}'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    db.execute.side_effect = [first]
    with patch.object(commercial_store, '_connection', return_value=db), \
         pytest.raises(CommercialError, match='letters or numbers'):
        commercial_store.add_connection_aliases('clinic', 'payer',
            ['---'], 'Payer label reviewed in eCW', 'administrator')
    assert db.execute.call_count == 1


def test_activation_requires_uhc_product_scoped_network_and_profile():
    row = {'status': 'draft', 'category': 'commercial_medical', 'display_name': 'UnitedHealthcare',
           'rules_json': json.dumps({'future_dos_current_month': True})}
    scoped = {'plan_matcher': 'Choice Plus', 'profile_plan_matcher': 'Choice Plus',
              'evidence_reference': 'library:signed-choice-plus'}
    db = MagicMock(spec=['execute', '__enter__', '__exit__'])
    db.__enter__.return_value = db
    first = MagicMock(); first.fetchone.return_value = row
    second = MagicMock(); second.fetchall.return_value = [scoped]
    third = MagicMock(); third.fetchall.return_value = []
    db.execute.side_effect = [first, second, MagicMock(), third, MagicMock()]
    with patch.object(commercial_store, '_connection', return_value=db), \
         patch.object(commercial_store, 'connection', return_value={'id': 'payer', 'status': 'staff_guidance_enabled'}):
        result = commercial_store.activate_connection('clinic', 'payer', '98',
                                                       'Signed product scope reviewed', 'administrator')
    assert result['status'] == 'staff_guidance_enabled'
    queries = db.execute.call_args_list
    assert 'practice_id=%s FOR UPDATE' in queries[0].args[0]
    assert queries[0].args[1] == ('payer', 'clinic')
    assert "n.status='verified'" in queries[1].args[0]
    assert "p.status='staff_guidance_enabled'" in queries[1].args[0]
    assert queries[1].args[1] == ('clinic', 'payer')
    stored = json.loads(queries[4].args[1][0])
    assert stored['future_dos_current_month'] is True
    assert stored['service_type_code'] == '98'
    assert stored['activation']['approved_by'] == 'administrator'
    assert stored['activation']['reference'] == 'Signed product scope reviewed'

    for links in ([], [{**scoped, 'plan_matcher': ''}], [{**scoped, 'plan_matcher': 'PPO'}],
                  [{**scoped, 'profile_plan_matcher': 'Other product'}]):
        db.execute.reset_mock(side_effect=True)
        first = MagicMock(); first.fetchone.return_value = row
        second = MagicMock(); second.fetchall.return_value = links
        db.execute.side_effect = [first, second]
        with patch.object(commercial_store, '_connection', return_value=db), \
             pytest.raises(CommercialError):
            commercial_store.activate_connection('clinic', 'payer', '98',
                                                 'Signed product scope reviewed', 'administrator')
        assert db.execute.call_count == 2


def test_new_connection_cannot_start_enabled_without_scoped_setup():
    with pytest.raises(CommercialError, match='draft'):
        commercial_store.create_connection('clinic', {'display_name': 'UnitedHealthcare',
            'stedi_payer_id': 'route', 'status': 'staff_guidance_enabled'})


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_draft_route_activates_in_place_with_scoped_product_and_no_rate():
    url = os.environ['STAFF_TEST_DATABASE_URL']
    schema = 'activation_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        scoped_url = make_conninfo(url, options=f'-csearch_path={schema}')
        with patch.object(patient_store, 'DATABASE_URL', scoped_url), \
             patch.object(patient_store, '_schema_initialized', False):
            payer = commercial_store.create_connection('clinic', {
                'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)',
                'stedi_payer_id': 'synthetic-route'})
            network = commercial_store.create_network('clinic', {
                'payer_connection_id': payer['id'], 'billing_npi': '1111111111',
                'rendering_npi': '2222222222', 'organization_name': 'Example Clinic',
                'tin': '123456789', 'location_id': 'office', 'place_of_service': '11',
                'network_name': 'Choice Plus Network', 'plan_matcher': 'Choice Plus',
                'effective_from': '2026-01-01', 'status': 'verified',
                'evidence_reference': 'payer-portal:synthetic-product-scope'})
            commercial_store.create_profile('clinic', {
                'payer_connection_id': payer['id'], 'provider_network_id': network['id'],
                'service_category': 'office', 'plan_matcher': 'Choice Plus',
                'status': 'staff_guidance_enabled', 'rules': {'cost_share': 'payer_returned'}})
            activated = commercial_store.activate_connection(
                'clinic', payer['id'], '98', 'Synthetic administrator approval', 'admin',
                aliases=['United Healthcare'])
            assert activated['id'] == payer['id']
            assert activated['status'] == 'staff_guidance_enabled'
            assert activated['rules']['service_type_code'] == '98'
            assert activated['rules']['activation']['approved_by'] == 'admin'
            assert activated['aliases'] == ['United Healthcare']
            assert api._ecw_practice_config('clinic')['payer_aliases']['unitedhealthcare'] == payer['id']
            revised = commercial_store.add_connection_aliases(
                'clinic', payer['id'], ['UHC Choice Plus'],
                'Synthetic alias approval', 'admin')
            assert revised['aliases'] == ['United Healthcare', 'UHC Choice Plus']
            assert revised['rules']['activation'] == activated['rules']['activation']
            assert api._ecw_practice_config('clinic')['payer_aliases']['uhcchoiceplus'] == payer['id']
            assert len(commercial_store.list_connections('clinic')) == 1
            assert commercial_store.list_rate_versions('clinic') == []
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
