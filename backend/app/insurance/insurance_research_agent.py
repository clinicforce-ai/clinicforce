"""Bounded Claude Sonnet 5 review of already saved, selected benefit evidence."""
from __future__ import annotations

import hashlib
import json
import os

import boto3
from botocore.config import Config

MODEL_ID = os.getenv('INSURANCE_RESEARCH_MODEL_ID', 'us.anthropic.claude-sonnet-5')


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
              'Return JSON only: {"findings":[{"code":"CPT","finding":"short explanation",'
              '"evidence_ids":["exact supplied benefit ID"],"next_action":"short action"}]}. '
              'Do not invent a value, network participation, deductible waiver, copay interaction, or permission to collect. '
              'If source facts cannot decide, say what must be verified. Facts: ' + json.dumps(facts, sort_keys=True))
    try:
        client = boto3.client('bedrock-runtime', config=Config(
            connect_timeout=3, read_timeout=15, retries={'max_attempts': 0}))
        response = client.converse(modelId=MODEL_ID,
            messages=[{'role': 'user', 'content': [{'text': prompt}]}],
            inferenceConfig={'maxTokens': 500})
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
