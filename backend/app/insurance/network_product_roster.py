"""Clinic-confirmed product participation, read from the commercial database."""
from __future__ import annotations

import re

from app.insurance import commercial_store, network_participation
from app.insurance.identity import generic_plan_label, normalized, patient_plan_applies


def payer_key(name: str, network_names: list[str] | None = None) -> str:
    value = str(name or '').casefold()
    if re.search(r'\bcigna\b', value):
        return 'cigna'
    if re.search(r'\baetna\b', value):
        return 'aetna'
    if re.search(r'\b(?:bcbs\s*(?:tx|texas)|blue\s+cross(?:\s+and)?\s+blue\s+shield\s+(?:of\s+)?(?:tx|texas))\b', value):
        return 'bcbstx'
    if re.search(r'\b(?:united\s*health\s*care|uhc)\b', value):
        return 'uhc'
    if re.search(r'\bwellpoint\b', value):
        return 'wellpoint'
    if re.search(r'\bhealth\s*smart\b', value):
        return 'healthsmart'
    if re.search(r'\b(?:multiplan|multi\s*plan|phcs)\b', value):
        return 'multiplan'
    if re.search(r'\bhumana\b', value):
        return 'humana'
    if any(re.search(r'\b(?:multiplan|multi\s*plan|phcs)\b', str(row), re.I)
           for row in network_names or []):
        return 'multiplan'
    return ''


def product_key(name: str) -> str:
    """Ignore typography and the roster's bracket code or PHCS card-logo side."""
    text = re.sub(r'[®℠™]', '', str(name or '').strip())
    text = re.sub(r'\s*\[(?:[A-Z]{2,4})\]\s*$', '', text)
    text = re.sub(r'\s*\(Logo on (?:front|back) of card\)\s*$', '', text, flags=re.I)
    return normalized(text)


def matches_contract_network(network: dict, source: dict, payer_name: str, day: str) -> bool:
    """A confirmed product may use a verified contract with the same network name."""
    names = [name for name in source.get('payer_plan_names') or [] if not generic_plan_label(name)]
    keys = {product_key(name) for name in names}
    key = product_key(network.get('network_name') or '')
    payer = payer_key(payer_name, source.get('payer_network_names'))
    return (source.get('payer_plan_bound') is True and len(keys) == 1 and key in keys
            and network.get('status') == 'verified' and bool(network.get('plan_matcher'))
            and bool(payer) and bool(day)
            and bool(commercial_store.matching_network_product(
                str(network.get('practice_id') or ''), payer, key, day)))


def apply(result: dict, benefits: dict, practice_id: str, payer_name: str,
          inputs: dict, source: dict, payload: dict | None = None) -> bool:
    """Use one current exact product and an identified active NMCT visit context."""
    day = str(inputs.get('service_date') or '')
    names = [str(name) for name in source.get('payer_plan_names') or [] if name]
    names = [name for name in names if not generic_plan_label(name)] or names
    networks = [str(name) for name in source.get('payer_network_names') or [] if name]
    key = payer_key(payer_name, networks)
    rendering = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    clinic_rendering = str((source.get('rendering_provider') or {}).get('npi') or '')
    returned_npi = str(((payload or {}).get('provider') or {}).get('npi') or '')
    billing_npi = str((source.get('billing_provider') or {}).get('npi') or '')
    if (not network_participation.is_nmct_practice(practice_id) or not key or not day
            or source.get('payer_plan_bound') is not True or len({product_key(name) for name in names}) != 1
            or not patient_plan_applies(benefits, benefits.get('identity') or result.get('identity'), day)
            or benefits.get('historical_only') or benefits.get('source_errors')
            or benefits.get('network_conflict')
            or not rendering or rendering != clinic_rendering
            or returned_npi and returned_npi not in {rendering, billing_npi}
            or not source.get('location_id') or not inputs.get('place_of_service')
            or (source.get('billing_provider') or {}).get('tin_conflict')):
        return False
    matches = {match_key: row for match_key in {product_key(name) for name in [*names, *networks]}
               if match_key and (row := commercial_store.matching_network_product(
                   practice_id, key, match_key, day))}
    if len(matches) != 1:
        return False
    row = next(iter(matches.values()))
    old_source = benefits.get('network_source')
    old_tier = benefits.get('network_status')
    if old_source == 'payer_participation_message' and old_tier == 'in_network':
        return False
    if (old_source == 'payer_participation_message' and old_tier == 'out_of_network'
            or old_source in {'clinic_nonparticipation_rule', 'clinic_contract_inventory'}
            or result.get('agreement_exclusion')):
        benefits.update(network_status='unknown', network_conflict=True)
        result['network_evidence'] = {'decision': 'conflicting_network_evidence',
            'source': 'clinic_product_roster', 'reference': row['source_reference'],
            'reason': 'NMCT lists this product as participating, but another provider-specific network record says otherwise. Verify with the insurer before quoting an in-network amount.'}
        return False
    if old_source == 'clinic_product_roster' and old_tier == 'in_network':
        return True
    if network_participation.verified_in_network(benefits):
        return False
    benefits.update(network_status='in_network', network_source='clinic_product_roster',
                    network_evidence_ids=[], network_provider_npi=None)
    result['network_evidence'] = {'decision': 'in_network_by_clinic_attestation',
        'source': 'clinic_product_roster', 'reference': row['source_reference'],
        'reason': 'NMCT confirms participation in this listed product for its selected provider and office. The insurer did not separately confirm this provider’s participation in the eligibility response.'}
    return True
