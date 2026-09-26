from copy import deepcopy
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


@pytest.mark.asyncio
async def test_name_rejection_retries_same_member_and_dob_without_guessing_names():
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


@pytest.mark.asyncio
async def test_insurance_card_requires_patient_binding_and_never_fetches_external_url():
    document = {'resourceType': 'DocumentReference', 'id': 'card', 'status': 'current', 'subject': {'reference': 'Patient/right'},
                'description': 'Insurance card', 'content': [{'attachment': {'contentType': 'application/pdf', 'url': 'https://untrusted.example/Binary/1'}}]}
    with patch.object(ecw_backend, 'settings', return_value=('client', 'https://ecw.example/fhir')), patch.object(ecw_backend, '_access_token', AsyncMock(return_value='token')), patch.object(ecw_backend, '_search', AsyncMock(return_value=[document])):
        assert await ecw_backend.insurance_cards('wrong') == []
        assert len(await ecw_backend.insurance_cards('right')) == 1
        with pytest.raises(ecw_backend.EcwBackendError):
            await ecw_backend.insurance_cards('right', document_id='card:0')
