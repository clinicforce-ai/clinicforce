from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from app.insurance import visit_estimate, stedi_payers, v1_sources, ecw_backend
from app.api.routers import commercial_estimator as api


def line(copay=2500, cost_share='copay'):
    return {'allowed_cents': 15000, 'benefits': {'copay_cents': copay, 'copay_period': 'VISIT', 'copay_id': 'office',
        'cost_share': cost_share, 'review_reasons': [], 'requirements': {'referral': {'cleared': True}, 'authorization': {'cleared': True}}}}


def test_collection_includes_all_visit_obligations_and_subtracts_verified_payments():
    benefits = {'active': True, 'network_status': 'in_network'}
    price = {'complete': True, 'lines': [line()], 'patient_max_cents': 13000}
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] == 13000
    result = visit_estimate.arrival_collection(price, benefits, {'payments': {'reference': 'receipt', 'amount_cents': 1000}})
    assert result['amount_cents'] == 12000 and result['payments_received_cents'] == 1000
    assert result['prior_balance_cents'] is None  # Not silently treated as a zero balance.
    assert visit_estimate.arrival_collection(price, {**benefits, 'active': None}, {})['amount_cents'] is None
    assert visit_estimate.arrival_collection(price, {**benefits, 'network_status': 'unknown'}, {})['amount_cents'] is None
    result = visit_estimate.arrival_collection({**price, 'conditional': True}, benefits, {})
    assert result['amount_cents'] is None and result['estimated_cents'] == 13000


def test_exact_directory_name_precedes_another_payers_shared_alias():
    payer = {'stediId': 'HPQRS', 'primaryPayerId': '60054', 'displayName': 'Aetna', 'transactionSupport': {'eligibilityCheck': 'SUPPORTED'}}
    other = {**payer, 'stediId': 'YEVAB', 'primaryPayerId': '54398', 'displayName': 'Allina Health Aetna', 'names': ['Aetna']}
    assert stedi_payers.select_payer([{'payer': payer}, {'payer': other}], 'AETNA', []) == payer
    assert stedi_payers.select_payer([{'payer': payer}, {'payer': {**payer, 'stediId': 'other'}}], 'AETNA', []) is None


def test_plain_card_id_and_printed_insurer_are_extracted_without_birthdate_invention():
    fields, evidence = v1_sources.card_fields([(1, 'BlueCross BlueShield\nof Texas\nName: SAMPLE M PERSON\nID: ABC1234\nPlan (80840): 9101000260\nPart B RxID: DIFFERENT')])
    assert fields['member_id'] == 'ABC1234' and fields['member_name'] == 'SAMPLE M PERSON'
    assert fields['payer'] == 'Blue Cross Blue Shield of Texas' and 'birth_date' not in fields
    assert evidence['member_id']['quote'] == 'ID: ABC1234'


def test_member_identifier_fallback_never_chooses_a_group_or_ambiguous_id():
    identifier = {'type': {'coding': [{'code': 'MB'}]}, 'value': 'ABC123'}
    assert ecw_backend.coverage_member_id({'identifier': [identifier]}) == 'ABC123'
    assert ecw_backend.coverage_member_id({'identifier': [identifier, {**identifier, 'value': 'OTHER'}]}) is None
    assert ecw_backend.coverage_member_id({'identifier': [{'value': 'GROUP123'}]}) is None
    assert ecw_backend.coverage_record_status({'extension': [{'url': 'http://eclinicalworks.com/supportingInfo/patientdemographics/coverage-eligibilityStatus', 'valueString': 'E - Eligible'}]})['status'] == 'E - Eligible'


class AsyncEvidenceTests(IsolatedAsyncioTestCase):
    async def test_name_rejection_retries_same_member_and_dob_without_guessing_names(self):
        client = AsyncMock()
        client.post.side_effect = [httpx.Response(200, json={'errors': [{'code': '73'}]}), httpx.Response(200, json={'plans': []})]
        body = {'payerId': 'payer', 'subscriber': {'memberId': 'ABC123', 'dateOfBirth': '1980-01-01', 'name': {'person': {'firstName': 'Sample', 'lastName': 'Person'}}}}
        await api._post_stedi_eligibility(client, {}, body, deepcopy(body))
        assert client.post.await_count == 2
        assert client.post.call_args.kwargs['json']['subscriber'] == {'memberId': 'ABC123', 'dateOfBirth': '1980-01-01'}
        client.post.reset_mock(side_effect=True)
        client.post.return_value = httpx.Response(200, json={'errors': [{'code': '73'}]})
        await api._post_stedi_eligibility(client, {}, {**body, 'dependent': {'name': 'Dependent'}}, {**body, 'dependent': {'name': 'Dependent'}})
        assert client.post.await_count == 1


    async def test_insurance_card_requires_patient_binding_and_never_fetches_external_url(self):
        document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current', 'subject': {'reference': 'Patient/right'},
                    'description': 'Insurance card', 'content': [{'attachment': {'contentType': 'application/pdf', 'url': 'https://untrusted.example/Binary/1'}}]}
        with patch.object(ecw_backend, 'settings', return_value=('client', 'https://ecw.example/fhir')), patch.object(ecw_backend, '_access_token', AsyncMock(return_value='token')), patch.object(ecw_backend, '_search', AsyncMock(return_value=[document])):
            assert await ecw_backend.insurance_cards('wrong') == []
            assert len(await ecw_backend.insurance_cards('right')) == 1
            with pytest.raises(ecw_backend.EcwBackendError):
                await ecw_backend.insurance_cards('right', document_id='card:0')


def test_verified_office_copay_is_separate_from_an_unavailable_total():
    benefits = {'active': True, 'network_status': 'in_network'}
    office = {'code': '99204', 'units': 1, 'benefits': {'copay_cents': 5000, 'copay_period': 'VISIT',
        'cost_share': 'copay', 'deductible_applies': False, 'oop_remaining_cents': 100000, 'requires_review': False}}
    price = {'complete': False, 'lines': [office]}
    value = visit_estimate.arrival_collection(price, benefits, {'payments': {'amount_cents': 1000, 'reference': 'receipt'}})
    assert value['status'] == 'copay_only' and value['amount_cents'] == 4000
    assert value['verified_copay_cents'] == 5000 and value['estimated_cents'] is None
    for changes in [{'has_secondary': True}, {'network_status': 'unknown'}, {'review_reasons': ['Confirm patient identity']}]:
        assert visit_estimate.arrival_collection(price, {**benefits, **changes}, {})['amount_cents'] is None
    office['benefits']['deductible_applies'] = True
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] is None


def test_ceo_deductible_then_coinsurance_examples_use_allowed_amount():
    from app.insurance.commercial import estimate
    benefits = {'active': True, 'network_status': 'in_network', 'coverage_level': 'individual',
                'deductible_remaining_cents': 10000, 'oop_remaining_cents': 100000, 'coinsurance_percent': '20'}
    lines = [{'code': '99204', 'units': 1, 'allowed_cents': 24078}]
    result = estimate(lines, benefits, {'cost_share': 'coinsurance'})
    assert result['patient_cents'] == 12816 and result['deductible_cents'] == 10000 and result['coinsurance_cents'] == 2816
    result = estimate(lines, {**benefits, 'deductible_remaining_cents': 50000}, {'cost_share': 'coinsurance'})
    assert result['patient_cents'] == 24078 and result['coinsurance_cents'] == 0


def test_payer_dependent_details_and_identity_mismatch_are_preserved():
    from app.insurance.commercial import payer_person, plan_details
    subscriber = {'name': {'person': {'firstName': 'Parent', 'lastName': 'Person'}}, 'memberId': 'ABC'}
    dependent = {'name': {'person': {'firstName': 'Pat', 'lastName': 'Different'}}, 'dateOfBirth': '1980-01-01',
                 'additionalInformation': {'group': {'number': 'GROUP'}, 'plan': {'number': 'PLAN'}}}
    payload = {'subscriber': subscriber, 'dependent': dependent, 'plans': []}
    assert payer_person(subscriber)['firstName'] == 'Parent'
    assert plan_details(payload)['group_number'] == 'GROUP' and plan_details(payload)['plan_number'] == 'PLAN'
    benefits = api._previsit_benefits(payload, {'member_id': 'ABC', 'active_coverages': 1,
        'patient_first': 'Pat', 'patient_last': 'Example', 'patient_birth_date': '1980-01-01'})
    assert benefits['requires_review'] and any('identity' in reason for reason in benefits['review_reasons'])
