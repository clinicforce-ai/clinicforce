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
    assert benefits['identity']['status'] == 'warning'
    assert not any('identity' in reason for reason in benefits['review_reasons'])


def test_returned_service_date_does_not_verify_a_different_appointment():
    from app.insurance.commercial import normalize_stedi_271
    payload = {'subscriber': {'dates': {'service': {'start': '2026-09-25'}, 'plan': {'start': '2026-01-01', 'end': '2026-12-31'}}},
               'plans': [{'benefits': {'statuses': [{'status': 'Active Coverage'}]}}]}
    benefits = normalize_stedi_271(payload, '98', True, service_date='2026-09-28')
    assert benefits['active'] is True and benefits['service_date_matches'] is False
    assert any('Payer evidence' in reason for reason in benefits['review_reasons'])
    assert visit_estimate.arrival_collection({'complete': True, 'patient_max_cents': 5000}, benefits, {})['amount_cents'] is None
    assert normalize_stedi_271(payload, '98', True, service_date='2026-09-25')['service_date_matches'] is True
    payload['subscriber']['dates']['plan']['end'] = '2026-09-27'
    payload['plans'][0]['dates'] = {'plan': {'start': '2026-01-01', 'end': '2026-12-31'}}
    assert normalize_stedi_271(payload, '98', True, service_date='2026-09-28')['active'] is None


def test_alternative_office_levels_collect_one_common_verified_copay():
    benefits = {'active': True, 'network_status': 'in_network'}
    lines = [{'code': str(code), 'units': 1, 'benefits': {
        'copay_cents': 4000, 'copay_period': 'VISIT', 'cost_share': 'copay',
        'deductible_applies': False, 'oop_remaining_cents': 100000,
        'requirements': {kind: {'cleared': True} for kind in ('referral', 'authorization')},
    }} for code in range(99202, 99206)]
    price = {'mode': 'alternatives', 'lines': lines}
    value = visit_estimate.arrival_collection(price, benefits, {})
    assert value['amount_cents'] == 4000 and value['status'] == 'copay_only'
    for change in ({'copay_cents': 5000}, {'deductible_applies': True}, {'requires_review': True},
                   {'requirements': {'referral': {'cleared': False}}}):
        varied = deepcopy(price)
        varied['lines'][-1]['benefits'].update(change)
        assert visit_estimate.arrival_collection(varied, benefits, {})['amount_cents'] is None
    assert visit_estimate.arrival_collection({**price, 'conditional': True}, benefits, {})['amount_cents'] is None
    assert visit_estimate.arrival_collection(price, {**benefits, 'network_status': 'unknown'}, {})['amount_cents'] is None
    for row in lines:
        row['benefits']['oop_remaining_cents'] = 0
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] == 0
    complete = {**price, 'complete': True, 'patient_min_cents': 0, 'patient_max_cents': 0}
    for row in lines:
        row['benefits'].update(cost_share='coinsurance', copay_cents=None)
    assert visit_estimate.arrival_collection(complete, benefits, {})['amount_cents'] == 0
    assert visit_estimate.arrival_collection({**complete, 'conditional': True}, benefits, {})['amount_cents'] is None
    assert visit_estimate.arrival_collection({**complete, 'patient_max_cents': 4000}, benefits, {})['amount_cents'] is None


def test_saved_ecw_eligibility_reconciles_by_time_without_inventing_benefits():
    saved = {'status': 'verified_inactive', 'active': False, 'checked_at': '2026-09-29T10:00:00Z'}
    assert visit_estimate.reconcile_eligibility(saved, {'active': True}, '2026-09-29T11:00:00Z')['requires_review'] is False
    conflict = visit_estimate.reconcile_eligibility(saved, {'active': True}, '2026-09-29T09:00:00Z')
    assert conflict['requires_review'] is True
    benefits = {'active': True, 'network_status': 'in_network', 'review_reasons': [conflict['message']]}
    assert visit_estimate.arrival_collection({'complete': True, 'patient_max_cents': 0}, benefits, {})['amount_cents'] is None
    assert visit_estimate.reconcile_eligibility(saved, {'active': True}, None)['requires_review'] is True
    assert visit_estimate.reconcile_eligibility(saved, {'active': False}, saved['checked_at'])['requires_review'] is False
    assert 'still needed' in visit_estimate.reconcile_eligibility(saved, {}, None)['message']


def test_uncoded_appointment_uses_em_alternatives_without_model_call():
    from app.insurance import cpt_suggester
    for kind, expected in [('new', range(99202, 99206)), ('established', range(99212, 99216))]:
        inputs = {'visit_type': kind, 'reason': 'Neurology consultation', 'lines': []}
        codes = api._previsit_codes(inputs)
        with patch.object(cpt_suggester.boto3, 'client') as model:
            plan = cpt_suggester.recommend_visit_plan(inputs, {}, codes)
        model.assert_not_called()
        assert [row['code'] for row in codes] == list(map(str, expected))
        assert plan['recommended_ids'] == ([] if kind == 'new' else [codes[1]['id']])
        assert plan['method'] == 'appointment'
    assert ecw_backend._display({'name': [{'use': 'official', 'text': 'Dr. Alex Example'}]}) == 'Dr. Alex Example'
