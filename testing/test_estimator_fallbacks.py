"""Synthetic regressions for service selection and evidence-backed advances."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.api.routers import commercial_estimator as api
from app.insurance import commercial, collection_guidance, cpt_suggester, stedi_payers, visit_estimate
from testing.synthetic_policy import COPAY_POLICY


def benefit(kind, amount, label, stc='98', pos='11'):
    return {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'},
            'service': {'system': 'STC', 'value': stc}, 'messages': [label],
            'placesOfService': [{'code': pos}], 'timePeriod': 'VISIT',
            'amount' if kind == 'coPayment' else 'percent': str(amount)}


def payload():
    return {'plans': [{'benefits': {'statuses': [{'status': 'ACTIVE_COVERAGE',
        'service': {'system': 'STC', 'value': '30'}}]}}]}


def normalize(raw, code='99203'):
    return commercial.normalize_stedi_271(raw, visit_estimate.service_type(code, {}), False,
        procedure_code=code, place_of_service='11', office_service_types=['BQ', 'BY', '98'])


def test_specialist_wins_over_general_and_pcp_but_conflicting_specialists_stay_unresolved():
    raw = payload(); b = raw['plans'][0]['benefits']
    b['coPayment'] = [benefit('coPayment', 30, ''), benefit('coPayment', 20, 'PCP'),
                      benefit('coPayment', 50, 'PHY VISIT - SPECIALIST')]
    b['coInsurance'] = [benefit('coInsurance', 0, 'PHY VISIT - SPECIALIST')]
    terms = normalize(raw)
    assert terms['copay_cents'] == 5000
    assert not any('conditional benefit' in r for r in terms['review_reasons'])
    r = {'collection_policy': COPAY_POLICY, 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}, 'benefits': {'active': True}, 'price_range': {'mode': 'services', 'lines': [
        {'code': '99203', 'units': 1, 'benefits': terms}]}}
    guidance = collection_guidance.build(r)
    assert guidance['amount_cents'] is None
    r['benefits']['network_status'] = 'in_network'
    r['benefits']['network_source'] = 'clinic_library_contract'
    guidance = collection_guidance.build(r)
    assert guidance['amount_cents'] == 5000 and guidance['basis'] == 'office_copay'
    b['coPayment'].append(benefit('coPayment', 70, 'PHY VISIT - SPECIALIST'))
    assert normalize(raw)['copay_cents'] is None


def test_diagnostic_terms_are_not_blocked_by_sleep_and_xray_in_same_category():
    raw = payload(); b = raw['plans'][0]['benefits']
    b['coInsurance'] = [benefit('coInsurance', '.2', 'DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL'),
        benefit('coInsurance', '.3', 'SLEEP STUDY - PROFESSIONAL', '73'),
        benefit('coInsurance', '.4', 'XRAY - PROFESSIONAL', '73')]
    for code in ('95907', '95886'):
        terms = normalize(raw, code)
        assert terms['coinsurance_percent'] == '20.0' and terms['resolved_service_type'] == '98'
    b['coInsurance'] = b['coInsurance'][1:]
    assert normalize(raw, '95907')['coinsurance_percent'] is None


def test_combined_booking_survives_without_llm_and_sp_abbreviation_is_clinic_scoped():
    inputs = {'visit_type': 'established', 'appointment_reason': 'SP',
              'reason': 'Reason: SP; Visit type: Follow Up'}
    available = [*api._previsit_codes(inputs), *[{'id': c, 'code': c, 'units': 1, 'source': 'CPT planning catalog'} for c in ('11104', '11105', '95907', '95910', '95886')],
                 {'id': '62270', 'code': '62270', 'units': 1, 'source': 'CPT planning catalog'}]
    with patch.object(cpt_suggester.boto3, 'client', side_effect=AssertionError('No model needed')):
        plan = cpt_suggester.recommend_visit_plan(inputs, {'spinal_tap_abbreviation': True}, available)
        rows = {row['id']: row['code'] for row in available}
        assert [rows[key] for key in plan['recommended_ids']] == ['62270']
        inputs.update(reason='Follow up; skin biopsy; 3 sites; EMG', appointment_reason='')
        plan = cpt_suggester.recommend_visit_plan(inputs, {}, available)
        assert set(rows[key] for key in plan['recommended_ids']) == {'11104', '11105'}
        assert plan['recommended_units'][next(k for k in plan['recommended_ids'] if rows[k] == '11105')] == 2
    assert not visit_estimate.is_spinal_tap({'appointment_reason': 'SP'})
    assert not visit_estimate.is_spinal_tap({'reason': 'Review spinal tap results'})
    assert not visit_estimate.is_emg_visit({'reason': 'Follow up; consider EMG'})


def test_pending_coverage_and_appointment_window_use_later_opening_date():
    today = datetime.now(ZoneInfo('America/New_York')).date()
    visit = (today + timedelta(days=2)).isoformat()
    opening = (today + timedelta(days=1)).isoformat()
    assert api._eligibility_timing(visit, opening)['check_from'] == opening
    assert api._eligibility_timing(visit, today.isoformat()) is None
    far = (today + timedelta(days=20)).isoformat()
    assert api._eligibility_timing(far, opening)['check_from'] == (today + timedelta(days=13)).isoformat()


def test_secondary_policy_can_be_queried_without_claiming_it_is_only_coverage():
    coverage = {k: 'value' for k in ('member_id', 'subscriber_first', 'subscriber_last',
        'subscriber_birth_date', 'patient_first', 'patient_last', 'patient_birth_date', 'coverage_id')}
    coverage.update(relationship='18', active_coverages=2)
    provider = {'billing_npi': '1234567890', 'organization_name': 'Clinic'}
    assert api.stedi_request_body({'stedi_payer_id': 'payer'}, provider, coverage, '2026-10-01', '98')['subscriber']['memberId'] == 'value'
    coverage['coverage_id'] = ''
    with pytest.raises(commercial.CommercialError):
        api.stedi_request_body({'stedi_payer_id': 'payer'}, provider, coverage, '2026-10-01', '98')
    r = {'benefits': {'active': True, 'has_secondary': True}, 'arrival_collection': {'amount_cents': 10000}}
    g = collection_guidance.build(r)
    assert g['amount_cents'] is None and g['basis'] == 'missing_collection_inputs'
    assert 'estimate is not available yet' in g['patient_message']


def test_original_medicare_routes_to_verified_cms_directory_entry_only():
    queries = []
    def handler(request):
        queries.append(request.url.params['query'])
        return httpx.Response(200, json={'items': [{'payer': {'stediId': 'cms-example', 'primaryPayerId': 'CMSMED',
            'displayName': 'CMS', 'coverageTypes': ['medical'], 'transactionSupport': {'eligibilityCheck': 'SUPPORTED'}}}]})
    transport = httpx.MockTransport(handler)
    result = asyncio.run(stedi_payers.search_payer('Medicare of Texas', [], 'test', plan='MB', transport=transport))
    assert result['stediId'] == 'cms-example' and queries == ['CMSMED']
    assert asyncio.run(stedi_payers.search_payer('Medicare Advantage', [], 'test', plan='MB', transport=transport)) is None


def test_ecw_payer_id_is_searched_before_an_ambiguous_name():
    queries = []
    payer = {'stediId': 'HUMAN', 'primaryPayerId': '61101', 'displayName': 'Humana',
             'coverageTypes': ['medical'], 'transactionSupport': {'eligibilityCheck': 'SUPPORTED'}}
    def handler(request):
        queries.append(request.url.params['query'])
        return httpx.Response(200, json={'items': [{'payer': payer}]})
    found = asyncio.run(stedi_payers.search_payer('Humana Health Plan', ['61101'], 'test',
                                                  transport=httpx.MockTransport(handler)))
    assert found == payer and queries == ['61101']
    other = {**payer, 'stediId': 'OTHER'}
    ambiguous = httpx.MockTransport(lambda _: httpx.Response(200, json={'items': [{'payer': payer}, {'payer': other}]}))
    assert asyncio.run(stedi_payers.search_payer('Humana Health Plan', ['61101'], 'test', transport=ambiguous)) is None
    routes = []
    assert asyncio.run(stedi_payers.search_payer('Humana Health Plan', ['61101'], 'test', transport=ambiguous, candidates=routes)) is None
    assert {row['stediId'] for row in routes} == {'HUMAN', 'OTHER'}
    assert all(row['match_basis'] == 'identifier' for row in routes)
    suggestions = []
    assert asyncio.run(stedi_payers.search_payer('Hummana typo', [], 'test', transport=ambiguous, candidates=suggestions)) is None
    assert len(suggestions) == 2 and all(row['match_basis'] == 'directory_suggestion' for row in suggestions)


def test_enrollment_must_be_live_for_exact_provider_payer_and_transaction():
    row = {'status': 'LIVE', 'payer': {'stediPayerId': 'cms'}, 'provider': {'npi': '1234567890'},
           'transactions': {'eligibilityCheck': {'enroll': True}}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'items': [row]}))
    assert asyncio.run(stedi_payers.eligibility_enrolled('cms', '1234567890', 'test', transport=transport))
    assert not asyncio.run(stedi_payers.eligibility_enrolled('cms', '9999999999', 'test', transport=transport))
    row['status'] = 'PROVIDER_ACTION_REQUIRED'
    assert not asyncio.run(stedi_payers.eligibility_enrolled('cms', '1234567890', 'test', transport=transport))
