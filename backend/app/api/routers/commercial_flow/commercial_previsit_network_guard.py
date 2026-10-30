"""Recheck saved payer participation against the rendering provider binding."""
from __future__ import annotations

import re
import hashlib
import json

from app.core import object_store, staff_store
from app.insurance import commercial, commercial_store, group_contract_support, insurance_product, inventory_certification, library_network_exclusions, network_participation
from app.insurance.identity import patient_benefits_bound, patient_plan_applies


def scoped_admin_nonparticipation(practice_id: str, payer: dict | None, inputs: dict,
                                  source: dict, plan_names: list[str], group_number: str) -> dict | None:
    """Match an administrator attestation to one payer product and visit scope."""
    if (not payer or source.get('payer_plan_bound') is not True or len(plan_names) != 1
            or str(group_number) != '13705'
            or insurance_product.classify(plan_names) != 'medicare_advantage_group_ppo'):
        return None
    norm = lambda value: re.sub(r'[^a-z0-9]', '', str(value or '').casefold())
    billing = source.get('billing_provider') or {}
    if billing.get('tin_conflict'):
        return None
    input_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    source_npi = str((source.get('rendering_provider') or {}).get('npi') or '')
    if input_npi and source_npi and input_npi != source_npi:
        return None
    scope = (str(billing.get('npi') or ''), input_npi or source_npi,
             re.sub(r'\D', '', str(billing.get('tin') or '')), str(source.get('location_id') or ''),
             str(inputs.get('place_of_service') or ''), str(inputs.get('service_date') or ''))
    if not scope[1] or not scope[5] or not group_number:
        return None
    matches = [row for row in commercial_store.list_networks(practice_id)
               if row['payer_connection_id'] == payer['id'] and row['status'] == 'nonparticipating'
               and str(row.get('evidence_reference') or '').startswith('clinic-admin-attestation:')
               and (row['billing_npi'], row['rendering_npi'], re.sub(r'\D', '', row['tin']),
                    row['location_id'], row['place_of_service']) == scope[:5]
               and norm(row.get('plan_matcher')) == norm(plan_names[0])
               and str(row.get('plan_group_number') or '') == str(group_number)
               and row['effective_from'] <= scope[5]
               and (not row.get('effective_to') or row['effective_to'] >= scope[5])]
    return matches[0] if len(matches) == 1 else None


def apply_admin_nonparticipation(result: dict, benefits: dict, row: dict | None) -> bool:
    if not row or network_participation.participation_conflict(benefits):
        return False
    if benefits.get('network_source') == 'payer_participation_message':
        if benefits.get('network_status') == 'in_network':
            benefits.update(network_status='unknown', network_conflict=True)
            result['network_evidence'] = {'decision': 'conflicting_network_evidence',
                'reason': 'A payer provider-participation statement conflicts with the clinic administrator attestation.'}
        return False
    benefits.update(network_status='out_of_network', network_source='clinic_nonparticipation_rule')
    result['network_evidence'] = {'decision': 'out_of_network_confirmed_by_scoped_nonparticipation_record',
        'source': 'clinic_admin_attestation', 'reference': row['evidence_reference'],
        'reason': 'Clinic administrator attestation for this payer product, group, rendering provider and recorded visit scope; payer participation is not independently confirmed.'}
    return True


def apply_nmct_library_contract(result: dict, benefits: dict, document: dict | None) -> bool:
    if not document or network_participation.participation_conflict(benefits):
        return False
    if (benefits.get('network_source') == 'payer_participation_message'
            and benefits.get('network_status') == 'in_network'):
        return False
    if (benefits.get('network_status') == 'in_network'
            and benefits.get('network_source') in {'clinic_library_contract', 'clinic_group_agreement'}
            and (result.get('network_evidence') or {}).get('document_id')):
        return False
    payer_disagrees = (benefits.get('network_source') == 'payer_participation_message'
                      and benefits.get('network_status') == 'out_of_network')
    benefits.update(network_status='in_network', network_source='clinic_library_contract',
                    network_evidence_ids=[document['id']], network_provider_npi=None)
    if payer_disagrees:
        benefits['network_conflict'] = True
    result['network_evidence'] = {'decision': 'conflicting_network_evidence' if payer_disagrees else 'confirmed_participating',
        'source': 'clinic_library_contract', 'document_id': document['id'],
        'document_label': document.get('label'),
        'reason': ('The payer reports this provider out of network despite NMCT’s current insurer contract; reconcile before collecting.'
                   if payer_disagrees else
                   'NMCT has a current Clinical Library contract for this insurer; its Medicare, HMO and PPO products are in network.')}
    return True


def apply_nmct_contract_absence(result: dict, benefits: dict, payer: str, document: dict | None,
                                service_date: str, plan_bound: bool) -> bool:
    if (document or not payer or not service_date
            or plan_bound is not True or benefits.get('active') is not True
            or benefits.get('service_date_matches') is False
            or benefits.get('network_source') == 'payer_participation_message'
            or network_participation.verified_out_of_network(benefits)
            or network_participation.participation_conflict(benefits)):
        return False
    benefits.update(network_status='out_of_network', network_source='clinic_no_insurer_contract',
                    network_evidence_ids=[], network_provider_npi=None)
    record_confirmed_out_of_network(result, benefits)
    return True


def record_confirmed_out_of_network(result: dict, benefits: dict) -> None:
    """Describe the payer statement or clinic rule that selected this tier."""
    source = benefits.get('network_source')
    if (result.get('network_evidence') or {}).get('source') == 'clinic_admin_attestation':
        return
    if source == 'clinic_contract_inventory':
        return  # Keep the certified scope and exclusion passage already attached.
    if source not in {'payer_participation_message', 'clinic_nonparticipation_rule',
                      'clinic_no_blue_contract', 'clinic_no_insurer_contract'}:
        return
    result['network_evidence'] = {
        'decision': ('out_of_network_confirmed_by_payer' if source == 'payer_participation_message'
                     else 'out_of_network_by_clinic_contract_record' if source in {'clinic_no_blue_contract', 'clinic_no_insurer_contract'}
                     else 'out_of_network_confirmed_by_scoped_nonparticipation_record'),
        'source': source, 'evidence_ids': benefits.get('network_evidence_ids') or [],
        'reason': ('Product-specific payer evidence identifies this requesting provider as out of network.'
                   if source == 'payer_participation_message' else
                   'No current Blue contract is registered in the clinic Library for this visit date.'
                   if source == 'clinic_no_blue_contract' else
                   'No current contract for this insurer is registered in the NMCT Clinical Library for this visit date.'
                   if source == 'clinic_no_insurer_contract' else
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
    benefits = result.get('benefits') or {}
    context = case.get('previsit_context') or {}
    source = ((case.get('previsit_discovery') or {}).get('pricing_source') or context.get('source') or {})
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    identity = result.get('identity') or benefits.get('identity') or {}
    date = str((context.get('inputs') or {}).get('service_date') or '')
    exact = {**source, 'payer_plan_bound': patient_plan_applies(benefits, identity, date)}
    names = [plan['identified_product']] if plan.get('identified_product') else plan.get('names') or []
    payer_name = str((payer or {}).get('display_name') or result.get('payer') or '')
    nmct_document = (network_participation.nmct_library_contract(
        staff_store.list_library(practice_id), payer_name, date)
        if network_participation.is_nmct_practice(practice_id) and payer_name else None)
    attestation = scoped_admin_nonparticipation(practice_id, payer, context.get('inputs') or {}, exact,
                                                 names, str(plan.get('group_number') or ''))
    if apply_admin_nonparticipation(result, benefits, attestation):
        if not nmct_document:
            return
        _clear_saved_tier(result, 'NMCT has a current insurer contract. Recalculate the saved out-of-network amount.')
    if benefits.get('network_source') == 'clinic_nonparticipation_rule':
        benefits.update(network_status='unknown', network_source=None)
    if benefits.get('network_source') == 'clinic_nmct_policy':
        _clear_saved_tier(result, 'NMCT now requires an applicable Clinical Library contract. Rerun this visit before collecting an insured amount.')
    if network_participation.is_nmct_practice(practice_id):
        if not nmct_document and benefits.get('network_source') == 'clinic_library_contract':
            _clear_saved_tier(result, 'The NMCT insurer contract is no longer current in the Clinical Library. Rerun before collecting.')
        if nmct_document:
            if benefits.get('network_status') == 'out_of_network' and benefits.get('network_source') != 'payer_participation_message':
                _clear_saved_tier(result, 'NMCT has a current insurer contract. Recalculate the saved out-of-network amount.')
            if apply_nmct_library_contract(result, benefits, nmct_document):
                return
        if (not nmct_document and payer_name
                and exact['payer_plan_bound'] and benefits.get('active') is True
                and benefits.get('service_date_matches') is not False
                and benefits.get('network_source') != 'payer_participation_message'
                and (benefits.get('network_source') == 'clinic_no_insurer_contract'
                     or not network_participation.verified_out_of_network(benefits))
                and not network_participation.participation_conflict(benefits)):
            if (benefits.get('network_source') != 'clinic_no_insurer_contract'
                    and ((result.get('price_range') or {}).get('complete')
                         or (result.get('price_range') or {}).get('patient_min_cents') is not None
                         or (result.get('price_range') or {}).get('patient_max_cents') is not None
                         or result.get('arrival_collection'))):
                _clear_saved_tier(result, 'No current insurer contract is registered for NMCT. Recalculate this visit before collecting an insured amount.')
            apply_nmct_contract_absence(result, benefits, payer_name, nmct_document, date,
                                        exact['payer_plan_bound'])
            return
    recheck_saved_inventory(result, case, practice_id, payer)
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
