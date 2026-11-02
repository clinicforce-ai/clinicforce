"""Requirement research reads scoped facts and cannot set authorization status."""
import json

from app.insurance import insurance_research_agent
from app.insurance.staging_synthetic import case


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
