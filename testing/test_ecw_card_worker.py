"""Synthetic background refresh checks; no real EHR, S3, or patient data."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.api.services import ecw_card_worker as worker


def test_success_publishes_protected_bytes_and_cleans_replaced_object():
    job = {'practice_id': 'synthetic-practice', 'ecw_patient_id': 'synthetic-patient', 'lease_until': 'synthetic-lease'}
    rows = [{'id': 'card:0', 'title': 'eCW insurance card', 'content_type': 'application/pdf', '_content': b'%PDF synthetic'}]
    with patch.object(worker.ecw_backend, 'insurance_cards', AsyncMock(return_value=rows)) as lookup, \
         patch.object(worker.object_store, 'put_insurance_evidence', return_value='new-private-key') as save, \
         patch.object(worker.ecw_card_refresh, 'complete', return_value=['old-private-key']) as complete, \
         patch.object(worker.object_store, 'delete_object') as delete:
        asyncio.run(worker._refresh(job))
    lookup.assert_awaited_once_with('synthetic-patient', include_bytes=True)
    assert save.call_args.args[1:] == ('card.pdf', b'%PDF synthetic', 'application/pdf')
    published = complete.call_args.args[1]
    assert published == [{'id': 'card:0', 'title': 'eCW insurance card',
                          'content_type': 'application/pdf', 'object_key': 'new-private-key'}]
    delete.assert_called_once_with('old-private-key')


def test_partial_object_write_is_not_published_and_is_cleaned_up():
    job = {'practice_id': 'synthetic-practice', 'ecw_patient_id': 'synthetic-patient', 'lease_until': 'synthetic-lease'}
    rows = [{'id': f'card:{index}', 'title': 'eCW insurance card',
             'content_type': 'application/pdf', '_content': b'%PDF synthetic'} for index in range(2)]
    with patch.object(worker.ecw_backend, 'insurance_cards', AsyncMock(return_value=rows)), \
         patch.object(worker.object_store, 'put_insurance_evidence', side_effect=['first-private-key', RuntimeError('synthetic failure')]), \
         patch.object(worker.ecw_card_refresh, 'complete', return_value=['first-private-key']) as complete, \
         patch.object(worker.object_store, 'delete_object') as delete:
        asyncio.run(worker._refresh(job))
    assert complete.call_args.kwargs == {'failed': True}
    assert complete.call_args.args[1][0]['object_key'] == 'first-private-key'
    delete.assert_called_once_with('first-private-key')
