"""Synthetic Cigna office workflow, including the unresolved-evidence boundaries."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow.commercial_previsit_tpa import (
    administrator_route, check_administrator, same_administrator_patient,
)
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_network_evidence
from app.insurance import collection_guidance, commercial, ecw_backend, identity, provider_specialty
from app.insurance.requirements import published
from testing.synthetic_policy import COPAY_POLICY


DAY = '2026-10-09'
NPI = '1234567893'


def example():
    patient = {'firstName': 'Alex', 'lastName': 'Example', 'dateOfBirth': '1980-01-15'}
    common = {'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
              'network': {'indicator': 'IN_NETWORK'}}
    payload = {'payerId': '62308', 'provider': {'npi': NPI},
        'subscriber': {**patient, 'memberId': '98765432101', 'groupNumber': 'SYNTHETIC-GROUP',
                       'dates': {'service': {'start': DAY}}},
        'plans': [{'benefits': {
            'statuses': [{'status': 'Active Coverage', **common}],
            'nonCovered': [{**common, 'messages': ['TMJ PCP AND TMJ Specialist']}],
            'coPayment': [
                {**common, 'amount': '25', 'timePeriod': 'VISIT', 'placesOfService': [{'code': '11'}], 'messages': ['PCP']},
                {**common, 'amount': '55', 'timePeriod': 'VISIT', 'placesOfService': [{'code': '11'}], 'messages': ['Specialist']},
                {**common, 'amount': '15', 'timePeriod': 'VISIT', 'placesOfService': [{'code': '10'}], 'messages': ['Specialist']}],
            'deductible': [{**common, 'amount': '0', 'timePeriod': 'CALENDAR_YEAR', 'messages': ['Specialist AND PCP']}],
            'outOfPocket': [{**common, 'amount': '2000', 'timePeriod': 'REMAINING'}],
            'limitations': [{**common, 'messages': ['Provider is in network based on NPI ID provided in request']}],
        }}]}
    inputs = {'service_date': DAY, 'visit_type': 'established', 'reason': 'Migraine follow-up',
        'diagnoses': ['G43.909'], 'lines': [], 'member_id': '987654321', 'relationship': '18',
        'group_number': 'SYNTHETIC-GROUP', 'payer_id': 'clinic-cigna', 'place_of_service': '11',
        'patient': {'patient_first_name': 'Alex', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-15'},
        'subscriber': {'firstName': 'Alex', 'lastName': 'Example', 'birthDate': '1980-01-15'},
        'rendering_provider': {'npi': NPI}}
    source = {'coverage_id': 'synthetic-policy', 'active_coverages': 1, 'appointment_id': 'synthetic-visit',
        'rendering_provider': {'npi': NPI, 'first_name': 'Morgan', 'last_name': 'Clinician', 'roles': ['Neurology']}}
    return payload, inputs, source


def finish(payload, inputs, source, review=None, payer=None, practice_id='synthetic-clinic'):
    case = {'id': 'synthetic-case', 'revision': 0}
    if review:
        case['visit_review'] = {'context_token': api._visit_context_token(inputs, source), **review}
    result = {'benefits': api._case_benefits(payload, inputs, source, case), 'issues': [], 'checked_at': DAY + 'T12:00:00+00:00'}
    if payer:
        result['payer'] = payer
    route = {'stedi_payer_id': '62308', 'rules': {'collection_policy': COPAY_POLICY}}
    with patch.object(api.commercial_store, 'list_connections', return_value=[route]), \
         patch.object(api.staff_store, 'list_library', return_value=[]), \
         patch.object(api.practice_prices, 'current', return_value=None), \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline')):
        api._finish_previsit(result, case, inputs, source, payload, {'practice_id': practice_id}, '62308')
    return result


def test_automatic_specialist_estimate_without_card_contract_or_ledger():
    result = finish(*example())
    assert result['identity']['reconciliation_rule'] == 'cigna_member_suffix'
    assert result['benefits']['active'] is True
    assert result['benefits']['network_source'] == 'payer_participation_message'
    assert result['collection_guidance']['amount_cents'] == 5500
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert result['arrival_collection']['prior_balance_cents'] is None
    assert result['arrival_collection']['payments_received_cents'] is None
    assert all(row['allowed_cents'] is None for row in result['price_range']['lines'])
    assert result['benefits']['excluded_service_evidence_ids'] == ['plans[0].benefits.nonCovered[0]']
    assert 'ledger is unavailable' in ' '.join(result['collection_guidance']['assumptions'])
    result = finish(*example(), review={'payments': {'reference': 'synthetic-receipt', 'amount_cents': 1500}})
    assert result['collection_guidance']['amount_cents'] == 4000


def test_tpa_office_copay_with_long_cigna_member_suffix_and_distinct_admin_group():
    payload, inputs, source = example()
    inputs['member_id'] = '987654321000'
    payload['subscriber']['memberId'] = '98765432100001'
    payload['plans'][0]['benefits']['coPayment'] = []
    payload['plans'][0]['benefits']['deductible'] = []
    payload['plans'][0]['benefits']['outOfPocket'] = []
    admin, _, _ = example()
    admin['payerId'] = '81040'
    admin['subscriber']['memberId'] = payload['subscriber']['memberId']
    admin['subscriber']['groupNumber'] = 'ADMIN-GROUP'
    admin['plans'][0]['benefits']['coPayment'][1]['amount'] = '40'
    admin['plans'][0]['benefits']['coPayment'].append({
        **deepcopy(admin['plans'][0]['benefits']['coPayment'][1]), 'amount': '20',
        'messages': ['Primary diagnosis in range- MENTAL - MENTAL', 'SPECIALIST']})
    admin['plans'][0]['benefits']['limitations'] = []
    general = deepcopy(admin)
    general['plans'][0]['benefits']['coPayment'] = []
    general['plans'][0]['benefits']['deductible'] = [{
        'service': {'system': 'STC', 'value': '30'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'amount': '1500', 'timePeriod': 'REMAINING'}]
    payload['_service_payloads'] = {'98': admin, '30': general}
    payload['_administrator_route'] = '81040'

    result = finish(payload, inputs, source)
    assert result['identity']['reconciliation_rule'] == 'cigna_member_suffix'
    assert result['price_range']['lines'][0]['benefits']['copay_cents'] == 4000
    assert result['benefits']['deductible_remaining_cents'] == 150000
    assert result['collection_guidance']['amount_cents'] == 4000
    assert result['collection_guidance']['basis'] == 'office_copay'

    inputs['diagnoses'] = []
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None

    inputs['diagnoses'] = ['G43.909']
    payload['plans'][0]['benefits']['limitations'] = []
    attested = finish(payload, inputs, source, payer='Cigna', practice_id='primary')
    assert attested['benefits']['network_source'] == 'clinic_attested_cigna_tpa'
    assert attested['collection_guidance']['amount_cents'] == 4000
    organization_query = deepcopy(payload)
    organization_query['provider']['npi'] = '9999999999'
    for response in organization_query['_service_payloads'].values():
        response['provider']['npi'] = '9999999999'
    source['billing_provider'] = {'npi': '9999999999'}
    organization = finish(organization_query, inputs, source, payer='Cigna', practice_id='primary')
    assert organization['collection_guidance']['amount_cents'] == 4000
    undated = deepcopy(payload)
    for response in [undated, *(undated['_service_payloads'].values())]:
        response['subscriber']['dates'] = {}
    inputs['service_date'] = datetime.now(timezone.utc).date().isoformat()
    same_day = finish(undated, inputs, source, payer='Cigna', practice_id='primary')
    assert same_day['benefits']['service_date_matches'] is True
    assert same_day['collection_guidance']['amount_cents'] == 4000
    inputs['service_date'] = DAY
    raw = api.json.dumps(payload).encode()
    saved = {'previsit_context': {'inputs': inputs, 'source': source, 'payer_id': '62308'},
             'previsit_raw_object_key': 'synthetic-protected-response',
             'previsit_raw_sha256': api.hashlib.sha256(raw).hexdigest()}
    with patch.object(api.staff_store, 'list_library', return_value=[]), \
         patch.object(api.object_store, 'get_object_bytes', return_value=raw):
        recheck_saved_network_evidence(attested, saved, 'primary', {'display_name': 'Cigna'})
    assert attested['benefits']['network_source'] == 'clinic_attested_cigna_tpa'
    assert attested['collection_guidance']['amount_cents'] == 4000


def test_cigna_administrator_contact_requires_one_named_medical_route():
    payload, _, _ = example()
    contact = {'service': {'system': 'STC', 'value': '30'},
               'relatedEntities': [{'type': 'PAYER',
                   'name': {'organization': 'ALLEGIANCE BENEFIT PLAN MANAGEMENT'}}]}
    payload['plans'][0]['benefits']['contactFollowingEntityForInformation'] = [contact]
    assert administrator_route(payload) == '81040'
    payload['plans'][0]['benefits']['contactFollowingEntityForInformation'].append({
        **contact, 'relatedEntities': [{'type': 'PAYER', 'name': {'organization': 'Unrelated Plan'}}]})
    assert administrator_route(payload) is None
    payload['plans'][0]['benefits']['contactFollowingEntityForInformation'] = [contact]
    payload['payerId'] = 'OTHER'
    assert administrator_route(payload) is None


def test_delegated_inquiry_uses_full_member_and_rejects_changed_patient():
    primary, inputs, source = example()
    inputs['member_id'] = '987654321000'
    primary['subscriber'].update(memberId='98765432100001',
        name={'person': {'firstName': 'Alex', 'lastName': 'Example'}})
    primary['plans'][0]['benefits']['statuses'].append({
        'status': 'Active Coverage', 'service': {'system': 'STC', 'value': '30'}})
    primary['plans'][0]['benefits']['contactFollowingEntityForInformation'] = [{
        'service': {'system': 'STC', 'value': '30'}, 'relatedEntities': [{
            'type': 'PAYER', 'name': {'organization': 'ALLEGIANCE BENEFIT PLAN MANAGEMENT'}}]}]
    office = deepcopy(primary)
    office['payerId'] = '81040'
    office['subscriber']['groupNumber'] = 'ADMIN-GROUP'
    office['plans'][0]['benefits']['coPayment'][1]['amount'] = '40'
    office['plans'][0]['benefits']['statuses'] = office['plans'][0]['benefits']['statuses'][:1]
    office['plans'][0]['benefits']['nonCovered'] = []
    general = deepcopy(office)
    general['plans'][0]['benefits']['statuses'] = [{
        'status': 'Active Coverage', 'service': {'system': 'STC', 'value': '30'}}]
    body = {'payerId': '62308', 'subscriber': {'memberId': inputs['member_id']},
            'encounter': {'services': [{'system': 'STC', 'value': '30'}]}}
    sent = []

    async def fake_post(_client, _headers, inquiry, _basic):
        sent.append(deepcopy(inquiry))
        response = httpx.Response(200, request=httpx.Request('POST', 'https://example.test'))
        return response, office if len(sent) == 1 else general, False

    async def in_threadpool(func, *args):
        return func(*args)

    case = {'previsit': {'request_state': 'pending'}}
    async def run_check():
        with patch.object(api, '_post_stedi_eligibility', side_effect=fake_post), \
             patch.object(api, 'run_in_threadpool', side_effect=in_threadpool), \
             patch.object(api, '_save', side_effect=lambda case, *_: case):
            return await check_administrator(api, primary=primary,
                coverage=api._coverage_snapshot(inputs, source), inputs=inputs, body=body,
                basic_body=body, api_key='synthetic-key', case=case, session={})

    linked, state = asyncio.run(run_check())
    assert state == 'complete' and set(linked) == {'98', '30'}
    assert [(row['payerId'], row['subscriber']['memberId'], row['encounter']['services'][0]['value'])
            for row in sent] == [('81040', '98765432100001', '98'), ('81040', '98765432100001', '30')]
    changed = deepcopy(office)
    changed['subscriber']['dateOfBirth'] = '1981-01-15'
    assert not same_administrator_patient(primary, changed, '81040')


def test_named_cigna_result_keeps_approved_fixed_copay_without_allowed_rate():
    result = finish(*example())
    result['payer'] = 'Cigna'
    assert collection_guidance.build(result)['amount_cents'] == 5500
    paid = collection_guidance.build(result, {'payments': {'reference': 'synthetic-receipt', 'amount_cents': 1500}})
    assert paid['amount_cents'] == 4000
    result.pop('collection_policy')
    assert collection_guidance.build(result)['amount_cents'] is None


def test_attribution_metadata_does_not_cancel_active_cigna_coverage():
    payload, inputs, source = example()
    payload['plans'][0]['benefits']['statuses'].append({
        'status': 'Active Coverage', 'service': {'system': 'STC', 'value': '30'}})
    payload['plans'][0]['benefits']['exclusions'] = [{
        'service': {'system': 'STC', 'value': '30'},
        'messages': ['Attribution Status - Not applicable']}]
    result = finish(payload, inputs, source)
    assert result['benefits']['active'] is True
    assert result['benefits']['coverage_status_conflict'] is False
    assert result['identity']['reconciliation_rule'] == 'cigna_member_suffix'

    payload['plans'][0]['benefits']['exclusions'][0]['messages'] = ['Office visits not covered']
    blocked = commercial.normalize_stedi_271(payload, '30', False, service_date=DAY)
    assert blocked['coverage_status_conflict'] is True
    payload['plans'][0]['benefits']['exclusions'][0]['messages'] = ['Attribution Status - Not applicable']
    payload['payerId'] = 'OTHER'
    other_payer = commercial.normalize_stedi_271(payload, '30', False, service_date=DAY)
    assert other_payer['coverage_status_conflict'] is True


@pytest.mark.parametrize('amount,cents', [('60', 6000), ('91.37', 9137)])
def test_uses_returned_amount_even_with_a_remaining_plan_deductible(amount, cents):
    payload, inputs, source = example()
    terms = payload['plans'][0]['benefits']
    terms['coPayment'][1]['amount'] = amount
    terms['deductible'].append({'service': {'system': 'STC', 'value': '30'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'amount': '1300', 'timePeriod': 'REMAINING'})
    result = finish(payload, inputs, source)
    assert result['benefits']['deductible_applicability'] == 'DOES_NOT_APPLY'
    assert result['benefits']['deductible_remaining_cents'] == 130000
    assert result['collection_guidance']['amount_cents'] == cents


@pytest.mark.parametrize('restriction', ['coinsurance', 'referral', 'authorization'])
def test_requirement_status_does_not_hide_supported_office_copay(restriction):
    payload, inputs, source = example()
    terms = payload['plans'][0]['benefits']
    row = terms['coPayment'][1]
    if restriction == 'coinsurance': terms['coInsurance'] = [{**row, 'percent': '0.20'}]
    if restriction == 'referral': row['messages'].append('Referral required')
    if restriction == 'authorization': row['priorAuthIndicator'] = 'REQUIRED'; row.pop('messages')
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] == (
        None if restriction == 'coinsurance' else 5500)


@pytest.mark.parametrize('change', ['patient', 'group', 'missing_group', 'payer', 'base', 'suffix', 'subscriber', 'relationship', 'multiple'])
def test_suffix_reconciliation_requires_independent_patient_and_policy_evidence(change):
    payload, inputs, source = example()
    if change == 'patient': payload['subscriber']['dateOfBirth'] = '1981-01-15'
    if change == 'group': payload['subscriber']['groupNumber'] = 'OTHER'
    if change == 'missing_group': payload['subscriber'].pop('groupNumber')
    if change == 'payer': payload['payerId'] = 'OTHER'
    if change == 'base': payload['subscriber']['memberId'] = '98765432201'
    if change == 'suffix': inputs['member_id'] = '98765432102'
    if change == 'subscriber': inputs['subscriber']['firstName'] = 'Other'
    if change == 'relationship': inputs['relationship'] = '19'
    if change == 'multiple': source['active_coverages'] = 2
    comparison = identity.compare(payload, api._coverage_snapshot(inputs, source))
    assert comparison['status'] == 'review'
    assert comparison['reconciliation_rule'] is None


def test_suffix_comparison_preserves_identifiers_and_supports_reverse_representation():
    payload, inputs, source = example()
    payload['subscriber']['memberId'], inputs['member_id'] = inputs['member_id'], payload['subscriber']['memberId']
    original = deepcopy((payload, inputs, source))
    result = identity.compare(payload, api._coverage_snapshot(inputs, source))
    assert result['status'] == 'warning' and result['reconciliation_rule'] == 'cigna_member_suffix'
    assert (payload, inputs, source) == original


def test_cigna_group_or_policy_number_corroborates_long_member_suffix():
    payload, inputs, source = example()
    inputs['member_id'] = '987654321000'
    payload['subscriber']['memberId'] = '98765432100001'
    payload['subscriber'].pop('groupNumber')
    payload['subscriber']['additionalInformation'] = {'groupOrPolicyNumber': 'SYNTHETIC-GROUP'}
    assert identity.compare(payload, api._coverage_snapshot(inputs, source))['reconciliation_rule'] == 'cigna_member_suffix'


def test_corroborated_cigna_member_suffix_qualifies_for_member_specific_rules():
    payload, inputs, source = example()
    comparison = identity.compare(payload, api._coverage_snapshot(inputs, source))
    assert comparison['reconciliation_rule'] == 'cigna_member_suffix'
    assert identity.payer_identity_verified(comparison)

    source['active_coverages'] = 2
    other = identity.compare(payload, api._coverage_snapshot(inputs, source))
    assert not identity.payer_identity_verified(other)


def test_cigna_oap_product_label_resolves_office_referral_rule():
    payload, inputs, source = example()
    payload['plans'][0]['name'] = 'C-24 TX NBN OAP'
    benefits = api._case_benefits(payload, inputs, source, {})
    result = {'payer': 'Cigna', 'benefit_source': 'Stedi', 'benefits': benefits,
              'identity': benefits['identity']}
    context = {'service_date': DAY, 'payer_names': {'cigna'},
               'member_id': inputs['member_id'], 'pos': '11'}
    referral = published._published_rule('referral', result, context,
                                         ['C-24 TX NBN OAP'], {'99203'})
    assert referral is not None and referral['status'] == 'Not required'
    authorization = published._published_rule('authorization', result, context,
                                              ['C-24 TX NBN OAP'], {'99203'})
    assert authorization is not None and authorization['status'] == 'Not required'


def test_cigna_oap_memory_office_visit_shows_copay_without_cigna_rate():
    payload, inputs, source = example()
    inputs.update(visit_type='new', reason='Memory concern')
    payload['plans'][0]['name'] = 'C-24 TX NBN OAP'
    payload['plans'][0]['benefits']['coPayment'][1]['amount'] = '20'
    payload['plans'][0]['benefits']['outOfPocket'][0]['amount'] = '2076.60'

    result = finish(payload, inputs, source, review={'selected_ids': ['99203:1']}, payer='Cigna')

    assert [row['code'] for row in result['codes']] == ['99203']
    assert result['benefits']['network_status'] == 'in_network'
    assert result['benefits']['network_source'] == 'payer_participation_message'
    assert result['price_range'].get('allowed_min_cents') is None
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert result['collection_guidance']['amount_cents'] == 2000
    assert result['requirement_evidence']['referral']['status'] == 'Not required'
    assert result['requirement_evidence']['authorization']['status'] == 'Not required'


@pytest.mark.parametrize('change', ['tmj_diagnosis', 'jaw_diagnosis', 'tmj_reason', 'no_diagnoses', 'generic_diagnosis', 'generic_exclusion', 'explicit_cpt', 'missing_role', 'unknown_pos', 'dob'])
def test_unresolved_or_applicable_restrictions_do_not_produce_a_copay(change):
    payload, inputs, source = example()
    exclusion = payload['plans'][0]['benefits']['nonCovered'][0]
    if change == 'tmj_diagnosis': inputs['diagnoses'].append('M26.609')
    if change == 'jaw_diagnosis': inputs['diagnoses'] = ['R68.84']
    if change == 'tmj_reason': inputs['reason'] = 'TMJ assessment with headache'
    if change == 'no_diagnoses': inputs['diagnoses'] = []
    if change == 'generic_diagnosis': inputs['diagnoses'] = ['Z09']
    if change == 'generic_exclusion': exclusion['messages'] = ['Office visits not covered']
    if change == 'explicit_cpt': exclusion['service'] = {'system': 'CPT', 'value': '99213'}
    if change == 'missing_role': source['rendering_provider']['roles'] = []
    if change == 'unknown_pos': inputs['place_of_service'] = None
    if change == 'dob': payload['subscriber']['dateOfBirth'] = '1981-01-15'
    result = finish(payload, inputs, source)
    assert result['collection_guidance']['amount_cents'] is None


@pytest.mark.parametrize('change', ['negated', 'contradictory', 'out_of_network', 'wrong_npi', 'no_provider', 'conditional', 'inactive', 'date'])
def test_network_statement_guards(change):
    payload, inputs, source = example()
    terms = payload['plans'][0]['benefits']
    if change == 'negated': terms['limitations'][0]['messages'] = ['Provider is not in network based on NPI ID provided in request']
    if change == 'contradictory': terms['limitations'][0]['messages'].append('Provider is not in network.')
    if change == 'out_of_network': terms['limitations'].append({'network': {'indicator': 'OUT_OF_NETWORK'}, 'messages': ['Provider is not in network.']})
    if change == 'wrong_npi': payload['provider']['npi'] = '9999999999'
    if change == 'no_provider': source['rendering_provider'].pop('first_name')
    if change == 'conditional': terms['limitations'][0]['messages'][0] += ' only if separately approved'
    if change == 'inactive': terms['statuses'][0]['status'] = 'Inactive Coverage'
    if change == 'date': payload['subscriber']['dates']['service']['start'] = '2026-10-08'
    benefits = api._case_benefits(payload, inputs, source, {})
    assert benefits['network_status'] == 'unknown'
    if change in {'contradictory', 'out_of_network'}:
        assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None


def test_combined_specialist_and_pcp_description_is_not_a_visit_limit():
    payload, inputs, source = example()
    terms = payload['plans'][0]['benefits']
    terms['coPayment'] = [terms['coPayment'][1]]
    terms['coPayment'][0]['messages'] = ['Specialist AND PCP']
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] == 5500
    terms['coPayment'][0]['messages'] = ['Specialist AND PCP after first 3 visits']
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None


def test_dependent_suffix_needs_matching_patient_and_subscriber():
    payload, inputs, source = example()
    payload['dependent'] = {'firstName': 'Alex', 'lastName': 'Example', 'dateOfBirth': '1980-01-15', 'relationToSubscriber': 'SPOUSE'}
    payload['subscriber'].update(firstName='Sam', dateOfBirth='1979-01-15')
    inputs['subscriber'].update(firstName='Sam', birthDate='1979-01-15')
    inputs['relationship'] = '01'
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] == 5500
    payload['subscriber']['dateOfBirth'] = '1978-01-15'
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None


def test_telehealth_label_without_pos_does_not_compete_with_office():
    payload, inputs, source = example()
    remote = payload['plans'][0]['benefits']['coPayment'][2]
    remote.pop('placesOfService')
    remote['messages'] = ['Telemedicine Specialist Visit']
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] == 5500


def test_legacy_response_layout_uses_the_same_scoped_rules():
    payload, inputs, source = example()
    rows = commercial._stedi_entries(payload)
    # Convert the synthetic modern payload to the legacy wire representation.
    for row in rows:
        row.pop('plan_index', None)
        if row['code'] == 'status': row['code'] = '1'
    payload.pop('plans')
    payload['benefitsInformation'] = rows
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] == 5500


@pytest.mark.parametrize('change', [None, 'wrong_npi', 'organization', 'inactive', 'ambiguous', 'unknown', 'timeout'])
def test_public_provider_lookup_is_bound_to_exact_individual_npi(change):
    provider_specialty._cache.clear()
    row = {'number': NPI, 'enumeration_type': 'NPI-1', 'basic': {'status': 'A'},
           'taxonomies': [{'primary': True, 'code': '2084N0400X', 'desc': 'Neurology'}]}
    if change == 'wrong_npi': row['number'] = '9999999999'
    if change == 'organization': row['enumeration_type'] = 'NPI-2'
    if change == 'inactive': row['basic']['status'] = 'D'
    if change == 'ambiguous': row['taxonomies'].append({'primary': True, 'desc': 'Family Medicine'})
    if change == 'unknown': row['taxonomies'][0]['desc'] = 'Unknown'
    response = httpx.Response(200, json={'results': [row]}, request=httpx.Request('GET', 'https://npiregistry.cms.hhs.gov/api/'))
    mock = AsyncMock(return_value=response, side_effect=httpx.ReadTimeout('timeout') if change == 'timeout' else None)
    with patch.object(provider_specialty.httpx.AsyncClient, 'get', mock):
        result = asyncio.run(provider_specialty.lookup(NPI))
        if change is None:
            assert result['office_role'] == 'specialist' and result['npi'] == NPI
            assert asyncio.run(provider_specialty.lookup(NPI)) == result
        else:
            assert result == {}
    assert mock.call_count == 1
    assert mock.call_args.kwargs['params'] == {'version': '2.1', 'number': NPI}
    provider_specialty._cache.clear()


@pytest.mark.parametrize('role_kind', ['missing', 'generic', 'own', 'foreign', 'ambiguous', 'expired'])
def test_ecw_provider_role_and_registry_fallback_reach_estimator(role_kind):
    patient = {'resourceType': 'Patient', 'id': 'p', 'name': [{'given': ['Alex'], 'family': 'Example'}], 'birthDate': '1980-01-15'}
    practitioner = {'resourceType': 'Practitioner', 'id': 'pr', 'identifier': [{'system': 'http://hl7.org/fhir/sid/us-npi', 'value': NPI}],
                    'name': [{'given': ['Morgan'], 'family': 'Clinician'}]}
    visit = {'resourceType': 'Encounter', 'id': 'v', 'status': 'planned', 'subject': {'reference': 'Patient/p'},
             'period': {'start': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
             'participant': [{'individual': {'reference': 'Practitioner/pr'}}]}
    role = {'resourceType': 'PractitionerRole', 'id': 'r', 'practitioner': {'reference': 'Practitioner/pr'}, 'specialty': [{'text': 'Neurology'}]}
    if role_kind != 'missing': visit['participant'].append({'individual': {'reference': 'PractitionerRole/r'}})
    if role_kind == 'generic': role.pop('specialty'); role['code'] = [{'text': 'Doctor'}]
    if role_kind == 'foreign': role['practitioner']['reference'] = 'Practitioner/other'
    if role_kind == 'ambiguous': role['specialty'].append({'text': 'Family Medicine'})
    if role_kind == 'expired': role['period'] = {'end': '2025-01-01'}
    resources = {'Patient/p': patient, 'Encounter/v': visit, 'Practitioner/pr': practitioner, 'PractitionerRole/r': role}
    def handler(request):
        return httpx.Response(200, json=resources.get(request.url.path.removeprefix('/fhir/'), {'resourceType': 'Bundle', 'entry': []}))
    evidence = {'npi': NPI, 'roles': ['Neurology'], 'office_role': 'specialist', 'source': 'NPPES primary taxonomy', 'checked_at': DAY}
    with patch.object(ecw_backend, 'settings', return_value=('client', 'https://ehr.test/fhir')), \
         patch.object(ecw_backend, '_access_token', AsyncMock(return_value='synthetic-token')), \
         patch.object(provider_specialty, 'lookup', AsyncMock(return_value=evidence)) as lookup:
        _, _, source = asyncio.run(ecw_backend.fetch_inputs(
            {'ecw_patient_id': 'p', 'patient_first_name': 'Alex', 'patient_last_name': 'Example', 'patient_birth_date': '1980-01-15'},
            {}, 'v', transport=httpx.MockTransport(handler)))
    fallback = role_kind in {'missing', 'generic', 'foreign', 'expired'}
    assert lookup.call_count == int(fallback)
    assert ('BQ' in api._office_service_types('synthetic-clinic', source)) == (role_kind != 'ambiguous')
    if fallback:
        lookup.assert_awaited_once_with(NPI)
        assert source['rendering_provider']['specialty_evidence'] == evidence
