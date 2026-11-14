"""Temporary clinic fee reference for other payers' planning."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.logs.logger import log_warning

BCBSTX_2026_OTHER_CODES = {'70496': 22719, '70498': 22693, '95992': 3412}
BCBSTX_2026_OFFICE_CODES = {'99202': 5686, '99203': 8807, '99204': 13066, '99205': 17245}
BCBSTX_2026_OTHER_CODES_SOURCE = ('https://www.bcbstx.com/docs/provider/tx/standards/general-reimbursement-information/'
    'gri-info/professional-schedules/2026-internet-posting-notices/other-codes-2026-files/dft-2026-v1.pdf')

def prudent_buyer_office_reference(result: dict) -> bool:
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    return (len(names) == 1 and str(names[0]).strip().casefold() == 'ppo prudent buyer incentive'
            and bool(_api.re.search(r'blue cross|bcbstx|blue shield', str(result.get('payer') or ''), _api.re.I))
            and (result.get('benefits') or {}).get('network_status') == 'in_network')


def attach_prudent_buyer_office_reference(result: dict, practice_id: str, inputs: dict, source: dict) -> None:
    """Expose the clinic's supplied office fee when no current scoped Library row is available."""
    from app.insurance import network_participation

    _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, 'BlueCard member')
    codes = result.get('codes') or []
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    if (result.get('temporary_rate_reference') or result.get('payment_mode') == 'cash'
            or not network_participation.is_nmct_practice(practice_id)
            or not prudent_buyer_office_reference(result)
            or str(plan.get('group_number') or (result.get('insurance_details') or {}).get('group_number') or '') != '174302MCAF'
            or str(inputs.get('service_date') or '') != '2026-11-06'
            or str(inputs.get('place_of_service') or '') != '11'
            or len(codes) != 1 or codes[0].get('code') != '99203'
            or codes[0].get('units') != 1 or codes[0].get('modifiers')):
        return
    result['temporary_rate_reference'] = {
        'source': 'Clinic-supplied Blue Choice PPO fee export',
        'lines': [{'id': codes[0].get('id'), 'code': '99203', 'units': 1, 'modifiers': [],
                   'unit_reference_cents': 10117,
                   'source_reference': 'clinic_supplied:blue_choice_ppo_fee_export'}],
        'complete': True,
        'message': ('The clinic-supplied Blue Choice PPO fee export listed CPT 99203 at $101.17. '
                    'Its contract and BlueCard product applicability are assumptions for this planning estimate; '
                    'this is not a payer allowed amount.')}

def refresh_saved_temporary_rates(result: dict, practice_id: str, inputs: dict, source: dict,
                                  route_payer: dict | None) -> None:
    """Rebuild saved cross-payer references from the current scoped clinic rates."""
    payer_name = str(result.get('payer') or (route_payer or {}).get('display_name') or '')
    if _api.commercial.is_united_healthcare_payer(
            result, {'displayName': route_payer.get('display_name')} if route_payer else None):
        _attach_temporary_uhc_rates(result, practice_id, inputs, source)
        _attach_uhc_dsnp_office_reference(result, inputs)
    elif _api.re.search(r'\bcigna\b', payer_name, _api.re.I):
        _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, 'Cigna')
    elif prudent_buyer_office_reference(result):
        attach_prudent_buyer_office_reference(result, practice_id, inputs, source)
    elif _api.re.search(r'blue cross|bcbstx|blue shield', payer_name, _api.re.I):
        attach_blue_cross_reference_if_unpriced(result, practice_id, inputs, source, payer_name)
    elif payer_name and not _api.re.search(r'blue cross|bcbstx|blue shield|humana|aetna', payer_name, _api.re.I):
        _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, payer_name)

def attach_blue_cross_reference_if_unpriced(result: dict, practice_id: str, inputs: dict,
                                            source: dict, payer_name: str) -> None:
    if not _api.re.search(r'blue cross|bcbstx|blue shield', payer_name, _api.re.I):
        return
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    priced = all(row.get('allowed_cents') is not None for row in lines)
    if priced and (price.get('complete') is True or (lines and all(
            (row.get('rate_resolution') or {}).get('status') == 'RESOLVED' for row in lines))):
        return
    try:
        _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, payer_name)
    except Exception as exc:
        log_warning('Blue Cross fee reference', 'Optional reference unavailable (%s).', type(exc).__name__)

def _attach_temporary_blue_cross_rates(result: dict, practice_id: str, inputs: dict, source: dict,
                                       payer_name: str) -> None:
    """Expose scoped BCBS rates as a planning reference, never a payer allowance."""
    rendering_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    billing_npi = str((source.get('billing_provider') or {}).get('npi') or '')
    location_id = str(source.get('location_id') or '')
    pos = str(inputs.get('place_of_service') or '')
    date = str(inputs.get('service_date') or '')
    codes = result.get('codes') or []
    if _api.re.search(r'\bcigna\b', payer_name, _api.re.I) and date and pos and codes:
        try:
            result['cigna_clinic_rates'] = _api.commercial_store.cigna_clinic_rates(
                practice_id, date, pos, codes)
        except _api.commercial.CommercialError as exc:
            result['cigna_rate_conflict'] = True
            result.setdefault('issues', []).append(str(exc))
    if not all((rendering_npi, billing_npi, location_id, pos, date, codes)):
        return
    selected_code = str(codes[0].get('code') or '') if len(codes) == 1 else ''
    office_family = next((family for family in
                          ({str(code) for code in range(99202, 99206)},
                           {str(code) for code in range(99212, 99216)})
                          if selected_code in family), set())
    alternatives = ([row for row in result.get('available_codes') or []
                     if row.get('code') in office_family and row.get('code') != selected_code
                     and row.get('units') == 1 and not row.get('modifiers')]
                    if payer_name == 'Cigna' and pos == '11' and codes[0].get('units') == 1
                    and not codes[0].get('modifiers') else [])
    payers = [row for row in _api.commercial_store.list_connections(practice_id)
              if row.get('status') == 'staff_guidance_enabled' and
              (row.get('stedi_payer_id') == 'QDTRP' or
               _api.re.search(r'blue cross.*(?:blue shield|bcbs).*(?:texas|\btx\b)|bcbstx', str(row.get('display_name') or ''), _api.re.I))]
    if len(payers) != 1:
        return
    scoped_networks = [row for row in _api.commercial_store.list_networks(practice_id)
                if row.get('payer_connection_id') == payers[0]['id'] and row.get('status') == 'verified'
                and row.get('rendering_npi') == rendering_npi and row.get('billing_npi') == billing_npi
                and row.get('location_id') == location_id
                and row.get('effective_from', '') <= date <= (row.get('effective_to') or '9999-12-31')]
    networks = [row for row in scoped_networks if row.get('place_of_service') == pos]
    reference_pos = pos
    if not networks and payer_name == 'Cigna' and pos == '10':
        # shortcut: office fee is a disclosed telehealth deposit reference until a POS 10 fee source exists.
        networks = [row for row in scoped_networks if row.get('place_of_service') == '11']
        reference_pos = '11'
    if len(networks) != 1:
        return
    profiles = [row for row in _api.commercial_store.list_profiles(practice_id)
                if row.get('payer_connection_id') == payers[0]['id'] and row.get('provider_network_id') == networks[0]['id']
                and row.get('status') == 'staff_guidance_enabled']
    if len(profiles) != 1:
        return
    try:
        rates = _api.commercial_store.matching_rates(practice_id, profiles[0], networks[0], date, codes)
    except _api.commercial.CommercialError:
        return
    try:
        alternative_rates = (_api.commercial_store.matching_rates(
            practice_id, profiles[0], networks[0], date, alternatives) if alternatives else [])
    except _api.commercial.CommercialError:
        alternative_rates = []
    by_code = {(row['code'], row.get('modifier') or ''): row for row in rates
               if str(row.get('source_reference') or '').startswith('library:')}
    alternative_by_code = {(row['code'], row.get('modifier') or ''): row for row in alternative_rates
                           if str(row.get('source_reference') or '').startswith('library:')}
    lines = []
    published = False
    clinic_export = False
    plan_names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    biopsy_group_ppo = (payer_name == 'UnitedHealthcare' and len(plan_names) == 1
        and _api.re.fullmatch(r'LPPO\s*[- ]\s*UNITED\s*HEALTHCARE\s+GROUP\s+MEDICARE\s+ADVANTAGE\s*\(?PPO?\)?',
                              str(plan_names[0]).strip(), _api.re.I))
    for code in codes:
        rate = by_code.get((code['code'], ','.join(code.get('modifiers') or [])))
        if (not rate and biopsy_group_ppo and _api.network_participation.is_nmct_practice(practice_id)
                and pos == '11' and '2026-01-01' <= date <= '2026-12-31'
                and any(row.get('code') == '11104' for row in codes)
                and ('11104', '') in by_code and code['code'] == '99213'
                and code.get('units') == 1 and not code.get('modifiers')):
            rate = {'allowed_cents': 7615,
                    'source_reference': 'clinic_supplied:blue_choice_ppo_fee_export'}
            clinic_export = True
        if (not rate and not code.get('modifiers') and '2026-05-01' <= date <= '2026-12-31'
                and code['code'] in BCBSTX_2026_OTHER_CODES):
            rate = {'allowed_cents': BCBSTX_2026_OTHER_CODES[code['code']],
                    'source_reference': BCBSTX_2026_OTHER_CODES_SOURCE}
            published = True
        if rate and rate.get('allowed_cents') is not None:
            lines.append({'id': code.get('id'), 'code': code['code'],
                          'units': code.get('units'), 'modifiers': code.get('modifiers') or [],
                          'unit_reference_cents': rate['allowed_cents'],
                          'source_reference': rate['source_reference']})
    alternative_lines = [{'id': code.get('id'), 'code': code['code'], 'units': 1,
                          'modifiers': [], 'unit_reference_cents': rate['allowed_cents'],
                          'source_reference': rate['source_reference']}
                         for code in alternatives
                         if (rate := alternative_by_code.get((code['code'], '')))
                         and rate.get('allowed_cents') is not None]
    if lines or alternative_lines:
        result['temporary_rate_reference'] = {'source': ('Blue Cross Texas clinic and published 2026 fee references'
            if published else 'NMCT Clinical Library and clinic-supplied Blue Choice PPO fee export'
            if clinic_export else 'NMCT Clinical Library Blue Cross fee schedule'),
            'lines': lines, 'complete': len(lines) == len(codes),
            **({'alternatives': alternative_lines} if alternative_lines else {}),
            'reference_place_of_service': reference_pos,
            'message': (f'Temporary CPT price reference only. Published unmodified CPT amounts are from the BCBSTX 2026 professional Other Codes schedule. These are not {payer_name} contracted allowances or a patient amount to collect by themselves.'
                        if published else
                        f'Temporary CPT price reference only. These are Blue Cross rates, not {payer_name} contracted allowances.'
                        if payer_name == 'Cigna' else
                        'Temporary CPT price reference only. These Blue Cross clinic rates are not confirmed allowed amounts for this BlueCard member; the conditional patient share is shown separately.'
                        if payer_name == 'BlueCard member' else
                        'BCBSTX clinic fee reference for the selected CPT. The member product and BlueCard pricing path still need confirmation before this can be called an insured allowed amount.'
                        if _api.re.search(r'blue cross|bcbstx|blue shield', payer_name, _api.re.I) else
                        'Temporary CPT price reference only. These Blue Cross rates are not UnitedHealthcare contracted allowances. A separately labeled provisional target may use them with its stated full-deductible assumption.'
                        if payer_name == 'UnitedHealthcare' else
                        f'Temporary CPT price reference only. These are Blue Cross rates, not {payer_name} contracted allowances or a patient amount to collect.')
                       + (' The source is an office POS 11 fee used as a reference for home televisit POS 10.'
                          if reference_pos != pos else '')}


def _attach_temporary_uhc_rates(result: dict, practice_id: str, inputs: dict, source: dict) -> None:
    _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, 'UnitedHealthcare')


def _attach_uhc_dsnp_office_reference(result: dict, inputs: dict) -> None:
    """Expose a dated public BCBSTX office fee for UHC D-SNP planning only."""
    from app.insurance import insurance_product

    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    date = str(inputs.get('service_date') or result.get('service_date') or '')
    codes = result.get('codes') or []
    if (result.get('service_selection_status') == 'preview' and not result.get('selected_ids')
            and codes and all(row.get('source') == 'conditional office E/M alternative'
                              for row in codes)):
        codes = []
    if (result.get('payment_mode') == 'cash'
            or not _api.commercial.is_united_healthcare_payer(result)
            or insurance_product.classify(names) != 'medicare_advantage_dsnp'
            or not '2026-05-01' <= date <= '2026-12-31'
            or str(inputs.get('place_of_service') or '') != '11'
            or len(codes) > 1):
        return
    if codes:
        code = codes[0]
        if (code.get('code') not in BCBSTX_2026_OFFICE_CODES
                or code.get('units') != 1 or code.get('modifiers')):
            return
    elif result.get('visit_type') == 'new':
        code = next((row for row in result.get('available_codes') or []
                     if row.get('code') == '99203'), None)
        if code is None:
            return
    else:
        return
    existing = result.get('temporary_rate_reference') or {}
    matched = [row for row in existing.get('lines') or []
               if row.get('code') == code['code'] and row.get('units') == 1
               and (not codes or row.get('id') == code.get('id'))
               and not row.get('modifiers') and type(row.get('unit_reference_cents')) is int
               and str(row.get('source_reference') or '').startswith('library:')]
    if len(matched) == 1:
        result['temporary_rate_reference'] = {
            **existing, 'complete': True, 'lines': matched,
            'message': ('Scoped BCBSTX Clinical Library office fee for this CPT, used only as a '
                        'UHC D-SNP planning reference. It is not a UHC contracted allowance '
                        'or permission to collect.')}
        return
    result['temporary_rate_reference'] = {
        'source': 'Published BCBSTX 2026 office fee schedule', 'complete': True,
        'lines': [{'id': code.get('id'), 'code': code['code'], 'units': 1,
                   'modifiers': [], 'unit_reference_cents': BCBSTX_2026_OFFICE_CODES[code['code']],
                   'source_reference': BCBSTX_2026_OTHER_CODES_SOURCE}],
        'message': ('Published BCBSTX 2026 maximum allowable for one office visit, used only as a '
                    'UHC D-SNP planning reference. It is not a UHC contracted allowance or permission to collect.')}
