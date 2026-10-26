"""Recheck saved payer participation against the rendering provider binding."""
from __future__ import annotations

import re
import hashlib
import json

from app.core import object_store
from app.insurance import commercial, group_contract_support, inventory_certification, library_network_exclusions
from app.insurance.identity import patient_benefits_bound, patient_plan_applies


def record_confirmed_out_of_network(result: dict, benefits: dict) -> None:
    """Describe the payer statement or clinic rule that selected this tier."""
    source = benefits.get('network_source')
    if source == 'clinic_contract_inventory':
        return  # Keep the certified scope and exclusion passage already attached.
    if source not in {'payer_participation_message', 'clinic_nonparticipation_rule', 'clinic_no_blue_contract'}:
        return
    result['network_evidence'] = {
        'decision': ('out_of_network_confirmed_by_payer' if source == 'payer_participation_message'
                     else 'out_of_network_by_clinic_contract_record' if source == 'clinic_no_blue_contract'
                     else 'out_of_network_confirmed_by_scoped_nonparticipation_record'),
        'source': source, 'evidence_ids': benefits.get('network_evidence_ids') or [],
        'reason': ('Product-specific payer evidence identifies this requesting provider as out of network.'
                   if source == 'payer_participation_message' else
                   'No current Blue contract is registered in the clinic Library for this visit date.'
                   if source == 'clinic_no_blue_contract' else
                   'A scoped clinic nonparticipation rule applies to this provider and product.')}


def resolve_unlinked_blue_route(result: dict, practice_id: str, inputs: dict, source: dict,
                                payer_id: str, route_payer: dict | None, benefits: dict) -> bool:
    """Select a supported BlueCard or absent-contract tier without a clinic payer connection."""
    route = group_contract_support.eligibility_route(result, payer_id, route_payer)
    supported = group_contract_support.resolve(practice_id, route, inputs, source, result, benefits)
    if supported and supported.get('product_network_source') == 'signed_bluecard_agreement':
        result['network_evidence'] = supported
        benefits['network_status'] = 'in_network'
        benefits['network_source'] = 'clinic_group_agreement'
        return True
    if (route and source.get('payer_plan_bound') is True and benefits.get('active') is True
            and benefits.get('service_date_matches') is not False
            and group_contract_support.no_blue_contract(practice_id, route, str(inputs.get('service_date') or ''))):
        benefits['network_status'] = 'out_of_network'
        benefits['network_source'] = 'clinic_no_blue_contract'
        record_confirmed_out_of_network(result, benefits)
        return True
    return False


def _clear_saved_tier(result: dict, reason: str) -> None:
    benefits = result.get('benefits') or {}
    result.setdefault('issues', []).append(reason)
    benefits.update(network_status='unknown', network_source=None, network_evidence_ids=[])
    result['network_evidence'] = {'reason': reason}
    price = result.get('price_range') or {}
    price.update(complete=False, patient_min_cents=None, patient_max_cents=None,
                 allowed_min_cents=None, allowed_max_cents=None,
                 plan_min_cents=None, plan_max_cents=None,
                 collection={'action': 'rerun', 'amount_cents': None, 'reason': reason})
    for line in price.get('lines') or []:
        line.update(allowed_cents=None, unit_allowed_cents=None, calculation=None)
        if line.get('benefits'):
            line['benefits'].update(network_status='unknown', network_source=None,
                                    network_evidence_ids=[], network_provider_npi=None)
    result.pop('arrival_collection', None)
    result.pop('planning_estimate', None)


def recheck_saved_inventory(result: dict, case: dict, practice_id: str, payer: dict | None) -> None:
    """A revoked or changed certification cannot revive a saved OON collection."""
    benefits = result.get('benefits') or {}
    if benefits.get('network_source') != 'clinic_contract_inventory':
        return
    context = case.get('previsit_context') or {}
    inputs = context.get('inputs') or {}
    source = ((case.get('previsit_discovery') or {}).get('pricing_source')
              or context.get('source') or {})
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    identity = result.get('identity') or benefits.get('identity') or {}
    names = plan.get('names') or []
    exact = {**source, 'payer_plan_bound': benefits.get('active') is True
             and benefits.get('service_date_matches') is True and patient_benefits_bound(identity),
             'payer_plan_names': names}
    exclusion = library_network_exclusions.applicable_exclusion(
        practice_id, str((payer or {}).get('display_name') or ''), names,
        str(inputs.get('service_date') or '')) if len(names) == 1 else None
    current = inventory_certification.applicable(practice_id, payer or {}, inputs, exact, exclusion)
    saved = result.get('network_evidence') or {}
    if (current and current.get('approved_at') == saved.get('approved_at')
            and current.get('document_hashes') == saved.get('document_hashes')
            and current.get('scope') == saved.get('scope')):
        return
    _clear_saved_tier(result, 'The clinic’s certified UHC agreement inventory changed or no longer covers this provider and visit. Recalculate network status before collecting.')
    if result.get('agreement_exclusion'):
        result['network_evidence'] = {**result['agreement_exclusion'],
            'reason': 'The product remains excluded under the uploaded agreement, but the inventory certification no longer applies to this visit.'}


def recheck_saved_network_evidence(result: dict, case: dict, practice_id: str, payer: dict | None) -> None:
    """Project current contract support from saved case context without a payer call or write."""
    recheck_saved_inventory(result, case, practice_id, payer)
    benefits = result.get('benefits') or {}
    prior_group = benefits.get('network_source') == 'clinic_group_agreement'
    prior_blue_absence = benefits.get('network_source') == 'clinic_no_blue_contract'
    if prior_group or prior_blue_absence:
        benefits.update(network_status='unknown', network_source=None)
        result['network_evidence'] = {'decision': 'lacking_applicable_network_evidence'}
    payer_id = str((case.get('previsit_context') or {}).get('payer_id') or '')
    route = group_contract_support.eligibility_route(result, payer_id, payer)
    if (not route or benefits.get('network_status') != 'unknown' or result.get('agreement_exclusion')
            or (result.get('network_evidence') or {}).get('decision') not in
               {None, 'lacking_applicable_network_evidence'}):
        if prior_group or prior_blue_absence:
            _clear_saved_tier(result, 'The signed clinic agreement no longer supports this network and visit.')
        return
    context = case.get('previsit_context') or {}
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    if (not plan.get('identified_product') or not plan.get('verification_payer')) and case.get('previsit_raw_sha256') and case.get('previsit_raw_object_key'):
        try:
            raw = object_store.get_object_bytes(case['previsit_raw_object_key'])
            if hashlib.sha256(raw).hexdigest() == case['previsit_raw_sha256']:
                parsed = commercial.plan_details(json.loads(raw))
                if parsed.get('names') == plan.get('names'):
                    plan = {**plan, **parsed}
                    result.setdefault('plan_details', {})['stedi'] = plan
        except Exception:
            pass  # Keep the frozen projection when its protected raw response cannot be reopened.
    names = [plan['identified_product']] if plan.get('identified_product') else plan.get('names') or []
    source = ((case.get('previsit_discovery') or {}).get('pricing_source')
              or context.get('source') or {})
    identity = result.get('identity') or benefits.get('identity') or {}
    service_date = str((context.get('inputs') or {}).get('service_date') or '')
    exact = {**source, 'payer_plan_bound': patient_plan_applies(benefits, identity, service_date),
             'payer_plan_names': names, 'payer_network_names': plan.get('network_names') or [],
             'payer_verification_party': plan.get('verification_payer')}
    supported = group_contract_support.resolve(practice_id, route, context.get('inputs') or {},
                                               exact, result, benefits)
    if supported:
        result['network_evidence'] = supported
        benefits['network_status'] = 'in_network'
        benefits['network_source'] = 'clinic_group_agreement'
    elif (exact['payer_plan_bound'] and group_contract_support.no_blue_contract(
            practice_id, route, service_date)):
        if prior_group:
            _clear_saved_tier(result, 'The signed clinic agreement no longer supports this network and visit.')
        benefits['network_status'] = 'out_of_network'
        benefits['network_source'] = 'clinic_no_blue_contract'
        record_confirmed_out_of_network(result, benefits)
    elif prior_group or prior_blue_absence:
        _clear_saved_tier(result, 'The signed clinic agreement no longer supports this network and visit.')


def recheck_saved_payer_message(result: dict, case: dict) -> None:
    benefits = result.get('benefits') or {}
    if benefits.get('network_source') != 'payer_participation_message':
        return
    context = case.get('previsit_context') or {}
    source = ((case.get('previsit_discovery') or {}).get('pricing_source')
              or context.get('source') or {})
    rendering = source.get('rendering_provider') or {}
    npi = str(rendering.get('npi') or '')
    saved_npi = str(benefits.get('network_provider_npi') or '')
    input_npi = str(((context.get('inputs') or {}).get('rendering_provider') or {}).get('npi') or '')
    if (re.fullmatch(r'\d{10}', npi) and saved_npi == npi
            and rendering.get('first_name') and rendering.get('last_name')
            and (not input_npi or input_npi == npi)):
        return
    _clear_saved_tier(result, 'The saved payer network statement is not bound to this rendering provider NPI. Rerun the payer check for this provider before collecting.')
    benefits['network_provider_npi'] = None
