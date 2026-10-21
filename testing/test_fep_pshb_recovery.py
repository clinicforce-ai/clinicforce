"""Synthetic FEP PSHB evidence through the shared saved-visit estimator."""

from copy import deepcopy
from unittest.mock import patch

from app.api.routers import commercial_estimator as api
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


def finish(payload, inputs, source, *, approved_policy=True):
    case = {'id': 'synthetic-case', 'revision': 0}
    result = {'benefits': api._case_benefits(payload, inputs, source, case),
              'issues': [], 'checked_at': DAY + 'T12:00:00+00:00'}
    connections = ([{'id': 'synthetic-route', 'stedi_payer_id': 'synthetic-bcbs',
                     'status': 'draft', 'rules': {'collection_policy': {
                         'mode': 'collect_verified_copay', 'reference': 'Synthetic clinic policy',
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
