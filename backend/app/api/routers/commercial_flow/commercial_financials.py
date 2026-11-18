"""Patient-bound remittance and paid-claim reads for one saved visit."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api
from app.insurance import stedi_claim_collection


async def saved_visit_financials(case: dict, practice_id: str) -> dict:
    context = case['previsit_context']
    inputs, source = context['inputs'], context['source']
    billing_npi = (source.get('billing_provider') or {}).get('npi')
    networks = await _api.run_in_threadpool(_api.commercial_store.list_networks, practice_id)
    tins = {row['tin'] for row in networks if row.get('status') == 'verified' and row.get('billing_npi') == billing_npi}
    identity = {**_api._coverage_snapshot(inputs, source), 'billing_npi': billing_npi,
                'tin': next(iter(tins)) if len(tins) == 1 else None}
    identity['claim_identifiers'] = await _api.ecw_backend.fetch_claim_identifiers(source)
    financials = await _api.stedi_financial.fetch_patient_financials(practice_id, identity)
    financials['payments_verified_cents'] = ((context.get('result') or {}).get('encounter_summary') or {}).get('payments_verified_cents')
    financials['claim_collection'] = await stedi_claim_collection.lookup(case, identity, financials,
        _api.stedi_financial.credentials(practice_id))
    return financials
