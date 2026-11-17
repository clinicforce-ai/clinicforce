"""A paid claim can support a bounded, labeled office collection estimate."""
from copy import deepcopy

from app.insurance.stedi_claim_collection import paid_office_scenario

DAY = '2026-10-07'


def fixture():
    coverage = {'patient_id': 'synthetic-patient', 'coverage_id': 'synthetic-coverage',
        'active_coverages': 1, 'relationship': '18', 'subscriber_patient_linked': True,
        'member_id': 'SYN123', 'patient_first': 'Alex', 'patient_last': 'Example',
        'patient_birth_date': '1994-05-03', 'subscriber_first': 'Alex',
        'subscriber_last': 'Example', 'subscriber_birth_date': '1994-05-03',
        'group_number': 'SYN-GROUP'}
    stc = lambda value: {'system': 'STC', 'value': value}
    eligibility = {'payerId': '60054', 'provider': {'npi': '1234567890'},
        'subscriber': {'memberId': 'SYN123', 'dateOfBirth': '1994-05-03',
            'name': {'person': {'firstName': 'Alex', 'lastName': 'Example'}},
            'dates': {'service': {'start': DAY}}},
        'plans': [{'name': 'Synthetic Choice POS II', 'benefits': {
            'statuses': [
                {'status': 'ACTIVE_COVERAGE', 'coverageLevel': 'EMPLOYEE_ONLY', 'service': stc('30')},
                {'status': 'ACTIVE_COVERAGE', 'coverageLevel': 'EMPLOYEE_ONLY', 'service': stc('98')}],
            'coPayment': [{'amount': '0', 'coverageLevel': 'EMPLOYEE_ONLY',
                'network': {'indicator': 'IN_NETWORK'}, 'service': stc('98'),
                'messages': ['Specialist Visit or Evaluation']}],
            'coInsurance': [{'percent': '0.2', 'coverageLevel': 'EMPLOYEE_ONLY',
                'network': {'indicator': 'IN_NETWORK'}, 'service': stc('98'),
                'messages': ['Specialist Visit or Evaluation']}],
            'deductible': [{'amount': '0', 'coverageLevel': 'INDIVIDUAL',
                'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'REMAINING',
                'service': stc('30')}],
            'outOfPocket': [{'amount': '0', 'coverageLevel': 'INDIVIDUAL',
                'network': {'indicator': 'IN_NETWORK'}, 'timePeriod': 'REMAINING',
                'service': stc('30')}],
            'limitations': [{'coverageLevel': 'EMPLOYEE_ONLY', 'service': stc('30'),
                'messages': ["Our records indicate the provider ID you entered is participating in this patient's network."]}],
        }}]}
    claim = {'subscriber': {'memberId': 'SYN123', 'firstName': 'Alex', 'lastName': 'Example'},
        'providers': [{'providerType': 'BillingProvider', 'npi': '9876543210'}],
        'claims': [{'claimStatus': {'statusCategoryCode': 'F1',
            'claimServiceDate': '20261007-20261007'},
            'serviceDetails': [{'service': {'procedureId': '99204', 'submittedUnits': '1',
                'submittedAmount': '271', 'amountPaid': '111.16'}}]}]}
    inputs = {'service_date': DAY}
    source = {'rendering_provider': {'npi': '1234567890'},
              'billing_provider': {'npi': '9876543210'}}
    return claim, eligibility, coverage, inputs, source, {'lines': [], 'payments_verified_cents': 0}


def test_paid_claim_infers_distinct_actual_cpt_and_explains_amount():
    args = fixture()
    result = paid_office_scenario(*args)
    assert result['status'] == 'estimated'
    assert result['code'] == '99204'
    assert result['allowed_cents'] == 13895
    assert result['insurer_paid_cents'] == 11116
    assert result['patient_share_cents'] == result['amount_cents'] == 2779
    assert '277 does not report' in result['reason']


def test_paid_claim_rejects_wrong_member_provider_or_ambiguous_lines():
    for change in ('member', 'provider', 'lines', 'status', 'network'):
        args = list(deepcopy(fixture()))
        if change == 'member':
            args[0]['subscriber']['memberId'] = 'OTHER'
        elif change == 'provider':
            args[0]['providers'][0]['npi'] = 'OTHER'
        elif change == 'lines':
            args[0]['claims'][0]['serviceDetails'].append(deepcopy(args[0]['claims'][0]['serviceDetails'][0]))
        elif change == 'status':
            args[0]['claims'][0]['claimStatus']['statusCategoryCode'] = 'P1'
        else:
            args[1]['plans'][0]['benefits']['limitations'] = []
        assert paid_office_scenario(*args) is None, change


def test_linked_835_controls_patient_share_and_verified_payments():
    args = list(fixture())
    args[-1] = {'payments_verified_cents': 1000, 'lines': [{
        'code': '99204', 'service_date': DAY, 'insurer_paid_cents': 11116,
        'allowed_cents': 13895, 'patient_responsibility_cents': 2779}]}
    result = paid_office_scenario(*args)
    assert result['status'] == 'adjudicated'
    assert result['amount_cents'] == 1779


def test_missing_recorded_payment_is_explicitly_an_assumption():
    args = list(fixture())
    args[-1]['payments_verified_cents'] = None
    result = paid_office_scenario(*args)
    assert result['amount_cents'] == 2779
    assert 'assume $0 pending a clinic ledger check' in result['reason']
