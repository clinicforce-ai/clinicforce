"""Bounded Claude Sonnet 5 review of already saved, selected benefit evidence."""
from __future__ import annotations

import hashlib
import json
import os
from textwrap import shorten
from urllib.parse import urlparse

import boto3
from botocore.config import Config

MODEL_ID = os.getenv('INSURANCE_RESEARCH_MODEL_ID', 'us.anthropic.claude-sonnet-5')
TOOL_NAMES = ('read_saved_ecw_context', 'read_selected_benefit', 'read_reference_price',
              'read_service_template', 'read_policy_scope', 'read_saved_requirement',
              'read_official_contact', 'read_prior_verified_rule',
              'read_approved_clinic_requirement', 'read_official_payer_rule',
              'read_delegated_administrator')


def _public_payer_url(value: object) -> bool:
    url = str(value or '')
    try:
        host = urlparse(url).hostname or ''
    except ValueError:
        return False
    return url.startswith('https://') and any(
        host == domain or host.endswith('.' + domain)
        for domain in ('bcbstx.com', 'cigna.com', 'aetna.com', 'uhcprovider.com'))


def _tool_config(names: tuple[str, ...] = TOOL_NAMES) -> dict:
    return {'tools': [{'toolSpec': {'name': name,
        'description': 'Read one deidentified, already saved and selected visit fact. Never approve collection.',
        'inputSchema': {'json': {'type': 'object', 'properties': {'code': {'type': 'string'}},
                                 'required': ['code'], 'additionalProperties': False}}}}
        for name in names]}


def _tools_for(facts: dict) -> tuple[str, ...]:
    flags = {flag for row in facts['lines'] for flag in row['review_flags']}
    if 'price_basis' in flags:
        return ('read_selected_benefit', 'read_reference_price', 'read_service_template')
    if 'benefit_scope' in flags and {'authorization', 'referral'} & flags:
        return ('read_selected_benefit', 'read_saved_ecw_context', 'read_saved_requirement')
    if 'benefit_scope' in flags:
        return ('read_selected_benefit', 'read_service_template', 'read_saved_ecw_context')
    if {'authorization', 'referral'} & flags:
        return ('read_saved_requirement', 'read_approved_clinic_requirement',
                'read_official_payer_rule', 'read_delegated_administrator',
                'read_official_contact', 'read_prior_verified_rule')
    return ('read_selected_benefit', 'read_saved_ecw_context', 'read_policy_scope')


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
    if name == 'read_saved_requirement':
        procedure = next((item for item in (result.get('requirement_evidence') or {}).get('procedures') or []
                          if item.get('code') == code), None)
        if not procedure:
            return {'status': 'unavailable'}
        return {'status': 'available', 'finding': {kind: {
            'status': (procedure.get(kind) or {}).get('status'),
            'approval_status': (procedure.get(kind) or {}).get('approval_status'),
            'source_count': len((procedure.get(kind) or {}).get('sources') or [])}
            for kind in ('referral', 'authorization')}, 'calculation_authority': False}
    if name == 'read_official_contact':
        from app.insurance.requirements.official_contact import fallback
        names = {str(result.get('payer') or '')}
        contacts = {kind: fallback(names, kind, code, str(result.get('service_date') or ''))
                    for kind in ('referral', 'authorization')}
        return {'status': 'available' if any(contacts.values()) else 'unavailable',
                'finding': contacts, 'calculation_authority': False}
    if name == 'read_prior_verified_rule':
        rows = [item for item in result.get('verified_requirement_research') or []
                if isinstance(item, dict) and item.get('code') == code and item.get('verified_at')
                and _public_payer_url(item.get('source_url'))
                and item.get('status') in {'Required', 'Not required'}]
        return {'status': 'available' if rows else 'unavailable',
                'finding': [{'status': row['status'], 'source_url': row['source_url'],
                             'verified_at': row['verified_at'], 'product': row.get('product')}
                            for row in rows[:3]], 'calculation_authority': False}
    if name in {'read_approved_clinic_requirement', 'read_official_payer_rule'}:
        procedure = next((item for item in (result.get('requirement_evidence') or {}).get('procedures') or []
                          if item.get('code') == code), None)
        if not procedure:
            return {'status': 'unavailable'}
        matches = []
        for kind in ('referral', 'authorization'):
            decision = procedure.get(kind) or {}
            for source in decision.get('sources') or []:
                ref = (f"{source['document_id']}:{source.get('page') or ''}"
                       if name == 'read_approved_clinic_requirement' and source.get('document_id')
                       and source.get('approval_status') == 'approved' else
                       source.get('url') if name == 'read_official_payer_rule'
                       and _public_payer_url(source.get('url')) else None)
                if ref:
                    matches.append({'kind': kind, 'saved_status': decision.get('status'),
                                    'source_reference': ref, 'effective_from': source.get('effective_from'),
                                    'effective_to': source.get('effective_to')})
        return {'status': 'available' if matches else 'unavailable',
                'finding': matches[:5], 'calculation_authority': False}
    if name == 'read_delegated_administrator':
        rows = [row for row in result.get('delegated_administrators') or []
                if isinstance(row, dict) and code in (row.get('codes') or [])
                and row.get('status') == 'verified' and _public_payer_url(row.get('source_url'))
                and row.get('product_binding') == result.get('payer_product_binding')
                and str(row.get('effective_from') or '') <= str(result.get('service_date') or '')
                <= str(row.get('effective_to') or '')]
        return {'status': 'available' if rows else 'unavailable',
                'finding': [{'name': row.get('name'), 'source_url': row['source_url'],
                             'service_date': result.get('service_date')}
                            for row in rows[:3]], 'calculation_authority': False}
    return {'status': 'unavailable'}


def _review_flags(benefits: dict) -> list[str]:
    # Review prose can contain patient details; only controlled categories leave ClinicForce.
    reasons = ' '.join(str(value).lower() for value in benefits.get('review_reasons') or [])
    return [code for code, terms in {
        'copay_interaction': ('copay', 'coinsurance', 'cost-sharing'),
        'deductible_applicability': ('deductible',),
        'oop_applicability': ('out-of-pocket',),
        'benefit_scope': ('conditional', 'service', 'benefit'),
        'price_basis': ('price', 'rate'),
        'authorization': ('prior authorization', 'precertification'),
        'referral': ('referral',),
    }.items() if any(term in reasons for term in terms)]


def _input(result: dict) -> dict:
    """Send selected financial facts only; never patient identity or raw payer objects."""
    lines = (result.get('price_range') or {}).get('lines') or []
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []
    def source_ref(source: dict) -> str | None:
        url = str(source.get('url') or '')
        if _public_payer_url(url):
            return url
        if source.get('document_id') and source.get('approval_status') == 'approved':
            return f"{source['document_id']}:{source.get('page') or ''}"
        return None
    def requirement_sources(code: str) -> list[str]:
        item = next((row for row in procedures if row.get('code') == code), {})
        return list(dict.fromkeys(ref
            for kind in ('referral', 'authorization')
            for source in (item.get(kind) or {}).get('sources') or []
            if (ref := source_ref(source))))[:8]
    def review_flags(line: dict) -> list[str]:
        flags = _review_flags(line.get('benefits') or {})
        item = next((row for row in procedures if row.get('code') == line.get('code')), {})
        for kind in ('referral', 'authorization'):
            decision = item.get(kind) or {}
            if decision.get('status') == 'Verification required' or (
                    decision.get('status') == 'Required' and
                    (decision.get('approval_status') != 'Verified' if kind == 'authorization'
                     else decision.get('referral_obtained') != 'YES')):
                flags.append(kind)
        return list(dict.fromkeys(flags))
    def requirement_status(code: str) -> dict:
        item = next((row for row in procedures if row.get('code') == code), {})
        allowed = {'Required', 'Not required', 'Verification required', 'Not confirmed',
                   'Denied', 'Approved'}
        return {kind: status if (status := (item.get(kind) or {}).get('status')) in allowed
                else 'unknown' for kind in ('referral', 'authorization')}
    coverage = result.get('benefits') or {}
    network = coverage.get('network_status')
    return {'service_date_matches': coverage.get('service_date_matches'),
            'provider_network_status': network if network in {'in_network', 'out_of_network', 'unknown'} else 'unknown',
            'lines': [{'code': line.get('code'), 'units': line.get('units'),
                      'benefit_evidence': (line.get('benefits') or {}).get('selected_benefit_ids') or [],
                      'requirement_evidence': requirement_sources(str(line.get('code') or '')),
                      'requirement_status': requirement_status(str(line.get('code') or '')),
                      'copay_cents': (line.get('benefits') or {}).get('copay_cents'),
                      'copay_period': (line.get('benefits') or {}).get('copay_period'),
                      'deductible_applies': (line.get('benefits') or {}).get('deductible_applies'),
                      'deductible_remaining_cents': (line.get('benefits') or {}).get('deductible_remaining_cents'),
                      'coinsurance_percent': (line.get('benefits') or {}).get('coinsurance_percent'),
                      'review_flags': review_flags(line)}
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
    allowed_tools = _tools_for(facts)
    prompt = ('Review these already selected, deidentified insurance benefit facts. '
              'You may call the provided read-only tools for these exact CPTs. At most three calls are supported. '
              'Return JSON only: {"findings":[{"code":"CPT","finding":"short explanation",'
              '"evidence_ids":["exact supplied benefit ID"],"next_action":"short action"}]}. '
              'Each evidence_ids value must exactly match a benefit_evidence or requirement_evidence ID supplied for that CPT; '
              'a CPT code is not an evidence ID. '
              'Do not invent a value, network participation, deductible waiver, copay interaction, or permission to collect. '
              'A requirement status of Verification required means it was evaluated but neither the requirement '
              'nor member approval is confirmed; an empty requirement_evidence list means no citable rule source. '
              'If source facts cannot decide, say what must be verified. Facts: ' + json.dumps(facts, sort_keys=True))
    try:
        client = boto3.client('bedrock-runtime', config=Config(
            connect_timeout=3, read_timeout=15, retries={'max_attempts': 0}))
        messages = [{'role': 'user', 'content': [{'text': prompt}]}]
        response = client.converse(modelId=MODEL_ID, messages=messages,
            toolConfig=_tool_config(allowed_tools), inferenceConfig={'maxTokens': 900})
        tool_uses = [part['toolUse'] for part in response['output']['message']['content'] if 'toolUse' in part]
        if tool_uses:
            if len(tool_uses) > 3:
                raise ValueError('too_many_research_tools')
            allowed_codes = {str(row['code']) for row in facts['lines']}
            results = []
            for call in tool_uses:
                name, code = call.get('name'), (call.get('input') or {}).get('code')
                if name not in allowed_tools or code not in allowed_codes or not call.get('toolUseId'):
                    raise ValueError('invalid_research_tool')
                results.append({'toolResult': {'toolUseId': call['toolUseId'],
                    'content': [{'json': _tool_result(name, code, facts, result)}]}})
            messages.extend([response['output']['message'], {'role': 'user', 'content': results}])
            response = client.converse(modelId=MODEL_ID, messages=messages,
                toolConfig=_tool_config(allowed_tools), inferenceConfig={'maxTokens': 1200})
            if any('toolUse' in part for part in response['output']['message']['content']):
                raise ValueError('research_tool_budget_exceeded')
        if response.get('stopReason') == 'max_tokens':
            raise ValueError('research_token_budget_exceeded')
        content = ''.join(part.get('text', '') for part in response['output']['message']['content']).strip()
        if content.startswith('```') and content.endswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        proposed = json.loads(content)
        allowed = {str(row['code']): set(row['benefit_evidence']) | set(row['requirement_evidence'])
                   for row in facts['lines']}
        findings = []
        for item in proposed.get('findings', [])[:12]:
            if not isinstance(item, dict) or item.get('code') not in allowed:
                continue
            citations = item.get('evidence_ids') or []
            if not isinstance(citations, list) or not citations or not set(citations) <= allowed[item['code']]:
                continue
            findings.append({'code': item['code'],
                             'finding': shorten(str(item.get('finding') or ''), width=400, placeholder='…'),
                             'evidence_ids': citations,
                             'next_action': shorten(str(item.get('next_action') or ''), width=240, placeholder='…')})
        if proposed.get('findings') and not findings:
            raise ValueError('unsupported_research_evidence')
        return {'status': 'complete', 'fingerprint': fingerprint, 'model_id': MODEL_ID,
                'findings': findings, 'calculation_authority': False}
    except Exception as exc:
        # Bedrock is advisory. Transport, quota, model-access and malformed JSON
        # leave the deterministic result intact and visible for staff review.
        return {'status': 'research_incomplete', 'fingerprint': fingerprint,
                'findings': [], 'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
