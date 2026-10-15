"""Conservative, source-bound referral and authorization summaries for a visit.

Only explicit PDF table cells can establish a yes/no contract rule. General
contract prose can explain why verification is needed, but never clears care.
This module does not participate in pricing.
"""
from __future__ import annotations

import re

from app.insurance import plan_matrix
from app.insurance.requirements.matrix_merge import merge_matrix_research
from app.insurance.requirements.approval import attach_approval_status

from app.insurance.requirements.evidence import (
    KINDS, CODE, PAGE, PROVISIONAL, OTHER_POLICY, CONDITIONAL, HEADERS,
    _key, _codes, _status, _source, _date, _matches_scope, _table_rules, _policy_page,
)


def _payer_evidence(result: dict, kind: str, codes: set[str], context: dict) -> dict:
    benefits = result.get('benefits') or {}
    source = result.get('benefit_source') or 'Payer response source not retained'
    check_id = str(result.get('response_reference') or '')
    payer_source = {'label': source + ' eligibility response'}
    if source == 'Stedi' and re.fullmatch(r'ec_[A-Za-z0-9-]+', check_id):
        payer_source['url'] = f'https://portal.stedi.com/app/healthcare/eligibility/checks/{check_id}'
    lines = (result.get('price_range') or {}).get('lines') or []
    signals = {code: set() for code in codes}
    for line in lines:
        code = str(line.get('code') or '')
        if code not in signals:
            continue
        terms = line.get('benefits') or {}
        signal = ((terms.get('requirements') or {}).get(kind) or {}).get('payer_signal') or terms.get(kind + '_signal')
        if (kind == 'authorization' and signal == 'not_required'
                and 'absent indicator' in str(terms.get('authorization_basis') or '').lower()):
            signal = None
        if signal in {'required', 'not_required'}:
            signals[code].add(signal)
    identity = benefits.get('identity') or result.get('identity') or {}
    if (codes and result.get('benefit_source') and benefits.get('active') is True
            and benefits.get('service_date_matches') is True
            and identity.get('status') in {'matched', 'reconciled'}
            and any(signals[code] == {'required'} for code in codes)
            and all(len(signals[code]) <= 1 for code in codes)):
        return {'status': 'Required',
                'reason': 'The current payer response requires this for at least one planned service.',
                'sources': [payer_source]}
    if (codes and result.get('benefit_source') and benefits.get('active') is True
            and benefits.get('service_date_matches') is True
            and identity.get('status') in {'matched', 'reconciled'}
            and context.get('member_id') and context.get('rendering_npi')
            and (context.get('group_number') or 'traditionalmedicare' in context.get('payer_names', set()))
            and context.get('plans')
            and (benefits.get('network_status') == 'in_network' or any('required' in signals[code] for code in codes))
            and all(len(signals[code]) == 1 for code in codes)):
        statuses = {next(iter(signals[code])) for code in codes}
        status = 'Required' if 'required' in statuses else 'Not required'
        reason = ('Payer response explicitly requires this for at least one planned service.' if status == 'Required' else
                  'Payer response explicitly marks every planned service not required.')
        return {'status': status, 'reason': reason, 'sources': [payer_source]}
    if (codes and result.get('benefit_source') and benefits.get('active') is True
            and all(len(signals[code]) == 1 for code in codes)):
        statuses = {next(iter(signals[code])) for code in codes}
        gaps = []
        if benefits.get('service_date_matches') is not True:
            gaps.append('visit-date eligibility')
        if benefits.get('network_status') != 'in_network' and 'not_required' in statuses:
            gaps.append('provider network')
        if identity.get('status') not in {'matched', 'reconciled'}:
            gaps.append('patient identity')
        if not context.get('group_number'):
            gaps.append('group number')
        if not context.get('plans'):
            gaps.append('exact plan')
        finding = 'required' if 'required' in statuses else 'not required'
        return {'status': 'Verification required',
                'reason': f'Payer response reports {kind} {finding} for the selected benefit, but {", ".join(gaps or ["other patient-specific checks"])} remain unverified.',
                'sources': [payer_source]}
    if kind == 'referral' and result.get('referral_evidence'):
        return {'status': 'Verification required',
                'reason': 'An eCW referral record exists; its applicability to the planned services is unconfirmed.',
                'sources': [{'label': 'eCW referral record'}]}
    if result.get('benefit_source'):
        return {'status': 'Verification required',
                'reason': 'The payer eligibility response does not explicitly settle this requirement for every planned service.',
                'sources': [payer_source] if payer_source.get('url') else []}
    return {'status': 'Verification required',
            'reason': 'No matching contract rule or explicit payer requirement was confirmed for this visit.',
            'sources': []}


def evaluate(result: dict, context: dict, documents: list[dict], *, include_procedures: bool = True) -> dict:
    """Return card evidence for the exact visit; never modify financial inputs."""
    raw_context = context
    codes = {str(row.get('code') or '').upper() for row in result.get('codes') or [] if CODE.fullmatch(str(row.get('code') or '').upper())}
    reported_plans = context.get('plans') or []
    context = {**context, 'codes': codes, 'plans': {_key(plan) for plan in context.get('plans') or []
                                                   if _key(plan) not in {'', 'ci', 'commercial', 'ppo', 'hmo'}}}
    payer_names = {_key(name) for name in context.get('payer_names') or [] if name}
    context['payer_names'] = payer_names
    linked_id = (result.get('network_evidence') or {}).get('document_id')
    service_date = _date(context['service_date'])
    matched = [doc for doc in documents if doc.get('kind') == 'contract' and doc.get('content_type') == 'application/pdf'
               and (doc.get('id') == linked_id or _key(doc.get('payer')) in payer_names and doc.get('payer'))
               and (not doc.get('effective_from') or _date(doc['effective_from']) <= service_date)
               and (not doc.get('effective_to') or _date(doc['effective_to']) >= service_date)]
    rules = {kind: [] for kind in KINDS}
    for document in matched:
        if (document.get('extraction') or {}).get('sha256') != document.get('sha256'):
            continue
        parsed = _table_rules(document, context, document['id'] == linked_id)
        for kind in KINDS:
            rules[kind].extend(parsed[kind])
    output = {}
    for kind in KINDS:
        payer = _payer_evidence(result, kind, codes, context)
        selected = rules[kind]
        document_sources = list({(item['source']['document_id'], item['source']['page']): item['source'] for item in selected}.values())
        unconfirmed_codes = any(row.get('needs_confirmation') or row.get('source') == 'conditional office E/M alternative'
                                for row in result.get('codes') or [])
        if selected:
            by_code = {code: {item['status'] for item in selected if item['code'] == code} for code in codes}
            statuses = {item['status'] for item in selected}
            if (unconfirmed_codes or len({item['source']['document_id'] for item in selected}) > 1
                    or any(len(values) != 1 or None in values for values in by_code.values())
                    or payer['status'] != 'Verification required'
                    and any(item['status'] != payer['status'] for item in selected)):
                output[kind] = {'status': 'Verification required',
                                'reason': 'Contract sources, planned services or payer evidence conflict or remain incomplete.',
                                'sources': [*document_sources, *payer['sources']]}
            else:
                status = 'Required' if 'Required' in statuses else 'Not required'
                benefits = result.get('benefits') or {}
                identity = benefits.get('identity') or result.get('identity') or {}
                if (benefits.get('active') is not True
                        or benefits.get('service_date_matches') is not True
                        or identity.get('status') not in {'matched', 'reconciled'}
                        or not context.get('member_id')
                        or not context.get('plans')):
                    output[kind] = {'status': 'Verification required',
                                    'reason': 'The contract rule is clear, but patient eligibility for this exact plan and visit is unverified.',
                                    'sources': [item['source'] for item in selected if item['status'] == status][:1]}
                else:
                    output[kind] = {'status': status,
                                    'reason': next(item['reason'] for item in selected if item['status'] == status),
                                    'sources': [item['source'] for item in selected if item['status'] == status][:1]}
            continue
        cited = []
        for document in matched:
            if (document.get('extraction') or {}).get('sha256') == document.get('sha256'):
                page = _policy_page(document, kind)
                if page:
                    cited.append(_source(document, page))
        if cited:
            output[kind] = {'status': 'Verification required',
                            'reason': 'Contract refers this requirement to the member coverage agreement or payer policies; confirm for the planned services.',
                            'sources': cited}
        elif any(document['id'] == linked_id for document in matched):
            output[kind] = {'status': 'Verification required',
                            'reason': 'The matching payer contract does not give a clear rule for every planned service and plan.',
                            'sources': [_source(document) for document in matched if document['id'] == linked_id]}
        else:
            output[kind] = payer
    research = plan_matrix.evaluate(result, {**context, 'plans': reported_plans},
                                    plan_matrix.uploaded(documents, context['service_date']))
    merge_matrix_research(output, research, rules, linked_id, KINDS)
    # An explicit current payer requirement is actionable even when a static
    # matrix lacks the exact product or a contract has no CPT-level rule.
    for kind in KINDS:
        payer = _payer_evidence(result, kind, codes, context)
        if payer['status'] == 'Required':
            previous = output[kind]
            sources = list({(source.get('document_id'), source.get('page'), source.get('url'), source.get('label')): source
                            for source in [*payer['sources'], *previous.get('sources', [])]}.values())
            reason = payer['reason']
            if 'conflict' in previous.get('reason', '').casefold():
                reason += ' A clinic document or plan matrix differs; the current payer requirement controls this visit.'
            output[kind] = {**payer, 'reason': reason, 'sources': sources}
    check_id = str(result.get('response_reference') or '')
    if result.get('benefit_source') == 'Stedi' and re.fullmatch(r'ec_[A-Za-z0-9-]+', check_id):
        check_source = {'label': 'Stedi benefit check',
                        'url': f'https://portal.stedi.com/app/healthcare/eligibility/checks/{check_id}'}
        for kind in KINDS:
            if not any(source.get('url') == check_source['url'] for source in output[kind]['sources']):
                output[kind]['sources'].append(check_source)
    attach_approval_status(output, result, codes)
    if include_procedures:
        price = result.get('price_range') or {}
        procedures = []
        for row in result.get('codes') or []:
            code = str(row.get('code') or '').upper()
            if not CODE.fullmatch(code):
                continue
            line_id = row.get('id')
            lines = [line for line in price.get('lines') or []
                     if (line.get('id') == line_id if line_id else str(line.get('code') or '').upper() == code)]
            single = {**result, 'codes': [row], 'price_range': {**price, 'lines': lines}}
            evidence = evaluate(single, raw_context, documents, include_procedures=False)
            procedures.append({'id': line_id or code, 'code': code, **evidence})
        output['procedures'] = procedures
        for kind in KINDS:
            required = [item for item in procedures if item[kind]['status'] == 'Required']
            if required and output[kind]['status'] == 'Verification required':
                unresolved = [item['code'] for item in procedures if item[kind]['status'] == 'Verification required']
                sources = list({(source.get('document_id'), source.get('page'), source.get('label')): source
                                for item in required for source in item[kind]['sources']}.values())
                output[kind] = {'status': 'Required',
                                'reason': f"Required for CPT {', '.join(item['code'] for item in required)}."
                                          + (f" Still unverified for CPT {', '.join(unresolved)}." if unresolved else ''),
                                'sources': sources,
                                **({'approval_status': 'Not verified'} if kind == 'authorization' else {})}
        required_auth = [item for item in procedures if item['authorization']['status'] == 'Required']
        if required_auth and output['authorization']['status'] == 'Required':
            output['authorization']['approval_status'] = ('Verified' if all(
                item['authorization']['approval_status'] == 'Verified' for item in required_auth)
                else 'Not verified')
            if len(required_auth) == 1 and output['authorization']['approval_status'] == 'Verified':
                output['authorization']['approval_reference'] = required_auth[0]['authorization']['approval_reference']
                output['authorization']['approval_verified_at'] = required_auth[0]['authorization']['approval_verified_at']
    return output
