"""Derive advance collection from visit evidence and approved clinic policy."""
from __future__ import annotations

from app.insurance.collection.cash import build as cash_guidance
from app.insurance.collection.options import emg_option as _emg_option
from app.insurance.collection.requirements import summarize_requirements
from app.insurance.collection.refundable_advance import apply as apply_refundable_advance
from app.insurance.collection.office_copay import (cigna_oap_televisit_office_copay, diagnostic_day_copay_amount, office_copay_amount)
from app.insurance.collection.billing_note import apply_billing_note
from app.insurance.collection.benefit_gates import prepare_benefit_evidence
from app.insurance.collection.healthselect_zero import applies as healthselect_zero_applies
from app.insurance.collection import secondary_order
from app.insurance.identity import patient_benefits_bound
from app.insurance.benefit_review import (annual_oop_conflict_with_known_remaining,
                                          selected_service_blocker, selected_service_detail)
from app.insurance.network_participation import participation_conflict, verified_in_network, verified_out_of_network
from app.insurance import encounter_summary
from app.insurance.insurance_product import is_medicare_advantage
from app.insurance.published_plan_benefits import GEHA_HIGH_PSHB_MA_16615_2026
def emg_option(result: dict, rates: list[dict], rules: dict, payload: dict | None,
               service_date: str, review: dict) -> dict:
    """Compare EMG scenarios using the same collection rules as the visit."""
    return _emg_option(result, rates, rules, payload, service_date, review, build_collection=build)

def build(result: dict, review: dict | None = None) -> dict:
    benefits = result.get('benefits') or {}
    active_payer_benefits = benefits.get('active') is True and benefits.get('service_date_matches') is not False
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    collection_benefits, unconfirmed_zero_oop = prepare_benefit_evidence(benefits, lines)
    strict = result.get('arrival_collection') or {}
    planning = result.get('planning_estimate') or {}
    assumptions = list(result.get('pricing_assumptions') or [])
    assumptions.extend(note for line in lines for note in line.get('benefits', {}).get('benefit_assumptions', []))
    actions = []
    candidate_network = (result.get('mapping_candidate') or {}).get('network') if collection_benefits.get('network_status') == 'unknown' else None
    guidance = {'amount_cents': None, 'status': 'action_required', 'basis': 'missing_collection_inputs',
                'office_visit_copay_advance': None,
                'reason': 'The available benefits do not establish an advance collection amount.',
                'patient_message': 'Your payment amount is not yet calculated. Missing information does not mean your patient responsibility is zero.',
                'assumptions': assumptions, 'patient_actions': actions, 'services': [],
                'network': 'In network · clinic agreement' if benefits.get('network_source') == 'clinic_group_agreement' else
                           'In network' if verified_in_network(benefits) else
                           'Out of network' if verified_out_of_network(benefits) else
                           ('Network not established' if candidate_network or
                            (result.get('network_evidence') or {}).get('network_name') else 'Not reported'),
                'network_source': ('Signed Texas BlueCard agreement and scoped clinic provider-network record'
                                   if (result.get('network_evidence') or {}).get('product_network_source') == 'signed_bluecard_agreement' else
                                   'Clinic Blue contract policy and scoped provider-network record'
                                   if (result.get('network_evidence') or {}).get('product_network_source') == 'clinic_blue_contract_policy' else
                                   'Current signed clinic agreement; review member product and clinician tier'
                                   if (result.get('network_evidence') or {}).get('decision') == 'in_network_contract_supported' else
                                   'Payer statement about the requesting provider' if verified_in_network(benefits) and benefits.get('network_source') == 'payer_participation_message' else
                                   'Current Clinical Library contract' if verified_in_network(benefits) and benefits.get('network_source') == 'clinic_library_contract' else
                                   'Clinic-stated PHCS/MultiPlan participation; payer-specific provider tier not returned' if benefits.get('network_source') == 'clinic_attested_phcs_roster' else
                                   'Clinic-stated Cigna participation for this Allegiance-administered office visit; provider tier not independently confirmed by the administrator' if benefits.get('network_source') == 'clinic_attested_cigna_tpa' else
                                   'Legacy NMCT clinic policy; recalculate against current Clinical Library contracts' if benefits.get('network_source') == 'clinic_nmct_policy' else
                                   'No contract linked to this exact payer product and provider' if benefits.get('network_source') == 'clinic_library_absent' else
                                   'Payer and clinic provider participation evidence conflict' if participation_conflict(benefits) else
                                   'In-network label has no current product/provider participation source' if benefits.get('network_status') == 'in_network' else
                                   'Payer statement about the requesting provider' if verified_out_of_network(benefits) and benefits.get('network_source') == 'payer_participation_message' else
                                   'Certified clinic agreement inventory; payer participation not independently confirmed' if verified_out_of_network(benefits) and benefits.get('network_source') == 'clinic_contract_inventory' else
                                   'No current Blue contract registered in the clinic Library' if verified_out_of_network(benefits) and benefits.get('network_source') == 'clinic_no_blue_contract' else
                                   'No current insurer contract registered in the NMCT Clinical Library' if verified_out_of_network(benefits) and benefits.get('network_source') == 'clinic_no_insurer_contract' else
                                   'Clinic administrator attestation for this product and provider; payer participation not independently confirmed' if verified_out_of_network(benefits) and (result.get('network_evidence') or {}).get('source') == 'clinic_admin_attestation' else
                                   'Current scoped clinic nonparticipation rule' if verified_out_of_network(benefits) else
                                   'The plan returned out-of-network benefit terms without establishing this provider tier' if benefits.get('network_status') == 'out_of_network' else
                                   f'No exact product and rendering-provider link to the {candidate_network} agreement' if candidate_network else
                                   f'Participation for this product and rendering provider is not established by the {(result.get("network_evidence") or {}).get("network_name")} agreement' if (result.get('network_evidence') or {}).get('network_name') else
                                   'No provider participation result available'),
                'requirements': {}, 'requirement_sources': {}}
    if result.get('eligibility_timing', {}).get('status') == 'too_early':
        guidance.update(status='too_early', basis='appointment_window', reason=result['eligibility_timing']['message'],
            patient_message='Benefits will be checked closer to this appointment.', network='Check closer to visit',
            network_source='', requirements={kind: 'Check closer to visit' for kind in ('referral', 'authorization')})
        return guidance
    if result.get('payment_mode') == 'cash':
        return cash_guidance(result, review, guidance, price, lines)
    if guidance['network'] == 'Not reported' and lines and all(line.get('benefits', {}).get('network_independent') for line in lines):
        guidance['network'] = 'Same benefits in / out of network'
        guidance['network_source'] = 'Payer benefit schedule. Provider participation not reported.'
    adverse = summarize_requirements(result, lines, guidance, actions)
    identity = result.get('identity') or benefits.get('identity') or {}
    line_blocker = selected_service_blocker(benefits.get('network_status') or 'unknown', identity, lines)
    unresolved_order, same_policy_conflict, primary_secondary_copay, medicare_secondary = secondary_order.state(result)
    medical_names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names')
                     or (benefits.get('plan_details') or {}).get('names') or [])
    ma_ppo = is_medicare_advantage(medical_names)
    qmb_clearance = result.get('billing_protection') or {}
    member_bound = patient_benefits_bound(identity)
    qmb_protected = (member_bound and benefits.get('qmb_status') == 'protected' or
        member_bound and qmb_clearance.get('status') == 'qmb'
        and qmb_clearance.get('identity_binding') == identity.get('binding')
        and qmb_clearance.get('reference') and qmb_clearance.get('verified_at'))
    qmb_unresolved = ma_ppo and not (qmb_clearance.get('status') == 'not_qmb'
        and qmb_clearance.get('reference') and qmb_clearance.get('verified_at')
        and qmb_clearance.get('identity_binding') == identity.get('binding')
        and not qmb_protected)
    unapproved_services = result.get('service_selection_status') == 'preview'
    recommended = (result.get('service_plan') or {}).get('recommended_ids') or []
    proposed_ids = [line.get('id') for line in result.get('codes') or []]
    priced_ids = [line.get('id') for line in lines]
    suggested_plan = (unapproved_services and price.get('mode') == 'services' and bool(recommended)
        and len(set(recommended)) == len(recommended)
        and set(recommended) == set(proposed_ids) == set(priced_ids))
    # A common office copay can be independent of the eventual E/M level.
    preview_office_copay = (unapproved_services and price.get('mode') == 'alternatives'
        and benefits.get('network_source') != 'clinic_contract_inventory'
        and bool(lines) and all(str(line.get('code')) in
            {str(n) for n in (*range(99202, 99206), *range(99212, 99216))} for line in lines)
        and len({(line.get('benefits') or {}).get('copay_cents') for line in lines}) == 1
        and (lines[0].get('benefits') or {}).get('copay_cents') is not None)
    preview_zero_oop = (unapproved_services and price.get('mode') == 'alternatives'
        and price.get('complete') is True and not price.get('conditional') and bool(lines)
        and benefits.get('oop_remaining_cents') == 0
        and price.get('patient_min_cents') == price.get('patient_max_cents') == 0
        and all(line.get('units') and line.get('allowed_basis') == 'clinic_contract'
                and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                and type(line.get('allowed_cents')) is int and line['allowed_cents'] > 0
                and (line.get('calculation') or {}).get('patient_cents') == 0
                and (line.get('calculation') or {}).get('plan_cents') == line['allowed_cents']
                and (line.get('benefits') or {}).get('active') is True
                and (line.get('benefits') or {}).get('network_status') == 'in_network'
                and (line.get('benefits') or {}).get('oop_remaining_cents') == 0
                and not (line.get('benefits') or {}).get('separate_copay_limit')
                for line in lines))
    safe = (benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and (result.get('card_check') or {}).get('independent_ecw_check') is not True and result.get('refresh_requires_selection') is not True
            and (not unapproved_services or suggested_plan or preview_office_copay or preview_zero_oop)
            and not benefits.get('coverage_status_conflict')
            and not benefits.get('coverage_dates_invalid')
            and line_blocker is None
            and benefits.get('historical_only') is not True
            and (collection_benefits.get('network_status') != 'out_of_network' or verified_out_of_network(benefits))
            and not benefits.get('network_conflict')
            and (not benefits.get('has_secondary') and not unresolved_order or primary_secondary_copay)
            and not same_policy_conflict and not (primary_secondary_copay and qmb_protected)
            and patient_benefits_bound(identity) and not price.get('service_issues') and not adverse
            and not qmb_unresolved
            and not any(('evidence' in str(reason).lower() or 'date' in str(reason).lower())
                        and not annual_oop_conflict_with_known_remaining(str(reason), benefits)
                        for reason in benefits.get('review_reasons', [])))
    source_errors = [error for terms in [benefits, *(line.get('benefits') or {} for line in lines)]
                     for error in terms.get('source_errors', [])]
    for label, values in [('arrival_collection', strict), ('price_range', price),
                          *((f'line:{line.get("id")}', line.get('calculation') or {}) for line in lines)]:
        for key in ('amount_cents', 'patient_min_cents', 'patient_max_cents', 'patient_cents'):
            value = values.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000):
                source_errors.append({'path': label + '.' + key, 'kind': 'invalid_calculation',
                                      'message': 'Invalid financial evidence: calculation returned an invalid amount.'})
    safe = safe and not source_errors
    amount = None
    cigna_oap_televisit = False
    if safe and preview_zero_oop and verified_in_network(benefits) and not primary_secondary_copay:
        amount = 0
        guidance.update(status='estimated', basis='out_of_pocket_limit',
            reason='Every priced alternative has a $0 patient share because the payer reports $0 remaining on the applicable individual in-network out-of-pocket limit. Select the performed CPT before billing; no advance payment is needed for these covered services.')
    if amount is None and safe and lines and not primary_secondary_copay:
        amount = cigna_oap_televisit_office_copay(result, collection_benefits, price, lines, guidance, assumptions)
        cigna_oap_televisit = amount is not None
    if (amount is None and safe and lines and price.get('mode') != 'options'
            and (not primary_secondary_copay or
                 (price.get('mode') == 'services' and len(lines) == 1
                  and verified_in_network(benefits)))):
        amount = office_copay_amount(result, collection_benefits, price, lines, guidance, assumptions)
        if amount is not None and primary_secondary_copay and guidance['basis'] == 'office_copay':
            guidance['primary_secondary_copay'] = True
            guidance['reason'] = ('Provisional copay from the verified primary office benefit. '
                                  'The secondary plan may change final responsibility after the claims process.')
            assumptions.append('Reconcile the primary and secondary claims and refund any excess collection.')
        elif primary_secondary_copay:
            amount = None
    if amount is None and safe and not primary_secondary_copay:
        amount = diagnostic_day_copay_amount(price, lines, guidance, assumptions)
    if amount is None and not primary_secondary_copay and (strict.get('amount_cents') is not None and safe and verified_in_network(benefits)
            and not price.get('conditional')):
        amount = strict['amount_cents']
        guidance.update(status='estimate', basis=strict.get('basis', 'patient_obligations'),
                        reason='Applicable copay, deductible and coinsurance for the anticipated services.' if strict.get('status') != 'copay_only' else
                               'Applicable office-visit copay. Other service shares are outside this advance and follow the processed claim and member protections.')
    elif amount is None and safe and not primary_secondary_copay and verified_in_network(benefits) and planning.get('basis') == 'out_of_pocket_limit':
        amount = 0
        assumptions.extend(planning.get('assumptions') or [])
        guidance.update(status='estimated', basis='out_of_pocket_limit',
                        reason='The payer reports $0 remaining on the applicable in-network out-of-pocket limit for the selected covered services. No advance payment is needed.')
    elif (amount is None and safe and not primary_secondary_copay and (verified_in_network(benefits) or benefits.get('network_status') == 'out_of_network')
          and price.get('complete') and not price.get('conditional') and price.get('mode') != 'options'
          and (not unapproved_services or suggested_plan)):
        amount = price.get('patient_min_cents')
        cms_based = any(line.get('allowed_basis') == 'cms_original_medicare_estimate' for line in lines)
        guidance.update(status='estimated', basis='anticipated_services',
                        reason=('Estimated patient share using the CMS Original Medicare benchmark, not a payer-confirmed allowed amount. Apply Medicare Advantage member billing protections.'
                                if cms_based else
                                'Estimated patient share of the payer out-of-network allowed amount. Apply Medicare Advantage member billing protections.'
                                if ma_ppo and benefits.get('network_status') == 'out_of_network' else
                                'Estimated patient share of the payer out-of-network allowed amount. Possible additional doctor charges remain unknown.'
                                if benefits.get('network_status') == 'out_of_network' else
                                'Estimated copay, deductible and coinsurance using the applicable visit benefits and clinic rates.'))
        if price.get('patient_min_cents') != price.get('patient_max_cents'):
            assumptions.append('Collect the lowest estimated office-visit amount now. Final complexity and any remaining responsibility follow the claim.')
        if benefits.get('network_status') == 'out_of_network' and not all(
                line.get('allowed_basis') in {'payer_out_of_network', 'cms_original_medicare_estimate'} for line in lines):
            amount = None
    amount, note = apply_billing_note(result, collection_benefits, price, lines, guidance, assumptions, amount, identity, source_errors, adverse)
    if (ma_ppo and qmb_protected and active_payer_benefits and not benefits.get('has_secondary')
            and not benefits.get('network_conflict') and not source_errors):
        amount = 0
        guidance.update(status='zero_advance', basis='qmb_protection',
            reason='Confirmed member-bound QMB protection prohibits collecting Medicare cost sharing at check-in.')
    # The published zero office benefit still needs selected-service and patient/network binding.
    geha_zero_office = (
        benefits.get('published_plan_rule') == GEHA_HIGH_PSHB_MA_16615_2026
        and active_payer_benefits and verified_in_network(benefits)
        and member_bound and not benefits.get('has_secondary')
        and not benefits.get('network_conflict') and not benefits.get('coverage_status_conflict')
        and not benefits.get('coverage_dates_invalid') and not benefits.get('historical_only')
        and not unresolved_order and not same_policy_conflict and not line_blocker
        and not source_errors and not adverse and not note
        and result.get('refresh_requires_selection') is not True
        and price.get('mode') == 'services' and not price.get('conditional')
        and not price.get('service_issues') and len(lines) == 1
        and len(result.get('selected_ids') or []) == 1
        and lines[0].get('id') == result['selected_ids'][0]
        and lines[0].get('code') in {'99202', '99203', '99204', '99205',
                                     '99212', '99213', '99214', '99215'}
        and lines[0].get('units') == 1
        and (lines[0].get('benefits') or {}).get('active') is True
        and (lines[0].get('benefits') or {}).get('copay_cents') == 0
        and (lines[0].get('benefits') or {}).get('coinsurance_percent') in {'0', '0.0', '0.00'}
        and (lines[0].get('benefits') or {}).get('deductible_applies') is not True
    )
    if geha_zero_office and not qmb_protected:
        amount = 0
        guidance.update(status='zero_advance', basis='published_geha_pshb_zero_office',
            reason='The matched 2026 G.E.H.A High PSHB Medicare Advantage plan publishes a $0 in-network specialist office visit, $0 medical deductible and $0 in-network medical out-of-pocket maximum. No payment is due for this planned covered office visit; final coding and claim adjudication still control.')
    healthselect_zero_televisit = healthselect_zero_applies(
        result, member_bound=member_bound, source_errors=source_errors,
        adverse=adverse, note=note, line_blocker=line_blocker,
        same_policy_conflict=same_policy_conflict)
    if healthselect_zero_televisit:
        amount = 0
        guidance.update(status='zero_advance', basis='healthselect_ma_zero_televisit',
            reason='The matched HealthSelect Medicare Advantage PPO plan reports a $0 virtual or specialist visit copay and 0% coinsurance, and ERS publishes no annual medical deductible. Collect $0 upfront for this one proposed covered telehealth E/M visit; code selection, payer order and final claim liability remain open.')
    policy = result.get('collection_policy') if isinstance(result.get('collection_policy'), dict) else {}
    policy_approved = bool(policy.get('approved_by') and policy.get('approved_at') and policy.get('reference'))
    policy_mode = policy.get('mode') if policy_approved else None
    fee_proxy = result.get('fee_proxy_estimate') or {}
    if (amount is None and safe and not primary_secondary_copay and not qmb_protected and not qmb_unresolved
            and fee_proxy.get('basis') == 'bcbstx_cigna_fee_proxy'
            and type(fee_proxy.get('patient_share_cents')) is int
            and 0 <= fee_proxy['patient_share_cents'] <= 100_000_000
            and verified_in_network(benefits)):
        amount = None
        guidance.update(cigna_estimate_required=True,
            reason='Cigna requires an Explanation of Estimate before collecting this estimated visit share; the BCBS Texas fee proxy is only a planning reference.')
        if fee_proxy.get('message'):
            assumptions.append(fee_proxy['message'])
    if (amount is None and safe and not primary_secondary_copay and not qmb_protected and not qmb_unresolved
            and fee_proxy.get('basis') == 'bcbstx_fee_proxy'
            and type(fee_proxy.get('patient_share_cents')) is int
            and policy_mode == 'collect_supported_advance'
            and policy.get('allow_supported_oon_advance') is True
            and policy.get('allow_blue_cross_proxy_advance') is True
            and verified_out_of_network(benefits)):
        amount = fee_proxy['patient_share_cents']
        guidance.update(status='estimated', basis='bcbstx_proxy_advance',
            reason='Provisional advance from the clinic-approved BCBS Texas fee proxy and the member diagnostic benefit. The UHC allowed amount is unknown.')
        assumptions.append('The advance uses clinic BCBS Texas fee references, not a payer-confirmed UHC allowance. Final responsibility follows the processed claim.')
    if (amount is None and safe and not primary_secondary_copay and not qmb_protected and not qmb_unresolved
            and fee_proxy.get('basis') == 'bcbstx_uhc_in_network_fee_proxy'
            and type(fee_proxy.get('patient_share_cents')) is int
            and 0 <= fee_proxy['patient_share_cents'] <= 100_000_000
            and policy_mode == 'collect_supported_advance'
            and policy.get('allow_blue_cross_proxy_advance') is True
            and verified_in_network(benefits)):
        amount = fee_proxy['patient_share_cents']
        guidance.update(status='estimated', basis='bcbstx_proxy_advance',
            reason='Provisional in-network advance from the clinic-approved BCBS Texas fee proxy and member diagnostic benefits. The UHC allowed amount is unknown.')
        assumptions.append('The advance uses clinic BCBS Texas fee references, not a payer-confirmed UHC allowance. Final responsibility follows the processed claim.')
    amount = secondary_order.allowed_advance(amount, medicare_secondary)
    oon_advance = (not verified_out_of_network(benefits) or
        policy.get('allow_supported_oon_advance') is True and
        (not any(line.get('allowed_basis') == 'cms_original_medicare_estimate' for line in lines)
         or policy.get('allow_cms_based_advance') is True))
    fixed_copay = guidance['basis'] == 'office_copay' or strict.get('status') == 'copay_only'
    published_copay = any((line.get('benefits') or {}).get('published_plan_rule') for line in lines)
    clinic_delegated_copay = (not policy_approved and guidance['basis'] == 'office_copay'
        and result.get('nmct_cigna_delegated_office') is True
        and verified_in_network(benefits)
        and result.get('benefit_source') == 'Stedi · plan administrator')
    policy_allows_advance = clinic_delegated_copay or (cigna_oap_televisit and not policy_approved) or (guidance['basis'] == 'cigna_refundable_office_copay' and (not policy_approved or policy_mode in {'collect_verified_copay', 'collect_supported_advance'})) or (policy_mode == 'collect_verified_copay' if published_copay else
        policy_mode == 'collect_supported_advance' or
        fixed_copay and policy_mode == 'collect_verified_copay' or
        verified_out_of_network(benefits) and policy_mode == 'defer_to_claim'
        and policy.get('allow_supported_oon_advance') is True)
    # A fully priced in-network visit may advance without route policy; explicit policies still control.
    calculated_in_network_advance = (
        not policy_approved and not published_copay and verified_in_network(benefits)
        and guidance['basis'] in {'patient_obligations', 'anticipated_services'}
        and price.get('complete') is True and not price.get('conditional')
        and price.get('mode') == 'services' and bool(lines)
        and price.get('patient_min_cents') == price.get('patient_max_cents')
        and all(line.get('rate_resolution', {}).get('status') == 'RESOLVED'
                and line.get('allowed_basis') == 'clinic_contract'
                and line.get('allowed_cents') is not None for line in lines)
    )
    if (amount is not None and amount > 0 and guidance['basis'] != 'ecw_billing_note'
            and not (oon_advance and (policy_allows_advance or calculated_in_network_advance))):
        if guidance['basis'] == 'diagnostic_day_copay' and not policy_approved and not unconfirmed_zero_oop:
            guidance['diagnostic_copay_reference_cents'] = amount
        amount = None
        guidance['published_copay_policy_missing' if published_copay else 'positive_advance_policy_missing'] = True
    if (amount is not None and amount > 0 and 'cigna' in str(result.get('payer') or '').casefold()
            and guidance['basis'] not in {'ecw_billing_note', 'office_copay', 'cigna_refundable_office_copay'}):
        amount = None
        guidance.update(cigna_estimate_required=True,
            reason='Cigna requires an Explanation of Estimate from its cost estimator before collecting an estimated deductible or coinsurance advance.')
    if amount is None:
        amount = apply_refundable_advance(result, lines, guidance, assumptions,
            safe and not note and not primary_secondary_copay and not qmb_protected and not qmb_unresolved
            and not verified_out_of_network(benefits)
            and (not policy_approved or policy_mode == 'collect_supported_advance'))
    if amount is not None and amount > 0 and unconfirmed_zero_oop and verified_in_network(benefits):
        amount = None
        guidance['unconfirmed_zero_oop'] = True
    if amount is not None and amount > 0 and calculated_in_network_advance:
        guidance['collection_rule'] = {'mode': 'calculated_in_network_advance',
                                       'reference': 'Selected CPTs, active clinic contract rates and member benefits'}
    if amount is not None and amount > 0 and policy_approved and guidance['basis'] not in {'ecw_billing_note', 'cigna_refundable_office_copay'}:
        guidance['collection_rule'] = {'mode': policy_mode, 'reference': policy['reference'],
                                       'approved_at': policy['approved_at']}
    elif amount is not None and amount > 0 and clinic_delegated_copay:
        guidance['collection_rule'] = {'mode': 'clinic_attested_cigna_delegated_copay',
            'reference': 'NMCT Cigna participation and same-patient Allegiance specialist office benefit'}
    elif amount is not None and amount > 0 and cigna_oap_televisit:
        guidance['collection_rule'] = {'mode': 'cigna_oap_televisit_office_copay',
            'reference': 'Current member-bound Cigna specialist office benefit and virtual-care billing guidance'}
    failure_codes = set((result.get('eligibility_failure') or {}).get('codes') or [])
    if not active_payer_benefits and failure_codes & {'72', '73', '65', '67', '71', '75', '76'}:
        actions.append('Contact your insurer to confirm the member ID, policyholder name and date of birth. Bring the corrected insurance details.')
    if amount is not None and amount > 0 and (result.get('card_check') or {}).get('independent_ecw_check') is True: amount = None
    if amount is not None:
        payments = (review or {}).get('payments') or {}
        if (strict.get('amount_cents') is None or guidance['basis'] == 'ecw_billing_note') and payments.get('reference'):
            amount = max(0, amount - payments['amount_cents'])
        guidance['amount_cents'] = max(0, amount)
        if (guidance['basis'] == 'office_copay'
                and result.get('delegated_office_diagnosis_exception_pending') is True):
            assumptions.append('The standard specialist copay is provisional because eCW did not return a primary diagnosis; '
                               'an alcohol or mental-health diagnosis can change it.')
        if guidance.get('range_max_cents') is not None and payments.get('reference'):
            guidance['range_min_cents'] = guidance['amount_cents']
            guidance['range_max_cents'] = max(0, guidance['range_max_cents'] - payments['amount_cents'])
        guidance['patient_message'] = ('This is a refundable advance based on the Cigna office copay. The clinic will apply it to the claim and refund any excess after processing.'
            if guidance['basis'] == 'cigna_refundable_office_copay' else
            'This is a refundable advance based on a Blue Cross Texas fee reference, not your Cigna benefit or final bill. The clinic will apply it to the claim and refund any excess after processing.'
            if guidance['basis'] == 'bcbstx_refundable_advance' else
            'This is an advance estimate for the planned care. The insurer determines final responsibility when it processes the claim.')
        if guidance['basis'] == 'claim_after_service' and 'cigna' in str(result.get('payer') or '').casefold():
            guidance['patient_message'] = 'No payment is requested at arrival without a Cigna Explanation of Estimate. Final responsibility follows the processed claim.'
        if guidance['basis'] == 'healthselect_ma_zero_televisit':
            guidance['patient_message'] = 'No payment is requested at arrival for the proposed covered virtual visit. Final coding, payer order and the processed claim determine any later responsibility.'
        if amount > 0 and not payments.get('reference') and strict.get('payments_received_cents') is None:
            assumptions.append('No prior payments or credits were deducted because the clinic ledger is unavailable. Tell us if you already paid for this visit and show your receipt to avoid paying twice.')
        elif payments.get('reference') or strict.get('payments_received_cents') is not None:
            guidance['reason'] += ' Recorded payments have been deducted.'
    else:
        guidance.update(status='action_required', basis='missing_collection_inputs')
        if guidance.get('published_copay_policy_missing') and not medicare_secondary:
            guidance['reason'] = 'The published plan specialist copay is supported, but this payer route has no administrator-approved fixed-copay collection policy.'
        elif guidance.get('positive_advance_policy_missing') and not medicare_secondary:
            guidance['reason'] = 'The patient share can be estimated, but this payer route has no approved policy for this check-in advance.'
        elif source_errors:
            guidance['reason'] = (
                'The protected saved payer response could not be reopened. Rerun the payer check before collecting.'
                if any(error.get('kind') == 'saved_raw_unavailable' for error in source_errors) else
                'Calculation/source error: invalid payer financial evidence was rejected. Clinic staff must verify the source before collecting.')
            guidance['source_errors'] = source_errors
        elif guidance.get('unconfirmed_zero_oop'):
            guidance['reason'] = ('The payer reports $0 remaining on an individual in-network out-of-pocket maximum, '
                                  'but its medical-plan applicability is unconfirmed. Confirm the member benefit '
                                  'with the payer before collecting the office copay.')
        elif benefits.get('active') is False:
            guidance['reason'] = 'The payer reports inactive coverage for this visit, so the insurance collection amount cannot be calculated from this policy.'
            actions.append('Contact your insurer to resolve inactive coverage or provide your current insurance card.')
        elif benefits.get('coverage_status_conflict'):
            guidance['reason'] = 'The payer returned conflicting active and inactive or noncovered status for the planned service.'
        elif benefits.get('coverage_dates_invalid'):
            guidance['reason'] = 'The payer returned a coverage date that cannot be interpreted for this visit.'
        elif same_policy_conflict:
            guidance['reason'] = 'The same medical policy returned conflicting subscriber, date or office benefit evidence. Reconcile the payer responses before collecting.'
        elif qmb_unresolved and price.get('complete'):
            guidance['reason'] = ('QMB protection prohibits collecting Medicare cost sharing from this member.'
                                  if qmb_protected else
                                  'Medicare Advantage QMB cost-sharing protection has not been ruled out for this member. Verify QMB status before any advance collection.')
        elif line_blocker:
            cause, action = selected_service_detail(line_blocker)
            guidance['reason'] = f'{cause[:1].upper()}{cause[1:]}. {action[:1].upper()}{action[1:]}.'
        elif identity.get('status') == 'review':
            guidance['reason'] = 'The returned policy does not match the patient details reliably, so its benefits cannot establish the collection amount for this patient.'
            actions.append('Contact your insurer to correct your member details and bring the updated card.')
        elif benefits.get('active') is True and not patient_benefits_bound(identity):
            guidance['reason'] = 'The saved payer response has no retained patient identity match for this visit.'
            actions.append('Compare the saved payer patient, subscriber and member ID with eCW before using these benefit balances.')
        elif adverse:
            guidance['reason'] = 'A referral or authorization issue affects the selected care. The collection amount depends on resolving that coverage requirement.'
        elif medicare_secondary: guidance['reason'] = secondary_order.action(True)
        elif price.get('conditional'):
            guidance['reason'] = 'The visit estimate depends on unresolved coverage or approval conditions. Verify them before collecting.'
        elif primary_secondary_copay:
            guidance['reason'] = 'The primary and secondary payer order is verified, but a supported office copay or approved collection policy is still needed.'
        elif benefits.get('has_secondary') or unresolved_order:
            guidance['reason'] = secondary_order.action(medicare_secondary)
            if not active_payer_benefits and (result.get('eligibility_failure') or {}).get('kind') == 'provider_enrollment':
                guidance['reason'] += ' A current payer check also requires clinic provider enrollment.'
        elif not active_payer_benefits and (result.get('eligibility_failure') or {}).get('kind') == 'provider_enrollment':
            failure = result['eligibility_failure']
            if failure.get('reason_code') == 'enrollment_lookup_failed':
                guidance['reason'] = 'Stedi could not read the clinic provider’s eligibility enrollment status. No paid eligibility request was submitted.'
                actions.append('Clinic administrator: retry the Stedi enrollment lookup for this payer and provider NPI.')
            else:
                guidance['reason'] = ((failure.get('detail') or
                    'Stedi did not return a live eligibility enrollment for this payer and provider NPI.').rstrip('.')
                    + '. No paid eligibility request was submitted.')
                actions.append('Clinic administrator: review the recorded Stedi enrollment status for this payer and provider NPI before rerunning eligibility.')
        elif ((result.get('recovery') or {}).get('outcome') == 'review_needed'
              and result.get('eligibility_error') != 'payer_routing_unresolved'
              and not (active_payer_benefits and member_bound)):
            guidance['reason'] = 'Stedi found possible coverage that needs a policy selection and a completed eligibility check.'
        elif result.get('eligibility_failure') or benefits.get('active') is not True:
            failure = result.get('eligibility_failure') or {}
            guidance['reason'] = failure.get('detail') or next((issue for issue in result.get('issues', []) if issue), None) or 'Current payer benefits are unavailable, and the remaining information does not establish a collection amount.'
        elif benefits.get('service_date_matches') is False:
            guidance['reason'] = 'The payer response covers a different date. Benefits for this appointment are needed to calculate its collection amount.'
        elif unapproved_services and not (suggested_plan or preview_office_copay):
            guidance['reason'] = 'No single supported CPT scenario is available to price this visit.'
        elif price.get('service_issues'):
            guidance['reason'] = (str(price['service_issues'][0]) if result.get('botox_review') is not None else
                'The selected services do not form a complete billable plan. A collection amount cannot be calculated for this selection.')
        elif any('remaining individual deductible' in reason.lower() or 'remaining individual out-of-pocket' in reason.lower()
                 for reason in benefits.get('review_reasons', [])):
            guidance['reason'] = 'The payer did not return the individual deductible or out-of-pocket balance needed for these services. Check the member-specific benefits or a payer cost estimate.'
        else:
            guidance['reason'] = 'The returned benefits and clinic rates do not establish a collection amount for the selected services.'
        guidance.update(amount_cents=None, status='action_required', basis='missing_collection_inputs',
            patient_message='The insurance estimate is not available yet. Coverage, applicable benefits and clinic prices are needed before an insured amount can be quoted.')
        policy = result.get('collection_policy')
        policy = policy if isinstance(policy, dict) else {}
        if (policy.get('mode') == 'defer_to_claim' and policy.get('approved_by')
                and policy.get('approved_at') and policy.get('reference')):
            guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
                patient_message=('Collect $0 today under the clinic deferral policy. Submit the insurance claim. '
                                 'bill a later patient share only if the processed claim and member protections permit it.'))
            guidance['collection_rule'] = {'mode': policy['mode'], 'reference': policy['reference'],
                                           'approved_at': policy['approved_at']}
            guidance['deferred_supported_share'] = encounter_summary.build(
                {**result, 'collection_guidance': guidance}, review)['complete']
        elif (safe and fee_proxy.get('scenario_kind') == 'oap_office_family_deductible'
              and fee_proxy.get('basis') == 'bcbstx_cigna_fee_proxy'
              and type(fee_proxy.get('patient_share_cents')) is int
              and verified_in_network(benefits) and not qmb_protected and not qmb_unresolved):
            guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
                reason='No fixed office copay was returned. Cigna requires an Explanation of Estimate before collecting an estimated deductible or coinsurance; defer collection and submit the claim.',
                patient_message='No payment is requested at arrival. The displayed BCBS Texas amount is a planning reference; final responsibility follows the processed Cigna claim.')
            guidance['collection_rule'] = {'mode': 'cigna_cost_estimate_deferral',
                'reference': 'Cigna Open Access Plus cost-of-care estimate guidance'}
        elif active_payer_benefits:
            guidance['policy_setup_required'] = True
        if guidance.get('cigna_estimate_required') and guidance['basis'] != 'claim_after_service':
            guidance.update(reason='The Cigna visit share needs its Explanation of Estimate before a check-in amount can be set.',
                patient_message='Your visit payment is pending a Cigna estimate; this does not mean you owe $0.')
    for line in lines:
        mapped = 'needs mapping' not in line.get('benefit_basis', '')
        terms = (line.get('benefits') or {}) if mapped else {}
        guidance['services'].append({'id': line.get('id'), 'code': line.get('code'),
            'benefit_source': line.get('benefit_source') or result.get('benefit_source'),
            'benefit': (line.get('benefit_basis') or 'Applicable service benefits') if mapped else 'Benefit determined when claim is processed',
            'patient_cents': (line.get('calculation') or {}).get('patient_cents') if safe and amount is not None and guidance['basis'] != 'claim_after_service' else None,
            'oop_remaining_cents': terms.get('oop_remaining_cents'),
            'copay_cents': terms.get('copay_cents'), 'coinsurance_percent': terms.get('coinsurance_percent'),
            'deductible_remaining_cents': terms.get('deductible_remaining_cents'),
            'deductible_annual_cents': terms.get('deductible_annual_cents'),
            'deductible_applicability': terms.get('deductible_applicability', 'UNKNOWN'),
            'selected_benefit_ids': list(dict.fromkeys([*(terms.get('selected_benefit_ids') or []),
                *[terms[key] for key in ('deductible_id', 'oop_id') if terms.get(key)]]))})
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('copay_limit_ids') or [])
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('deductible_waiver_ids') or [])
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('deductible_applicability_ids') or [])
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('family_oop_ids') or [])
        if guidance['basis'] == 'out_of_pocket_limit':
            guidance['services'][-1]['patient_cents'] = 0
    if guidance['basis'] == 'ecw_billing_note':
        guidance['patient_message'] = ('No payment is requested at arrival under the recorded eCW billing instruction. Final responsibility follows claim processing.'
            if note['kind'] == 'no_collection' else 'This advance uses the office copay recorded by the clinic. Additional services and final responsibility follow claim processing.')
        for service in guidance['services']:
            service['patient_cents'] = None
    if guidance['amount_cents'] is not None and guidance['basis'] in {'patient_obligations', 'anticipated_services', 'bcbstx_proxy_advance', 'cigna_fee_proxy_advance', 'bcbstx_refundable_advance'}:
        error = encounter_summary.build({**result, 'collection_guidance': guidance}, review)['integrity_error']
        if error:
            reasons = {
                'patient_total_mismatch': 'The saved patient total differs from its selected CPT calculations',
                'line_amount_mismatch': 'A saved CPT patient share differs from its copay, deductible and coinsurance calculation',
                'collection_amount_mismatch': 'The saved collection differs from the selected CPT patient share after recorded payment',
            }
            guidance.update(amount_cents=None, status='action_required', basis='calculation_conflict',
                            integrity_error=error,
                            reason=f'{reasons[error]}. Rerun the insured estimate before collecting.',
                            patient_message='The clinic needs to recalculate the insurance advance for this visit.')
            for service in guidance['services']:
                service['patient_cents'] = None
    guidance['assumptions'] = list(dict.fromkeys(assumptions))
    if (guidance['basis'] == 'office_copay' and guidance['amount_cents'] is not None
            and len(lines) == len([line for line in lines if str(line.get('code') or '').isdigit()
                 and (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)])):
        guidance['office_visit_copay_advance'] = {'amount_cents': guidance['amount_cents'],
                                                    'source': 'selected_office_benefit'}
    return guidance
