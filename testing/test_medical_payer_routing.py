"""CMS HETS enrollment is scoped to Original Medicare, never issuer MA routes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.api.routers import commercial_estimator  # noqa: F401
from app.api.routers.commercial_flow.commercial_previsit_submit import submit_previsit_eligibility
from app.api.routers.commercial_flow.commercial_previsit_status import _enrollment_failure
from app.insurance import insurance_product, stedi_payers


def _run(payer: dict, *, source: dict | None = None, enrollment: str = 'MISSING') -> tuple[dict, SimpleNamespace]:
    result = {'issues': [], 'payer': payer.get('displayName')}
    case = {'id': 'synthetic-case'}
    async def thread(fn, *args):
        return fn(*args)
    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
    async def pending(*_):
        raise httpx.ReadTimeout('synthetic interruption before any payer request')
    api = SimpleNamespace(stedi_payers=stedi_payers,
        run_in_threadpool=thread, _save=lambda value, *_: value,
        _finish_previsit=lambda *_: None, _previsit_benefits=lambda *_: {'active': None},
        _start_previsit_recovery=AsyncMock(return_value=result),
        _enrollment_failure=_enrollment_failure,
        collection_event=lambda *_, **__: None,
        _post_stedi_eligibility=AsyncMock(side_effect=pending),
        httpx=SimpleNamespace(AsyncClient=lambda **_: FakeClient(), Timeout=lambda *_ , **__: None,
                              HTTPError=httpx.HTTPError),
        stedi_request_body=lambda *_: {})
    enrollment_call = AsyncMock(return_value={'status': enrollment,
        'reason_code': 'enrollment_missing' if enrollment == 'MISSING' else 'enrollment_live'})
    api.stedi_payers = SimpleNamespace(**{name: getattr(stedi_payers, name) for name in
        ('original_medicare_route', 'original_medicare_routing_conflict', 'enrollment_required')},
        eligibility_enrollment=enrollment_call)
    provider = {'rendering_npi': '1111111111', 'rendering_first': 'Example',
                'rendering_last': 'Doctor', 'billing_npi': '2222222222'}
    asyncio.run(submit_previsit_eligibility(api, payer=payer, provider=provider,
        api_key='synthetic-key', session={'practice_id': 'synthetic-practice',
            'intake': {'id': 'synthetic-intake'}}, case=case, result=result,
        coverage={}, inputs={'service_date': '2026-10-14'}, source=source or {},
        background_tasks=None, requested_stc=['98'], body={}, basic_body={}))
    return result, api


def _payer(route: str, support: str = 'SUPPORTED') -> dict:
    return {'stediId': route, 'displayName': route,
            'transactionSupport': {'eligibilityCheck': support}}


def test_original_medicare_hets_uses_billing_npi_and_blocks_incomplete_attestation():
    result, api = _run(_payer('CMS'))
    api.stedi_payers.eligibility_enrollment.assert_awaited_once_with('CMS', '2222222222', 'synthetic-key')
    api._post_stedi_eligibility.assert_not_awaited()
    assert result['enrollment']['route'] == 'original_medicare_hets'
    assert 'CMS HETS' in result['eligibility_failure']['detail']
    assert 'attestation' in result['eligibility_failure']['action']


def test_enrolled_original_medicare_enters_normal_eligibility_submission():
    _, api = _run(_payer('CMS'), enrollment='LIVE')
    api.stedi_payers.eligibility_enrollment.assert_awaited_once_with('CMS', '2222222222', 'synthetic-key')
    api._post_stedi_eligibility.assert_awaited_once()


@pytest.mark.parametrize('route,plan', [
    ('KMQTZ', 'RPPO-UHC Complete Care Support TX-1A'),
    ('87726', 'UnitedHealthcare Group Medicare Advantage PPO'),
    ('87726', 'UnitedHealthcare Medicare Advantage HMO'),
    ('87726', 'UnitedHealthcare Medicare Advantage D-SNP'),
    ('QDTRP', 'BCBS Texas PPO'),
    ('OTHERMA', 'Other Medicare Advantage HMO'),
    ('OTHERCOMM', 'Other commercial PPO'),
])
def test_other_payer_routes_do_not_inherit_cms_hets_enrollment(route: str, plan: str):
    result, api = _run(_payer(route), source={'plan': plan, 'payer': route})
    api.stedi_payers.eligibility_enrollment.assert_not_awaited()
    api._post_stedi_eligibility.assert_awaited_once()
    assert not result.get('eligibility_failure')


def test_other_payer_enrollment_uses_its_own_route_and_provider():
    result, api = _run(_payer('OTHERMA', 'ENROLLMENT_REQUIRED'),
                       source={'plan': 'Other Medicare Advantage HMO'})
    api.stedi_payers.eligibility_enrollment.assert_awaited_once_with(
        'OTHERMA', '1111111111', 'synthetic-key')
    assert result['enrollment']['route'] == 'payer_specific_eligibility'


def test_stale_part_b_code_cannot_send_known_ma_issuer_to_hets():
    assert stedi_payers.original_medicare_routing_conflict(_payer('CMS'),
        product='RPPO-UHC Complete Care Support TX-1A', issuer='UnitedHealthcare')
    result, api = _run(_payer('CMS'), source={'plan': 'RPPO-UHC Complete Care Support TX-1A',
                                            'payer': 'UnitedHealthcare'})
    assert result['eligibility_failure']['kind'] == 'payer_routing_unresolved'
    api.stedi_payers.eligibility_enrollment.assert_not_awaited()
    api._post_stedi_eligibility.assert_not_awaited()


def test_group_hmo_dsnp_and_original_medicare_product_classification():
    assert insurance_product.classify(['RPPO-UHC Complete Care Support TX-1A']) == 'medicare_advantage_regional_ppo'
    assert insurance_product.classify(['UnitedHealthcare Group Medicare Advantage PPO']) == 'medicare_advantage_group_ppo'
    assert insurance_product.classify(['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP']) == 'medicare_advantage_group_ppo'
    assert insurance_product.classify(['UnitedHealthcare Medicare Advantage HMO']) == 'medicare_advantage_hmo'
    assert insurance_product.classify(['UnitedHealthcare Medicare Advantage D-SNP']) == 'medicare_advantage_dsnp'
    assert insurance_product.classify([], payer_id='CMS') == 'original_medicare'
