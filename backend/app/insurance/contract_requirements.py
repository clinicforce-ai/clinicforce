"""Conservative, source-bound referral and authorization summaries for a visit.

Only explicit PDF table cells can establish a yes/no contract rule. General
contract prose can explain why verification is needed, but never clears care.
This module does not participate in pricing.
"""
from __future__ import annotations

import re

from app.insurance import plan_matrix
from app.insurance.library_network_exclusions import product_kind
from app.insurance.identity import generic_plan_label, payer_identity_verified
from app.insurance import stedi_cpt_authorization
from app.insurance.requirements.published import (
    UHC_MA_2026_AUTH_SOURCE, _published_rule, _uhc_choice_plus_referral, _uhc_ma_ppo_referral,
)
from app.insurance.requirements.matrix_merge import merge_matrix_research
from app.insurance.requirements.approval import attach_approval_status

from app.insurance.requirements.evidence import (
    KINDS, CODE, PAGE, PROVISIONAL, OTHER_POLICY, CONDITIONAL, HEADERS,
    _key, _codes, _status, _source, _date, _matches_scope, _table_rules, _policy_page,
)

def _payer_evidence(result: dict, kind: str, codes: set[str], context: dict) -> dict:
    benefits = result.get('benefits') or {}
    exact = result.get('cpt_authorization') or {}
    decisions = exact.get('decisions') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (kind == 'authorization' and codes and exact.get('state') == 'complete'
            and stedi_cpt_authorization.visit_coverage_bound(benefits, context.get('service_date') or '')
            and codes <= decisions.keys() and exact.get('service_date') == context.get('service_date')
            and exact.get('pos') == str(context.get('pos') or '')
            and exact.get('network') == benefits.get('network_status')
            and exact.get('patient_binding') == identity.get('binding')
            and payer_identity_verified(identity)):
        statuses = {decisions[code] for code in codes}
        if statuses <= {'Required', 'Not required'}:
            check_id = str(exact.get('check_id') or '')
            source = {'label': 'Stedi CPT and place-of-service eligibility check'}
            if re.fullmatch(r'ec_[A-Za-z0-9-]+', check_id):
                source['url'] = f'https://portal.stedi.com/app/healthcare/eligibility/checks/{check_id}'
            status = 'Required' if 'Required' in statuses else 'Not required'
            return {'status': status, 'reason': 'Current Stedi response explicitly reports the authorization requirement for each selected CPT and this visit setting.',
                    'sources': [source], 'cpt_specific': True}
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
    identity = result.get('identity') or benefits.get('identity') or {}
    if (codes and result.get('benefit_source') and benefits.get('active') is True
            and benefits.get('service_date_matches') is True
            and payer_identity_verified(identity)
            and any(signals[code] == {'required'} for code in codes)
            and all(len(signals[code]) <= 1 for code in codes)):
        return {'status': 'Required',
                'reason': 'The current payer response requires this for at least one planned service.',
                'sources': [payer_source]}
    if (codes and result.get('benefit_source') and benefits.get('active') is True
            and benefits.get('service_date_matches') is True
            and payer_identity_verified(identity)
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
        if not payer_identity_verified(identity):
            gaps.append('patient identity')
        if not context.get('group_number'):
            gaps.append('group number')
        if not context.get('plans'):
            gaps.append('exact plan')
        finding = 'required' if 'required' in statuses else 'not required'
        return {'status': 'Verification required',
                'reason': f'Payer response reports {kind} {finding} for the selected benefit; the visit lacks {", ".join(gaps or ["member-specific plan details"])}.',
                'sources': [payer_source]}
    if kind == 'referral' and result.get('referral_evidence'):
        return {'status': 'Verification required',
                'reason': 'An eCW referral record exists, but it does not identify the planned services it covers.',
                'sources': [{'label': 'eCW referral record'}]}
    if result.get('benefit_source'):
        return {'status': 'Verification required',
                'reason': 'The payer eligibility response does not explicitly settle this requirement for every planned service.',
                'sources': [payer_source] if payer_source.get('url') else []}
    return {'status': 'Verification required',
            'reason': 'No matching contract rule or explicit payer requirement was returned for this visit.',
            'sources': []}


def evaluate(result: dict, context: dict, documents: list[dict], *, include_procedures: bool = True,
             conditional_candidate: bool = False) -> dict:
    """Return card evidence for the exact visit; never modify financial inputs."""
    raw_context = {**context, 'selected_codes': context.get('selected_codes') or {
        str(row.get('code') or '').upper() for row in result.get('codes') or []}}
    codes = {str(row.get('code') or '').upper() for row in result.get('codes') or [] if CODE.fullmatch(str(row.get('code') or '').upper())}
    reported_plans = context.get('plans') or []
    context = {**context, 'codes': codes, 'plans': {_key(plan) for plan in context.get('plans') or []
                                                   if not generic_plan_label(plan)}}
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
    unconfirmed_ids = {row.get('id') or str(row.get('code') or '').upper()
                       for row in result.get('codes') or []
                       if row.get('needs_confirmation') or row.get('source') == 'conditional office E/M alternative'}
    unconfirmed_codes = bool(unconfirmed_ids)
    output = {}
    for kind in KINDS:
        payer = _payer_evidence(result, kind, codes, context)
        selected = rules[kind]
        document_sources = list({(item['source']['document_id'], item['source']['page']): item['source'] for item in selected}.values())
        if selected:
            by_code = {code: {item['status'] for item in selected if item['code'] == code} for code in codes}
            statuses = {item['status'] for item in selected}
            if ((unconfirmed_codes and not conditional_candidate)
                    or len({item['source']['document_id'] for item in selected}) > 1
                    or any(len(values) != 1 or None in values for values in by_code.values())
                    or payer['status'] != 'Verification required'
                    and any(item['status'] != payer['status'] for item in selected)):
                output[kind] = {'status': 'Verification required',
                                'reason': 'Contract sources, planned services or payer evidence conflict or remain incomplete.',
                                'sources': [*document_sources, *payer['sources']]}
            else:
                status = 'Required' if 'Required' in statuses else 'Not required'
                benefits = result.get('benefits') or {}
                identity = result.get('identity') or benefits.get('identity') or {}
                if (benefits.get('active') is not True
                        or benefits.get('service_date_matches') is not True
                        or not payer_identity_verified(identity)
                        or not context.get('member_id')
                        or not context.get('plans')):
                    output[kind] = {'status': 'Verification required',
                                    'reason': 'The contract states the rule, but the returned member, plan or visit details do not establish its applicability.',
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
                            'reason': 'The contract refers this decision to member coverage or payer policies; no matching member-specific rule was returned.',
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
    choice_plus = _uhc_choice_plus_referral(result, context, reported_plans)
    ma_ppo = _uhc_ma_ppo_referral(result, context, reported_plans)
    if (choice_plus and output['referral']['status'] == 'Verification required'
            and not rules['referral'] and 'conflict' not in output['referral']['reason'].casefold()):
        output['referral'] = choice_plus
    if (ma_ppo and output['referral']['status'] == 'Verification required'
            and not rules['referral'] and 'conflict' not in output['referral']['reason'].casefold()):
        output['referral'] = ma_ppo
    for kind in KINDS:
        published = _published_rule(kind, result, context, reported_plans, codes)
        if not published:
            continue
        current = output[kind]
        if current['status'] == 'Verification required':
            if not rules[kind] and 'conflict' not in current['reason'].casefold():
                output[kind] = {**current, **published,
                                'sources': [*published['sources'], *current['sources']]}
        elif current['status'] != published['status'] and published['status'] != 'Verification required':
            output[kind] = {'status': 'Verification required',
                            'reason': 'The published product rule differs from the visit-specific payer or contract evidence. Confirm the member rule.',
                            'sources': [*current['sources'], *published['sources']],
                            'member_specific_check': True}
        elif current['status'] == published['status']:
            output[kind] = {**current, 'sources': [*current['sources'], *published['sources']],
                            'applicable_scope': published['applicable_scope']}
    if (output['authorization']['status'] == 'Verification required'
            and {'unitedhealthcare', 'uhc'} & payer_names
            and any(product_kind(plan) for plan in reported_plans)
            and '2026-10-01' <= context['service_date'] <= '2026-12-31'):
        authorization = output['authorization']
        if not authorization.get('verify_url') or authorization['verify_url'].rstrip('/') in {
                'https://uhcprovider.com', 'https://www.uhcprovider.com'}:
            authorization['verify_url'] = UHC_MA_2026_AUTH_SOURCE['url']
        if UHC_MA_2026_AUTH_SOURCE not in authorization['sources']:
            authorization['sources'].append(UHC_MA_2026_AUTH_SOURCE)
    # An explicit current payer requirement is actionable even when a static
    # matrix lacks the exact product or a contract has no CPT-level rule.
    for kind in KINDS:
        payer = _payer_evidence(result, kind, codes, context)
        if (payer['status'] == 'Required' or payer.get('cpt_specific') and payer['status'] == 'Not required') and (conditional_candidate or not unconfirmed_codes):
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
            evidence = evaluate(single, raw_context, documents, include_procedures=False,
                                conditional_candidate=True)
            procedures.append({'id': line_id or code, 'code': code, **evidence})
        output['procedures'] = procedures
        for kind in KINDS:
            required = [item for item in procedures
                        if item['id'] not in unconfirmed_ids and item[kind]['status'] == 'Required']
            if required and output[kind]['status'] == 'Verification required':
                unresolved = [item['code'] for item in procedures if item[kind]['status'] == 'Verification required']
                sources = list({(source.get('document_id'), source.get('page'), source.get('label')): source
                                for item in required for source in item[kind]['sources']}.values())
                output[kind] = {'status': 'Required',
                                'reason': f"Required for CPT {', '.join(item['code'] for item in required)}."
                                          + (f" No applicable rule was returned for CPT {', '.join(unresolved)}." if unresolved else ''),
                                'sources': sources,
                                **({'approval_status': 'Not verified'} if kind == 'authorization' else {})}
        if unconfirmed_codes:
            for kind in KINDS:
                if any(item['id'] not in unconfirmed_ids and item[kind]['status'] == 'Required'
                       for item in procedures):
                    continue
                statuses = {item[kind]['status'] for item in procedures}
                sources = list({(source.get('document_id'), source.get('page'),
                                 source.get('url'), source.get('label')): source
                                for source in [*output[kind].get('sources', []),
                                               *(source for item in procedures for source in item[kind]['sources'])]}.values())
                if statuses != {output[kind]['status']} or output[kind]['status'] == 'Verification required':
                    output[kind] = {'status': 'Verification required',
                                    'reason': ('Select the documented CPT. The listed alternatives have different or missing requirement rules.'
                                               if len(statuses) > 1 or 'Verification required' in statuses else
                                               'Select the documented CPT before applying this per-code rule to the visit.'),
                                    'sources': sources}
        attach_approval_status(output, result, codes)
        required_auth = [item for item in procedures
                         if item['id'] not in unconfirmed_ids and item['authorization']['status'] == 'Required']
        if required_auth and output['authorization']['status'] == 'Required':
            output['authorization']['approval_status'] = ('Verified' if all(
                item['authorization']['approval_status'] == 'Verified' for item in required_auth)
                else 'Not verified')
            states = {item['authorization']['pa_approval'] for item in required_auth}
            output['authorization']['pa_approval'] = next(
                (state for state in ('DENIED', 'PENDING', 'EXPIRED') if state in states),
                'APPROVED' if states == {'APPROVED'} else 'UNVERIFIED')
            if len(required_auth) == 1 and output['authorization']['approval_status'] == 'Verified':
                output['authorization']['approval_reference'] = required_auth[0]['authorization']['approval_reference']
                output['authorization']['approval_verified_at'] = required_auth[0]['authorization']['approval_verified_at']
    for kind in KINDS:
        if output[kind]['status'] == 'Verification required':
            output[kind]['member_specific_check'] = True
    return output
