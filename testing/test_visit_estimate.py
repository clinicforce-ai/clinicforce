"""Synthetic regressions for scoped evidence and money calculations; no payer calls."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
import json

import httpx
import pytest
from fastapi import HTTPException
from app.insurance import commercial, ecw_backend, visit_estimate
from app.api.routers import commercial_estimator as api

DAY = date.today().isoformat()


def test_non_office_procedures_keep_their_configured_or_diagnostic_service_type():
    assert visit_estimate.service_type('11104', {}) == '2'
    assert visit_estimate.service_type('62270', {}) == '2'
    assert visit_estimate.service_type('95886', {}) == '73'
    assert visit_estimate.service_type('11104', {'service_type_by_code': {'11104': '98'}}) == '98'


def test_booked_ncs_alternatives_price_independently_from_diagnostic_terms():
    raw = payload()
    raw['benefitsInformation'][-1]['serviceTypeCodes'] = ['73']
    raw['benefitsInformation'].append({'code': 'B', 'serviceTypeCodes': ['98'],
        'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y',
        'timeQualifierCode': '7', 'benefitAmount': '90'})
    identity = {'status': 'matched', 'binding': 'synthetic-member', 'match_version': 2}
    benefits = {**commercial.normalize_stedi_271(raw, '73', True), 'identity': identity,
                'network_status': 'in_network', 'network_source': 'clinic_library_contract'}
    choices = api._previsit_codes({'reason': 'EMG/NCS booked', 'lines': []})
    rates = [{'code': row['code'], 'allowed_cents': 10000 + index * 2000}
             for index, row in enumerate(choices)]
    priced = visit_estimate.price_lines(choices, rates, benefits, {'cost_share': 'payer_returned'},
        payload=raw, service_date=DAY, alternatives=True)
    assert priced['mode'] == 'alternatives'
    assert len(priced['lines']) == 7
    assert all(line['benefits']['copay_cents'] is None for line in priced['lines'])
    assert all(line['benefit_basis'] == 'Service category 73' for line in priced['lines'])
    assert priced['allowed_min_cents'] == 10000 and priced['allowed_max_cents'] == 22000
    planning = visit_estimate.planning_estimate(priced, benefits, identity)
    assert priced['complete'] and planning['min_cents'] == 6000 and planning['max_cents'] == 8000
    assert planning['amount_cents'] is None and planning['collectable'] is False


def payload():
    base = {'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y', 'serviceTypeCodes': ['30']}
    return {'subscriber': {'memberId': 'M123', 'firstName': 'Pat', 'lastName': 'Example',
                           'dateOfBirth': '1980-01-01'}, 'benefitsInformation': [
        {**base, 'code': '1', 'planCoverageDescription': 'Choice Medical'},
        {**base, 'code': 'C', 'timeQualifierCode': '29', 'benefitAmount': '50'},
        {**base, 'code': 'G', 'timeQualifierCode': '29', 'benefitAmount': '80'},
        {**base, 'code': 'A', 'benefitPercent': '.20', 'authOrCertIndicator': 'N',
         'additionalInformation': [{'description': 'No referral required'}]},
    ]}


def test_out_of_pocket_maximum_is_reported_only_from_a_unique_plan_wide_limit():
    raw = payload()
    annual = {'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y',
              'serviceTypeCodes': ['30'], 'code': 'G', 'timeQualifierCode': '23',
              'benefitAmount': '1000'}
    raw['benefitsInformation'].append(annual)
    terms = commercial.normalize_stedi_271(raw, '98', True)
    assert terms['oop_annual_cents'] == 100000
    assert terms['oop_annual_id'] == 'benefitsInformation[4]'
    raw['benefitsInformation'].append({**annual, 'benefitAmount': '2000'})
    assert commercial.normalize_stedi_271(raw, '98', True)['oop_annual_cents'] is None


def test_network_specific_oop_zero_is_not_a_zero_price_without_participation():
    benefit = {'active': True, 'network_status': 'unknown', 'oop_remaining_cents': 0,
               'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}
    price = {'lines': [{'code': '99203', 'units': 1, 'benefits': benefit}], 'complete': False}
    assert visit_estimate.planning_estimate(price, benefit, {}) is None
    benefit['network_status'] = 'in_network'
    assert visit_estimate.planning_estimate(price, benefit, benefit['identity'])['basis'] == 'out_of_pocket_limit'


def test_nested_stedi_plan_out_of_pocket_maximum_uses_contract_period():
    row = {'amount': '3000', 'timePeriod': 'CONTRACT', 'coverageLevel': 'INDIVIDUAL',
           'network': {'indicator': 'IN_NETWORK'}, 'service': {'system': 'STC', 'value': '30'}}
    raw = {'plans': [{'benefits': {'outOfPocket': [row]}}]}
    terms = commercial.normalize_stedi_271(raw, '98', True)
    assert terms['oop_annual_cents'] == 300000
    assert terms['oop_annual_id'] == 'plans[0].benefits.outOfPocket[0]'


def lines():
    return [{'id': 'a', 'code': '99203', 'units': 1}, {'id': 'b', 'code': '93000', 'units': 1}]


def rates():
    return [{'code': row['code'], 'allowed_cents': 10000, 'source_reference': 'Contract 2026'} for row in lines()]


def priced(raw=None, **kwargs):
    raw = raw or payload()
    return visit_estimate.price_lines(lines(), rates(), commercial.normalize_stedi_271(raw, '98', True),
        {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}},
        payload=raw, service_date=DAY, **kwargs)


def evidence(**changes):
    return {'reference': 'Payer reference 123', 'verified_at': datetime.now(timezone.utc).isoformat(),
            'verified_by': 'Billing reviewer', 'valid_from': DAY, 'valid_to': DAY, 'units': 2, 'status': 'valid', **changes}


def test_shared_balances_are_allocated_once_and_totals_reconcile():
    result = priced()
    assert result['complete']
    assert [row['calculation']['patient_cents'] for row in result['lines']] == [6000, 2000]
    assert result['patient_max_cents'] == 8000
    assert sum(row['calculation']['deductible_cents'] for row in result['lines']) == 5000
    assert result['collection']['action'] == 'confirm_payments'
    alternative = priced(alternatives=True)
    assert alternative['patient_max_cents'] == 6000
    assert alternative['allowed_max_cents'] == 10000


def test_emg_addons_keep_line_prices_but_require_companion_for_visit_total():
    raw = payload()
    codes = [{'id': code, 'code': code, 'units': 1} for code in ('95885', '95886', '95907')]
    rates = [{'code': row['code'], 'allowed_cents': 10000} for row in codes]
    def calculate(selected, **kwargs):
        return visit_estimate.price_lines(selected, rates, commercial.normalize_stedi_271(raw, '98', True),
            {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY, **kwargs)
    partial = calculate(codes[:2])
    assert not partial['complete'] and 'patient_max_cents' not in partial
    assert all(line['calculation'] for line in partial['lines'])
    assert partial['allowed_max_cents'] == 20000
    assert '95907–95913' in partial['service_issues'][0]
    assert partial['collection']['amount_cents'] is None
    full = calculate(codes)
    assert full['complete'] and not full['service_issues']
    assert full['patient_max_cents'] + full['plan_max_cents'] == 30000
    assert not calculate([codes[-1], {'id': 'ncs2', 'code': '95908', 'units': 1}])['complete']
    examples = calculate(codes[:2], alternatives=True)
    assert examples['complete'] and not examples['service_issues']  # independent examples


def test_nested_payer_errors_never_establish_active_coverage():
    raw = payload()
    raw['subscriber']['aaaErrors'] = [{'code': '72'}]
    assert commercial.normalize_stedi_271(raw, '98', True)['active'] is None
    assert not priced(raw)['complete']


def test_per_visit_copay_is_charged_once_and_unknown_units_block_total():
    raw = payload()
    raw['benefitsInformation'][1]['benefitAmount'] = '0'
    raw['benefitsInformation'][3].update(code='B', benefitAmount='30', timeQualifierCode='27')
    result = priced(raw)
    assert result['complete'] and result['patient_max_cents'] == 3000
    assert [row['calculation']['copay_cents'] for row in result['lines']] == [3000, 0]
    codes = lines(); codes[0]['units'] = None
    result = visit_estimate.price_lines(codes, rates(), commercial.normalize_stedi_271(raw, '98', True), {'cost_share': 'copay'}, payload=raw, service_date=DAY)
    assert not result['complete'] and 'patient_max_cents' not in result
    assert result['lines'][1]['calculation'] is None


def test_procedure_scope_missing_level_family_and_unrelated_dental_are_preserved():
    raw = payload()
    raw['benefitsInformation'] += [
        {'code': '6', 'serviceTypeCodes': ['35']},
        {'code': 'B', 'benefitAmount': '15', 'inPlanNetworkIndicatorCode': 'Y',
         'compositeMedicalProcedureIdentifier': {'procedureCode': '93000'}},
        {'code': 'C', 'benefitAmount': '600', 'coverageLevelCode': 'FAM', 'serviceTypeCodes': ['30']},
    ]
    office = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203')
    assert office['active'] is True and office['copay_cents'] is None
    procedure = commercial.normalize_stedi_271(raw, '98', True, procedure_code='93000')
    assert procedure['copay_cents'] == 1500
    assert any(row['coverage_level'] == 'FAM' for row in office['reported_options'])
    assert commercial.normalize_stedi_271({'benefitsInformation': [{'code': '1', 'serviceTypeCodes': ['35']}]}, '98', True)['active'] is None
    assert commercial.normalize_stedi_271({**raw, 'status': 'ERROR'}, '98', True)['active'] is None
    assert ecw_backend._plan({'class': [{'type': {'coding': [{'code': kind}]}, 'value': value} for kind, value in [('plan', 'Exact Plan'), ('subplan', 'Gold'), ('network', 'PPO')]]}) == 'Exact Plan'


def test_unselected_office_cpt_alternatives_do_not_adopt_99202_only_benefits():
    raw = payload()
    base = {'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y', 'serviceTypeCodes': ['98']}
    raw['benefitsInformation'] += [
        {**base, 'code': 'B', 'benefitAmount': '30'},
        {**base, 'code': 'B', 'benefitAmount': '10',
         'compositeMedicalProcedureIdentifier': {'procedureCode': '99202'}},
        {**base, 'code': 'B', 'benefitAmount': '20',
         'compositeMedicalProcedureIdentifier': {'procedureCode': '99203'}},
    ]
    inputs = {'visit_type': 'new', 'reason': 'New patient office visit', 'lines': [],
              'place_of_service': '11', 'service_date': DAY}
    source = {'rendering_provider': {'roles': ['neurology']}}
    coverage = {'active_coverages': 1, 'member_id': 'M123'}
    alternatives = api._previsit_benefits(raw, coverage, DAY, inputs=inputs, source=source)
    assert alternatives['copay_cents'] == 3000  # The shared office term, not the 99202-only term.
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203')['copay_cents'] == 2000
    inputs['lines'] = [{'code': '99203', 'service_status': 'scheduled', 'units': 1}]
    scheduled = api._previsit_benefits(raw, coverage, DAY, inputs=inputs, source=source)
    assert scheduled['copay_cents'] == 2000


def test_approval_status_is_reported_without_making_the_cpt_price_conditional():
    raw = payload(); raw['benefitsInformation'][3]['authOrCertIndicator'] = 'Y'
    pending = priced(raw)
    assert pending['complete'] and not pending['conditional']
    reviews = {row['id']: {'authorization': evidence()} for row in lines()}
    result = priced(raw, reviews=reviews)
    assert result['complete']
    assert result['lines'][0]['benefits']['requirements']['authorization']['required'] == 'required'
    for change in ({'status': 'pending'}, {'status': 'denied'}, {'status': 'not_required'}, {'valid_to': '2000-01-01'}, {'units': 0}):
        reviews['a']['authorization'] = evidence(**change)
        pending = priced(raw, reviews=reviews)
        assert pending['complete'] and not pending['conditional']
        assert pending['collection']['action'] == 'confirm_payments'


def test_conditional_benefits_need_evidence_and_waiver_is_explicit():
    raw = payload(); raw['benefitsInformation'][3].update(code='B', benefitAmount='20', timeQualifierCode='27', additionalInformation=[{'description': 'FIRST ten visits. No referral required.'}])
    result = priced(raw)
    assert not result['complete']
    selected = evidence(cost_share='copay', deductible_applies=False, selected_ids=['benefitsInformation[3]'])
    result = priced(raw, reviews={row['id']: {'benefits': selected} for row in lines()})
    assert result['complete'] and result['patient_max_cents'] == 2000
    assert all(row['calculation']['deductible_cents'] == 0 for row in result['lines'])
    raw['benefitsInformation'][3]['authOrCertIndicator'] = 'U'
    assert not priced(raw)['complete']


def test_eligibility_period_excludes_expired_rows_without_hiding_returned_facts():
    raw = payload(); raw['planDateInformation'] = {'planBegin': '20200101', 'planEnd': '20211231'}
    result = commercial.normalize_stedi_271(raw, '98', True, service_date=DAY)
    assert result['active'] is False and result['copay_cents'] is None
    assert result['coverage_evidence']['status'] == 'EXPIRED'
    assert result['reported_options']
    raw['planDateInformation'] = {'planBegin': '20200101', 'planEnd': '20991231'}
    raw['benefitsInformation'][3]['benefitsDateInformation'] = {'eligibilityEnd': '20210101'}
    result = commercial.normalize_stedi_271(raw, '98', True, service_date=DAY)
    assert result['active'] is True and result['coinsurance_percent'] is None
    raw['benefitsInformation'][3]['benefitsDateInformation'] = {'eligibilityEnd': 'not-a-date'}
    assert any('dates' in reason for reason in commercial.normalize_stedi_271(raw, '98', True, service_date=DAY)['review_reasons'])


class VisitReviewTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.library_patch = patch.object(api.library_pricing, 'prepare', return_value=[])
        self.library_patch.start()
        self.addCleanup(self.library_patch.stop)
        self.cash_patch = patch.object(api.practice_prices, 'current', return_value=None)
        self.cash_patch.start()
        self.addCleanup(self.cash_patch.stop)

    async def test_scoped_review_recalculates_without_stedi_and_rejects_stale_changes(self):
        session = {'practice_id': 'clinic-b', 'intake_id': 'intake-b', 'patient_id': 'patient-b', 'share_manifest_id': '',
                   'staff': {'id': 'staff-b', 'display_name': 'Staff B'}, 'intake': {'updated_at': 'v', 'patient': {'given_name': 'Pat', 'family_name': 'Example', 'birth_date': '1980-01-01'}, 'fields': {}}}
        inputs = {'service_date': DAY, 'member_id': 'M123', 'relationship': '18', 'subscriber': {'firstName': 'Pat', 'lastName': 'Example', 'birthDate': '1980-01-01'},
                  'patient': {'patient_first_name': 'Pat', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-01'},
                  'visit_type': 'new', 'lines': [{**row, 'service_status': 'scheduled'} for row in lines()], 'place_of_service': '11', 'rendering_provider': {'npi': '1234567890'}}
        source = {'appointment_id': 'visit', 'active_coverages': 1, 'plan': 'Choice', 'coverage_id': 'coverage', 'location_id': 'office'}
        token = api._visit_context_token(inputs, source)
        raw = payload()
        result = {'check_id': 'case', 'codes': api._previsit_codes(inputs), 'available_codes': api._previsit_codes(inputs), 'suggestions': None, 'issues': [], 'checked_at': datetime.now(timezone.utc).isoformat()}
        case = {'id': 'case', 'revision': 4, 'source_mode': 'commercial_previsit', 'previsit': {'checked_at': result['checked_at']}, 'previsit_raw_object_key': 'protected',
                'previsit_context': {'result': result, 'context_token': token, 'inputs': inputs, 'source': source, 'payer_id': 'payer-b'}}
        with patch.object(api, '_session', AsyncMock(return_value=session)), patch.object(api.store, 'get_case', return_value=case) as get_case, \
             patch.object(api.ecw_backend, 'fetch_inputs', AsyncMock(return_value=(inputs, [], source))) as fetch_inputs, patch.object(api, '_ecw_practice_config', return_value={}), \
             patch.object(api.staff_store, 'list_library', return_value=[]), \
             patch.object(api.object_store, 'get_object_bytes', return_value=json.dumps(raw).encode()), \
             patch.object(api.commercial_store, 'list_connections', return_value=[{'id': 'payer', 'stedi_payer_id': 'payer-b', 'status': 'staff_guidance_enabled'}]), \
             patch.object(api, '_practice_path', return_value=({}, {'plan_matcher': 'Choice', 'evidence_reference': 'library:synthetic-contract'}, {'rules': {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '30'}, 'library_source': {'document_id': 'synthetic-contract', 'reference': 'library:synthetic-contract', 'sha256': 'synthetic-hash'}}})), \
             patch.object(api.staff_store, 'get_library', return_value={'kind': 'contract', 'content_type': 'application/pdf', 'sha256': 'synthetic-hash', 'label': 'Synthetic contract'}), \
             patch.object(api.commercial_store, 'matching_rates', return_value=[{**row, 'version_id': 'synthetic-rate'} for row in rates()]), patch.object(api, '_save', side_effect=lambda value, *_: {**value, 'revision': 5}) as save, \
             patch.object(api.httpx, 'AsyncClient', side_effect=AssertionError('No paid payer call during review')):
            updated = await api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=4, payments_received_cents=1500, payment_reference='Ledger #23'), MagicMock())
            assert updated['price_range']['collection']['amount_cents'] == 6500
            assert updated['revision'] == 5
            assert get_case.call_args.kwargs['practice_id'] == 'clinic-b'
            assert case['visit_review_history'][0]['actor_id'] == 'staff-b'
            assert updated['audit_trail'][0]['changes'] == ['Recorded payments']
            assert updated['audit_trail'][0]['by'] == 'Staff B'
            assert 'actor_id' not in updated['audit_trail'][0]
            for data in ({'revision': 3}, {'context_token': 'x'*64}, {'selected_ids': ['not-a-service']}, {'confirmed_units': {'a': 0}}):
                with self.assertRaises(HTTPException):
                    await api.update_visit_review('case', api.VisitReviewInput(**{'context_token': token, 'revision': 4, **data}), MagicMock())
            assert save.call_count == 1
            case['previsit']['checked_at'] = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
            with self.assertRaises(HTTPException):
                await api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=4), MagicMock())
            historical_inputs = {**inputs, 'service_date': (date.today()-timedelta(days=1)).isoformat()}
            historical_token = api._visit_context_token(historical_inputs, source)
            case['previsit_context'].update(inputs=historical_inputs, context_token=historical_token)
            case['visit_review']['context_token'] = historical_token
            fetch_inputs.return_value = (historical_inputs, [], source)
            historical = await api.update_visit_review('case', api.VisitReviewInput(
                context_token=historical_token, revision=4), MagicMock())
            assert historical['revision'] == 5
            assert fetch_inputs.await_args.kwargs['review_service_date'] == historical_inputs['service_date']

    async def test_ecw_pagination_follows_only_practice_origin_and_reports_truncation(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={'entry': [{'resource': {'id': str(len(calls))}}], 'link': [{'relation': 'next', 'url': '?page=2'}] if len(calls) == 1 else []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows = await ecw_backend._search(client, 'https://ecw.example/fhir', 'secret', 'Coverage', {'patient': 'p'})
        assert [row['id'] for row in rows] == ['1', '2']
        assert calls[1] == 'https://ecw.example/fhir/Coverage?page=2'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'link': [{'relation': 'next', 'url': 'https://other.example/leak'}]}))) as client:
            with self.assertRaises(ecw_backend.EcwBackendError):
                await ecw_backend._search(client, 'https://ecw.example/fhir', 'secret', 'Coverage', {})


def test_cash_prices_are_separate_and_available_even_when_insurance_fails():
    cash_rates = [{**row, 'cash_cents': 7500} for row in rates()]
    codes = lines(); codes[1]['units'] = 2
    result = visit_estimate.price_lines(codes, cash_rates, {'active': False, 'network_status': 'unknown'}, {})
    assert result['cash_complete'] and result['cash_max_cents'] == 22500
    assert not result['complete'] and 'patient_max_cents' not in result
    assert result['lines'][1]['cash_cents'] == 15000
    cash_rates[1]['cash_cents'] = None
    partial = visit_estimate.price_lines(codes, cash_rates, {}, {})
    assert not partial['cash_complete'] and 'cash_max_cents' not in partial
    assert partial['lines'][0]['cash_source'] == 'Contract 2026'
    assert partial['lines'][1]['cash_cents'] is None  # never fall back to allowed amount
    cash_rates[1]['cash_cents'] = 0
    zero = visit_estimate.price_lines(codes, cash_rates, {}, {}, alternatives=True)
    assert zero['cash_min_cents'] == 0 and zero['cash_max_cents'] == 7500


def test_unavailable_allowed_rate_is_typed_and_does_not_use_cash_or_another_cpt():
    selected = [{'id': 'office', 'code': '99204', 'units': 1}]
    rate = {'code': '99203', 'allowed_cents': 14000, 'cash_cents': 9000}
    result = visit_estimate.price_lines(selected, [rate], {'active': True, 'network_status': 'in_network'}, {})
    line = result['lines'][0]
    assert line['rate_resolution'] == {'status': 'RATE_UNAVAILABLE', 'matched_candidate_count': 0,
                                       'source_checked': 'scoped_active_contract_rate_candidates',
                                       'provider_network_verified': True}
    assert line['allowed_cents'] is None and line['calculation'] is None
    conflict = visit_estimate.price_lines(selected, [{**rate, 'code': '99204'}, {**rate, 'code': '99204'}],
                                          {'active': True, 'network_status': 'in_network'}, {})
    assert conflict['lines'][0]['rate_resolution']['status'] == 'RATE_CONFLICT'


def test_procedure_groups_and_recommendations_are_grounded_in_comparable_ecw_visits():
    codes = [{'id': code, 'code': code, 'source': 'Contract service option', 'units': 1}
             for code in ['99203', '95907', '95909', '95886']]
    history = [{'visit_type': 'new', 'coded_services': ['99203']},
               {'visit_type': 'not_returned', 'coded_services': ['95909', '95886']},
               {'visit_type': 'not_returned', 'coded_services': ['95909', '95886']}]
    result = visit_estimate.service_plan(codes, {'reason': 'EMG for arm numbness', 'visit_type': 'new'}, {'recent_completed_appointments': history})
    assert result['kind'] == 'emg_ncs'
    assert [group['id'] for group in result['groups']] == ['nerve', 'muscle', 'visit']
    assert result['recommended_ids'] == ['95909', '95886']
    assert result['groups'][0]['selection_mode'] == 'single'
    assert result['groups'][1]['role'] == 'Additional services'
    assert visit_estimate.service_plan(codes, {'reason': 'EMG'}, {})['recommended_ids'] == []
    history.append({'coded_services': ['95907']}); history.append({'coded_services': ['95907']})
    assert '95909' not in visit_estimate.service_plan(codes, {'reason': 'EMG'}, {'recent_completed_appointments': history})['recommended_ids']
    codes[1]['source'] = 'eCW scheduled service'
    assert visit_estimate.service_plan(codes, {'reason': 'EMG'}, {})['recommended_ids'] == ['95907']
    alternatives = api._previsit_codes({'reason': 'New patient EMG', 'visit_type': 'new', 'lines': []})
    assert [row['code'] for row in alternatives] == [str(code) for code in range(95907, 95914)]
    assert all(row['source'] == 'conditional NCS alternative' for row in alternatives)
    assert not any(row['code'] in {'95885', '95886', '95887'} for row in alternatives)
    assert api._previsit_codes({'reason': 'EMG, 3 nerve conduction studies planned', 'lines': []})[0]['code'] == '95908'
    assert len(api._previsit_codes({'reason': 'EMG; 3 sleep studies on file', 'lines': []})) == 7


def test_saved_ecw_verification_requires_matching_policy_date_and_fresh_success():
    row = {'resourceType': 'CoverageEligibilityResponse', 'id': 'verified', 'status': 'active', 'outcome': 'complete',
           'patient': {'reference': 'Patient/p'}, 'servicedDate': DAY,
           'created': datetime.now(timezone.utc).isoformat(), 'insurance': [{'coverage': {'reference': 'Coverage/c'}, 'inforce': True}]}
    def check(value):
        return ecw_backend.saved_eligibility_summary([value], 'p', 'c', DAY)
    assert check(row)['status'] == 'verified_active'
    assert check({**row, 'insurance': [{'coverage': {'reference': 'Coverage/c'}, 'inforce': False}]})['status'] == 'verified_inactive'
    for change in ({'status': 'cancelled'}, {'outcome': 'partial'}, {'patient': {'reference': 'Patient/other'}},
                   {'servicedDate': '2000-01-01'}, {'created': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
                   {'created': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
                   {'insurance': [{'coverage': {'reference': 'Coverage/other'}, 'inforce': True}]},
                   {'insurance': [{'coverage': {'reference': 'Coverage/c'}, 'inforce': 'true'}]}, {'insurance': 42},
                   {'servicedDate': None, 'servicedPeriod': {'start': 'invalid', 'end': 'invalid'}}):
        assert check({**row, **change})['status'] == 'check_with_stedi'
    opposite = {**row, 'insurance': [{'coverage': {'reference': 'Coverage/c'}, 'inforce': False}]}
    assert ecw_backend.saved_eligibility_summary([row, opposite], 'p', 'c', DAY)['status'] == 'review'
    assert ecw_backend.saved_eligibility_summary([{'resourceType': 'Coverage', 'status': 'active'}], 'p', 'c', DAY)['status'] == 'check_with_stedi'


def test_coded_emg_wins_over_generic_visit_text_and_duplicate_ncs_cannot_double_price():
    inputs = {'reason': 'Scheduled follow-up', 'visit_type': 'established',
              'lines': [{'code': '95909', 'service_status': 'scheduled'}]}
    available = [{'id': 'order', 'code': '95909', 'source': 'eCW scheduled service'},
                 {'id': 'charge', 'code': '95909', 'source': 'eCW scheduled service'}]
    plan = visit_estimate.service_plan(available, inputs, {})
    assert plan['kind'] == 'emg_ncs'
    assert plan['groups'][0]['role'] == 'Main service'
    assert plan['recommended_ids'] == []
    from app.insurance import cpt_rules
    assert cpt_rules.service_selection_issues(['95909', '95909'])
