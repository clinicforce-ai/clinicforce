"""Requirement research reads scoped facts and cannot set authorization status."""
import json

from app.insurance import insurance_research_agent
from app.insurance.staging_synthetic import case, visit


def test_new_office_scope_flags_reach_agent_without_patient_identity():
    value = visit('aetna_new_office_date_mismatch')
    value['patient_name'] = 'Private name must stay local'
    facts = insurance_research_agent._input(value)
    assert facts['service_date_matches'] is False
    assert facts['provider_network_status'] == 'unknown'
    assert facts['lines'][0]['code'] == '99203'
    assert 'benefit_scope' in facts['lines'][0]['review_flags']
    assert facts['lines'][0]['requirement_status'] == {
        'referral': 'Verification required', 'authorization': 'Verification required'}
    assert 'read_saved_requirement' in insurance_research_agent._tools_for(facts)
    assert 'patient_name' not in json.dumps(facts)


def test_requirement_tools_use_official_citation_without_saved_check_link(monkeypatch):
    value = case(diagnostic=None, approved=False)
    value['payer'] = 'Cigna Synthetic'
    value['patient_name'] = 'Must stay local'
    value['price_range']['lines'][0]['benefits']['review_reasons'] = ['Prior authorization unclear']
    official = 'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'
    value['requirement_evidence'] = {'procedures': [{'code': '99204',
        'authorization': {'status': 'Verification required', 'sources': [
            {'label': 'Official Cigna rules', 'url': official},
            {'label': 'Saved check', 'url': 'https://portal.stedi.com/app/healthcare/eligibility/checks/ec_private'}]},
        'referral': {'status': 'Verification required', 'sources': []}}]}
    calls = []

    class FakeBedrock:
        def converse(self, **request):
            calls.append(request)
            if len(calls) == 1:
                return {'output': {'message': {'role': 'assistant', 'content': [
                    {'toolUse': {'toolUseId': 'one', 'name': 'read_saved_requirement',
                                 'input': {'code': '99204'}}}]}}}
            return {'output': {'message': {'role': 'assistant', 'content': [{'text':
                json.dumps({'findings': [{'code': '99204', 'finding': 'Verify exact product',
                    'evidence_ids': [official], 'next_action': 'Ask provider services'}]})}]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    answer = insurance_research_agent.investigate(value)
    assert answer['status'] == 'complete'
    assert answer['calculation_authority'] is False
    assert answer['findings'][0]['evidence_ids'] == [official]
    assert 'read_saved_requirement' in [tool['toolSpec']['name'] for tool in calls[0]['toolConfig']['tools']]
    assert 'Must stay local' not in json.dumps(calls)
    assert 'ec_private' not in json.dumps(calls)


def test_research_request_distinguishes_unconfirmed_requirement_from_absent_source(monkeypatch):
    value = visit('aetna_new_office_date_mismatch')
    calls = []

    class FakeBedrock:
        def converse(self, **request):
            calls.append(request)
            return {'output': {'message': {'role': 'assistant', 'content': [{'text':
                json.dumps({'findings': [{'code': '99203',
                    'finding': 'The visit-date benefit and provider network need confirmation.',
                    'evidence_ids': ['synthetic-benefit-prior-date'],
                    'next_action': 'Verify the visit date and network tier.'}]})}]}}}

    monkeypatch.setattr(insurance_research_agent.boto3, 'client', lambda *args, **kwargs: FakeBedrock())
    answer = insurance_research_agent.investigate(value)
    assert answer['status'] == 'complete'
    prompt = calls[0]['messages'][0]['content'][0]['text']
    assert 'Verification required means it was evaluated' in prompt
    assert 'read_saved_requirement' in [tool['toolSpec']['name']
                                        for tool in calls[0]['toolConfig']['tools']]
    assert 'Synthetic CollectionFixture' not in prompt


def test_requirement_source_tools_expose_only_scoped_saved_references():
    value = case(diagnostic=None, approved=False)
    value['payer_product_binding'] = 'synthetic-cigna-oap'
    value['requirement_evidence'] = {'procedures': [{'code': '99204',
        'authorization': {'status': 'Verification required', 'sources': [
            {'document_id': 'synthetic-clinic-library', 'page': 2,
             'approval_status': 'approved'},
            {'url': 'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'},
            {'url': 'https://portal.stedi.com/app/healthcare/eligibility/checks/ec_private'}]},
        'referral': {'status': 'Verification required', 'sources': []}}]}
    value['delegated_administrators'] = [{'name': 'Synthetic delegated group',
        'codes': ['99204'], 'status': 'verified', 'product_binding': 'synthetic-cigna-oap',
        'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        'source_url': 'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'}]
    facts = insurance_research_agent._input(value)
    assert {'authorization', 'referral'} <= set(facts['lines'][0]['review_flags'])
    allowed = insurance_research_agent._tools_for(facts)
    assert 'read_delegated_administrator' in allowed
    clinic = insurance_research_agent._tool_result(
        'read_approved_clinic_requirement', '99204', facts, value)
    official = insurance_research_agent._tool_result(
        'read_official_payer_rule', '99204', facts, value)
    delegated = insurance_research_agent._tool_result(
        'read_delegated_administrator', '99204', facts, value)
    assert clinic['finding'][0]['source_reference'] == 'synthetic-clinic-library:2'
    assert official['finding'][0]['source_reference'].startswith('https://www.cigna.com/')
    assert delegated['finding'][0]['name'] == 'Synthetic delegated group'
    assert all(item['calculation_authority'] is False for item in (clinic, official, delegated))
    assert 'ec_private' not in json.dumps((clinic, official, delegated))
    value['delegated_administrators'][0]['product_binding'] = 'other-product'
    assert insurance_research_agent._tool_result(
        'read_delegated_administrator', '99204', facts, value)['status'] == 'unavailable'
    value['requirement_evidence']['procedures'][0]['authorization']['sources'][0].pop('approval_status')
    assert insurance_research_agent._tool_result(
        'read_approved_clinic_requirement', '99204', facts, value)['status'] == 'unavailable'
    value['requirement_evidence']['procedures'][0]['authorization'].update(
        status='Required', approval_status='Verified')
    value['requirement_evidence']['procedures'][0]['referral'].update(
        status='Required', referral_obtained='YES')
    assert not {'authorization', 'referral'} & set(
        insurance_research_agent._input(value)['lines'][0]['review_flags'])
