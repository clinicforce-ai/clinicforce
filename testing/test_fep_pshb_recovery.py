"""Synthetic FEP PSHB evidence through the shared saved-visit estimator."""

from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

from app.api.routers import commercial_estimator as api
from app.api.routers.commercial_flow.commercial_previsit_saved_pricing import (
    _reprice_saved_new_network, restore_saved_fep_standard_product,
    saved_fep_standard_office_needs_reprice)
from app.insurance import commercial


DAY = '2026-10-21'
NPI = '1234567893'


def evidence():
    person = {'firstName': 'Alex', 'lastName': 'Example', 'dateOfBirth': '1980-01-15'}
    scope = {'service': {'system': 'STC', 'value': '98'},
             'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}}
    payload = {'provider': {'npi': NPI}, 'x12': 'Visit FEPBLUE.ORG for benefit details',
               'subscriber': {**person, 'memberId': 'R12345678',
                              'additionalInformation': {'group': {'number': '33A'}},
                              'dates': {'service': {'start': DAY}}},
               'plans': [{'name': 'BASIC', 'benefits': {
                   'statuses': [{**scope, 'status': 'ACTIVE',
                                 'messages': ['Provider is in network based on NPI ID provided in request']}],
                   'coPayment': [{**scope, 'amount': '50', 'messages': ['Telemedicine specialist']},
                                 {**scope, 'amount': '50', 'messages': ['Applied behavior analysis specialist'],
                                  'priorAuthIndicator': 'REQUIRED'}],
                   'coInsurance': [{**scope, 'percent': '0.35',
                                    'messages': ['AGENTS, DRUGS AND/OR SUPPLIES ADMINISTERED OR OBTAINED IN CONNECTION WITH CARE']}],
                   'outOfPocket': [{**scope, 'service': {'system': 'STC', 'value': '30'},
                                    'amount': '6000', 'timePeriod': 'REMAINING'}],
               }}]}
    inputs = {'service_date': DAY, 'visit_type': 'established', 'reason': 'Neurology follow-up',
              'lines': [], 'member_id': 'R12345678', 'relationship': '18',
              'group_number': '33A', 'payer_id': 'synthetic-bcbs', 'place_of_service': '11',
              'patient': {'patient_first_name': 'Alex', 'patient_last_name': 'Example',
                          'patient_birth_date': '1980-01-15'},
              'subscriber': {'firstName': 'Alex', 'lastName': 'Example', 'birthDate': '1980-01-15'},
              'rendering_provider': {'npi': NPI}}
    source = {'coverage_id': 'synthetic-policy', 'active_coverages': 1,
              'appointment_id': 'synthetic-visit', 'plan': 'CI',
              'rendering_provider': {'npi': NPI, 'first_name': 'Morgan',
                                     'last_name': 'Clinician', 'roles': ['Neurology']}}
    return payload, inputs, source


def finish(payload, inputs, source, *, approved_policy=True,
           policy_mode='collect_verified_copay'):
    case = {'id': 'synthetic-case', 'revision': 0}
    result = {'benefits': api._case_benefits(payload, inputs, source, case),
              'issues': [], 'checked_at': DAY + 'T12:00:00+00:00'}
    connections = ([{'id': 'synthetic-route', 'stedi_payer_id': 'synthetic-bcbs',
                     'status': 'draft', 'rules': {'collection_policy': {
                         'mode': policy_mode, 'reference': 'Synthetic clinic policy',
                         'approved_by': 'synthetic-admin', 'approved_at': DAY + 'T00:00:00Z'}}}]
                   if approved_policy else [])
    with patch.object(api.commercial_store, 'list_connections', return_value=connections), \
         patch.object(api.practice_prices, 'current', return_value=None), \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline')):
        api._finish_previsit(result, case, inputs, source, payload,
                             {'practice_id': 'synthetic-clinic'}, 'synthetic-bcbs')
    return result


def test_identifies_pshb_basic_only_from_correlated_enrollment_and_fep_evidence():
    payload, _, _ = evidence()
    details = commercial.plan_details(payload)
    assert details['names'] == ['BASIC']
    assert details['identified_product'] == 'FEP Blue Basic PSHB'
    assert details['product_evidence']['fep_reference'] == 'x12'
    for changed in ('group', 'marker', 'member', 'name'):
        other = deepcopy(payload)
        if changed == 'group':
            other['subscriber']['additionalInformation']['group']['number'] = '111'
        elif changed == 'marker':
            other.pop('x12')
        elif changed == 'member':
            other['subscriber']['memberId'] = 'X12345678'
        else:
            other['plans'][0]['name'] = 'STANDARD'
        assert commercial.plan_details(other)['identified_product'] is None


def test_preferred_pshb_office_recovers_published_copay_without_a_rate_or_final_em():
    result = finish(*evidence())
    assert result['plan_details']['stedi']['identified_product'] == 'FEP Blue Basic PSHB'
    assert result['benefits']['network_source'] == 'payer_participation_message'
    assert result['service_selection_status'] == 'preview'
    assert result['collection_guidance']['amount_cents'] == 5000
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert all(line['allowed_cents'] is None for line in result['price_range']['lines'])
    assert all(line['benefits']['deductible_applies'] is False for line in result['price_range']['lines'])
    assert all(line['benefit_source'] == '2026 OPM PSHB brochure' for line in result['price_range']['lines'])
    assert result['requirement_evidence']['referral']['status'] == 'Not required'


def test_pshb_copay_stays_unavailable_without_provider_participation():
    payload, inputs, source = evidence()
    payload['plans'][0]['benefits']['statuses'][0]['messages'][0] = 'Preferred benefit description only'
    result = finish(payload, inputs, source)
    assert result['benefits']['network_status'] == 'unknown'
    assert result['collection_guidance']['amount_cents'] is None
    assert all(line['benefits']['copay_cents'] is None for line in result['price_range']['lines'])


def test_specialist_copay_does_not_apply_to_primary_care_provider():
    payload, inputs, source = evidence()
    source['rendering_provider']['roles'] = ['Family Medicine']
    result = finish(payload, inputs, source)
    assert result['collection_guidance']['amount_cents'] is None
    assert all(line['benefits']['copay_cents'] is None for line in result['price_range']['lines'])


def test_published_copay_requires_approved_collection_policy():
    result = finish(*evidence(), approved_policy=False)
    assert result['collection_guidance']['amount_cents'] is None
    assert result['collection_guidance']['published_copay_policy_missing'] is True


def test_basic_date_requirement_remains_explicit():
    payload, inputs, source = evidence()
    payload['subscriber'].pop('dates')
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None


def test_basic_published_copay_still_needs_fixed_copay_policy():
    result = finish(*evidence(), policy_mode='collect_supported_advance')
    assert result['collection_guidance']['amount_cents'] is None


def standard_evidence():
    payload, inputs, source = evidence()
    payload['plans'][0]['name'] = 'STANDARD'
    payload['subscriber']['additionalInformation']['group']['number'] = '105'
    payload['plans'][0]['benefits']['coPayment'] = []
    scope = {'service': {'system': 'STC', 'value': '30'},
             'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}}
    payload['plans'][0]['benefits']['deductible'] = [
        {**scope, 'amount': '350', 'timePeriod': 'CALENDAR_YEAR'},
        {**scope, 'amount': '264.24', 'timePeriod': 'REMAINING'},
    ]
    payload['plans'][0]['benefits']['outOfPocket'][0]['amount'] = '5716.66'
    inputs.update(visit_type='new', group_number='105', reason='Right facial nerve pain',
                  lines=[{'code': '99203', 'units': 1, 'service_status': 'scheduled'}])
    source['plan'] = 'STANDARD'
    return payload, inputs, source


def test_identifies_fep_standard_only_from_correlated_evidence():
    payload, _, _ = standard_evidence()
    assert commercial.plan_details(payload)['identified_product'] == 'FEP Blue Standard FEHB'
    for changed in ('group', 'member', 'name', 'marker'):
        other = deepcopy(payload)
        if changed == 'group':
            other['subscriber']['additionalInformation']['group']['number'] = '106'
        elif changed == 'member':
            other['subscriber']['memberId'] = 'X12345678'
        elif changed == 'name':
            other['plans'][0]['name'] = 'BASIC'
        else:
            other.pop('x12')
        assert commercial.plan_details(other)['identified_product'] is None


def test_fep_standard_new_patient_office_collects_forty_dollar_copay():
    result = finish(*standard_evidence())
    assert result['plan_details']['stedi']['identified_product'] == 'FEP Blue Standard FEHB'
    assert result['collection_guidance']['amount_cents'] == 4000
    assert result['collection_guidance']['basis'] == 'office_copay'
    assert [line['code'] for line in result['price_range']['lines']] == ['99203']
    assert result['price_range']['lines'][0]['benefits']['deductible_applies'] is False
    assert result['benefits']['deductible_remaining_cents'] == 26424
    assert result['requirement_evidence']['referral']['status'] == 'Not required'
    assert result['requirement_evidence']['authorization']['status'] == 'Not required'


def test_fep_standard_supported_advance_policy_can_collect_fixed_office_copay():
    result = finish(*standard_evidence(), policy_mode='collect_supported_advance')
    assert result['collection_guidance']['amount_cents'] == 4000
    assert result['collection_guidance']['basis'] == 'office_copay'


def test_fep_standard_active_service_date_with_no_returned_plan_window():
    payload, inputs, source = standard_evidence()
    payload['subscriber'].pop('dates')
    result = finish(payload, inputs, source, policy_mode='collect_supported_advance')
    assert result['benefits']['service_date_matches'] is None
    assert result['collection_guidance']['amount_cents'] == 4000


def test_fep_standard_payer_video_exception_does_not_block_in_person_office_copay():
    payload, inputs, source = standard_evidence()
    payload['subscriber'].pop('dates')
    payload['plans'][0]['benefits']['coPayment'] = [{
        'service': {'system': 'STC', 'value': '98'}, 'coverageLevel': 'INDIVIDUAL',
        'network': {'indicator': 'IN_NETWORK'}, 'amount': '40', 'timePeriod': 'VISIT',
        'messages': ['PREFERRED SPECIALIST; TELEMEDICINE EXCEPT WHEN PREVENTIVE'],
    }]
    result = finish(payload, inputs, source, policy_mode='collect_supported_advance')
    assert result['collection_guidance']['amount_cents'] == 4000
    assert result['price_range']['lines'][0]['benefit_source'] == '2026 FEP Blue Standard brochure'

    payload['plans'][0]['benefits']['coPayment'][0]['messages'] = ['PREFERRED SPECIALIST; AFTER 3 VISITS']
    conditional = finish(payload, inputs, source, policy_mode='collect_supported_advance')
    assert conditional['collection_guidance']['amount_cents'] is None


def test_fep_standard_needs_preferred_provider_and_collection_policy():
    payload, inputs, source = standard_evidence()
    payload['plans'][0]['benefits']['statuses'][0]['messages'][0] = 'Preferred benefit description only'
    assert finish(payload, inputs, source)['collection_guidance']['amount_cents'] is None
    assert finish(*standard_evidence(), approved_policy=False)['collection_guidance']['amount_cents'] is None


def test_fep_standard_signed_group_scope_supports_office_copay_without_fee_rate():
    payload, inputs, source = standard_evidence()
    benefits = api._case_benefits(payload, inputs, source, {'id': 'synthetic-case'})
    benefits.update(network_status='in_network', network_source='clinic_group_agreement')
    source.update(payer_plan_bound=True, payer_plan_names=['FEP Blue Standard FEHB'],
                  billing_provider={'npi': '1111111111'}, location_id='synthetic-office')
    evidence = {'decision': 'in_network_contract_supported',
                'product_network_source': 'official_payer_provider_manual',
                'document_id': 'synthetic-contract', 'document_sha256': 'synthetic-digest',
                'product': 'FEP Blue Standard FEHB', 'rendering_npi': NPI,
                'billing_npi': '1111111111', 'location_id': 'synthetic-office',
                'place_of_service': '11', 'service_date': DAY}
    codes = [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}]
    with patch.object(api, '_previsit_practice_path', side_effect=api.HTTPException(409, 'No FEP rate')):
        price, _ = api._previsit_range('synthetic-clinic', inputs, source, 'synthetic-bcbs',
                                      codes, benefits, payload=payload, network_evidence=evidence)
    assert price['lines'][0]['allowed_cents'] is None
    assert price['lines'][0]['benefits']['copay_cents'] == 4000
    assert price['lines'][0]['benefits']['deductible_applies'] is False

    with patch.object(api, '_previsit_practice_path', side_effect=api.HTTPException(409, 'No FEP rate')):
        unscoped, _ = api._previsit_range('synthetic-clinic', inputs, source, 'synthetic-bcbs',
                                         codes, benefits, payload=payload)
    assert unscoped['lines'][0]['benefits']['copay_cents'] is None
    with patch.object(api, '_previsit_practice_path', side_effect=api.HTTPException(409, 'No FEP rate')):
        wrong_provider, _ = api._previsit_range('synthetic-clinic', inputs, source, 'synthetic-bcbs',
            codes, benefits, payload=payload, network_evidence={**evidence, 'rendering_npi': '0000000000'})
    assert wrong_provider['lines'][0]['benefits']['copay_cents'] is None


def test_saved_fep_standard_office_refreshes_from_existing_response():
    payload, inputs, source = standard_evidence()
    result = finish(payload, inputs, source)
    result['plan_details']['stedi'].update(identified_product=None, product_evidence=None)
    result['benefits']['plan_details'].update(identified_product=None, product_evidence=None)
    result['price_range']['lines'][0]['benefits']['published_plan_rule'] = None
    result['price_range']['lines'][0]['benefit_source'] = 'Stedi'
    raw = json.dumps(payload).encode()
    case = {'previsit_raw_object_key': 'synthetic-protected-response',
            'previsit_raw_sha256': hashlib.sha256(raw).hexdigest(),
            'previsit_context': {'inputs': inputs, 'source': source, 'payer_id': 'synthetic-bcbs'}}
    payer = {'id': 'synthetic-route'}
    with patch.object(api.object_store, 'get_object_bytes', return_value=raw), \
         patch.object(api, '_previsit_practice_path', side_effect=api.HTTPException(409, 'No FEP rate')):
        restore_saved_fep_standard_product(result, case)
        assert saved_fep_standard_office_needs_reprice(result)
        _reprice_saved_new_network(result, case, 'synthetic-clinic', payer)
    assert result['plan_details']['stedi']['identified_product'] == 'FEP Blue Standard FEHB'
    assert result['benefits']['published_plan_rule']['office_specialist_copay_cents'] == 4000
    assert result['price_range']['lines'][0]['benefits']['copay_cents'] == 4000
    assert result['price_range']['lines'][0]['benefits']['deductible_applies'] is False
    assert not saved_fep_standard_office_needs_reprice(result)


def test_saved_fep_standard_requires_intact_protected_response():
    payload, inputs, source = standard_evidence()
    result = finish(payload, inputs, source)
    result['plan_details']['stedi']['identified_product'] = None
    raw = json.dumps(payload).encode()
    case = {'previsit_raw_object_key': 'synthetic-protected-response',
            'previsit_raw_sha256': 'wrong-digest',
            'previsit_context': {'inputs': inputs, 'source': source}}
    with patch.object(api.object_store, 'get_object_bytes', return_value=raw):
        restore_saved_fep_standard_product(result, case)
    assert result['plan_details']['stedi']['identified_product'] is None
