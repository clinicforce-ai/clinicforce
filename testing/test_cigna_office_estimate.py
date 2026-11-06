"""Synthetic Cigna office workflow, including the unresolved-evidence boundaries."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow.commercial_previsit_tpa import (
    administrator_route, check_administrator, same_administrator_patient,
)
from app.api.routers.commercial_flow.commercial_previsit_network_guard import recheck_saved_network_evidence
from app.api.routers.commercial_flow import commercial_previsit_reuse as reuse
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


def finish(payload, inputs, source, review=None, payer=None, practice_id='synthetic-clinic',
           configured_connections=None, route_payer_id='62308', card_check=None):
    case = {'id': 'synthetic-case', 'revision': 0}
    if review:
        case['visit_review'] = {'context_token': api._visit_context_token(inputs, source), **review}
    result = {'benefits': api._case_benefits(payload, inputs, source, case), 'issues': [],
              'service_date': inputs['service_date'], 'checked_at': DAY + 'T12:00:00+00:00'}
    if inputs.get('telehealth_modality'):
        result['telehealth'] = {'place_of_service': inputs['place_of_service'],
                                'modality': inputs['telehealth_modality']}
    if card_check:
        result['card_check'] = card_check
    if payer:
        result['payer'] = payer
    route = {'stedi_payer_id': '62308', 'rules': {'collection_policy': COPAY_POLICY}}
    with patch.object(api.commercial_store, 'list_connections',
                      return_value=[route] if configured_connections is None else configured_connections), \
         patch.object(api.staff_store, 'list_library', return_value=[]), \
         patch.object(api.practice_prices, 'current', return_value=None), \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline')):
        api._finish_previsit(result, case, inputs, source, payload, {'practice_id': practice_id}, route_payer_id)
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


def oap_televisit():
    payload, inputs, source = example()
    payload['plans'][0]['name'] = 'Open Access Plus'
    benefits = payload['plans'][0]['benefits']
    benefits['coPayment'][1]['amount'] = '60'
    benefits['coPayment'][1]['dates'] = {'benefit': {'start': '2026-04-01'}}
    benefits['coPayment'][2]['messages'] = ['Telehealth through contracted vendor']
    benefits['coPayment'][2]['placesOfService'] = [{'code': '02'}]
    benefits['coInsurance'] = [{
        'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'percent': '0',
        'placesOfService': [{'code': '11'}], 'messages': ['PCP AND Specialist']}]
    benefits['coInsurance'][0]['dates'] = {'benefit': {'start': '2026-04-01'}}
    benefits['deductible'][0]['placesOfService'] = [{'code': '11'}]
    benefits['deductible'].append({
        'service': {'system': 'STC', 'value': '30'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'amount': '4162.28',
        'timePeriod': 'REMAINING'})
    inputs.update(place_of_service='10', telehealth_modality='audio_video')
    source['appointment_type'] = 'Televisit'
    card = {'status': 'matched', 'source': 'clinicforce_card',
            'office_copay_cents': 6000, 'conflicts': [], 'differences': []}
    return payload, inputs, source, card


def test_cigna_oap_specialist_televisit_collects_matched_card_copay_without_allowed_rate():
    payload, inputs, source, card = oap_televisit()
    result = finish(payload, inputs, source, payer='Cigna', card_check=card,
                    configured_connections=[])
    assert result['benefits']['active'] is True
    assert result['benefits']['network_status'] == 'in_network'
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    assert result['collection_guidance']['amount_cents'] == 6000
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert result['collection_guidance']['collection_rule']['mode'] == 'cigna_oap_televisit_office_copay'
    assert result['collection_guidance']['office_visit_copay_advance']['amount_cents'] == 6000
    assert result['collection_guidance']['payer_reported_office_copay_cents'] == 6000
    paid = finish(payload, inputs, source, payer='Cigna', card_check=card,
                  configured_connections=[], review={'payments': {'reference': 'synthetic-receipt',
                                                                  'amount_cents': 1500}})
    assert paid['collection_guidance']['amount_cents'] == 4500
    card_unavailable = finish(payload, inputs, source, payer='Cigna',
                              card_check={'status': 'unavailable', 'failure_reason': 'card_read_timeout'},
                              configured_connections=[])
    assert card_unavailable['collection_guidance']['amount_cents'] == 6000


def test_cigna_oap_televisit_keeps_verified_copay_when_selected_line_reports_it():
    payload, inputs, source, card = oap_televisit()
    result = finish(payload, inputs, source, payer='Cigna', card_check=card,
                    configured_connections=[])
    result['price_range']['lines'][0]['benefits'].update(
        copay_cents=6000, copay_period='VISIT', deductible_applies=False,
        coinsurance_percent='0', oop_remaining_cents=864094)

    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 6000
    assert guidance['basis'] == 'office_copay'
    assert guidance['collection_rule']['mode'] == 'cigna_oap_televisit_office_copay'


@pytest.mark.parametrize('change', ['card_mismatch', 'unverified_card', 'expired_copay', 'pos02',
    'wrong_plan', 'audio_only', 'primary_care', 'office_deductible', 'office_coinsurance',
    'out_of_network', 'secondary'])
def test_cigna_oap_televisit_requires_matching_office_evidence(change):
    payload, inputs, source, card = oap_televisit()
    if change == 'card_mismatch':
        card['office_copay_cents'] = 5000
    elif change == 'unverified_card':
        card['status'] = 'unverified'
    elif change == 'expired_copay':
        payload['plans'][0]['benefits']['coPayment'][1]['dates'] = {
            'benefit': {'start': '2026-04-01', 'end': '2026-10-01'}}
    elif change == 'pos02':
        inputs['place_of_service'] = '02'
    elif change == 'wrong_plan':
        payload['plans'][0]['name'] = 'PPO'
    elif change == 'audio_only':
        inputs['telehealth_modality'] = 'audio_only'
    elif change == 'primary_care':
        source['rendering_provider']['roles'] = ['Family Medicine']
    elif change == 'office_deductible':
        payload['plans'][0]['benefits']['deductible'][0]['amount'] = '100'
    elif change == 'office_coinsurance':
        payload['plans'][0]['benefits']['coInsurance'][0]['percent'] = '20'
    elif change == 'out_of_network':
        payload['plans'][0]['benefits']['limitations'] = []
    else:
        source['active_coverages'] = 2
    result = finish(payload, inputs, source, payer='Cigna', card_check=card,
                    configured_connections=[])
    assert result['collection_guidance']['amount_cents'] is None


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
    inputs['service_date'] = date.today().isoformat()
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


def test_cigna_missing_ecw_group_binds_exact_00_suffix_and_delegated_copay():
    payload, inputs, source = example()
    inputs['member_id'] = '987654321000'
    inputs['group_number'] = None
    payload['subscriber']['memberId'] = '98765432100000'
    payload['subscriber'].pop('groupNumber')
    payload['subscriber']['additionalInformation'] = {'groupOrPolicyNumber': 'SYNTHETIC-GROUP'}
    coverage = api._coverage_snapshot(inputs, source)
    comparison = identity.compare(payload, coverage)
    assert comparison['status'] == 'warning'
    assert comparison['reconciliation_rule'] == 'cigna_member_suffix'
    assert 'any eCW group agrees' in comparison['message']
    assert identity.patient_benefits_bound(comparison)

    administrator = deepcopy(payload)
    administrator['payerId'] = '81040'
    administrator['plans'][0]['benefits']['coPayment'][1]['amount'] = '40'
    administrator['plans'][0]['benefits']['coPayment'].append({
        **deepcopy(administrator['plans'][0]['benefits']['coPayment'][1]), 'amount': '20',
        'messages': ['Primary diagnosis in range- MENTAL - MENTAL', 'SPECIALIST']})
    administrator['plans'][0]['benefits']['nonCovered'] = []
    administrator['subscriber']['additionalInformation'] = {'groupOrPolicyNumber': 'ADMIN-GROUP'}
    payload['_service_payloads'] = {'98': administrator, '30': deepcopy(administrator)}
    payload['_administrator_route'] = '81040'
    result = finish(payload, inputs, source, payer='Cigna', practice_id='primary')
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert result['collection_guidance']['amount_cents'] == 4000

    inputs['diagnoses'] = []
    result = finish(payload, inputs, source, payer='Cigna', practice_id='primary')
    assert result['collection_guidance']['amount_cents'] == 4000
    assert any('primary diagnosis' in note for note in result['collection_guidance']['assumptions'])
    unconfigured = finish(payload, inputs, source, payer='Cigna', practice_id='primary', configured_connections=[])
    assert unconfigured.get('collection_policy') is None
    assert unconfigured['collection_guidance']['amount_cents'] == 4000, {
        'guidance': unconfigured['collection_guidance'],
        'network': (unconfigured.get('benefits') or {}).get('network_source'),
        'benefit_source': unconfigured.get('benefit_source'),
        'price_issues': (unconfigured.get('price_range') or {}).get('service_issues')}
    assert unconfigured['collection_guidance']['collection_rule']['mode'] == 'clinic_attested_cigna_delegated_copay'
    assert any('primary diagnosis' in note for note in collection_guidance.build(unconfigured)['assumptions'])
    route_payload = deepcopy(payload)
    route_payload['plans'][0]['benefits']['limitations'] = []
    for delegated in route_payload['_service_payloads'].values():
        delegated['plans'][0]['benefits']['limitations'] = []
    stedi_route = finish(route_payload, inputs, source, payer='Cigna', practice_id='primary',
                         configured_connections=[], route_payer_id='HGJLR')
    assert stedi_route['benefits']['network_source'] == 'clinic_attested_cigna_tpa'
    assert stedi_route['nmct_cigna_delegated_office'] is True
    assert stedi_route['collection_guidance']['amount_cents'] == 4000
    raw = api.json.dumps(route_payload).encode()
    saved_route = {'previsit_context': {'inputs': inputs, 'source': source, 'payer_id': 'HGJLR'},
                   'previsit_raw_object_key': 'synthetic-protected-response',
                   'previsit_raw_sha256': api.hashlib.sha256(raw).hexdigest()}
    with patch.object(api.staff_store, 'list_library', return_value=[]), \
         patch.object(api.object_store, 'get_object_bytes', return_value=raw):
        recheck_saved_network_evidence(stedi_route, saved_route, 'primary', {'display_name': 'Cigna'})
    assert stedi_route['benefits']['network_source'] == 'clinic_attested_cigna_tpa'
    unrelated = deepcopy(payload)
    unrelated['payerId'] = 'OTHER'
    assert finish(unrelated, inputs, source, payer='Other', practice_id='primary',
                  configured_connections=[], route_payer_id='HGJLR')['nmct_cigna_delegated_office'] is False
    other_clinic = finish(payload, inputs, source, payer='Cigna', practice_id='unrelated', configured_connections=[])
    assert other_clinic['collection_guidance']['amount_cents'] is None
    deferred = finish(payload, inputs, source, payer='Cigna', practice_id='primary',
        configured_connections=[{'stedi_payer_id': '62308', 'rules': {'collection_policy': {
            **COPAY_POLICY, 'mode': 'defer_to_claim'}}}])
    assert deferred['collection_guidance']['amount_cents'] == 0
    inputs['reason'] = 'Mental health follow-up'
    assert finish(payload, inputs, source, payer='Cigna', practice_id='primary')['collection_guidance']['amount_cents'] is None

    conflicting_group = deepcopy(inputs)
    conflicting_group['group_number'] = 'OTHER-GROUP'
    assert not identity.patient_benefits_bound(identity.compare(payload, api._coverage_snapshot(conflicting_group, source)))
    missing_payer_group = deepcopy(payload)
    missing_payer_group['subscriber'].pop('additionalInformation')
    assert not identity.patient_benefits_bound(identity.compare(missing_payer_group, coverage))
    different_suffix = deepcopy(payload)
    different_suffix['subscriber']['memberId'] = '98765432100001'
    assert not identity.patient_benefits_bound(identity.compare(different_suffix, coverage))


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
             patch.object(api, '_save', side_effect=lambda case, *_: {**case, 'revision': case.get('revision', 0) + 1}):
            return await check_administrator(api, primary=primary,
                coverage=api._coverage_snapshot(inputs, source), inputs=inputs, body=body,
                basic_body=body, api_key='synthetic-key', case=case, session={})

    linked, state = asyncio.run(run_check())
    assert state == 'complete' and set(linked) == {'98', '30'}
    assert case['revision'] == 1  # The pending write must advance the caller's optimistic lock.
    assert [(row['payerId'], row['subscriber']['memberId'], row['encounter']['services'][0]['value'])
            for row in sent] == [('81040', '98765432100001', '98'), ('81040', '98765432100001', '30')]
    changed = deepcopy(office)
    changed['subscriber']['dateOfBirth'] = '1981-01-15'
    assert not same_administrator_patient(primary, changed, '81040')
    partial_case = {'previsit': {'request_state': 'complete'}}
    async def partial_post(_client, _headers, _inquiry, _basic):
        response = httpx.Response(200, request=httpx.Request('POST', 'https://example.test'))
        if 'office_sent' not in partial_case:
            partial_case['office_sent'] = True
            return response, office, False
        return response, changed, False
    async def run_partial():
        with patch.object(api, '_post_stedi_eligibility', side_effect=partial_post), \
             patch.object(api, 'run_in_threadpool', side_effect=in_threadpool), \
             patch.object(api, '_save', side_effect=lambda row, *_: {**row, 'revision': row.get('revision', 0) + 1}):
            return await check_administrator(api, primary=primary,
                coverage=api._coverage_snapshot(inputs, source), inputs=inputs, body=body,
                basic_body=body, api_key='synthetic-key', case=partial_case, session={})
    _, partial_state = asyncio.run(run_partial())
    assert partial_state == 'partial'
    assert partial_case['revision'] == 2
    assert partial_case['previsit']['administrator_request_state'] == 'partial'


def test_fresh_legacy_cigna_response_adds_delegated_office_terms_without_repeating_primary():
    primary, inputs, source = example()
    primary['plans'][0]['benefits']['coPayment'] = []
    primary['plans'][0]['benefits']['contactFollowingEntityForInformation'] = [{
        'service': {'system': 'STC', 'value': '30'}, 'relatedEntities': [{
            'type': 'PAYER', 'name': {'organization': 'ALLEGIANCE BENEFIT PLAN MANAGEMENT'}}]}]
    office = deepcopy(primary)
    office['payerId'] = '81040'
    office['subscriber']['groupNumber'] = 'ADMIN-GROUP'
    office['plans'][0]['benefits']['coPayment'] = [{
        'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'amount': '40',
        'timePeriod': 'VISIT', 'messages': ['Specialist']}]
    general = deepcopy(office)
    general['plans'][0]['benefits']['coPayment'] = []
    general['plans'][0]['benefits']['statuses'] = [{
        'status': 'Active Coverage', 'service': {'system': 'STC', 'value': '30'}}]
    raw_by_key = {'old-protected': api.json.dumps(primary).encode()}
    case = {'id': 'synthetic-case', 'revision': 0,
            'previsit': {'request_state': 'complete',
                         'checked_at': datetime.now(timezone.utc).isoformat(),
                         'requested_service_types': ['98'],
                         'benefits': {'active': True, 'service_date_matches': True}},
            'previsit_raw_object_key': 'old-protected', 'cpt_authorization': {'state': 'pending'}}
    result = {'issues': [], 'payer': 'Cigna'}
    captured = []

    async def in_threadpool(func, *args):
        return func(*args)

    async def administrator_check(*_args, **kwargs):
        kwargs['case']['previsit']['administrator_request_state'] = 'complete'
        return {'98': office, '30': general}, 'complete'

    def put_protected(_practice, _case, _name, raw, _type):
        raw_by_key['new-protected'] = raw
        return 'new-protected'

    async def run_reuse():
        with patch.object(reuse, 'check_administrator', new=AsyncMock(side_effect=administrator_check)) as check, \
             patch.object(api, 'run_in_threadpool', side_effect=in_threadpool), \
             patch.object(api.object_store, 'get_object_bytes', side_effect=raw_by_key.__getitem__), \
             patch.object(api.object_store, 'put_staff_document', side_effect=put_protected), \
             patch.object(api.visit_estimate, 'reconcile_eligibility', return_value={'requires_review': False}), \
             patch.object(api, '_finish_previsit', side_effect=lambda _result, _case, _inputs, _source, payload, *_: captured.append(payload)), \
             patch.object(api, '_save', side_effect=lambda row, *_: {**row, 'revision': row['revision'] + 1}), \
             patch.object(api, '_previsit_result', side_effect=lambda *_: result), \
             patch.object(api.insurance_identity, 'unresolved_policy_difference', return_value=False):
            response = await reuse.reuse_previsit_case(api, case=case, result=result, inputs=inputs,
                source=source, session={'practice_id': 'synthetic-clinic'},
                coverage=api._coverage_snapshot(inputs, source), provider={}, background_tasks=None,
                requested_stc=['98'], payer_id='62308', refresh_benefits=False,
                api_key='synthetic-key', body={'payerId': '62308'}, basic_body={'payerId': '62308'})
            assert response is result
            check.assert_awaited_once()

    asyncio.run(run_reuse())
    assert case['previsit_raw_object_key'] == 'new-protected'
    assert case['previsit_raw_sha256'] == api.hashlib.sha256(raw_by_key['new-protected']).hexdigest()
    assert captured[0]['_administrator_route'] == '81040'
    assert finish(captured[0], inputs, source, payer='Cigna')['collection_guidance']['amount_cents'] == 4000


def test_named_cigna_result_keeps_approved_fixed_copay_without_allowed_rate():
    result = finish(*example())
    result['payer'] = 'Cigna'
    assert collection_guidance.build(result)['amount_cents'] == 5500
    paid = collection_guidance.build(result, {'payments': {'reference': 'synthetic-receipt', 'amount_cents': 1500}})
    assert paid['amount_cents'] == 4000
    result.pop('collection_policy')
    assert collection_guidance.build(result)['amount_cents'] is None


def test_oap_followup_collects_fixed_copay_without_cigna_allowed_rate():
    payload, inputs, source = example()
    payload['plans'][0]['name'] = 'Open Access Plus'
    payload['plans'][0]['benefits']['coPayment'][1]['amount'] = '60'
    inputs['reason'] = 'Eight week follow-up'
    result = finish(payload, inputs, source, review={'selected_ids': ['99213:1']}, payer='Cigna')
    result['temporary_rate_reference'] = {'source': 'BCBS Texas clinic schedule',
        'lines': [{'code': '99213', 'units': 1, 'unit_reference_cents': 7615}]}

    assert [line['code'] for line in result['price_range']['lines']] == ['99213']
    assert result['price_range']['lines'][0]['allowed_cents'] is None
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert result['collection_guidance']['amount_cents'] == 6000
    assert result['encounter_summary']['collect_today_cents'] == 6000


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
