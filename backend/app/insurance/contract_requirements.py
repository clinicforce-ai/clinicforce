"""Conservative, source-bound referral and authorization summaries for a visit.

Only explicit PDF table cells can establish a yes/no contract rule. General
contract prose can explain why verification is needed, but never clears care.
This module does not participate in pricing.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from app.insurance import plan_matrix

KINDS = ('referral', 'authorization')
CODE = re.compile(r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])')
PAGE = re.compile(r'^Page (\d+)\b', re.I)
PROVISIONAL = re.compile(r'\b(?:draft|proposed|pending|suggested|unapproved)\b', re.I)
OTHER_POLICY = re.compile(r'\b(?:provider manual|policies and procedures|coverage agreement|payer policy|medical policy|see policy|member handbook|plan benefits)\b', re.I)
CONDITIONAL = re.compile(r'\b(?:if|unless|except|subject to|depending on|as applicable|where required)\b', re.I)
HEADERS = {
    'referral': {'referral', 'referralrequired', 'referralrequirement'},
    'authorization': {'priorauthorization', 'priorauthorizationrequired', 'priorauth', 'priorauthrequired',
                      'preauthorization', 'preauthorizationrequired', 'authorization',
                      'authorizationrequired', 'precertification'},
}


def _key(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _codes(value: object) -> set[str]:
    found = set()
    for part in re.split(r'[,;/+]', str(value or '').upper()):
        part = part.strip()
        if CODE.fullmatch(part):
            found.add(part)
        elif re.fullmatch(r'\d{5}\s*[-–]\s*\d{5}', part):
            start, end = map(int, re.split(r'\s*[-–]\s*', part))
            if start <= end <= start + 20:
                found.update(str(code) for code in range(start, end + 1))
    return found


def _status(value: object) -> str | None:
    text = re.sub(r'\s+', ' ', str(value or '').strip()).casefold()
    if text in {'yes', 'y', 'required', 'referral required', 'authorization required',
                'prior authorization required', 'preauthorization required'}:
        return 'Required'
    if text in {'no', 'n', 'not required', 'no referral required', 'referral not required',
                'no authorization required', 'prior authorization not required',
                'preauthorization not required'}:
        return 'Not required'
    return None


def _source(document: dict, page: int | None = None) -> dict:
    return {'filename': document['filename'], 'document_id': document['id'], **({'page': page} if page else {})}


def _date(value: object) -> date | None:
    if not value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _matches_scope(values: dict, context: dict, document: dict, linked: bool) -> bool:
    service_date = context['service_date']
    start = values.get('effectivefrom') or document.get('effective_from') or ''
    end = values.get('effectiveto') or document.get('effective_to') or ''
    try:
        visit_date, start_date, end_date = _date(service_date), _date(start), _date(end)
        if (start_date and start_date > visit_date) or (end_date and end_date < visit_date):
            return False
    except (ValueError, TypeError):
        return False
    matches = {
        'plan': lambda value: _key(value) in context['plans'],
        'product': lambda value: _key(value) in context['plans'],
        'payer': lambda value: _key(value) in context['payer_names'],
        'payername': lambda value: _key(value) in context['payer_names'],
        'network': lambda value: _key(value) == _key(context.get('network')),
        'renderingnpi': lambda value: value == context.get('rendering_npi'),
        'providernpi': lambda value: value == context.get('rendering_npi'),
        'billingnpi': lambda value: value == context.get('billing_npi'),
        'tin': lambda value: value == context.get('tin'),
        'groupnumber': lambda value: _key(value) == _key(context.get('group_number')),
        'plannumber': lambda value: _key(value) == _key(context.get('plan_number')),
        'locationid': lambda value: value == context.get('location_id'),
        'pos': lambda value: value == context.get('pos'),
        'placeofservice': lambda value: value == context.get('pos'),
    }
    known = {*matches, 'effectivefrom', 'effectiveto', 'cpt', 'hcpcs', 'code', 'procedurecode', 'cptcode',
             *HEADERS['referral'], *HEADERS['authorization'], 'notes', 'note', 'description', 'service'}
    if any(value and key not in known for key, value in values.items()):
        return False
    if any(value and key in matches and not matches[key](value) for key, value in values.items()):
        return False
    if linked:
        return True
    # An unlinked PDF must carry enough exact qualifiers to establish the
    # provider, product, network, setting and date for its own table row.
    return (context.get('payer_verified') and bool(start)
            and any(values.get(key) for key in ('plan', 'product'))
            and bool(values.get('network')) and bool(values.get('renderingnpi') or values.get('providernpi'))
            and bool(values.get('billingnpi')) and bool(values.get('pos') or values.get('placeofservice')))


def _table_rules(document: dict, context: dict, linked: bool) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = defaultdict(list)
    for table in (document.get('extraction') or {}).get('tables') or []:
        page = PAGE.match(table.get('locator') or '')
        rows = table.get('rows') or []
        if not page or len(rows) < 2 or PROVISIONAL.search(table.get('context') or ''):
            continue
        headers = [_key(cell) for cell in rows[0]]
        code_index = next((index for index, header in enumerate(headers)
                           if header in {'cpt', 'hcpcs', 'code', 'procedurecode', 'cptcode'}), None)
        if code_index is None:
            continue
        kind_columns = {kind: next((index for index, header in enumerate(headers) if header in HEADERS[kind]), None)
                        for kind in KINDS}
        if all(index is None for index in kind_columns.values()):
            continue
        for row in rows[1:]:
            values = {header: str(row[index] or '').strip() if index < len(row) else ''
                      for index, header in enumerate(headers)}
            codes = _codes(row[code_index] if code_index < len(row) else '') & context['codes']
            if not codes or PROVISIONAL.search(' '.join(values.values())) or not _matches_scope(values, context, document, linked):
                continue
            note = ' '.join(values.get(key, '') for key in ('notes', 'note', 'description', 'service'))
            for kind, index in kind_columns.items():
                if index is None:
                    continue
                cell = str(row[index] or '').strip() if index < len(row) else ''
                status = (_status(cell) if not (OTHER_POLICY.search(cell + ' ' + note)
                                              or CONDITIONAL.search(cell + ' ' + note)) else None)
                if not cell:
                    continue
                for code in codes:
                    found[kind].append({'code': code, 'status': status, 'source': _source(document, int(page[1])),
                                        'reason': (f'Contract row marks CPT {code} {kind} {status.lower()}.' if status else
                                                   f'Contract row for CPT {code} needs payer-policy or condition review.')})
    return found


def _policy_page(document: dict, kind: str) -> int | None:
    term = r'referral' if kind == 'referral' else r'(?:preauthoriz\w*|prior authoriz\w*|precertif\w*|authorization)'
    pages = (document.get('extraction') or {}).get('pages') or []
    for pattern in (rf'provider (?:will|must) comply with all .{{0,70}}{term}.{{0,180}}'
                    rf'(?:provider manual|policies and procedures|coverage agreement)',
                    rf'{term}.{{0,100}}(?:per|under|according to|set forth in).{{0,100}}'
                    rf'(?:provider manual|policies and procedures|coverage agreement)'):
        for page in pages:
            if re.search(pattern, re.sub(r'\s+', ' ', page.get('text') or ''), re.I):
                return page.get('number')
    return None


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
    if research:
        for kind in KINDS:
            current, matrix = output[kind], research[kind]
            cited = [source for source in current['sources']
                     if not source.get('document_id') or source.get('page') or source.get('document_id') == linked_id]
            sources = list({(source.get('document_id'), source.get('page'), source.get('label')): source
                            for source in [*matrix['sources'], *cited]}.values())
            verify_url = matrix.get('verify_url')
            if current['status'] != 'Verification required' and matrix['status'] != 'Verification required':
                if current['status'] != matrix['status']:
                    output[kind] = {'status': 'Verification required',
                                    'reason': 'The contract or payer response conflicts with plan-specific Excel research; confirm with the payer.',
                                    'sources': sources, 'verify_url': verify_url}
                else:
                    output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            elif current['status'] == 'Verification required' and matrix['status'] != 'Verification required' and not rules[kind]:
                output[kind] = {**matrix, 'sources': sources}
            elif matrix['status'] == 'Verification required' and current['status'] != 'Verification required' and \
                    matrix['reason'].startswith('Matrix does not settle authorization'):
                output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            elif matrix['status'] == 'Verification required':
                output[kind] = {**matrix, 'sources': sources,
                                'reason': matrix['reason'] + (' ' + current['reason'] if current['reason'].startswith('Payer response reports ') else '')}
            else:
                output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            for field in ('verification', 'contact'):
                if matrix.get(field):
                    output[kind][field] = matrix[field]
    check_id = str(result.get('response_reference') or '')
    if result.get('benefit_source') == 'Stedi' and re.fullmatch(r'ec_[A-Za-z0-9-]+', check_id):
        check_source = {'label': 'Stedi benefit check',
                        'url': f'https://portal.stedi.com/app/healthcare/eligibility/checks/{check_id}'}
        for kind in KINDS:
            if not any(source.get('url') == check_source['url'] for source in output[kind]['sources']):
                output[kind]['sources'].append(check_source)
    authorization = output['authorization']
    approval_lines = [line for line in (result.get('price_range') or {}).get('lines') or []
                      if str(line.get('code') or '').upper() in codes]
    approval_verified = bool(approval_lines) and len(approval_lines) == len(result.get('codes') or []) and all(
        (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('status') == 'valid'
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('cleared') is True
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('reference')
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('verified_at')
        for line in approval_lines)
    authorization['approval_status'] = ('Verified' if approval_verified else 'Not verified') if authorization['status'] == 'Required' else 'Unknown' if authorization['status'] == 'Verification required' else 'Not needed under the stated rule'
    if approval_verified and len(approval_lines) == 1:
        approval = approval_lines[0]['benefits']['requirements']['authorization']
        authorization['approval_reference'] = approval['reference']
        authorization['approval_verified_at'] = approval['verified_at']
    if authorization['status'] == 'Required' and authorization['approval_status'] != 'Verified' and 'approval' not in authorization['reason'].lower():
        authorization['reason'] += ' Authorization approval is a separate check.'
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
