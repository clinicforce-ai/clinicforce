"""Clinic approval is the only way to attach a defer-to-claim instruction."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routers import commercial_estimator as api
from app.insurance import commercial_store


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
