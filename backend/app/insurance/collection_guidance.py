"""Derive advance collection from visit evidence; missing amounts stay unknown."""
from __future__ import annotations

from app.insurance.collection.cash import build as cash_guidance
from app.insurance.collection.options import emg_option as _emg_option
from app.insurance.collection.requirements import summarize_requirements
from app.insurance.collection.office_copay import office_copay_amount
from app.insurance.collection.billing_note import apply_billing_note


def emg_option(result: dict, rates: list[dict], rules: dict, payload: dict | None,
               service_date: str, review: dict) -> dict:
    """Compare EMG scenarios using the same collection rules as the visit."""
    return _emg_option(result, rates, rules, payload, service_date, review, build_collection=build)


def build(result: dict, review: dict | None = None) -> dict:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    strict = result.get('arrival_collection') or {}
    planning = result.get('planning_estimate') or {}
    assumptions = list(result.get('pricing_assumptions') or [])
    assumptions.extend(note for line in lines for note in line.get('benefits', {}).get('benefit_assumptions', []))
    actions = []
    candidate_network = (result.get('mapping_candidate') or {}).get('network') if benefits.get('network_status') == 'unknown' else None
    guidance = {'amount_cents': None, 'status': 'unavailable', 'basis': 'insufficient_evidence',
                'reason': 'The available benefits do not establish an advance collection amount.',
                'patient_message': 'Your payment amount is not yet calculated. Missing information does not mean your patient responsibility is zero.',
                'assumptions': assumptions, 'patient_actions': actions, 'services': [],
                'network': 'In network' if benefits.get('network_status') == 'in_network' else
                           'Out of network' if benefits.get('network_status') == 'out_of_network' else
                           ((result.get('network_evidence') or {}).get('network_name') or
                            (f'{candidate_network} contract; patient network unverified' if candidate_network else 'Not reported')),
                'network_source': ('Payer statement about the requesting provider' if benefits.get('network_source') == 'payer_participation_message' else
                                   'Current Clinical Library contract and clinic provider-network record' if benefits.get('network_source') == 'clinic_library_contract' else
                                   'No current matching Clinical Library contract for this visit' if benefits.get('network_source') == 'clinic_library_absent' else
                                   'Clinic provider-network record' if benefits.get('network_status') in {'in_network', 'out_of_network'} else
                                   'Clinic provider-network record; exact product match not confirmed' if candidate_network else
                                   'Clinic contract; exact product match not confirmed' if (result.get('network_evidence') or {}).get('network_name') else
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
        guidance['network_source'] = 'Payer benefit schedule; provider participation not reported'
    adverse = summarize_requirements(result, lines, guidance, actions)
    identity = result.get('identity') or benefits.get('identity') or {}
    safe = (benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and (benefits.get('network_status') != 'out_of_network' or benefits.get('network_source') == 'payer_participation_message')
            and not benefits.get('has_secondary')
            and identity.get('status') != 'review' and not price.get('service_issues') and not adverse
            and not any('evidence' in str(reason).lower() or 'date' in str(reason).lower()
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
    if strict.get('amount_cents') is not None and safe and not price.get('conditional'):
        amount = strict['amount_cents']
        guidance.update(status='estimate', basis=strict.get('basis', 'patient_obligations'),
                        reason='Applicable copay, deductible and coinsurance for the anticipated services.' if strict.get('status') != 'copay_only' else
                               'Applicable office-visit copay. Additional service costs will be billed after the claim.')
    elif safe and planning.get('basis') == 'out_of_pocket_limit':
        amount = 0
        assumptions.extend(planning.get('assumptions') or [])
        guidance.update(status='estimated', basis='out_of_pocket_limit',
                        reason='The payer reports $0 remaining on the applicable in-network out-of-pocket limit for the selected covered services. No advance payment is needed.')
    elif safe and price.get('complete') and not price.get('conditional') and price.get('mode') != 'options':
        amount = price.get('patient_min_cents')
        guidance.update(status='estimated', basis='anticipated_services',
                        reason=('Estimated patient share of the payer out-of-network allowed amount; possible additional doctor charges remain unknown.'
                                if benefits.get('network_status') == 'out_of_network' else
                                'Estimated copay, deductible and coinsurance using the applicable visit benefits and clinic rates.'))
        if price.get('patient_min_cents') != price.get('patient_max_cents'):
            assumptions.append('Collect the lowest estimated office-visit amount now. Final complexity and any remaining responsibility follow the claim.')
        if benefits.get('network_status') == 'out_of_network' and not all(
                line.get('allowed_basis') == 'payer_out_of_network' for line in lines):
            amount = None
        elif benefits.get('network_status') == 'unknown' and not result.get('pricing_assumptions'):
            amount = None
    elif safe and lines and price.get('mode') != 'options':
        amount = office_copay_amount(result, benefits, price, lines, guidance, assumptions)
    amount, note = apply_billing_note(result, benefits, price, lines, guidance, assumptions, amount, identity, source_errors, adverse)
    failure_codes = set((result.get('eligibility_failure') or {}).get('codes') or [])
    if failure_codes & {'72', '73', '65', '67', '71', '75', '76'}:
        actions.append('Contact your insurer to confirm the member ID, policyholder name and date of birth; bring the corrected insurance details.')
    if amount is not None:
        payments = (review or {}).get('payments') or {}
        if (strict.get('amount_cents') is None or guidance['basis'] == 'ecw_billing_note') and payments.get('reference'):
            amount = max(0, amount - payments['amount_cents'])
        guidance['amount_cents'] = max(0, amount)
        if guidance.get('range_max_cents') is not None and payments.get('reference'):
            guidance['range_min_cents'] = guidance['amount_cents']
            guidance['range_max_cents'] = max(0, guidance['range_max_cents'] - payments['amount_cents'])
        guidance['patient_message'] = 'This is an advance estimate for the planned care. The insurer determines final responsibility when it processes the claim.'
        if amount > 0 and not payments.get('reference') and strict.get('payments_received_cents') is None:
            assumptions.append('No prior payments or credits were deducted because the clinic ledger is unavailable. Tell us if you already paid for this visit and show your receipt to avoid paying twice.')
        elif payments.get('reference') or strict.get('payments_received_cents') is not None:
            guidance['reason'] += ' Recorded payments have been deducted.'
    else:
        guidance.update(status='unavailable', basis='insufficient_evidence')
        if source_errors:
            guidance['reason'] = 'Calculation/source error: invalid payer financial evidence was rejected. Clinic staff must verify the source before collecting.'
            guidance['source_errors'] = source_errors
        elif benefits.get('active') is False:
            guidance['reason'] = 'The payer reports inactive coverage for this visit, so the insurance collection amount cannot be calculated from this policy.'
            actions.append('Contact your insurer to resolve inactive coverage or provide your current insurance card.')
        elif identity.get('status') == 'review':
            guidance['reason'] = 'The returned policy does not match the patient details reliably, so its benefits cannot establish the collection amount for this patient.'
            actions.append('Contact your insurer to correct your member details and bring the updated card.')
        elif adverse:
            guidance['reason'] = 'A referral or authorization issue affects the selected care. The collection amount depends on resolving that coverage requirement.'
        elif price.get('conditional'):
            guidance['reason'] = 'The visit estimate depends on unresolved coverage or approval conditions. Verify them before collecting.'
        elif benefits.get('has_secondary'):
            guidance['reason'] = 'More than one plan may pay for this visit. The collection amount depends on how those plans coordinate benefits.'
            if (result.get('eligibility_failure') or {}).get('kind') == 'provider_enrollment':
                guidance['reason'] += ' A current payer check also requires clinic provider enrollment.'
        elif (result.get('eligibility_failure') or {}).get('kind') == 'provider_enrollment':
            guidance['reason'] = 'The clinic provider’s eligibility enrollment with this payer is not verified in Stedi. No paid eligibility request was submitted.'
            actions.append('Clinic administrator: verify or complete provider eligibility enrollment in Stedi to retrieve these benefits.')
        elif (result.get('recovery') or {}).get('outcome') == 'review_needed' and result.get('eligibility_error') != 'payer_routing_unresolved':
            guidance['reason'] = 'Stedi found possible coverage that needs a policy selection and a completed eligibility check.'
        elif result.get('eligibility_failure') or benefits.get('active') is not True:
            failure = result.get('eligibility_failure') or {}
            guidance['reason'] = failure.get('detail') or next((issue for issue in result.get('issues', []) if issue), None) or 'Current payer benefits are unavailable, and the remaining information does not establish a collection amount.'
        elif benefits.get('service_date_matches') is False:
            guidance['reason'] = 'The payer response covers a different date. Benefits for this appointment are needed to calculate its collection amount.'
        elif price.get('service_issues'):
            guidance['reason'] = 'The selected services do not form a complete billable plan. A collection amount cannot be calculated for this selection.'
        elif any('remaining individual deductible' in reason.lower() or 'remaining individual out-of-pocket' in reason.lower()
                 for reason in benefits.get('review_reasons', [])):
            guidance['reason'] = 'The payer did not return the individual deductible or out-of-pocket balance needed for these services. Check the member-specific benefits or a payer cost estimate.'
        else:
            guidance['reason'] = 'The returned benefits and clinic rates do not establish a collection amount for the selected services.'
        guidance.update(amount_cents=None, status='deferred_billing', basis='deferred_billing',
            patient_message='The insurance estimate is not available yet. Coverage, applicable benefits and clinic prices are needed before an insured amount can be quoted.')
    for line in lines:
        mapped = 'needs mapping' not in line.get('benefit_basis', '')
        terms = (line.get('benefits') or {}) if mapped else {}
        guidance['services'].append({'id': line.get('id'), 'code': line.get('code'),
            'benefit_source': line.get('benefit_source') or result.get('benefit_source'),
            'benefit': (line.get('benefit_basis') or 'Applicable service benefits') if mapped else 'Benefit determined when claim is processed',
            'patient_cents': (line.get('calculation') or {}).get('patient_cents') if safe and amount is not None else None,
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
    guidance['assumptions'] = list(dict.fromkeys(assumptions))
    return guidance
