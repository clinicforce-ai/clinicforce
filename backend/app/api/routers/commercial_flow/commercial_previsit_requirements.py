"""Evaluate documented coverage requirements for selected and candidate services."""
from __future__ import annotations

import hashlib
import json
from types import ModuleType
from app.insurance.identity import patient_benefits_bound
from app.insurance import stedi_cpt_authorization, visit_estimate
from app.insurance.requirements.published import PUBLISHED_RULESET_VERSION


def requirement_sources(documents: list[dict]) -> list[dict]:
    return sorted(({'id': row['id'], 'sha256': row.get('sha256'),
                    'effective_from': str(row['effective_from']) if row.get('effective_from') else None,
                    'effective_to': str(row['effective_to']) if row.get('effective_to') else None,
                    'status': (row.get('extraction_summary') or {}).get('status')}
                   for row in documents if row.get('kind') == 'plan_matrix' or
                   row.get('kind') == 'contract' and row.get('content_type') == 'application/pdf'),
                  key=lambda row: row['id'])


def _rules_version(rows: list[dict]) -> str:
    scope = sorted((row['id'], row['updated_at']) for row in rows)
    digest = hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:16]
    return f'{PUBLISHED_RULESET_VERSION}:{digest}'


async def check_cpt_authorization(api: ModuleType, result: dict, case: dict, payload: dict,
                                  inputs: dict, session: dict, api_key: str | None) -> dict:
    """Check selected CPTs for this member, date, network and office setting."""
    codes = sorted({str(row.get('code') or '') for row in result.get('codes') or []
                    if api.re.fullmatch(r'\d{5}', str(row.get('code') or ''))})
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    pos = '' if visit_estimate.telehealth_pos_unresolved(inputs) else str(inputs.get('place_of_service') or '')
    if (not codes or not api_key
            or not stedi_cpt_authorization.visit_coverage_bound(benefits, inputs['service_date'])
            or not patient_benefits_bound(identity)
            or benefits.get('network_status') not in {'in_network', 'out_of_network'}
            or not api.re.fullmatch(r'\d{2}', pos)):
        return case
    provider, subscriber = payload.get('provider') or {}, payload.get('subscriber') or {}
    if not (payload.get('payerId') and provider.get('npi') and provider.get('name')
            and subscriber.get('memberId') and subscriber.get('name')):
        return case
    cigna = bool(api.re.search(r'\bcigna\b', str(result.get('payer') or ''), api.re.I))
    scope_values = [payload.get('id'), payload['payerId'], provider['npi'],
        subscriber['memberId'], identity.get('binding'), inputs['service_date'], pos,
        benefits['network_status'], codes]
    if cigna:
        scope_values.insert(0, 'cigna-individual-cpt-v3')
    scope = api.hashlib.sha256(api.json.dumps(scope_values, sort_keys=True).encode()).hexdigest()
    prior = case.get('cpt_authorization') or {}
    if prior.get('scope') == scope:
        return case
    case['cpt_authorization'] = {'scope': scope, 'state': 'pending', 'codes': codes,
                                 'service_date': inputs['service_date'], 'pos': pos,
                                 'network': benefits['network_status'], 'patient_binding': identity.get('binding')}
    case = await api.run_in_threadpool(api._save, case, session, 'cpt-authorization-pending')
    body = {'payerId': payload['payerId'],
            'provider': {key: provider[key] for key in ('name', 'npi')},
            'subscriber': {key: subscriber[key] for key in ('memberId', 'dateOfBirth', 'name') if subscriber.get(key)},
            'encounter': {'placeOfService': {'code': pos}}}
    dependent = payload.get('dependent') or {}
    if dependent:
        body['dependent'] = {key: dependent[key] for key in ('dateOfBirth', 'name') if dependent.get(key)}
    if cigna or inputs['service_date'] != api.date.today().isoformat():
        body['encounter']['dates'] = {'service': {'start': inputs['service_date']}}
    def same_name(left: dict, right: dict) -> bool:
        first = (left.get('name') or {}).get('person') or {}
        second = (right.get('name') or {}).get('person') or {}
        normalize = lambda value: api.re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
        return all(normalize(first.get(field)) and normalize(first.get(field)) == normalize(second.get(field))
                   for field in ('firstName', 'lastName'))
    normalize_id = lambda value: api.re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    groups = [[code] for code in codes] if cigna else [codes]
    checks = []
    for group in groups:
        inquiry = {**body, 'encounter': {**body['encounter'],
            'services': [{'system': 'CPT', 'value': code} for code in group]}}
        try:
            async with api.httpx.AsyncClient(timeout=api.httpx.Timeout(30, connect=5)) as client:
                response = await client.post(api.STEDI_URL,
                    headers={'Authorization': api_key, 'Content-Type': 'application/json'}, json=inquiry)
                checked = response.json()
        except (api.httpx.HTTPError, ValueError):
            case['cpt_authorization']['state'] = 'indeterminate'
            return await api.run_in_threadpool(api._save, case, session, 'cpt-authorization-indeterminate')
        returned_subscriber = (checked.get('subscriber') or {}) if isinstance(checked, dict) else {}
        returned_dependent = (checked.get('dependent') or {}) if isinstance(checked, dict) else {}
        if (response.is_error or not isinstance(checked, dict)
                or checked.get('payerId') != payload['payerId']
                or normalize_id(returned_subscriber.get('memberId')) != normalize_id(subscriber['memberId'])
                or returned_subscriber.get('dateOfBirth') != subscriber.get('dateOfBirth')
                or not same_name(subscriber, returned_subscriber)
                or bool(dependent) != bool(returned_dependent)
                or dependent and (returned_dependent.get('dateOfBirth') != dependent.get('dateOfBirth')
                                  or not same_name(dependent, returned_dependent))
                or str((checked.get('provider') or {}).get('npi') or '') != str(provider['npi'])):
            case['cpt_authorization']['state'] = 'unavailable'
            return await api.run_in_threadpool(api._save, case, session, 'cpt-authorization-unavailable')
        raw = api.json.dumps(checked).encode()
        raw_key = await api.run_in_threadpool(api.object_store.put_staff_document,
            session['practice_id'], case['id'], f'cpt-authorization-{group[0]}-stedi-271.json', raw, 'application/json')
        case['cpt_authorization'].setdefault('raw_object_keys', {})[group[0]] = raw_key
        case['cpt_authorization'].setdefault('check_ids', []).append(checked.get('id'))
        checks.append((group, checked, raw_key))
    decisions = {}
    terms = {}
    for group, checked, _ in checks:
        decisions.update(stedi_cpt_authorization.decisions(checked, group, pos, benefits['network_status']))
        if cigna:
            terms[group[0]] = stedi_cpt_authorization.financial_terms(
                checked, group[0], pos, benefits['network_status'])
    case['cpt_authorization'].update(state='complete',
        check_id=checks[0][1].get('id'), check_ids=[checked.get('id') for _, checked, _ in checks],
        decisions=decisions, financial_terms=terms,
        raw_object_key=checks[0][2], raw_object_keys={group[0]: key for group, _, key in checks})
    return await api.run_in_threadpool(api._save, case, session, 'cpt-authorization-complete')


def attach_requirements(api: ModuleType, result: dict, case: dict, inputs: dict,
                        source: dict, session: dict, connections: list[dict], payer_id: str) -> None:
    stored_rules = api.commercial_store.list_cpt_requirements(session['practice_id'])
    documents = (session.get('_library_documents') if '_library_documents' in session else
                 api.staff_store.list_library(session['practice_id']) if case.get('library_pricing') else None)
    if documents is not None:
        result['requirement_sources'] = requirement_sources(documents)
    library_ids = ([row['id'] for row in documents
                    if row.get('kind') == 'plan_matrix' or row.get('kind') == 'contract' and row.get('content_type') == 'application/pdf']
                   if documents is not None else
                   [row['document_id'] for row in case.get('library_pricing', []) if row.get('document_id')])
    library_sources = [api.staff_store.get_library(document_id, session['practice_id']) for document_id in library_ids]
    payer_connections = [row for row in connections if row.get('stedi_payer_id') == payer_id]
    connection = payer_connections[0] if len(payer_connections) == 1 else {}
    approved_product = ((result.get('network_evidence') or {}).get('product_mapping_approval') or {}).get('contract_product')
    identified_product = ((result.get('plan_details') or {}).get('stedi') or {}).get('identified_product')
    requirement_context = {
        'stored_rules': stored_rules,
        'service_date': inputs['service_date'], 'payer_verified': bool(connection),
        'payer_names': [result.get('payer'), source.get('payer'), connection.get('display_name'),
                        connection.get('stedi_payer_id'), *(connection.get('aliases') or [])],
        'plans': [approved_product] if approved_product else [identified_product] if identified_product and patient_benefits_bound(result.get('identity') or {}) else [source.get('plan'), result.get('plan'),
                  *(name for names in (source.get('plan_details') or {}).values() for name in names),
                  *(result['plan_details']['stedi'].get('names') or []),
                  *([(result.get('network_evidence') or {}).get('network_name')]
                    if result.get('benefits', {}).get('network_status') == 'in_network' else [])],
        'network': (result.get('network_evidence') or {}).get('network_name'),
        'rendering_npi': (inputs.get('rendering_provider') or {}).get('npi'),
        'billing_npi': (source.get('billing_provider') or {}).get('npi'),
        'tin': (source.get('billing_provider') or {}).get('tin'),
        'group_number': result['insurance_details'].get('group_number') or result['plan_details']['stedi'].get('group_number') or inputs.get('group_number'),
        'plan_number': inputs.get('plan_number') or result['plan_details']['stedi'].get('plan_number'),
        'member_id': result['insurance_details'].get('member_id') or inputs.get('member_id'),
        'coverage_period': source.get('coverage_period') or result['insurance_details'].get('payer_coverage_period'),
        'location_id': source.get('location_id'),
        'pos': None if visit_estimate.telehealth_pos_unresolved(inputs) else inputs.get('place_of_service'),
        'diagnoses': inputs.get('diagnoses'),
    }
    requirement_documents = [document for document in library_sources if document]
    result['requirement_evidence'] = api.contract_requirements.evaluate(result, requirement_context, requirement_documents)
    result['published_requirements_version'] = _rules_version(stored_rules)
    candidates = api._requirement_candidates(result)
    if candidates:
        # Research the other suggested choices without changing the requirement
        # summary for the services actually selected for this visit.
        candidate_result = {**result, 'codes': candidates, 'price_range': {'lines': []}}
        result['candidate_requirements'] = api.contract_requirements.evaluate(
            candidate_result, requirement_context, requirement_documents)['procedures']
    else:
        result['candidate_requirements'] = []


def refresh_saved_requirements(api: ModuleType, result: dict, case: dict, practice_id: str) -> None:
    """Recheck new saves when source documents or the projected network changes."""
    if 'requirement_sources' not in result and 'published_requirements_version' not in result:
        return
    documents = api.staff_store.list_library(practice_id)
    rules_version = _rules_version(api.commercial_store.list_cpt_requirements(practice_id))
    saved_result = (case.get('previsit_context') or {}).get('result') or {}
    network_changed = any(
        (result.get('benefits') or {}).get(field) != (saved_result.get('benefits') or {}).get(field)
        for field in ('network_status', 'network_source')
    ) or (result.get('network_evidence') or {}).get('network_name') != (saved_result.get('network_evidence') or {}).get('network_name')
    if (result.get('requirement_sources') == requirement_sources(documents) and not network_changed
            and saved_result.get('published_requirements_version') == rules_version):
        return
    context = case['previsit_context']
    attach_requirements(api, result, case, context['inputs'], context['source'],
                        {'practice_id': practice_id, '_library_documents': documents},
                        api.commercial_store.list_connections(practice_id), context.get('payer_id') or '')
