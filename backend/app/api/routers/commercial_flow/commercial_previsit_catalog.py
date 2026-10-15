"""Build visit service choices from documented codes and active clinic rates."""
from __future__ import annotations

from types import ModuleType


def build_catalog(api: ModuleType, result: dict, session: dict, inputs: dict, source: dict,
                  pricing_source: dict | None, exact_source: dict, payer_id: str,
                  payload: dict | None) -> tuple[str, list[dict], list[dict], list[dict], dict | None]:
    codes = api._previsit_codes(inputs)
    available = list(codes)
    known = {row['code'] for row in codes}
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
            if network.get('plan_matcher') and (pricing_source or source).get('plan'):
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
    if not codes and catalog_codes:
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
    uploaded_cash = api.practice_prices.current(session['practice_id'], inputs['service_date'])
    if uploaded_cash:
        known_codes = {row['code'] for row in available}
        available.extend({'id': 'cash:' + row['code'], 'code': row['code'], 'description': row['description'], 'source': 'Practice cash schedule', 'units': 1, 'modifiers': []} for row in uploaded_cash['rows'] if row['code'] not in known_codes and row.get('price_kind') not in {'package', 'named_package'})
    # Clinical options work for every practice, even before its prices arrive.
    # This catalog supplies no prices or coverage assumptions.
    known_codes = {row['code'] for row in available}
    descriptions = {**{str(n): 'Nerve conduction studies' for n in range(95907, 95914)},
                    '95885': 'Limited needle EMG', '95886': 'Complete needle EMG',
                    '62270': 'Diagnostic spinal tap · without imaging', '62328': 'Diagnostic spinal tap · with imaging',
                    **{str(n): 'Office consultation' for n in range(99202, 99206)},
                    **{str(n): 'Office consultation' for n in range(99212, 99216)}}
    available.extend({'id': 'planning:' + code, 'code': code, 'description': description,
                      'source': 'CPT planning catalog', 'units': 1, 'modifiers': []}
                     for code, description in descriptions.items() if code not in known_codes)
    for row in available:
        matches = [rate for rate in catalog if rate['code'] == row['code'] and (rate.get('modifier') or '') == ','.join(row.get('modifiers') or [])]
        if len(matches) == 1:
            row.update(unit_allowed_cents=matches[0]['allowed_cents'], unit_cash_cents=matches[0].get('cash_cents'),
                       description=row.get('description') or matches[0].get('description'), rate_source=matches[0].get('source_reference'))
    return payer_id, connections, codes, available, uploaded_cash
