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


def test_cms_271_networkless_part_b_terms_reach_saved_benefits():
    from app.api.routers import commercial_estimator as api

    payload = {'payerId': 'CMS', 'subscriber': {'memberId': 'SYNTHETIC-MBI',
        'dateOfBirth': '1950-01-01', 'name': {'person': {'firstName': 'Pat', 'lastName': 'Example'}}},
        'plans': [{'name': 'Original Medicare Part B', 'benefits': {
            'statuses': [{'status': 'ACTIVE_COVERAGE', 'service': {'system': 'STC', 'value': '98'}}],
            'deductible': [{'amount': '150', 'coverageLevel': 'INDIVIDUAL',
                'timePeriod': 'REMAINING', 'service': {'system': 'STC', 'value': '30'}}],
            'coInsurance': [{'percent': '0.2', 'coverageLevel': 'INDIVIDUAL',
                'service': {'system': 'STC', 'value': '98'}}]}}]}
    coverage = {'active_coverages': 1, 'relationship': '18', 'member_id': 'SYNTHETIC-MBI',
        'subscriber_first': 'Pat', 'subscriber_last': 'Example', 'subscriber_birth_date': '1950-01-01',
        'patient_first': 'Pat', 'patient_last': 'Example', 'patient_birth_date': '1950-01-01'}
    benefits = api._previsit_benefits(payload, coverage, '2026-11-16', ['98', '30'])
    assert benefits['active'] is True
    assert benefits['identity']['status'] == 'matched'
    assert benefits['plan_details']['names'] == ['Original Medicare Part B']
    assert benefits['deductible_remaining_cents'] == 15000
    assert benefits['coinsurance_percent'] == '20.0'
    assert benefits['network_status'] == 'unknown'
    commercial = api._previsit_benefits({**payload, 'payerId': 'EXAMPLE'}, coverage,
                                         '2026-11-16', ['98', '30'])
    assert commercial['deductible_remaining_cents'] is None
    assert commercial['coinsurance_percent'] is None


def test_texas_mac_cms_response_keeps_part_b_office_terms_separate_from_part_a():
    from app.api.routers import commercial_estimator as api

    service = lambda code: {'system': 'STC', 'value': code}
    payload = {'payerId': '04412', 'payer': {'name': {'organization': 'CMS'}},
        'subscriber': {'memberId': 'SYNTHETIC-MBI', 'dateOfBirth': '1950-01-01',
            'name': {'person': {'firstName': 'Pat', 'lastName': 'Example'}},
            'dates': {'service': {'start': '2026-11-19'}}},
        'plans': [{'benefits': {
            'statuses': [{'status': 'INACTIVE', 'service': service('88')},
                         {'status': 'ACTIVE_COVERAGE', 'insuranceType': 'MEDICARE_PART_A', 'service': service('30')},
                         {'status': 'ACTIVE_COVERAGE', 'insuranceType': 'MEDICARE_PART_B', 'service': service('30')},
                         {'status': 'ACTIVE_COVERAGE', 'insuranceType': 'MEDICARE_PART_B', 'service': service('98')}],
            'deductible': [{'amount': '1736', 'insuranceType': 'MEDICARE_PART_A',
                            'coverageLevel': 'INDIVIDUAL', 'timePeriod': 'REMAINING', 'service': service('30')},
                           {'amount': '283', 'insuranceType': 'MEDICARE_PART_B',
                            'coverageLevel': 'INDIVIDUAL', 'timePeriod': 'CALENDAR_YEAR', 'service': service('30')},
                           {'amount': '0', 'insuranceType': 'MEDICARE_PART_B',
                            'coverageLevel': 'INDIVIDUAL', 'timePeriod': 'REMAINING', 'service': service('30')}],
            'coInsurance': [{'percent': '0.2', 'insuranceType': 'MEDICARE_PART_B',
                             'coverageLevel': 'INDIVIDUAL', 'timePeriod': 'VISIT', 'service': service('30')}]}}]}
    coverage = {'active_coverages': 1, 'relationship': '18', 'member_id': 'SYNTHETIC-MBI',
        'subscriber_first': 'Pat', 'subscriber_last': 'Example', 'subscriber_birth_date': '1950-01-01',
        'patient_first': 'Pat', 'patient_last': 'Example', 'patient_birth_date': '1950-01-01'}
    benefits = api._previsit_benefits(payload, coverage, '2026-11-19', ['98', '30'],
        inputs={'reason': 'New patient evaluation', 'visit_type': 'new', 'service_date': '2026-11-19',
                'place_of_service': '11', 'lines': [{'code': '99203', 'units': 1}]}, source={})
    assert benefits['original_medicare'] is True
    assert benefits['active'] is True
    assert benefits['coverage_status_conflict'] is False
    assert benefits['deductible_annual_cents'] == 28300
    assert benefits['deductible_remaining_cents'] == 0
    assert benefits['coinsurance_percent'] == '20.0'
    assert benefits['network_status'] == 'unknown'
    assert not api.stedi_payers.original_medicare_271({**payload, 'payer': {'name': {'organization': 'Other'}}})


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
        coverage={}, inputs={'service_date': '2026-10-14'},
        source={'_request_ip_chain': '192.0.2.1', **(source or {})},
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


def test_current_stedi_cms_route_uses_billing_enrollment():
    payer = {**_payer('JDNSN', 'ENROLLMENT_REQUIRED'), 'primaryPayerId': 'CMS'}
    result, api = _run(payer, enrollment='LIVE')
    api.stedi_payers.eligibility_enrollment.assert_awaited_once_with('JDNSN', '2222222222', 'synthetic-key')
    api._post_stedi_eligibility.assert_awaited_once()
    assert api._post_stedi_eligibility.await_args.args[1]['X-Forwarded-For'] == '192.0.2.1'
    assert result['enrollment']['route'] == 'original_medicare_hets'
    assert insurance_product.classify([], payer_id='JDNSN') == 'original_medicare'


def test_cms_skips_paid_request_when_origin_trace_is_missing():
    result, api = _run(_payer('JDNSN', 'ENROLLMENT_REQUIRED'),
                       source={'_request_ip_chain': None}, enrollment='LIVE')
    assert result['eligibility_failure']['kind'] == 'request_trace_unavailable'
    api._post_stedi_eligibility.assert_not_awaited()


def test_live_texas_part_b_enrollment_unblocks_cms_for_same_billing_npi():
    requests = []
    def handler(request):
        requests.append(request)
        assert set(request.url.params.get_list('payerIds')) == {'JDNSN', 'TPTWE'}
        return httpx.Response(200, json={'items': [
            {'status': 'LIVE', 'payer': {'stediPayerId': 'TPTWE'},
             'provider': {'npi': '0000000000'},
             'transactions': {'eligibilityCheck': {'enroll': True}}},
            {'status': 'LIVE', 'payer': {'stediPayerId': 'TPTWE'},
             'provider': {'npi': '2222222222'},
             'transactions': {'eligibilityCheck': {'enroll': True}}}]})
    enrollment = asyncio.run(stedi_payers.eligibility_enrollment('JDNSN', '2222222222',
        'synthetic-key', transport=httpx.MockTransport(handler)))
    assert enrollment == {'status': 'LIVE', 'reason_code': 'enrollment_live'}
    assert len(requests) == 1


def test_recorded_texas_part_b_id_routes_to_cms_but_advantage_does_not():
    def handler(request):
        return httpx.Response(200, json={'items': [{'payer': {'stediId': 'JDNSN',
            'primaryPayerId': 'CMS', 'displayName': 'National Centers for Medicare & Medicaid Services',
            'coverageTypes': ['medical'],
            'transactionSupport': {'eligibilityCheck': 'ENROLLMENT_REQUIRED'}}}]})
    route = asyncio.run(stedi_payers.search_payer('Medicare Texas Part B', ['04412'],
        'synthetic-key', plan='MB', transport=httpx.MockTransport(handler)))
    assert route['stediId'] == 'JDNSN'
    assert stedi_payers.original_medicare_route({'stediId': 'TPTWE'})
    assert insurance_product.classify([], payer_id='TPTWE') == 'original_medicare'


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
