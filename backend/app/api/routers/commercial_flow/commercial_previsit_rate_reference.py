"""Temporary clinic fee reference for UHC planning."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

def _attach_temporary_uhc_rates(result: dict, practice_id: str, inputs: dict, source: dict) -> None:
    """Expose the clinic's scoped BCBS rates as a UHC planning reference, never an insured allowance."""
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
    for code in codes:
        rate = by_code.get((code['code'], ','.join(code.get('modifiers') or [])))
        if rate and rate.get('allowed_cents') is not None:
            lines.append({'code': code['code'], 'unit_reference_cents': rate['allowed_cents'],
                          'source_reference': rate['source_reference']})
    if lines:
        result['temporary_rate_reference'] = {'source': 'NMCT Clinical Library Blue Cross fee schedule',
            'lines': lines, 'complete': len(lines) == len(codes),
            'message': 'Temporary CPT price reference only. These are Blue Cross rates, not UnitedHealthcare contracted allowances or a patient amount to collect.'}
