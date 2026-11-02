"""Synthetic visit-level benchmark and copay regression fixtures."""
import json
import os
from uuid import uuid4
from unittest.mock import patch

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.insurance import canonical_evidence, collection_guidance, commercial, encounter_summary, insurance_research_agent, visit_collection_model
from app.insurance import store
from app.core import patient_store, staff_store
from testing.test_cigna_office_estimate import example as synthetic_eligibility


from app.insurance.staging_synthetic import case


def test_office_copay_only():
    result = visit_collection_model.build(case(diagnostic=None))
    assert result['estimated_patient_responsibility_cents'] == 4000
    assert result['collect_now_cents'] == 4000
    assert result['service_groups'][0]['interaction_rule'] == 'COPAY_ONLY'
    assert result['service_groups'][0]['copay_cents'] == 4000
    assert result['service_groups'][0]['copay_frequency'] == 'VISIT'
    assert result['service_groups'][0]['deductible_applies'] is False


def test_missing_coinsurance_does_not_silently_make_a_copay_exclusive():
    value = case(diagnostic=None)
    value['price_range']['lines'][0]['benefits']['coinsurance_percent'] = None
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['collect_now_cents'] is None
    assert 'Confirm the cost-sharing interaction for specialist_office.' in modeled['remaining_actions']
    assert modeled['estimated_low_cents'] == 0
    assert modeled['estimated_high_cents'] == 20000
    assert modeled['estimate_assumptions']


def test_explicit_copay_only_requires_selected_source_evidence():
    value = case(diagnostic=None)
    terms = value['price_range']['lines'][0]['benefits']
    terms.update(coinsurance_percent=None, interaction_rule='COPAY_ONLY',
                 interaction_evidence_id='unselected-row')
    assert visit_collection_model.build(value)['estimated_patient_responsibility_cents'] is None
    terms['interaction_evidence_id'] = 'specialist-office'
    assert visit_collection_model.build(value)['estimated_patient_responsibility_cents'] == 4000


def test_conditional_range_keeps_independently_resolved_office_copay():
    value = case(diagnostic='deductible')
    for line in value['price_range']['lines'][1:]:
        line['benefits']['coinsurance_percent'] = None
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['estimated_low_cents'] == 0
    assert modeled['estimated_high_cents'] == 32347  # $40 office + $283.47 diagnostic reference
    assert modeled['collect_now_cents'] is None


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
    assert result['service_groups'][1]['copay_cents'] == 7500


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


@pytest.mark.parametrize(('pattern', 'extras', 'expected'), [
    ('DEDUCTIBLE_THEN_COPAY', {'copay_cents': 7500, 'copay_period': 'DAY',
        'copay_after_deductible': True, 'coinsurance_percent': None}, 21500),
    ('COPAY_PLUS_COINSURANCE', {'copay_cents': 7500, 'copay_period': 'DAY',
        'deductible_remaining_cents': None, 'coinsurance_percent': '20',
        'coinsurance_base': 'AFTER_COPAY'}, 15669),
    ('COPAY_PLUS_DEDUCTIBLE_COINSURANCE', {'copay_cents': 7500,
        'copay_period': 'DAY', 'copay_component_price_cents': 10000,
        'coinsurance_percent': '20'}, 23169),
])
def test_explicit_source_backed_combined_interactions(pattern, extras, expected):
    value = case(diagnostic='deductible')
    for line in value['price_range']['lines'][1:]:
        line['benefits'].update(extras, interaction_rule=pattern,
            interaction_evidence_id='diagnostic-share')
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] == expected
    assert modeled['collect_now_cents'] == expected
    assert modeled['service_groups'][1]['interaction_rule'] == pattern


def test_combined_interaction_without_selected_source_stays_unresolved():
    value = case(diagnostic='deductible')
    for line in value['price_range']['lines'][1:]:
        line['benefits'].update(interaction_rule='COPAY_PLUS_COINSURANCE',
            copay_cents=7500, copay_period='DAY', coinsurance_base='FULL_PRICE')
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['collect_now_cents'] is None


def test_verified_oop_caps_visit_but_unverified_note_does_not():
    result = visit_collection_model.build(case(oop=2500))
    assert result['estimated_patient_responsibility_cents'] == 2500
    assert result['collect_now_cents'] == 2500
    without_remaining = case()
    for line in without_remaining['price_range']['lines']:
        line['benefits']['oop_remaining_cents'] = None
    without_remaining['staff_note'] = 'OOP met'
    assert visit_collection_model.build(without_remaining)['estimated_patient_responsibility_cents'] == 11500


def test_unknown_oop_requires_exact_policy_opt_in_for_positive_collection():
    value = case()
    for line in value['price_range']['lines']:
        line['benefits']['oop_remaining_cents'] = None
    assert visit_collection_model.build(value)['collect_now_cents'] is None
    value['collection_policy']['allow_unknown_oop'] = True
    assert visit_collection_model.build(value)['collect_now_cents'] == 11500


def test_verified_applicable_family_oop_zero_caps_once():
    value = case()
    for line in value['price_range']['lines']:
        line['benefits']['oop_remaining_cents'] = None
    value['benefits'].update(family_oop_zero_applies=True, family_oop_remaining_cents=0)
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] == 0
    assert modeled['collect_now_cents'] == 0
    assert modeled['collection_decision_type'] == 'DOCUMENTED_ZERO'


def test_planning_amount_is_not_permission_to_collect():
    result = visit_collection_model.build(case(diagnostic='deductible', approved=False))
    assert result['estimated_patient_responsibility_cents'] == 17669
    assert result['collect_now_cents'] is None
    assert result['collection_decision_type'] == 'POLICY_APPROVAL_REQUIRED'


def test_policy_percentage_and_exclusion_are_applied_after_modeling():
    value = case(diagnostic='deductible')
    value['collection_policy']['advance_percent'] = 50
    value['collection_policy']['max_cents'] = 9000
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] == 17669
    assert modeled['collect_now_cents'] == 8835
    value['collection_policy']['excluded_cpts'] = ['95908']
    excluded = visit_collection_model.build(value)
    assert excluded['collect_now_cents'] is None
    assert excluded['collection_decision_type'] == 'POLICY_EXCLUSION'


def test_policy_requires_disclosure_and_verified_payment_reference():
    value = case(diagnostic=None)
    value['collection_policy'].pop('disclosure')
    assert visit_collection_model.build(value)['collect_now_cents'] is None
    value['collection_policy']['disclosure'] = 'Synthetic estimate for check-in.'
    value['visit_review']['payments'].pop('reference')
    assert visit_collection_model.build(value)['collect_now_cents'] is None


def test_verified_protection_is_zero_collection_not_zero_modeled_share():
    value = case(diagnostic=None)
    value['billing_protection'] = {'status': 'qmb'}
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] == 4000
    assert modeled['collect_now_cents'] == 0
    assert modeled['collection_decision_type'] == 'PROHIBITED_COLLECTION'


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


def test_reference_scope_and_price_acquisition_remain_visible_during_benefit_conflict():
    value = case(diagnostic='deductible')
    value['temporary_rate_reference']['lines'][-1]['place_of_service'] = '02'
    value['price_range']['lines'][-1]['benefits']['source_errors'] = [{'kind': 'conflict'}]
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['price_acquisition'] == [{
        'code': '95886', 'modifiers': [], 'place_of_service': '11',
        'effective_date': '2026-10-01', 'price_source': 'NMCT_BCBSTX_REFERENCE',
        'action': 'Acquire and approve a scoped BCBSTX CPT rate in the Clinical Library.'}]
    assert modeled['service_groups'][1]['price_lines'][-1]['extended_cents'] is None


def test_expired_reference_version_is_not_used_for_new_service_date():
    value = case(diagnostic='deductible')
    value['temporary_rate_reference']['lines'][-1]['effective_to'] = '2026-09-30'
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['price_acquisition'][0]['code'] == '95886'


def test_provider_scope_change_invalidates_reference():
    value = case(diagnostic='deductible')
    value['pricing_scope'] = {'place_of_service': '11', 'rendering_npi': 'synthetic-new-provider'}
    for row in value['temporary_rate_reference']['lines']:
        row['rendering_npi'] = 'synthetic-original-provider'
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert {task['code'] for task in modeled['price_acquisition']} == {'99204', '95908', '95886'}


def test_missing_provider_scope_cannot_use_a_scoped_benchmark_rate():
    value = case(diagnostic='deductible')
    value['pricing_scope'].pop('rendering_npi')
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['collect_now_cents'] is None
    assert not any(fact['field'] == 'reference_unit_price_cents'
                   for fact in canonical_evidence.build(value)['facts'])


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


def test_identity_conflict_still_identifies_independent_reference_gap():
    value = case(diagnostic='deductible')
    value['benefits']['identity']['status'] = 'review'
    value['temporary_rate_reference']['lines'].pop()
    modeled = visit_collection_model.build(value)
    assert modeled['estimated_patient_responsibility_cents'] is None
    assert modeled['collect_now_cents'] is None
    assert [task['code'] for task in modeled['price_acquisition']] == ['95886']


def test_canonical_evidence_preserves_conflict_and_selected_source_without_patient_identity():
    value = case(diagnostic=None)
    value['benefit_source'] = 'Stedi'
    value['patient_name'] = 'Sensitive Example'
    value['benefits']['identity']['status'] = 'review'
    line = value['price_range']['lines'][0]
    line['benefits']['source_errors'] = [{'kind': 'conflicting_copay'}]
    facts = canonical_evidence.build(value, {'payments': {'amount_cents': 0,
        'reference': 'synthetic-payment-check'}})
    assert next(row for row in facts['resolutions'] if row['field'] == 'patient_policy_binding')['status'] == 'conflicting'
    assert next(row for row in facts['resolutions'] if row['field'] == 'benefits')['status'] == 'conflicting'
    copay = next(row for row in facts['facts'] if row['field'] == 'copay_cents')
    assert copay['value'] == 4000
    assert copay['verification_state'] == 'conflicting'
    assert copay['scope']['cpt'] == '99204'
    assert 'Sensitive Example' not in json.dumps(facts)
    assert next(row for row in facts['facts'] if row['field'] == 'visit_payments_cents')['value'] == 0


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
                '```json\n{"findings":[{"code":"99204","finding":"Verify copay scope",'
                '"evidence_ids":["specialist-office"],"next_action":"Check plan terms"}]}\n```'}]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    finding = insurance_research_agent.investigate(value)
    assert calls[0]['modelId'] == 'us.anthropic.claude-sonnet-5'
    assert 'Must never enter prompt' not in calls[0]['messages'][0]['content'][0]['text']
    assert finding['findings'][0]['evidence_ids'] == ['specialist-office']
    value['insurance_research'] = finding
    assert insurance_research_agent.investigate(value) == finding
    assert len(calls) == 1
    assert visit_collection_model.build(value)['collect_now_cents'] is None


def test_sonnet_cpt_code_is_not_accepted_as_benefit_citation(monkeypatch):
    value = case(diagnostic=None, approved=False)
    value['price_range']['lines'][0]['benefits']['review_reasons'] = ['Copay interaction unclear']

    class FakeBedrock:
        def converse(self, **_request):
            return {'output': {'message': {'content': [{'text':
                '{"findings":[{"code":"99204","finding":"Collect office copay",'
                '"evidence_ids":["99204"],"next_action":"None"}]}'}]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    research = insurance_research_agent.investigate(value)
    assert research['status'] == 'research_incomplete'
    assert research['reason'] == 'unsupported_research_evidence'
    assert research['findings'] == []


def test_sonnet_tool_reads_only_selected_saved_fact(monkeypatch):
    value = case(diagnostic=None, approved=False)
    value['price_range']['lines'][0]['benefits']['review_reasons'] = ['Copay interaction unclear']
    value['patient_name'] = 'Sensitive Example'
    calls = []

    class FakeBedrock:
        def converse(self, **request):
            calls.append(request)
            if len(calls) == 1:
                return {'output': {'message': {'role': 'assistant', 'content': [
                    {'toolUse': {'toolUseId': 'synthetic-use', 'name': 'read_selected_benefit',
                                 'input': {'code': '99204'}}}]}}}
            return {'output': {'message': {'role': 'assistant', 'content': [{'text':
                '{"findings":[{"code":"99204","finding":"Verify office scope",'
                '"evidence_ids":["specialist-office"],"next_action":"Check plan"}]}' }]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    research = insurance_research_agent.investigate(value)
    assert research['status'] == 'complete'
    assert len(calls) == 2
    tool_content = calls[1]['messages'][2]['content'][0]['toolResult']['content'][0]['json']
    assert tool_content['finding']['copay_cents'] == 4000
    assert 'Sensitive Example' not in json.dumps(calls)
    assert research['calculation_authority'] is False


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
