"""Synthetic visit-level benchmark and copay regression fixtures."""
import json
import os
from uuid import uuid4
from unittest.mock import patch

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.insurance import collection_guidance, commercial, encounter_summary, insurance_research_agent, visit_collection_model
from app.insurance import store
from app.core import patient_store, staff_store
from testing.test_cigna_office_estimate import example as synthetic_eligibility


def case(*, office=4000, diagnostic='copay', oop=100000, approved=True):
    benefit = {'active': True, 'copay_cents': office, 'copay_period': 'VISIT',
               'deductible_applies': False, 'coinsurance_percent': '0',
               'oop_remaining_cents': oop, 'oop_id': 'individual-in',
               'selected_benefit_ids': ['specialist-office']}
    lines = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': [],
              'benefits': benefit, 'rate_resolution': {'status': 'RATE_UNAVAILABLE'}}]
    references = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': [],
                   'unit_reference_cents': 20000, 'source_reference': 'library:synthetic',
                   'reference_basis': 'BCBSTX_CROSS_PAYER_REFERENCE', 'rate_version': 'reference-v1'}]
    if diagnostic:
        for index, (code, units, cents) in enumerate([('95908', 1, 10511), ('95886', 2, 8918)]):
            terms = ({'active': True, 'copay_cents': 7500, 'copay_period': 'DAY',
                      'deductible_applies': False, 'coinsurance_percent': '0',
                      'oop_remaining_cents': oop, 'oop_id': 'individual-in',
                      'selected_benefit_ids': ['diagnostic-day']}
                     if diagnostic == 'copay' else
                     {'active': True, 'copay_cents': None, 'deductible_applies': True,
                      'deductible_remaining_cents': 10000, 'deductible_id': 'individual-in',
                      'coinsurance_percent': '20', 'oop_remaining_cents': oop,
                      'oop_id': 'individual-in', 'selected_benefit_ids': ['diagnostic-share']})
            line_id = f'diagnostic-{index}'
            lines.append({'id': line_id, 'code': code, 'units': units, 'modifiers': [],
                          'benefits': terms, 'rate_resolution': {'status': 'RATE_UNAVAILABLE'}})
            references.append({'id': line_id, 'code': code, 'units': units, 'modifiers': [],
                               'unit_reference_cents': cents, 'source_reference': 'library:synthetic',
                               'reference_basis': 'BCBSTX_CROSS_PAYER_REFERENCE', 'rate_version': 'reference-v1'})
    policy = ({'mode': 'collect_visit_model', 'version': 1, 'id': 'synthetic-policy',
               'approved_by': 'synthetic-admin', 'approved_at': '2026-10-01',
               'reference': 'synthetic approval', 'allow_benchmark_advance': True,
               'product_binding': 'synthetic-product', 'network_tier': 'in_network',
               'service_groups': ['specialist_office', *(['emg_ncs'] if diagnostic else [])],
               'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
               'max_cents': 100000}
              if approved else {})
    return {'payment_mode': 'insurance', 'price_range': {'mode': 'services', 'lines': lines},
            'temporary_rate_reference': {'lines': references},
            'benefits': {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract',
                         'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}},
            'collection_policy': policy, 'service_selection_status': 'approved',
            'payer_product_binding': 'synthetic-product', 'service_date': '2026-10-01',
            'arrival_collection': {'payments_received_cents': 0}}


def test_office_copay_only():
    result = visit_collection_model.build(case(diagnostic=None))
    assert result['estimated_patient_responsibility_cents'] == 4000
    assert result['collect_now_cents'] == 4000
    assert result['service_groups'][0]['interaction_rule'] == 'COPAY_ONLY'


def test_copay_is_capped_by_supported_exact_price():
    value = case(diagnostic=None)
    line = value['price_range']['lines'][0]
    line.update(allowed_cents=2500, allowed_basis='clinic_contract',
                rate_resolution={'status': 'RESOLVED'})
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] == 2500
    assert result['collect_now_cents'] == 2500


def test_office_plus_one_diagnostic_day_copay_for_two_cpts():
    result = visit_collection_model.build(case())
    assert result['estimated_patient_responsibility_cents'] == 11500
    assert result['collect_now_cents'] == 11500
    assert result['service_groups'][1]['estimated_patient_share_cents'] == 7500


def test_one_route_can_select_distinct_office_and_combined_visit_policies():
    office = case(diagnostic=None)
    combined = case()
    policies = [office.pop('collection_policy'), combined.pop('collection_policy')]
    for value in (office, combined):
        value['visit_collection_policies'] = policies
    assert visit_collection_model.build(office)['collect_now_cents'] == 4000
    assert visit_collection_model.build(combined)['collect_now_cents'] == 11500


def test_one_encounter_copay_covering_office_and_procedure_is_not_added_twice():
    value = case()
    for line in value['price_range']['lines']:
        line['benefits'].update(copay_cents=4000, copay_period='VISIT', copay_id='one-encounter')
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] == 4000
    assert result['service_groups'][1]['duplicate_encounter_copay'] is True


def test_office_plus_shared_deductible_and_coinsurance():
    result = visit_collection_model.build(case(diagnostic='deductible'))
    assert result['estimated_patient_responsibility_cents'] == 17669
    assert result['service_groups'][1]['deductible_cents'] == 10000
    assert result['service_groups'][1]['coinsurance_cents'] == 3669
    assert result['pricing_basis'] == 'BCBSTX_CROSS_PAYER_REFERENCE'
    assert result['reference_schedule_versions'] == ['reference-v1']
    assert result['estimated_low_cents'] == result['estimated_high_cents'] == 17669


def test_verified_oop_caps_visit_but_unverified_note_does_not():
    result = visit_collection_model.build(case(oop=2500))
    assert result['estimated_patient_responsibility_cents'] == 2500
    assert result['collect_now_cents'] == 2500
    without_remaining = case()
    for line in without_remaining['price_range']['lines']:
        line['benefits']['oop_remaining_cents'] = None
    without_remaining['staff_note'] = 'OOP met'
    assert visit_collection_model.build(without_remaining)['estimated_patient_responsibility_cents'] == 11500


def test_planning_amount_is_not_permission_to_collect():
    result = visit_collection_model.build(case(diagnostic='deductible', approved=False))
    assert result['estimated_patient_responsibility_cents'] == 17669
    assert result['collect_now_cents'] is None
    assert result['collection_decision_type'] == 'POLICY_APPROVAL_REQUIRED'


def test_verified_oop_exhaustion_is_documented_zero_without_advance_policy():
    result = visit_collection_model.build(case(oop=0, approved=False))
    assert result['estimated_patient_responsibility_cents'] == 0
    assert result['collect_now_cents'] == 0
    assert result['collection_decision_type'] == 'DOCUMENTED_ZERO'


def test_missing_reference_cpt_never_invents_price():
    value = case(diagnostic='deductible')
    value['temporary_rate_reference']['lines'].pop()
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] is None
    assert result['collect_now_cents'] is None


def test_unapproved_aetna_portal_reference_is_not_mislabeled_as_bcbstx():
    value = case(diagnostic='deductible')
    for row in value['temporary_rate_reference']['lines']:
        row.update(source_reference='Aetna portal', rate_version=None, reference_basis=None)
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] is None
    assert result['collect_now_cents'] is None


def test_identity_conflict_cannot_authorize_positive():
    value = case()
    value['benefits']['identity']['status'] = 'review'
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] is None
    assert result['collect_now_cents'] is None


def test_unconfirmed_procedure_stays_planning_only():
    value = case()
    value['service_selection_status'] = 'preview'
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] == 11500
    assert result['collect_now_cents'] is None


def test_duplicate_general_copay_cannot_replace_specialist():
    value = case(diagnostic=None)
    line = value['price_range']['lines'][0]
    line['benefits']['reported_options'] = [
        {'id': 'general', 'amount_cents': 4000, 'kind': 'copay'},
        {'id': 'specialist-office', 'amount_cents': 8000, 'kind': 'copay'}]
    line['benefits']['copay_cents'] = 8000
    line['benefits']['deductible_remaining_cents'] = 0
    line['benefits']['coinsurance_percent'] = '0'
    line['benefits']['oop_remaining_cents'] = None
    value['staff_note'] = 'OOP met'
    result = visit_collection_model.build(value)
    assert result['estimated_patient_responsibility_cents'] == 8000
    assert result['service_groups'][0]['benefit_evidence'] == ['specialist-office']


def test_mixed_271_selects_specialist_not_pcp_or_general_coinsurance():
    payload, _, _ = synthetic_eligibility()
    rows = payload['plans'][0]['benefits']
    rows.pop('nonCovered')
    rows['coPayment'] = rows['coPayment'][:2]
    rows['coPayment'][0]['amount'] = '40'
    rows['coPayment'][1]['amount'] = '80'
    rows['deductible'][0]['timePeriod'] = 'REMAINING'
    rows['coInsurance'] = [
        {**rows['coPayment'][1], 'percent': '0', 'messages': ['Specialist']},
        {**rows['coPayment'][0], 'service': {'system': 'STC', 'value': '30'},
         'percent': '.30', 'messages': ['General plan']}]
    rows['outOfPocket'] = [{**rows['deductible'][0], 'amount': '5000',
                            'timePeriod': 'CALENDAR_YEAR'}]
    terms = commercial.normalize_stedi_271(payload, '98', True, procedure_code='99204',
        service_date='2026-10-09', place_of_service='11',
        office_service_types=['BQ', 'BY', '98'])
    assert (terms['copay_cents'], terms['coinsurance_percent'],
            terms['deductible_remaining_cents'], terms['oop_remaining_cents']) == (8000, '0', 0, None)
    assert 'coPayment[1]' in terms['selected_benefit_ids'][0]
    value = case(diagnostic=None, approved=False)
    value['price_range']['lines'][0]['benefits'] = terms
    value['staff_note'] = 'OOP met'
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] == 8000
    assert modeled['collect_now_cents'] is None


def test_saved_guidance_and_summary_use_one_authoritative_visit_amount(monkeypatch):
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
    value = case(diagnostic='deductible')
    guidance = collection_guidance.build(value)
    value['collection_guidance'] = guidance
    summary = encounter_summary.build(value)
    assert guidance['amount_cents'] == guidance['collect_now_cents'] == 17669
    assert guidance['visit_model']['service_groups'][1]['estimated_patient_share_cents'] == 13669
    assert summary['collect_today_cents'] == 17669


def test_enabled_visit_model_never_leaves_a_legacy_positive_alias(monkeypatch):
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
    value = case(diagnostic='deductible')
    value['temporary_rate_reference']['lines'].pop()
    value['arrival_collection']['amount_cents'] = 9999
    guidance = collection_guidance.build(value)
    assert guidance['visit_model']['collect_now_cents'] is None
    assert guidance['amount_cents'] is None


def test_saved_case_reload_retains_approved_visit_policy(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.api.routers.commercial_flow import commercial_previsit_status as status
    from app.api.routers.commercial_flow import commercial_previsit_routes as routes
    value = case(diagnostic=None)
    policy = value.pop('collection_policy')
    saved = {'source_mode': 'commercial_previsit', 'previsit_context': {'result': value, 'context_token': 'synthetic',
        'payer_id': 'synthetic-route', 'source': {}, 'inputs': {'service_date': '2026-10-01'}},
        'previsit': {'request_state': 'complete'}, 'visit_review': {
            'context_token': 'synthetic', 'payments': {'amount_cents': 0, 'reference': 'synthetic-ledger-check'}}}
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
    monkeypatch.setattr(status._api.commercial_store, 'list_connections', lambda *_: [
        {'stedi_payer_id': 'synthetic-route', 'rules': {'visit_collection_policies': [policy]}}])
    monkeypatch.setattr(status, '_recheck_saved_clinic_pricing', lambda *args: None)
    monkeypatch.setattr(status, 'recheck_saved_network_evidence', lambda *args: None)
    monkeypatch.setattr(status, '_recheck_saved_payer_allowance', lambda *args: None)
    monkeypatch.setattr(status, 'refresh_saved_requirements', lambda *args: None)
    monkeypatch.setattr(status, 'refresh_saved_temporary_rates', lambda result, *args: result.update(
        temporary_rate_reference=case(diagnostic=None)['temporary_rate_reference']))
    monkeypatch.setattr(status.blue_cross_proxy, 'estimate', lambda *args: None)
    monkeypatch.setattr(status.blue_cross_proxy, 'cigna_estimate', lambda *args: None)
    monkeypatch.setattr(status.blue_cross_proxy, 'cigna_oap_office_scenario', lambda *args: None)
    monkeypatch.setattr(routes._api, '_session', AsyncMock(return_value={
        'practice_id': 'synthetic-clinic', 'patient_id': 'synthetic-patient', 'share_manifest_id': ''}))
    monkeypatch.setattr(routes._api, '_scope', lambda *_: {})
    monkeypatch.setattr(routes._api.store, 'get_case', lambda *args, **kwargs: saved)
    reloaded = asyncio.run(routes.saved_previsit('synthetic-case', MagicMock()))
    assert reloaded['visit_collection_policies'][0]['id'] == 'synthetic-policy'
    assert reloaded['collection_guidance']['collect_now_cents'] == 4000


def test_sonnet_finding_is_cited_and_cannot_change_money(monkeypatch):
    value = case(diagnostic=None, approved=False)
    value['price_range']['lines'][0]['benefits']['review_reasons'] = ['Copay interaction unclear']
    value['patient_name'] = 'Must never enter prompt'
    calls = []

    class FakeBedrock:
        def converse(self, **request):
            calls.append(request)
            return {'output': {'message': {'content': [{'text':
                '{"findings":[{"code":"99204","finding":"Verify copay scope",'
                '"evidence_ids":["specialist-office"],"next_action":"Check plan terms"}]}'}]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    finding = insurance_research_agent.investigate(value)
    assert calls[0]['modelId'] == 'us.anthropic.claude-sonnet-5'
    assert 'Must never enter prompt' not in calls[0]['messages'][0]['content'][0]['text']
    assert finding['findings'][0]['evidence_ids'] == ['specialist-office']
    value['insurance_research'] = finding
    assert insurance_research_agent.investigate(value) == finding
    assert len(calls) == 1
    assert visit_collection_model.build(value)['collect_now_cents'] is None


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_visit_decision_is_saved_and_reloaded_with_snapshot():
    url = os.environ['STAFF_TEST_DATABASE_URL']
    schema = 'visit_model_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        scoped = make_conninfo(url, options=f'-csearch_path={schema}')
        with patch.object(patient_store, 'DATABASE_URL', scoped), \
             patch.object(patient_store, '_schema_initialized', False), \
             patch.object(staff_store, '_schema_initialized', False), \
             patch.dict(os.environ, {'COLLECTION_ENGINE_V2_ENABLED': 'true'}):
            with staff_store._connection() as db:
                db.execute("INSERT INTO staff_members (id,username,display_name,practice_id,practice_name,role,password_hash,created_at) VALUES ('staff','synthetic','Synthetic','clinic','Clinic','admin','unused','2026-10-01')")
                db.execute("INSERT INTO staff_patients (id,practice_id,given_name,family_name,birth_date,created_at) VALUES ('patient','clinic','Pat','Example','1980-01-01','2026-10-01')")
                db.execute("INSERT INTO staff_intakes (id,practice_id,created_by,patient_id,status,created_at,updated_at) VALUES ('intake','clinic','staff','patient','in_progress','2026-10-01','2026-10-01')")
            result = case(diagnostic='deductible')
            review = {'payments': {'amount_cents': 0, 'reference': 'synthetic-ledger-check'}}
            result['collection_guidance'] = collection_guidance.build(result, review)
            workflow = {'source_mode': 'commercial_previsit', 'previsit_context': {
                'result': result, 'context_token': 'synthetic', 'payer_id': 'synthetic-route',
                'inputs': {'service_date': '2026-10-01'}, 'source': {}},
                'previsit': {'request_state': 'complete'}, 'visit_review': review}
            saved = store.create_case('patient', '', 'synthetic', workflow,
                practice_id='clinic', intake_id='intake', workflow_slot='commercial:synthetic')
            saved = store.save_case(saved, 'patient', '', 'visit-estimated',
                snapshot={'result': result, 'patient': {'given_name': 'Pat', 'family_name': 'Example'}},
                practice_id='clinic', intake_id='intake')
            loaded = store.get_case(saved['id'], 'patient', '', practice_id='clinic', intake_id='intake')
            assert loaded['previsit_context']['result']['collection_guidance']['collect_now_cents'] == 17669
            with patient_store._connection() as db:
                row = db.execute('SELECT snapshot_json FROM insurance_case_events WHERE case_id=%s AND stage=%s',
                    (saved['id'], 'visit-estimated')).fetchone()
            assert json.loads(row['snapshot_json'])['result']['collection_guidance']['visit_model']['estimated_patient_responsibility_cents'] == 17669
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
