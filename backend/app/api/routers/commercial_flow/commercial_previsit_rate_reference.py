"""Temporary clinic fee reference for other payers' planning."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

BCBSTX_2026_OTHER_CODES = {'70496': 22719, '70498': 22693}
BCBSTX_2026_OTHER_CODES_SOURCE = ('https://www.bcbstx.com/docs/provider/tx/standards/general-reimbursement-information/'
    'gri-info/professional-schedules/2026-internet-posting-notices/other-codes-2026-files/dft-2026-v1.pdf')

def refresh_saved_temporary_rates(result: dict, practice_id: str, inputs: dict, source: dict,
                                  route_payer: dict | None) -> None:
    """Rebuild saved cross-payer references from the current scoped clinic rates."""
    payer_name = str(result.get('payer') or (route_payer or {}).get('display_name') or '')
    if _api.commercial.is_united_healthcare_payer(
            result, {'displayName': route_payer.get('display_name')} if route_payer else None):
        _attach_temporary_uhc_rates(result, practice_id, inputs, source)
    elif _api.re.search(r'\bcigna\b', payer_name, _api.re.I):
        _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, 'Cigna')
    elif _api.re.search(r'\baetna\b', payer_name, _api.re.I):
        _attach_temporary_aetna_rates(result, practice_id, inputs, source)
    elif payer_name and not _api.re.search(r'blue cross|bcbstx|blue shield|humana', payer_name, _api.re.I):
        _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, payer_name)

def _attach_temporary_blue_cross_rates(result: dict, practice_id: str, inputs: dict, source: dict,
                                       payer_name: str) -> None:
    """Expose scoped BCBS rates as a planning reference, never a payer allowance."""
    rendering_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    billing_npi = str((source.get('billing_provider') or {}).get('npi') or '')
    location_id = str(source.get('location_id') or '')
    pos = str(inputs.get('place_of_service') or '')
    date = str(inputs.get('service_date') or '')
    codes = result.get('codes') or []
    if not all((rendering_npi, billing_npi, location_id, pos, date, codes)):
        return
    payers = [row for row in _api.commercial_store.list_connections(practice_id)
              if row.get('status') == 'staff_guidance_enabled' and
              _api.re.search(r'blue cross.*(?:blue shield|bcbs).*texas|bcbstx', str(row.get('display_name') or ''), _api.re.I)]
    if len(payers) != 1:
        return
    networks = [row for row in _api.commercial_store.list_networks(practice_id)
                if row.get('payer_connection_id') == payers[0]['id'] and row.get('status') == 'verified'
                and row.get('rendering_npi') == rendering_npi and row.get('billing_npi') == billing_npi
                and row.get('location_id') == location_id and row.get('place_of_service') == pos
                and row.get('effective_from', '') <= date <= (row.get('effective_to') or '9999-12-31')]
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
    by_code = {(row['code'], row.get('modifier') or ''): row for row in rates
               if str(row.get('source_reference') or '').startswith('library:')}
    lines = []
    published = False
    for code in codes:
        rate = by_code.get((code['code'], ','.join(code.get('modifiers') or [])))
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
    if lines:
        result['temporary_rate_reference'] = {'source': ('Blue Cross Texas clinic and published 2026 fee references'
            if published else 'NMCT Clinical Library Blue Cross fee schedule'),
            'lines': lines, 'complete': len(lines) == len(codes),
            'message': (f'Temporary CPT price reference only. Published unmodified CTA amounts are from the BCBSTX 2026 professional Other Codes schedule. These are not {payer_name} contracted allowances or a patient amount to collect.'
                        if published else
                        f'Temporary CPT price reference only. These are Blue Cross rates, not {payer_name} contracted allowances.'
                        if payer_name == 'Cigna' else
                        f'Temporary CPT price reference only. These are Blue Cross rates, not {payer_name} contracted allowances or a patient amount to collect.')}


def _attach_temporary_uhc_rates(result: dict, practice_id: str, inputs: dict, source: dict) -> None:
    _attach_temporary_blue_cross_rates(result, practice_id, inputs, source, 'UnitedHealthcare')


def _attach_temporary_aetna_rates(result: dict, practice_id: str, inputs: dict, source: dict) -> None:
    """Show a sourced, noncollectible Aetna diagnostic planning estimate."""
    from app.insurance import published_plan_benefits

    day = str(inputs.get('service_date') or '')
    benefits = result.get('reference_benefits') or result.get('benefits') or {}
    codes = result.get('codes') or []
    if (result.get('payment_mode') == 'cash' or str(inputs.get('place_of_service') or '') != '11'
            or len(codes) != 2 or {row.get('code') for row in codes} != {'95908', '95886'}
            or any(row.get('units') != 1 or row.get('modifiers') for row in codes)
            or any(not published_plan_benefits.aetna_signature_diagnostic(
                benefits, day, row['code'], payer_verified=True) for row in codes)):
        return
    payers = [row for row in _api.commercial_store.list_connections(practice_id)
              if row.get('stedi_payer_id') == 'HPQRS' and row.get('status') in {'draft', 'staff_guidance_enabled'}]
    if len(payers) != 1:
        return
    rates = _api.commercial_store.aetna_meppo_reference_rates(
        practice_id, payers[0]['id'], day, ['95908', '95886'])
    if len(rates) != 2 or {(row['code'], row.get('modifier') or '') for row in rates} != {
            ('95908', ''), ('95886', '')} or any(type(row.get('allowed_cents')) is not int
            or row['allowed_cents'] < 5000 for row in rates):
        return
    by_code = {row['code']: row for row in rates}
    lines = [{'id': code.get('id'), 'code': code['code'], 'units': 1, 'modifiers': [],
              'unit_reference_cents': by_code[code['code']]['allowed_cents'],
              'source_reference': by_code[code['code']]['source_reference']}
             for code in codes]
    total = sum(row['unit_reference_cents'] for row in lines)
    result['temporary_rate_reference'] = {
        'source': 'Aetna MEPPO portal table values supplied by clinic', 'lines': lines,
        'complete': True,
        'message': 'Dated Aetna maximums from a clinic-supplied portal table. The provider and contract scope is unverified; these are planning references, not active clinic allowed rates.'}
    oop = benefits.get('oop_remaining_cents')
    if type(oop) is not int or oop < 0 or benefits.get('deductible_remaining_cents') != 0:
        return
    copay = published_plan_benefits.AETNA_SIGNATURE_TX_2026['diagnostic_copay_cents'] * len(lines)
    patient = min(total, copay, oop)
    result['aetna_diagnostic_reference'] = {
        'estimated_allowed_cents': total, 'copay_cents': copay,
        'deductible_cents': 0, 'coinsurance_cents': 0, 'coinsurance_percent': '0',
        'oop_reduction_cents': copay - patient, 'oop_remaining_cents': oop,
        'patient_share_cents': patient, 'payer_share_cents': total - patient,
        'benefit_source': published_plan_benefits.AETNA_SIGNATURE_TX_2026,
        'message': 'Planning estimate from the likely 2026 Aetna Signature PPO diagnostic benefit. Verify the exact member plan, Medicare billing protection and prior payments before charging.'}
