"""Build visit service choices from documented codes and active clinic rates."""
from __future__ import annotations

from types import ModuleType
from app.insurance import botox_review, cpt_rules


def build_catalog(api: ModuleType, result: dict, session: dict, inputs: dict, source: dict,
                  pricing_source: dict | None, exact_source: dict, payer_id: str,
                  payload: dict | None) -> tuple[str, list[dict], list[dict], list[dict], dict | None]:
    codes = api._previsit_codes(inputs)
    available = list(codes)
    botox_visit = botox_review.is_botox_visit(inputs)
    if botox_visit:
        available.extend({'id': row.get('id') or f"ecw:{index}:{row['code']}",
                          'code': row['code'], 'source': 'eCW service candidate',
                          'description': row.get('description'), 'units': row.get('units'),
                          'modifiers': row.get('modifiers') or []}
                         for index, row in enumerate(inputs.get('lines') or [])
                         if row.get('service_status') == 'scheduled' and row.get('code'))
    known = {row['code'] for row in available}
    # A clinic-scoped catalog is useful before final coding exists. Prices and
    # descriptions come from its active contract, never from AI or example defaults.
    configured_connections = api.commercial_store.list_connections(session['practice_id'])
    if not payer_id and payload is None:
        # A deferred payer check must not discard the already mapped eCW policy.
        # A returned payer route/product still takes precedence once benefits arrive.
        mapped = [row for row in configured_connections if row.get('id') == inputs.get('payer_id')
                  and row.get('category') == 'commercial_medical' and row.get('status') == 'staff_guidance_enabled']
        if len(mapped) == 1:
            payer_id = str(mapped[0].get('stedi_payer_id') or '')
    connections = [row for row in configured_connections
                   if row.get('stedi_payer_id') == payer_id and row.get('status') == 'staff_guidance_enabled']
    catalog = []
    if len(connections) == 1:
        try:
            _, network, profile = api._previsit_practice_path(session['practice_id'], {**inputs, 'payer_id': connections[0]['id']}, exact_source)
            contract = api._network_contract_evidence(session['practice_id'], network, profile, inputs['service_date'])
            if network.get('plan_matcher') and exact_source.get('plan') and contract.get('document_id'):
                catalog = api.commercial_store.matching_rates(session['practice_id'], profile, network, inputs['service_date'], None)
        except (api.HTTPException, api.commercial.CommercialError):
            pass
    catalog_codes = []
    for rate in catalog:
        if rate.get('modifier') or rate['code'] in known:
            continue
        row = {'id': 'contract:' + rate['code'], 'code': rate['code'], 'source': 'Contract service option',
               'description': rate.get('description') or rate['code'], 'units': 1, 'modifiers': []}
        available.append(row)
        catalog_codes.append(row)
        known.add(rate['code'])
    if not codes and catalog_codes and not botox_visit:
        matches = {row['code'] for row in api.cpt_suggester._matching_candidate_rates(str(inputs.get('reason') or ''), catalog)}
        if matches & api.cpt_rules.EMG_ADD_ONS:
            matches |= api.cpt_rules.NCS_CODES
        codes = [row for row in catalog_codes if row['code'] in matches] or catalog_codes
    for group in (result.get('suggestions') or {}).get('groups', []):
        for candidate in group.get('codes', []):
            code = str(candidate.get('code') or '').upper()
            if api.re.fullmatch(r'(?:\d{5}|[A-Z]\d{4}|\d{4}[A-Z])', code) and code not in known:
                available.append({'id': 'suggested:' + code, 'code': code, 'source': 'Clinician-confirmed candidate',
                                  'description': candidate.get('reason'), 'units': None, 'modifiers': []})
                known.add(code)
    for key in ('cash_schedule', 'cash_quote', 'practice_cash_source', 'collection_policy'):
        result.pop(key, None)
    policy_connections = [row for row in configured_connections if row.get('stedi_payer_id') == payer_id]
    if len(policy_connections) == 1:
        policy = (policy_connections[0].get('rules') or {}).get('collection_policy') or {}
        if (isinstance(policy, dict) and policy.get('mode') in {'defer_to_claim', 'collect_verified_copay', 'collect_supported_advance'} and policy.get('approved_by')
                and policy.get('approved_at') and policy.get('reference')):
            result['collection_policy'] = policy
    uploaded_cash = api.practice_prices.current(session['practice_id'], inputs['service_date'])
    if uploaded_cash:
        known_codes = {row['code'] for row in available}
        for row in uploaded_cash['rows']:
            if row['code'] in known_codes or row.get('price_kind') in {'package', 'named_package'}:
                continue
            available.append({'id': 'cash:' + row['code'], 'code': row['code'],
                              'description': row['description'], 'source': 'Practice cash schedule',
                              'units': 1, 'modifiers': []})
            known_codes.add(row['code'])
    # Clinical options work for every practice, even before its prices arrive.
    # This catalog supplies no prices or coverage assumptions.
    known_codes = {row['code'] for row in available}
    descriptions = {**{str(n): 'Nerve conduction studies' for n in range(95907, 95914)},
                    '95885': 'Limited needle EMG', '95886': 'Complete needle EMG',
                    '11104': 'Skin punch biopsy · first site',
                    '11105': 'Skin punch biopsy · each additional site',
                    '62270': 'Diagnostic spinal tap · without imaging', '62328': 'Diagnostic spinal tap · with imaging',
                    **{str(n): 'Office consultation' for n in range(99202, 99206)},
                    **{str(n): 'Office consultation' for n in range(99212, 99216)}}
    if botox_visit:
        descriptions.update({code: description for family in ('botulinum_drug', 'chemodenervation')
                             for code, description in cpt_rules.SERVICE_FAMILIES[family]['candidate_descriptions'].items()})
    if api.re.search(r'\b(?:DBS|deep brain stimulation)\b',
                     ' '.join(str(inputs.get(key) or '') for key in ('reason', 'appointment_type')), api.re.I):
        descriptions.update({'95970': 'Implanted neurostimulator analysis · no programming',
                             '95983': 'Deep brain neurostimulator programming · first 15 minutes',
                             '95984': 'Deep brain neurostimulator programming · each additional 15 minutes'})
    available.extend({'id': 'planning:' + code, 'code': code, 'description': description,
                      'source': 'CPT planning catalog',
                      'units': None if code == 'J0585' else 1, 'modifiers': []}
                     for code, description in descriptions.items() if code not in known_codes)
    for row in available:
        definition = cpt_rules.service_definition(row['code'])
        row.update(service_family=definition['family'], service_role=definition['role'],
                   service_group=definition['group'], catalogue_version=definition['catalogue_version'],
                   benefit_service_types=list(definition.get('benefit_service_types') or []))
        matches = [rate for rate in catalog if rate['code'] == row['code'] and (rate.get('modifier') or '') == ','.join(row.get('modifiers') or [])]
        if len(matches) == 1:
            row.update(unit_allowed_cents=matches[0]['allowed_cents'], unit_cash_cents=matches[0].get('cash_cents'),
                       description=row.get('description') or matches[0].get('description'), rate_source=matches[0].get('source_reference'))
    # CPT 95900 was retired in 2013; an old eCW or AI candidate cannot enter a current visit plan.
    if inputs['service_date'] >= '2013-01-01':
        codes = [row for row in codes if row['code'] != '95900']
        available = [row for row in available if row['code'] != '95900']
    return payer_id, connections, codes, available, uploaded_cash
