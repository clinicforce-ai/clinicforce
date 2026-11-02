"""Bounded Claude Sonnet 5 review of already saved, selected benefit evidence."""
from __future__ import annotations

import hashlib
import json
import os

import boto3
from botocore.config import Config

MODEL_ID = os.getenv('INSURANCE_RESEARCH_MODEL_ID', 'us.anthropic.claude-sonnet-5')
TOOL_NAMES = ('read_saved_ecw_context', 'read_selected_benefit', 'read_reference_price',
              'read_service_template', 'read_policy_scope')


def _tool_config() -> dict:
    return {'tools': [{'toolSpec': {'name': name,
        'description': 'Read one deidentified, already saved and selected visit fact. Never approve collection.',
        'inputSchema': {'json': {'type': 'object', 'properties': {'code': {'type': 'string'}},
                                 'required': ['code'], 'additionalProperties': False}}}}
        for name in TOOL_NAMES]}


def _tool_result(name: str, code: str, facts: dict, result: dict) -> dict:
    selected = [row for row in facts['lines'] if row['code'] == code]
    if len(selected) != 1:
        return {'status': 'unavailable'}
    if name == 'read_selected_benefit':
        return {'status': 'available', 'source_system': result.get('benefit_source') or 'saved_eligibility',
                'finding': selected[0], 'calculation_authority': False}
    if name == 'read_saved_ecw_context':
        reconciliation = result.get('eligibility_reconciliation') or {}
        return {'status': 'available' if reconciliation else 'unavailable',
                'finding': {'benefit_source': result.get('benefit_source'),
                            'service_date': result.get('service_date'),
                            'reconciliation_status': reconciliation.get('status')},
                'calculation_authority': False}
    if name == 'read_reference_price':
        rows = [row for row in (result.get('temporary_rate_reference') or {}).get('lines') or []
                if row.get('code') == code and row.get('rate_version')
                and str(row.get('source_reference') or '').startswith('library:')]
        return {'status': 'available' if len(rows) == 1 else 'unavailable',
                'finding': {'unit_reference_cents': rows[0]['unit_reference_cents'],
                            'rate_version': rows[0]['rate_version'],
                            'source_reference': rows[0]['source_reference']} if len(rows) == 1 else None,
                'calculation_authority': False}
    if name == 'read_service_template':
        from app.insurance.cpt_rules import service_definition
        definition = service_definition(code)
        return {'status': 'available', 'finding': {'code': code,
            'family': definition.get('family'), 'role': definition.get('role')},
            'calculation_authority': False}
    if name == 'read_policy_scope':
        policies = result.get('visit_collection_policies') or []
        return {'status': 'available' if policies else 'unavailable',
                'finding': [{'mode': item.get('mode'), 'version': item.get('version'),
                             'service_groups': item.get('service_groups'),
                             'allow_benchmark_advance': item.get('allow_benchmark_advance')}
                            for item in policies[:5] if isinstance(item, dict)],
                'calculation_authority': False}
    return {'status': 'unavailable'}


def _review_flags(benefits: dict) -> list[str]:
    # Review prose can contain patient details; only controlled categories leave ClinicForce.
    reasons = ' '.join(str(value).lower() for value in benefits.get('review_reasons') or [])
    return [code for code, terms in {
        'copay_interaction': ('copay', 'coinsurance', 'cost-sharing'),
        'deductible_applicability': ('deductible',),
        'oop_applicability': ('out-of-pocket',),
        'benefit_scope': ('conditional', 'service', 'benefit'),
    }.items() if any(term in reasons for term in terms)]


def _input(result: dict) -> dict:
    """Send selected financial facts only; never patient identity or raw payer objects."""
    lines = (result.get('price_range') or {}).get('lines') or []
    return {'lines': [{'code': line.get('code'), 'units': line.get('units'),
                      'benefit_evidence': (line.get('benefits') or {}).get('selected_benefit_ids') or [],
                      'copay_cents': (line.get('benefits') or {}).get('copay_cents'),
                      'copay_period': (line.get('benefits') or {}).get('copay_period'),
                      'deductible_applies': (line.get('benefits') or {}).get('deductible_applies'),
                      'deductible_remaining_cents': (line.get('benefits') or {}).get('deductible_remaining_cents'),
                      'coinsurance_percent': (line.get('benefits') or {}).get('coinsurance_percent'),
                      'review_flags': _review_flags(line.get('benefits') or {})}
                     for line in lines[:12]]}


def investigate(result: dict) -> dict:
    """Return cited proposals only; this result never selects a monetary rule."""
    facts = _input(result)
    fingerprint = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    prior = result.get('insurance_research') or {}
    if prior.get('fingerprint') == fingerprint:
        return prior
    if not facts['lines'] or not any(row['review_flags'] for row in facts['lines']):
        return {'status': 'not_needed', 'fingerprint': fingerprint, 'findings': []}
    prompt = ('Review these already selected, deidentified insurance benefit facts. '
              'You may call the provided read-only tools for these exact CPTs. At most three calls are supported. '
              'Return JSON only: {"findings":[{"code":"CPT","finding":"short explanation",'
              '"evidence_ids":["exact supplied benefit ID"],"next_action":"short action"}]}. '
              'Do not invent a value, network participation, deductible waiver, copay interaction, or permission to collect. '
              'If source facts cannot decide, say what must be verified. Facts: ' + json.dumps(facts, sort_keys=True))
    try:
        client = boto3.client('bedrock-runtime', config=Config(
            connect_timeout=3, read_timeout=15, retries={'max_attempts': 0}))
        messages = [{'role': 'user', 'content': [{'text': prompt}]}]
        response = client.converse(modelId=MODEL_ID, messages=messages,
            toolConfig=_tool_config(), inferenceConfig={'maxTokens': 500})
        tool_uses = [part['toolUse'] for part in response['output']['message']['content'] if 'toolUse' in part]
        if tool_uses:
            if len(tool_uses) > 3:
                raise ValueError('too_many_research_tools')
            allowed_codes = {str(row['code']) for row in facts['lines']}
            results = []
            for call in tool_uses:
                name, code = call.get('name'), (call.get('input') or {}).get('code')
                if name not in TOOL_NAMES or code not in allowed_codes or not call.get('toolUseId'):
                    raise ValueError('invalid_research_tool')
                results.append({'toolResult': {'toolUseId': call['toolUseId'],
                    'content': [{'json': _tool_result(name, code, facts, result)}]}})
            messages.extend([response['output']['message'], {'role': 'user', 'content': results}])
            response = client.converse(modelId=MODEL_ID, messages=messages,
                toolConfig=_tool_config(), inferenceConfig={'maxTokens': 500})
            if any('toolUse' in part for part in response['output']['message']['content']):
                raise ValueError('research_tool_budget_exceeded')
        content = ''.join(part.get('text', '') for part in response['output']['message']['content'])
        proposed = json.loads(content)
        allowed = {str(row['code']): set(row['benefit_evidence']) for row in facts['lines']}
        findings = []
        for item in proposed.get('findings', [])[:12]:
            if not isinstance(item, dict) or item.get('code') not in allowed:
                continue
            citations = item.get('evidence_ids') or []
            if not isinstance(citations, list) or not citations or not set(citations) <= allowed[item['code']]:
                continue
            findings.append({'code': item['code'], 'finding': str(item.get('finding') or '')[:400],
                             'evidence_ids': citations, 'next_action': str(item.get('next_action') or '')[:240]})
        return {'status': 'complete', 'fingerprint': fingerprint, 'model_id': MODEL_ID,
                'findings': findings, 'calculation_authority': False}
    except Exception as exc:
        # Bedrock is advisory. Transport, quota, model-access and malformed JSON
        # leave the deterministic result intact and visible for staff review.
        return {'status': 'research_incomplete', 'fingerprint': fingerprint,
                'findings': [], 'reason': type(exc).__name__}
