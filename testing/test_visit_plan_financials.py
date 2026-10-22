"""Synthetic recommendation and financial-boundary checks; no live payer calls."""
import asyncio
from copy import deepcopy
from unittest.mock import MagicMock, patch

import httpx

from app.insurance import cpt_suggester, stedi_financial


def catalog():
    return [{'id': code, 'code': code, 'source': 'Practice cash schedule', 'units': 1}
            for code in ['95907', '95909', '95885', '95886', '99203', '99204', '99213', 'J0585']]


def model_reply(services):
    return {'output': {'message': {'content': [{'toolUse': {'name': 'submit_visit_plan',
        'input': {'services': services, 'reason': 'Comparable eCW arm examinations.',
                  'assumptions': ['Anticipated study range; editable before the visit.']}}}]}}}


def test_emg_history_and_model_select_editable_study_scenario():
    model = MagicMock()
    model.converse.return_value = model_reply([{'code': '95909', 'units': 1}, {'code': '95886', 'units': 2}])
    history = [{'date': '2026-09-01', 'reason': 'Similar arm symptoms', 'coded_services': ['95909', '95886']}] * 5
    with patch.object(cpt_suggester.boto3, 'client', return_value=model):
        plan = cpt_suggester.recommend_visit_plan({'reason': 'EMG both arms'}, {'recent_completed_appointments': history}, catalog())
        assert plan['method'] == 'llm'
        assert plan['recommended_ids'] == ['95909', '95886']
        assert plan['recommended_units'] == {'95909': 1, '95886': 2}
        assert plan['history_count'] == 5
        assert any('previsit estimate' in text for text in plan['assumptions'])
        model.converse.assert_called_once()


def test_emg_prior_completed_cpts_are_selected_when_model_unavailable():
    history = [{'date': '2026-09-01', 'reason': 'Similar arm symptoms',
                'coded_services': ['95909', '95886']}]
    with patch.object(cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline')):
        plan = cpt_suggester.recommend_visit_plan(
            {'reason': 'EMG for arm numbness'}, {'recent_completed_appointments': history}, catalog())
    assert plan['method'] == 'history'
    assert plan['recommended_ids'] == ['95909', '95886']


def test_emg_with_scheduled_office_still_suggests_testing_and_keeps_office():
    available = catalog()
    office = next(row for row in available if row['code'] == '99213')
    office['source'] = 'eCW scheduled service'
    model = MagicMock()
    model.converse.return_value = model_reply([{'code': '95909', 'units': 1},
                                                {'code': '99203', 'units': 1}])
    with patch.object(cpt_suggester.boto3, 'client', return_value=model):
        plan = cpt_suggester.recommend_visit_plan({'reason': 'EMG both arms'}, {}, available)
    assert plan['recommended_ids'] == ['95909', '99213']
    model.converse.assert_called_once()


def test_explicit_scheduled_care_wins_and_wrong_patient_status_cannot_be_inferred():
    available = catalog()
    available[1]['source'] = 'eCW scheduled service'
    with patch.object(cpt_suggester.boto3, 'client') as model:
        result = cpt_suggester.recommend_visit_plan({'reason': 'EMG'}, {}, available)
        assert result['recommended_ids'] == ['95909']
        model.assert_not_called()
    available[1] = {**available[1], 'id': 'documented-95908', 'code': '95908'}
    with patch.object(cpt_suggester.boto3, 'client') as model:
        result = cpt_suggester.recommend_visit_plan({'reason': 'EMG'}, {}, available)
        assert result['recommended_ids'] == ['documented-95908']
        model.assert_not_called()
    unknown_muscle = [*available, {'id': 'muscle-unknown', 'code': '95886',
        'source': 'eCW scheduled service', 'units': None}]
    with patch.object(cpt_suggester.boto3, 'client') as model:
        result = cpt_suggester.recommend_visit_plan({'reason': 'EMG'}, {}, unknown_muscle)
        assert result['recommended_ids'] == ['documented-95908']
        model.assert_not_called()
    model = MagicMock()
    model.converse.return_value = model_reply([{'code': '99203', 'units': 1}])
    with patch.object(cpt_suggester.boto3, 'client', return_value=model):
        result = cpt_suggester.recommend_visit_plan({'reason': 'Follow up', 'visit_type': 'established'}, {}, catalog())
        assert result['recommended_ids'] == []
        assert result['method'] == 'ecw'


IDENTITY = {'billing_npi': '1234567890', 'tin': '123456789', 'member_id': 'M123',
            'patient_first': 'Alex', 'patient_last': 'Morgan', 'claim_identifiers': ['patient-account-1']}
TX = '7647d644-9348-4596-a3b4-6830b8b48cc8'


def era():
    return {'meta': {'applicationMode': 'production'}, 'transactions': [{
        'payee': {'npi': '1234567890', 'federalTaxPayersIdentificationNumber': '123456789'},
        'payer': {'name': 'Example payer'}, 'detailInfo': [{'paymentInfo': [{
            'patientName': {'firstName': 'Alex', 'lastName': 'Morgan', 'memberId': 'M123'},
            'claimPaymentInfo': {'payerClaimControlNumber': 'claim-1', 'patientControlNumber': 'patient-account-1', 'claimStatusCode': '1'},
            'serviceLines': [{'serviceDate': '20260901',
                'servicePaymentInformation': {'adjudicatedProcedureCode': '95909', 'lineItemProviderPaymentAmount': '80.00'},
                'serviceSupplementalAmounts': {'allowedActual': '100.00'},
                'serviceAdjustments': [{'claimAdjustmentGroupCode': 'PR', 'adjustmentAmount1': '20.00'}]}]}]}]}]}


def test_era_requires_practice_and_patient_match_and_preserves_missing_values():
    rows = stedi_financial.patient_lines(era(), IDENTITY, TX, '2026-09-02')
    assert len(rows) == 1
    assert rows[0]['allowed_cents'] == 10000
    assert rows[0]['insurer_paid_cents'] == 8000
    assert rows[0]['patient_responsibility_cents'] == 2000
    assert 'patient_first' not in str(rows) and 'bank' not in str(rows)
    for field in IDENTITY:
        assert stedi_financial.patient_lines(era(), {**IDENTITY, field: 'other'}, TX, '') == []
        assert stedi_financial.patient_lines(era(), {**IDENTITY, field: ''}, TX, '') == []
    test = era(); test['meta']['applicationMode'] = 'test'
    assert stedi_financial.patient_lines(test, IDENTITY, TX, '') == []
    missing = era()['transactions'][0]['detailInfo'][0]['paymentInfo'][0]['serviceLines'][0]
    del missing['serviceSupplementalAmounts']; del missing['serviceAdjustments']
    payload = era(); payload['transactions'][0]['detailInfo'][0]['paymentInfo'][0]['serviceLines'] = [missing]
    row = stedi_financial.patient_lines(payload, IDENTITY, TX, '')[0]
    assert row['allowed_cents'] is None and row['patient_responsibility_cents'] is None


def test_live_client_pages_and_never_posts_or_exposes_another_practice():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.headers['Authorization'] == 'practice-key'
        if request.url.path.endswith('/transactions'):
            assert request.url.params['transactionSetId'] == '835'
            if request.url.params.get('pageToken'):
                return httpx.Response(200, json={'items': []})
            return httpx.Response(200, json={'items': [{'transactionId': TX, 'mode': 'production', 'processedAt': '2026-09-02'}], 'nextPageToken': 'page2'})
        return httpx.Response(200, json=era())
    with patch.dict('os.environ', {'STEDI_PRACTICE_API_KEYS': '{"practice-a":"practice-key"}', 'STEDI_API_KEY': 'platform-key'}):
        result = asyncio.run(stedi_financial.fetch_patient_financials('practice-a', IDENTITY, transport=httpx.MockTransport(handler)))
        assert result['status'] == 'available' and len(result['lines']) == 1
        assert len(calls) == 3
        assert stedi_financial.credentials('practice-b') == ''
        calls.clear()
        result = asyncio.run(stedi_financial.fetch_patient_financials('practice-b', IDENTITY, transport=httpx.MockTransport(handler)))
        assert result['status'] == 'not_connected' and not calls


def test_financial_failures_are_not_reported_as_zero_patient_responsibility():
    with patch.dict('os.environ', {'STEDI_PRACTICE_API_KEYS': '', 'STEDI_API_KEY': 'platform-key'}):
        result = asyncio.run(stedi_financial.fetch_patient_financials('practice', IDENTITY,
            transport=httpx.MockTransport(lambda _: httpx.Response(403))))
        assert result['status'] == 'connection_error'
        assert result['lines'] == [] and result['partial']
