"""Deidentified offline replay and CMS benchmark scenarios."""
from __future__ import annotations

from copy import deepcopy
import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from app.insurance import cms_pfs, cpt_suggester, historical_replay, identity, stedi_discovery
from app.api.routers import commercial_estimator as api
from testing.test_stedi_discovery import setup as discovery_setup
from testing.test_inventory_certification import CERTIFICATE, DOC, INPUTS, PAYER, SCOPE, SOURCE


DAY = '2026-10-14'
PLAN = 'RPPO-UHC Complete Care Support TX-1A'
EXCLUSION = {'decision': 'excluded_under_available_agreement',
             'document_id': DOC['id'], 'document_sha256': DOC['sha256'],
             'page': 7, 'product_kind': 'regional_medicare_advantage_ppo',
             'passage': 'Regional PPO Medicare Advantage products are excluded.'}


def _bundle(rows: list[dict], codes: list[dict] | None = None, *, policy: dict | None = None) -> dict:
    case = {'intake_id': 'synthetic-intake', 'previsit_raw_object_key': 'synthetic/saved-271.json',
            'previsit': {'checked_at': '2026-10-14T15:00:00Z'},
            'previsit_context': {'inputs': deepcopy(INPUTS), 'source': deepcopy(SOURCE),
                                 'payer_id': '87726', 'result': {
                'identity': {'status': 'matched', 'match_version': 2, 'binding': 'synthetic-member-binding'},
                'collection_guidance': {'amount_cents': 25000, 'basis': 'historical_only'}}},
            'visit_review': {'billing_protection': {
                'status': 'not_qmb', 'identity_binding': 'synthetic-member-binding',
                'reference': 'synthetic-member-check', 'verified_at': '2026-10-14T12:00:00Z'}}}
    document = {**DOC, 'created_at': '2026-09-01T00:00:00Z',
                'extraction': {'compiled': {'exclusions': [EXCLUSION]}}}
    scope = {**SCOPE, 'cms_locality': '11', 'cms_qp_status': 'non_qp',
             'cms_payment_reference': 'Synthetic locality and QP certification'}
    certificate = {**CERTIFICATE, 'document_hashes': {DOC['id']: DOC['sha256']},
                   'scopes': [scope]}
    payer = {**PAYER, 'rules': {'agreement_inventory': certificate,
                               'collection_policy': policy or {}}}
    payload = {'payerId': '87726', 'plans': [{'name': PLAN}],
               'benefitsInformation': [
                   {'code': '1', 'serviceTypeCodes': ['30'], 'planCoverage': PLAN}, *rows]}
    return {'case_reference': 'synthetic', 'intake_id': case['intake_id'],
            'case': case, 'raw_reference': case['previsit_raw_object_key'],
            'raw_response': payload, 'documents': [document], 'payer_route': payer,
            'practice_id': 'synthetic-clinic', 'service_date': DAY,
            'selection': {'origin': 'synthetic', 'lines': codes or [
                {'id': 'office', 'code': '99204', 'units': 1, 'modifiers': []}]}}


def _row(kind: str, value: float, service: str = '98', *, note: str = '') -> dict:
    row = {'code': kind, 'serviceTypeCodes': [service],
           'network': {'indicator': 'N'}, 'planCoverage': PLAN}
    row['benefitPercent' if kind == 'A' else 'benefitAmount'] = value
    if kind in {'C', 'G'}:
        row['timeQualifierCode'] = '29'
        row['coverageLevelCode'] = 'IND'
    if kind == 'A':
        row['authOrCertIndicator'] = 'N'
        row['additionalInformation'] = [{'description': 'No referral required'}]
    if note:
        row['messages'] = [note]
    return row


def _positive_policy() -> dict:
    return {'mode': 'defer_to_claim', 'approved_by': 'synthetic-admin',
            'approved_at': '2026-10-01T12:00:00Z', 'reference': 'Synthetic approval',
            'allow_supported_oon_advance': True, 'allow_cms_based_advance': True}


def test_replay_preserves_verified_dependent_policy_reconciliation():
    source_case, _, _, payload = discovery_setup()
    inputs = source_case['previsit_context']['inputs']
    source = source_case['previsit_context']['source']
    inputs['service_date'] = DAY
    inputs['member_id'] = 'FAMILY-123'
    payload['planDateInformation']['planBegin'] = DAY.replace('-', '')
    payload['dependent'] = {**payload['subscriber'], 'relationToSubscriberCode': '01'}
    payload['subscriber'] = {'firstName': 'Sam', 'lastName': 'Example',
                             'memberId': 'FAMILY-123'}
    source['payer_identifiers'] = ['example']
    assert stedi_discovery.corroborates_patient_policy(payload, inputs, source)
    assert stedi_discovery.automatic_match(payload, inputs, source)
    recorded = api._coverage_snapshot(inputs, source)
    reconciled = api._discovered_coverage(inputs, source, payload)
    saved_identity = identity.compare(payload, reconciled)
    assert saved_identity['status'] == 'matched'
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')])
    bundle['raw_response'] = payload
    bundle['case']['previsit_context'].update(inputs=inputs, source=source)
    bundle['case']['previsit_context']['result'].update(
        identity=saved_identity,
        insurance_details=recorded,
        benefits={'policy_match': {'source': 'payer_response', 'status': 'verified'}},
    )
    report = historical_replay.replay(bundle)
    assert report['status'] != 'unlinked_saved_response'
    altered = deepcopy(bundle)
    altered['raw_response']['dependent']['firstName'] = 'Different'
    assert historical_replay.replay(altered)['status'] == 'unlinked_saved_response'


def test_cms_dallas_office_rate_is_dated_and_scope_bound():
    rate = cms_pfs.estimate_rate('99204', [], 1, service_date=DAY, locality='11',
        qp_status='non_qp', place_of_service='11', provider_npi='1234567890',
        patient_binding='synthetic-member-binding')
    assert rate['allowed_cents'] == 17667
    assert rate['basis'] == 'cms_original_medicare_estimate'
    assert cms_pfs.estimate_rate('99204', [], 1, service_date='2027-01-01', locality='11',
        qp_status='non_qp', place_of_service='11', provider_npi='1234567890',
        patient_binding='synthetic-member-binding') is None
    assert cms_pfs.estimate_rate('99204', [], 1, service_date=DAY, locality='',
        qp_status='non_qp', place_of_service='11', provider_npi='1234567890',
        patient_binding='synthetic-member-binding') is None


def test_replay_cutoff_uses_timezone_aware_instant_and_exact_saved_bytes():
    cutoff = '2026-10-14T15:00:00Z'
    assert historical_replay._as_of({'approved_at': '2026-10-14T11:00:00-04:00'}, cutoff)
    assert historical_replay._as_of({'approved_at': '2026-10-14T11:00:01-04:00'}, cutoff) is None
    assert historical_replay._as_of({'approved_at': '2026-10-14T12:00:00'}, cutoff) is None
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')],
                     policy=_positive_policy())
    raw = json.dumps(bundle.pop('raw_response'), indent=2).encode()
    bundle['raw_response_b64'] = base64.b64encode(raw).decode()
    bundle['case']['previsit_raw_sha256'] = hashlib.sha256(raw).hexdigest()
    assert historical_replay.replay(bundle)['source_binding'] == 'saved_case_raw_digest'
    bundle['raw_response_b64'] = base64.b64encode(raw + b' ').decode()
    assert historical_replay.replay(bundle)['status'] == 'unlinked_saved_response'


def test_authenticated_replay_export_uses_scoped_saved_case_and_raw_bytes(monkeypatch):
    from app.api.routers.commercial_flow import commercial_previsit_routes
    api = commercial_previsit_routes._api
    bundle = _bundle([])
    case = {**bundle['case'], 'source_mode': 'commercial_previsit'}
    raw = json.dumps(bundle['raw_response'], indent=2).encode()
    case['previsit_raw_sha256'] = hashlib.sha256(raw).hexdigest()
    session = {'patient_id': 'synthetic-patient', 'share_manifest_id': '',
               'practice_id': 'synthetic-clinic', 'intake_id': 'synthetic-intake',
               'staff': {'role': 'admin', 'id': 'synthetic-admin',
                         'display_name': 'Synthetic Administrator'}}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    monkeypatch.setattr(api.store, 'get_case', lambda *args, **kwargs: case)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda key: raw)
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda practice: [bundle['payer_route']])
    monkeypatch.setattr(api.staff_store, 'list_library', lambda practice: [])
    exported = asyncio.run(commercial_previsit_routes.historical_replay_bundle('synthetic-case', None))
    assert exported['raw_reference'] == case['previsit_raw_object_key']
    assert exported['retrieved_raw_sha256'] == case['previsit_raw_sha256']
    assert exported['source_digest_recorded'] is True
    assert base64.b64decode(exported['raw_response_b64']) == raw
    session['staff']['role'] = 'staff'
    with pytest.raises(api.HTTPException) as forbidden:
        asyncio.run(commercial_previsit_routes.historical_replay_bundle('synthetic-case', None))
    assert forbidden.value.status_code == 403


def test_scenario_a_fixed_copay_collects_supported_advance():
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')],
                     policy=_positive_policy())
    for field in ('cms_locality', 'cms_qp_status', 'cms_payment_reference'):
        bundle['payer_route']['rules']['agreement_inventory']['scopes'][0].pop(field)
    original = deepcopy(bundle['case'])
    report = historical_replay.replay(bundle)
    assert report['network_evidence']['decision'] == 'out_of_network_by_clinic_contract_inventory'
    assert report['result']['price_range']['lines'][0]['allowed_cents'] is None
    assert report['result']['collection_guidance']['amount_cents'] == 4000
    assert report['result']['collection_guidance']['basis'] == 'office_copay'
    assert bundle['case'] == original
    assert report['result']['payment_explanation']['sentence'].startswith(
        'The patient has RPPO-UHC Complete Care Support TX-1A, which is excluded')


def test_cancelled_later_appointment_keeps_previsit_projection_separate_from_actual_bill():
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')],
                     policy=_positive_policy())
    bundle['case']['appointment'] = {'status': 'cancelled',
                                    'status_recorded_at': '2026-10-20T12:00:00Z'}
    saved = deepcopy(bundle['case'])
    report = historical_replay.replay(bundle)
    assert report['mode'] == 'counterfactual_simulation'
    assert report['retrospective_headline'].startswith('Amount NMCT would have collected at check-in:')
    assert report['original_estimate_unchanged'] is True
    assert bundle['case'] == saved
    assert 'actual_amount_owed' not in report


def test_scenario_b_deductible_and_coinsurance_use_remaining_balance():
    bundle = _bundle([_row('C', 50, '30'), _row('G', 500, '30'),
                      _row('A', .20)], policy=_positive_policy())
    report = historical_replay.replay(bundle)
    line = report['result']['price_range']['lines'][0]
    assert line['allowed_cents'] == 17667
    assert line['calculation']['deductible_cents'] == 5000
    assert line['calculation']['coinsurance_cents'] == 2533
    assert report['result']['collection_guidance']['amount_cents'] == 7533


def test_scenario_c_consultation_ncs_and_emg_are_distinct_lines():
    lines = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': []},
             {'id': 'ncs', 'code': '95907', 'units': 1, 'modifiers': []},
             {'id': 'emg', 'code': '95886', 'units': 1, 'modifiers': []}]
    bundle = _bundle([_row('C', 50, '30'), _row('G', 500, '30'),
                      _row('A', .20, '98'), _row('A', .20, '73')], lines,
                     policy=_positive_policy())
    report = historical_replay.replay(bundle)
    priced = report['result']['price_range']['lines']
    assert [line['allowed_cents'] for line in priced] == [17667, 9401, 9970]
    assert sum((line.get('calculation') or {}).get('deductible_cents', 0) for line in priced) == 5000
    assert report['result']['collection_guidance']['amount_cents'] == 11407
    assert report['result']['encounter_summary']['integrity_error'] is None


def test_synthetic_bedrock_plan_requires_doctor_selection_and_emg_only_has_no_em(monkeypatch):
    available = [{'id': key, 'code': code, 'units': 1, 'source': source}
                 for key, code, source in [('office', '99204', 'eCW scheduled service'),
                                           ('ncs', '95907', 'Contract service option'),
                                           ('emg', '95886', 'Contract service option')]]
    response = {'output': {'message': {'content': [{'toolUse': {
        'name': 'submit_visit_plan', 'input': {'services': [
            {'code': '99204', 'units': 1}, {'code': '95907', 'units': 1},
            {'code': '95886', 'units': 1}], 'reason': 'Synthetic plan',
            'assumptions': [], 'cash_package': ''}}}]}}}
    class FakeBedrock:
        def converse(self, **_):
            return response
    monkeypatch.setattr(cpt_suggester.boto3, 'client', lambda *_, **__: FakeBedrock())
    proposal = cpt_suggester.recommend_visit_plan(
        {'reason': 'Consultation and nerve testing', 'visit_type': 'new',
         'emg_required': True}, {}, available)
    assert proposal['method'] == 'llm'
    assert proposal['recommended_ids'] == ['office']
    # The model proposes; only the explicitly selected subset reaches replay.
    lines = [{'id': row['id'], 'code': row['code'], 'units': 1, 'modifiers': []}
             for row in available if row['id'] in {'ncs', 'emg'}]
    bundle = _bundle([_row('C', 50, '30'), _row('G', 500, '30'),
                      _row('A', .20, '73')], lines, policy=_positive_policy())
    priced = historical_replay.replay(bundle)['result']['price_range']['lines']
    assert [line['code'] for line in priced] == ['95907', '95886']
    assert sum((line.get('calculation') or {}).get('deductible_cents', 0) for line in priced) == 5000


def test_scenario_d_missing_benefits_defer_only_with_historical_policy():
    bundle = _bundle([])
    report = historical_replay.replay(bundle)
    assert report['result']['collection_guidance']['amount_cents'] is None
    assert report['result']['payment_explanation']['reason_code'] == 'cost_share_term_unresolved'
    assert 'cost-sharing terms' in report['result']['payment_explanation']['sentence']
    bundle['payer_route']['rules']['collection_policy'] = {
        'mode': 'defer_to_claim', 'approved_by': 'synthetic-admin',
        'approved_at': '2026-10-01T12:00:00Z', 'reference': 'Synthetic approval'}
    deferred = historical_replay.replay(bundle)
    assert deferred['result']['collection_guidance']['amount_cents'] == 0
    assert deferred['result']['collection_guidance']['basis'] == 'claim_after_service'
    assert deferred['retrospective_headline'] == '$0 collected at check-in — simulated deferral policy'
    assert 'this deferral policy is simulated' in deferred['result']['payment_explanation']['sentence']


def test_future_approval_and_quote_cannot_change_historical_advance():
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')],
                     policy={**_positive_policy(), 'approved_at': '2026-10-15T12:00:00Z'})
    report = historical_replay.replay(bundle)
    assert report['result']['collection_guidance']['amount_cents'] is None
    bundle['payer_route']['rules']['agreement_inventory']['approved_at'] = '2026-10-15T12:00:00Z'
    report = historical_replay.replay(bundle)
    assert report['network_evidence'] is None


def test_uncertified_historical_inventory_stays_separate_from_simulated_approval():
    bundle = _bundle([_row('B', 40, note='Deductible does not apply')])
    certificate = bundle['payer_route']['rules'].pop('agreement_inventory')
    historical = historical_replay.replay(bundle)
    assert historical['exclusion']['document_id'] == DOC['id']
    assert historical['network_evidence'] is None
    assert historical['result']['collection_guidance']['amount_cents'] is None
    bundle['simulated_inventory'] = certificate
    simulated = historical_replay.replay(bundle)
    assert simulated['mode'] == 'counterfactual_simulation'
    assert simulated['network_evidence']['decision'] == 'out_of_network_by_clinic_contract_inventory'


def test_wrong_intake_or_response_object_is_rejected():
    bundle = _bundle([])
    bundle['intake_id'] = 'different-intake'
    assert historical_replay.replay(bundle)['status'] == 'unlinked_saved_response'
    bundle['intake_id'] = 'synthetic-intake'
    bundle['raw_reference'] = 'other-object'
    assert historical_replay.replay(bundle)['status'] == 'unlinked_saved_response'


def test_saved_member_identity_binding_rejects_a_different_271():
    bundle = _bundle([])
    bundle['case']['previsit_context']['result']['insurance_details'] = {
        'member_id': 'SYNTHETIC001', 'patient_first': 'Example',
        'patient_last': 'Patient', 'patient_birth_date': '1980-01-01',
        'relationship': '18', 'coverage_id': 'coverage-synthetic', 'payer_id': '87726'}
    bundle['raw_response']['subscriber'] = {'memberId': 'DIFFERENT', 'firstName': 'Example',
        'lastName': 'Patient', 'dateOfBirth': '1980-01-01'}
    assert historical_replay.replay(bundle)['status'] == 'unlinked_saved_response'


def test_claimed_documented_cpt_must_exist_in_saved_selection():
    bundle = _bundle([])
    bundle['selection']['origin'] = 'documented'
    with pytest.raises(ValueError, match='Documented lines'):
        historical_replay.replay(bundle)


def test_historical_cutoff_cannot_look_past_saved_check():
    bundle = _bundle([])
    bundle['historical_cutoff'] = '2026-10-14T16:00:00Z'
    with pytest.raises(ValueError, match='historical cutoff'):
        historical_replay.replay(bundle)
