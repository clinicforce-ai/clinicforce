"""Synthetic regressions for the result-first workflow; no live payer traffic."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from app.api.routers import commercial_estimator as api, staff
from app.insurance import commercial, ecw_backend, identity, visit_estimate

DAY = date.today().isoformat()


def policy():
    return {'coverage_id': 'policy-a', 'payer_id': 'payer-a', 'member_id': 'ABC123', 'active_coverages': 1,
            'patient_first': 'Alex', 'patient_last': 'Morgan', 'patient_birth_date': '2001-02-03',
            'subscriber_first': 'Alex', 'subscriber_last': 'Morgan', 'relationship': '18'}


def response(last='Morgan'):
    base = {'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'Y', 'serviceTypeCodes': ['30']}
    return {'subscriber': {'memberId': 'ABC123', 'firstName': 'Sam', 'lastName': 'Other'},
            'dependent': {'firstName': 'Alex', 'lastName': last, 'dateOfBirth': '2001-02-03', 'relationToSubscriber': 'CHILD'},
            'benefitsInformation': [{**base, 'code': '1', 'planCoverageDescription': 'Choice Medical'},
                {**base, 'code': 'C', 'timeQualifierCode': '29', 'benefitAmount': '0'},
                {**base, 'code': 'G', 'timeQualifierCode': '29', 'benefitAmount': '1000'},
                {**base, 'code': 'B', 'timeQualifierCode': '7', 'benefitAmount': '40',
                 'authOrCertIndicator': 'N', 'additionalInformation': [{'description': 'No referral required'}]}]}


def test_dependent_patient_is_compared_separately_from_policyholder():
    result = identity.compare(response(), policy())
    assert result['status'] == 'matched' and result['policy_differences']
    assert not any('identity' in r for r in api._previsit_benefits(response(), policy(), DAY)['review_reasons'])
    raw = response('Mór-gan')
    raw['dependent']['dateOfBirth'] = '20010203'
    assert identity.compare(raw, policy())['status'] == 'matched'


def test_name_resolution_is_bound_to_patient_policy_and_payer_not_visit():
    raw = response('Former')
    current = identity.compare(raw, policy())
    assert current['status'] == 'warning' and current['can_confirm']
    evidence = {'binding': current['binding'], 'reference': 'Payer call 123', 'actor_id': 'reviewer', 'verified_at': DAY}
    assert identity.compare(raw, {**policy(), 'appointment_id': 'next'}, evidence)['status'] == 'reconciled'
    assert identity.compare(raw, {**policy(), 'patient_first': ' ALEX '}, evidence)['status'] == 'reconciled'
    for key, value in [('member_id', 'DIFFERENT'), ('patient_birth_date', '2002-02-03'), ('payer_id', 'other')]:
        changed = identity.compare(raw, {**policy(), key: value}, evidence)
        assert changed['status'] != 'reconciled'
    assert not identity.compare(raw, {**policy(), 'member_id': ''})['can_confirm']
    public = staff.public({'patient': {'id': 'p', 'insurance_identities': {'private': evidence}}})
    assert public['patient'] == {'id': 'p'}


def test_true_duplicates_resolve_but_hidden_restrictions_remain_distinct():
    raw = response()
    raw['benefitsInformation'].append(deepcopy(raw['benefitsInformation'][-1]))
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY)
    assert benefits['copay_cents'] == 4000 and len(benefits['selected_benefit_ids']) == 2
    raw['benefitsInformation'][-1]['placeOfService'] = '22'
    assert commercial.normalize_stedi_271(raw, '98', True)['copay_cents'] is None


def test_office_service_hierarchy_respects_notes_and_dates():
    raw = response()
    base = raw['benefitsInformation'].pop()
    raw['benefitsInformation'] += [
        {**base, 'serviceTypeCodes': ['98']},
        {**base, 'serviceTypeCodes': ['BY'], 'benefitAmount': '80', 'messages': [{'message': 'INJECTIONS'}]},
        {**base, 'serviceTypeCodes': ['5'], 'benefitAmount': '25'},
        {**base, 'serviceTypeCodes': ['BY'], 'benefitAmount': '50', 'benefitsDateInformation': {'planEnd': '20000101'}},
    ]
    benefits = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY)
    assert benefits['copay_cents'] == 4000 and benefits['resolved_service_type'] == '98'
    raw['benefitsInformation'].append({**base, 'serviceTypeCodes': ['BQ'], 'benefitAmount': '30'})
    neuro = commercial.normalize_stedi_271(raw, '98', True, procedure_code='99204', service_date=DAY, office_service_types=['BQ', 'BY', '98'])
    assert neuro['copay_cents'] == 3000 and neuro['resolved_service_type'] == 'BQ'
    assert len(neuro['reported_options']) == 7  # Full payer evidence remains available.


def test_daily_copay_is_allocated_once_and_zero_percent_is_not_an_extra_charge():
    raw = response()
    raw['benefitsInformation'].append({**raw['benefitsInformation'][-1], 'code': 'A', 'benefitPercent': '0'})
    codes = [{'id': 'office', 'code': '99204', 'units': 1}, {'id': 'test', 'code': '93000', 'units': 1}]
    rates = [{'code': row['code'], 'allowed_cents': 15000} for row in codes]
    benefits = commercial.normalize_stedi_271(raw, '98', True)
    result = visit_estimate.price_lines(codes, rates, benefits, {'cost_share': 'payer_returned', 'service_type_by_code': {'93000': '98'}}, payload=raw, service_date=DAY)
    assert result['complete'] and result['patient_max_cents'] == 4000
    assert [row['calculation']['copay_cents'] for row in result['lines']] == [4000, 0]


def test_zero_oop_gives_conditional_result_without_clearing_identity_or_collecting():
    raw = response('Former')
    raw['benefitsInformation'][2]['benefitAmount'] = '0'
    benefits = api._previsit_benefits(raw, policy(), DAY)
    price = visit_estimate.price_lines([{'id': 'office', 'code': '99204', 'units': 1}], [], benefits, {}, payload=raw, service_date=DAY)
    planning = visit_estimate.planning_estimate(price, benefits, benefits['identity'])
    assert planning['amount_cents'] == 0 and not planning['collectable']
    assert len(planning['assumptions']) == 2
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] is None
    for changes in [{'active': False}, {'network_status': 'out_of_network'}, {'has_secondary': True}, {'service_date_matches': False}, {'review_reasons': ['Verify payer dates']}]:
        assert visit_estimate.planning_estimate(price, {**benefits, **changes}, benefits['identity']) is None
    assert visit_estimate.planning_estimate(price, benefits, {'status': 'review', 'can_confirm': False}) is None
    price['lines'][0]['benefits']['requirements']['authorization']['status'] = 'denied'
    assert visit_estimate.planning_estimate(price, benefits, benefits['identity']) is None


def test_ecw_context_preserves_comments_and_financial_fields_without_coding_them():
    comment = 'Patient wrote: headaches; please explain treatment.\nDo not change my words.'
    visit = {'resourceType': 'Encounter', 'id': 'v', 'comment': comment,
             'extension': [{'url': 'https://ehr.test/General_Notes', 'valueString': 'Payment preference: insurance'},
                           {'url': 'https://ehr.test/visit-copay', 'valueMoney': {'value': 250, 'currency': 'USD'}}],
             'text': {'div': '<div><p>Visit context</p><script>hidden()</script></div>'}}
    fields = ecw_backend.appointment_fields(visit)
    assert fields[0]['text'] == comment
    assert any(row['text'] == '250 USD' for row in fields)
    assert fields[-1]['text'] == 'Visit context'
    assert not ecw_backend._visit_context(visit, [], [], [])


def test_general_notes_fill_only_missing_policy_fields_from_labeled_header():
    header = 'Patient payment preference : Example Health - Texas, Subscriber No. : ( ABC123 )'
    fields = [{'label': 'General Notes', 'text': header + '\nPatient entered comments on healow : symptoms and preferences', 'source': 'Encounter/v/GeneralNotes'}]
    fallback = ecw_backend.appointment_insurance_fallback
    assert fallback(fields, None, None, 1) == {'payer': 'Example Health - Texas', 'member_id': 'ABC123', 'source': 'Encounter/v/GeneralNotes'}
    assert fallback(fields, 'ABC123', None, 1)['payer'] == 'Example Health - Texas'
    assert fallback(fields, None, 'Example Health - Texas', 1)['member_id'] == 'ABC123'
    assert fallback(fields, 'ABC123', 'Example Health - Texas', 1) == {}
    assert fallback(fields, 'OTHER', None, 1) == {}
    assert fallback(fields, None, 'Different Health', 1) == {}
    for count in (0, 2):
        assert fallback(fields, None, None, count) == {}
    assert fallback([*fields, {**fields[0], 'text': header.replace('ABC123', 'OTHER')}], None, None, 1) == {}
    assert fallback([{**fields[0], 'text': 'Patient entered comments on healow : ' + header}], None, None, 1) == {}
    assert fallback([{**fields[0], 'label': 'Patient comments'}], None, None, 1) == {}


def test_product_mapping_is_scoped_to_current_network_evidence_dates_and_plan(monkeypatch):
    payer = {'id': 'payer-a', 'category': 'commercial_medical', 'status': 'staff_guidance_enabled'}
    network = {'id': 'network', 'payer_connection_id': 'payer-a', 'status': 'verified', 'billing_npi': '1',
               'rendering_npi': '2', 'location_id': 'office', 'place_of_service': '11', 'plan_matcher': 'CI',
               'effective_from': '2020-01-01', 'evidence_reference': 'contract-v1'}
    mapping = {'products': ['Choice Medical'], 'reference': 'Contract p2', 'actor_id': 'admin',
               'valid_from': DAY, 'valid_to': DAY, 'network_id': 'network', 'network_reference': 'contract-v1',
               'plan_scope': {'group_number': 'group-a', 'plan_number': None}}
    profile = {'payer_connection_id': 'payer-a', 'provider_network_id': 'network', 'status': 'staff_guidance_enabled',
               'plan_matcher': 'CI', 'rules': {'product_mappings': [mapping]}}
    monkeypatch.setattr(api.commercial_store, 'connection', lambda *a: payer)
    monkeypatch.setattr(api.commercial_store, 'list_networks', lambda *a: [network])
    monkeypatch.setattr(api.commercial_store, 'list_profiles', lambda *a: [profile])
    inputs = {'payer_id': 'payer-a', 'service_date': DAY, 'rendering_provider': {'npi': '2'}, 'place_of_service': '11'}
    source = {'billing_provider': {'npi': '1'}, 'location_id': 'office', 'plan': 'CI',
              'payer_plan_names': ['Choice Medical'], 'payer_plan_scope': mapping['plan_scope']}
    assert api._practice_path('clinic', inputs, source)[2] == profile
    for changes in [{'payer_plan_scope': {'group_number': 'another'}}, {'payer_plan_names': ['Other Medical']}]:
        with pytest.raises(HTTPException): api._practice_path('clinic', inputs, {**source, **changes})
    network['evidence_reference'] = 'contract-v2'
    with pytest.raises(HTTPException): api._practice_path('clinic', inputs, source)


def test_identity_review_persists_across_visits_and_rejects_stale_or_unauthorized_edits(monkeypatch):
    inputs = {'service_date': DAY, 'payer_id': 'payer-a', 'member_id': 'ABC123', 'relationship': '18',
              'subscriber': {'firstName': 'Alex', 'lastName': 'Morgan', 'birthDate': '2001-02-03'},
              'patient': {'patient_first_name': 'Alex', 'patient_last_name': 'Morgan', 'patient_birth_date': '2001-02-03'},
              'lines': [{'code': '99204', 'units': 1, 'service_status': 'scheduled'}], 'visit_type': 'new'}
    source = {'appointment_id': 'visit-a', 'coverage_id': 'policy-a', 'active_coverages': 1, 'plan': 'CI'}
    session = {'practice_id': 'clinic', 'patient_id': 'patient', 'share_manifest_id': '', 'intake_id': 'intake',
               'staff': {'id': 'staff', 'role': 'staff', 'display_name': 'Reviewer'}, 'intake': {'fields': {}, 'patient': {'id': 'patient', 'given_name': 'Alex', 'family_name': 'Morgan', 'birth_date': '2001-02-03'}}}
    raw = response('Former'); raw['benefitsInformation'][2]['benefitAmount'] = '0'
    checked = datetime.now(timezone.utc).isoformat()
    result = {'checked_at': checked, 'check_id': 'case', 'issues': [], 'suggestions': None,
              'codes': api._previsit_codes(inputs), 'benefits': api._case_benefits(raw, inputs, source, {})}
    case = {'id': 'case', 'revision': 1, 'source_mode': 'commercial_previsit',
            'previsit': {'checked_at': checked}, 'previsit_raw_object_key': 'protected'}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    monkeypatch.setattr(api, '_ecw_practice_config', lambda *a: {})
    monkeypatch.setattr(api.ecw_backend, 'fetch_inputs', AsyncMock(return_value=(inputs, [], source)))
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda *a: [])
    monkeypatch.setattr(api.practice_prices, 'current', lambda *a: None)
    monkeypatch.setattr(api.nmct_cash, 'schedule', lambda *a: None)
    monkeypatch.setattr(api.cpt_suggester, 'recommend_visit_plan', lambda i, s, rows: {
        'version': 2, 'recommended_ids': [rows[0]['id']], 'recommended_units': {rows[0]['id']: 1}})
    monkeypatch.setattr(api.store, 'get_case', lambda *a, **kw: case)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', lambda *a: json.dumps(raw).encode())
    monkeypatch.setattr(api, '_save', lambda row, *a: {**row, 'revision': row['revision'] + 1})
    persist = MagicMock(side_effect=lambda patient, practice, proof: session['intake']['patient'].update(insurance_identities={proof['binding']: proof}))
    monkeypatch.setattr(api.staff_store, 'save_insurance_identity', persist)
    api._finish_previsit(result, case, inputs, source, raw, session, 'payer-a')
    token = result['context_token']
    confirm = {'binding': result['identity']['binding'], 'reference': 'Patient card 123', 'notes': 'Patient verified former surname on this insurance.'}
    updated = asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, identity_confirmation=confirm), None))
    assert updated['identity']['status'] == 'reconciled'
    assert not any('identity' in r for r in updated['benefits']['review_reasons'])
    assert persist.call_args.args[:2] == ('patient', 'clinic')
    later_source = {**source, 'appointment_id': 'visit-b'}
    later = {**result, 'benefits': api._case_benefits(raw, inputs, later_source, {})}
    api._finish_previsit(later, {}, inputs, later_source, raw, session, 'payer-a')
    assert later['identity']['status'] == 'reconciled'
    with pytest.raises(HTTPException) as stale:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=0, identity_confirmation=confirm), None))
    assert stale.value.status_code == 409 and persist.call_count == 1
    with pytest.raises(HTTPException) as unauthorized:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, product_mapping={
            'reference': 'Contract', 'notes': 'Verified contract', 'valid_from': DAY, 'valid_to': DAY}), None))
    assert unauthorized.value.status_code == 403
    with pytest.raises(HTTPException) as mismatch:
        asyncio.run(api.update_visit_review('case', api.VisitReviewInput(context_token=token, revision=1, identity_confirmation={**confirm, 'binding': 'f' * 64}), None))
    assert mismatch.value.status_code == 409 and persist.call_count == 1


def test_name_difference_does_not_block_calculating_the_returned_policy():
    raw = response('Former')
    benefits = api._previsit_benefits(raw, policy(), DAY)
    benefits['network_status'] = 'in_network'
    price = visit_estimate.price_lines([{'id': 'office', 'code': '99204', 'units': 1}],
        [{'code': '99204', 'allowed_cents': 15000}], benefits, {'cost_share': 'payer_returned'}, payload=raw, service_date=DAY)
    assert price['complete'] and not price['conditional']
    result = visit_estimate.planning_estimate(price, benefits, benefits['identity'])
    assert result['amount_cents'] == 4000 and not result['collectable']
    assert benefits['identity']['status'] == 'warning'  # Neither EHR nor payer identity is overwritten.
    assert visit_estimate.arrival_collection(price, benefits, {})['amount_cents'] == 4000


def test_surname_notice_requires_first_name_dob_and_member_corroboration():
    raw = response('Former')
    assert identity.compare(raw, policy())['status'] == 'warning'
    for target, key, value in [(raw['dependent'], 'dateOfBirth', '2002-02-03'),
                               (raw['dependent'], 'firstName', 'Other'),
                               (raw['subscriber'], 'memberId', 'different')]:
        original = target[key]
        target[key] = value
        benefits = api._previsit_benefits(raw, policy(), DAY)
        assert benefits['identity']['status'] == 'review'
        assert any('identity' in reason for reason in benefits['review_reasons'])
        assert visit_estimate.arrival_collection({'complete': True, 'patient_max_cents': 4000},
            {**benefits, 'network_status': 'in_network'}, {})['amount_cents'] is None
        target[key] = original
        del target[key]
        assert identity.compare(raw, policy())['status'] == 'review'
        target[key] = original


def test_optional_test_mentions_do_not_replace_the_office_visit():
    for reason in ['Neurology consultation; consider EMG', 'New patient, possible EMG/NCS',
                   'Neurology consultation and EMG if needed', 'No EMG planned']:
        inputs = {'visit_type': 'new', 'reason': reason, 'lines': []}
        assert not visit_estimate.is_emg_visit(inputs)
        assert [row['code'] for row in api._previsit_codes(inputs)] == ['99202', '99203', '99204', '99205']
    for reason in ['EMG/NCS both arms', 'EMG for possible radiculopathy', 'EMG, no separate office visit']:
        assert visit_estimate.is_emg_visit({'reason': reason})
    assert visit_estimate.is_emg_visit({'reason': 'Consider EMG', 'lines': [{'code': '95909', 'service_status': 'scheduled'}]})


def test_saved_payer_response_is_scoped_and_never_cached(monkeypatch):
    session = {'patient_id': 'patient', 'share_manifest_id': '', 'practice_id': 'clinic', 'intake_id': 'intake', 'staff': {'id': 'staff', 'display_name': 'Clinician'}}
    monkeypatch.setattr(api, '_session', AsyncMock(return_value=session))
    case = {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'complete'}, 'previsit_raw_object_key': 'protected'}
    read_case = MagicMock(return_value=case)
    read_object = MagicMock(return_value=b'{"id":"payer-reference","benefitsInformation":[]}')
    monkeypatch.setattr(api.store, 'get_case', read_case)
    monkeypatch.setattr(api.object_store, 'get_object_bytes', read_object)
    result = asyncio.run(api.saved_payer_response('case', None))
    assert result.headers['Cache-Control'] == 'no-store'
    assert json.loads(result.body)['response']['id'] == 'payer-reference'
    assert read_case.call_args.kwargs == {'practice_id': 'clinic', 'intake_id': 'intake', 'staff_actor': {'id': 'staff', 'display_name': 'Clinician'}}
    read_object.reset_mock()
    read_case.return_value = None
    with pytest.raises(HTTPException) as missing:
        asyncio.run(api.saved_payer_response('foreign-case', None))
    assert missing.value.status_code == 404
    read_object.assert_not_called()
    for state in ['pending', 'indeterminate']:
        read_case.return_value = {**case, 'previsit': {'request_state': state}}
        with pytest.raises(HTTPException): asyncio.run(api.saved_payer_response('case', None))
    read_object.assert_not_called()


def test_shared_accumulator_with_office_and_injection_labels_is_not_discarded():
    raw = response('Former')
    del raw['benefitsInformation']
    common = {'coverageLevel': 'INDIVIDUAL', 'network': {'indicator': 'IN_NETWORK'}}
    raw['plans'] = [{'name': 'Example PPO', 'benefits': {
        'statuses': [{'status': 'ACTIVE', 'service': {'system': 'STC', 'value': '30'}}],
        'coPayment': [
            {**common, 'service': {'system': 'STC', 'value': 'BY'}, 'amount': '40', 'timePeriod': 'DAY', 'messages': ['INJECTIONS - PROFESSIONAL']},
            {**common, 'service': {'system': 'STC', 'value': '98'}, 'amount': '40', 'timePeriod': 'DAY', 'messages': ['OFFICE VISIT - PROFESSIONAL']}],
        'outOfPocket': [{**common, 'service': {'system': 'STC', 'value': '30'}, 'amount': '0', 'timePeriod': 'REMAINING',
            'messages': ['DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL;INJECTIONS - PROFESSIONAL;LABORATORY- PROFESSIONAL;OFFICE VISIT - PROFESSIONAL;SURGERY- PROFESSIONAL;XRAY - PROFESSIONAL']}]
    }}]
    benefits = api._previsit_benefits(raw, policy(), DAY)
    priced = visit_estimate.price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], benefits, {}, payload=raw, service_date=DAY)
    terms = priced['lines'][0]['benefits']
    assert terms['copay_cents'] == 4000 and terms['oop_remaining_cents'] == 0
    assert terms['resolved_service_type'] == '98'
    assert visit_estimate.planning_estimate(priced, benefits, benefits['identity'])['amount_cents'] == 0
    # Mentioning injections within an office benefit must not remove it either.
    raw['plans'][0]['benefits']['coPayment'][1]['messages'] = ['Office visit including injections']
    assert commercial.normalize_stedi_271(raw, '98', True, procedure_code='99203', service_date=DAY)['copay_cents'] == 4000
