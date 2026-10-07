"""Derive advance collection from visit evidence; missing amounts stay unknown."""
from __future__ import annotations



def build(result: dict, review: dict | None = None) -> dict:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    strict = result.get('arrival_collection') or {}
    planning = result.get('planning_estimate') or {}
    assumptions = list(result.get('pricing_assumptions') or [])
    assumptions.extend(note for line in lines for note in line.get('benefits', {}).get('benefit_assumptions', []))
    actions = []
    guidance = {'amount_cents': None, 'status': 'unavailable', 'basis': 'insufficient_evidence',
                'reason': 'The available benefits do not establish an advance collection amount.',
                'patient_message': 'Your payment amount is not yet calculated. Missing information does not mean your patient responsibility is zero.',
                'assumptions': assumptions, 'patient_actions': actions, 'services': [],
                'network': 'In network' if benefits.get('network_status') == 'in_network' else
                           'Out of network' if benefits.get('network_status') == 'out_of_network' else
                           (result.get('network_evidence') or {}).get('network_name') or 'Not reported',
                'network_source': ('Payer statement about the requesting provider' if benefits.get('network_source') == 'payer_participation_message' else
                                   'Clinic provider-network record' if benefits.get('network_status') in {'in_network', 'out_of_network'} else
                                   'Clinic contract; exact product match not confirmed' if (result.get('network_evidence') or {}).get('network_name') else
                                   'No provider participation result available'),
                'requirements': {}, 'requirement_sources': {}}
    if result.get('eligibility_timing', {}).get('status') == 'too_early':
        guidance.update(status='too_early', basis='appointment_window', reason=result['eligibility_timing']['message'],
            patient_message='Benefits will be checked closer to this appointment.', network='Check closer to visit',
            network_source='', requirements={kind: 'Check closer to visit' for kind in ('referral', 'authorization')})
        return guidance
    if guidance['network'] == 'Not reported' and lines and all(line.get('benefits', {}).get('network_independent') for line in lines):
        guidance['network'] = 'Same benefits in / out of network'
        guidance['network_source'] = 'Payer benefit schedule; provider participation not reported'
    adverse = False
    for kind in ('referral', 'authorization'):
        requirements = [line.get('benefits', {}).get('requirements', {}).get(kind, {}) for line in lines]
        guidance['requirement_sources'][kind] = (result.get('benefit_source') or 'Stedi') + ' / clinic evidence'
        if any(item.get('status') in {'denied', 'expired', 'pending'} or
               item.get('required') == 'required' and not item.get('cleared') for item in requirements):
            adverse = True
            guidance['requirements'][kind] = 'Required · patient action below'
            actions.append('Ask your referring doctor to send the referral for this visit.' if kind == 'referral' else
                           'Contact your plan about the authorization requirement before the service; the treating office may need to submit clinical information.')
        elif requirements and all(item.get('cleared') for item in requirements):
            guidance['requirements'][kind] = 'On file' if any(item.get('required') == 'required' for item in requirements) else 'Not required'
            if kind == 'authorization' and any('absent indicator' in line.get('benefits', {}).get('authorization_basis', '') for line in lines):
                guidance['requirements'][kind] = 'No requirement indicated'
                guidance['requirement_sources'][kind] = 'Stedi: no authorization indicator'
        else:
            explicit_unknown = kind == 'authorization' and any(line.get('benefits', {}).get('authorization_basis') == 'Payer indicator' for line in lines)
            guidance['requirements'][kind] = 'Unknown in payer response' if explicit_unknown else 'No requirement reported'
            guidance['requirement_sources'][kind] = 'Stedi' if explicit_unknown else 'eCW + Stedi'
            if kind == 'referral' and result.get('referral_evidence'):
                guidance['requirements'][kind] = 'Referral record on file'
                guidance['requirement_sources'][kind] = 'eCW; applicability to this service not established'
    identity = result.get('identity') or benefits.get('identity') or {}
    safe = (benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and benefits.get('network_status') != 'out_of_network' and not benefits.get('has_secondary')
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
    if strict.get('amount_cents') is not None and safe:
        amount = strict['amount_cents']
        guidance.update(status='estimate', basis=strict.get('basis', 'patient_obligations'),
                        reason='Applicable copay, deductible and coinsurance for the anticipated services.' if strict.get('status') != 'copay_only' else
                               'Applicable office-visit copay. Additional service costs will be billed after the claim.')
    elif safe and planning.get('basis') == 'out_of_pocket_limit':
        amount = 0
        assumptions.extend(planning.get('assumptions') or [])
        guidance.update(status='estimated', basis='out_of_pocket_limit',
                        reason='The payer reports $0 remaining on the applicable in-network out-of-pocket limit for the selected covered services. No advance payment is needed.')
    elif safe and price.get('complete') and price.get('mode') != 'options':
        amount = price.get('patient_min_cents')
        guidance.update(status='estimated', basis='anticipated_services',
                        reason='Estimated copay, deductible and coinsurance using the applicable visit benefits and clinic rates.')
        if price.get('patient_min_cents') != price.get('patient_max_cents'):
            assumptions.append('Collect the lowest estimated office-visit amount now. Final complexity and any remaining responsibility follow the claim.')
        if benefits.get('network_status') != 'in_network' and not result.get('pricing_assumptions'):
            amount = None
    elif safe and lines and price.get('mode') != 'options':
        # A unique fixed office copay can stand alone without an allowed rate.
        office = [line for line in lines if line.get('code', '').isdigit() and
                  (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)]
        terms = [line.get('benefits') or {} for line in office]
        if (terms
                and (len(office) == 1 or price.get('mode') == 'alternatives')
                and all(line.get('units') == 1 for line in office)
                and all(t.get('active') is True and t.get('copay_cents') is not None
                        and t.get('copay_period') in {'', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'}
                        and (t.get('deductible_applies') is not True or t.get('deductible_remaining_cents') == 0)
                        and (not t.get('separate_copay_limit') or t.get('copay_remaining_cents') is not None)
                        and t.get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                        and t.get('cost_share') not in {'coinsurance', 'cumulative'}
                        and not any(any(word in r.lower() for word in ('conditional', 'limit', 'date', 'identity', 'evidence'))
                                    for r in t.get('review_reasons', [])) for t in terms)
                and len({t['copay_cents'] for t in terms}) == 1):
            cap = terms[0].get('copay_remaining_cents') if terms[0].get('separate_copay_limit') else terms[0].get('oop_remaining_cents')
            amount = min(terms[0]['copay_cents'], cap if cap is not None else terms[0]['copay_cents'])
            if all(line.get('allowed_cents') is not None for line in office):
                amount = min(amount, min(line['allowed_cents'] for line in office))
            if any(t.get('deductible_remaining_cents') != 0 and t.get('deductible_applies') is not False for t in terms):
                assumptions.append('The payer returned a fixed office copay but did not establish whether the deductible applies to it. This advance includes the copay only; additional deductible responsibility may follow the claim.')
            if any(t.get('separate_copay_limit') for t in terms):
                assumptions.append('The payer reports a separate remaining copay maximum. The office copay uses that balance, while the general out-of-pocket balance remains separate.')
            if any(not t.get('copay_period') for t in terms):
                assumptions.append('The payer did not specify a copay period. This advance applies one copay to the single office consultation.')
            if benefits.get('network_status') != 'in_network' and not result.get('pricing_assumptions') and not all(t.get('network_independent') for t in terms):
                assumptions.append('This provisional office advance uses the returned in-network specialist copay. Provider participation is unverified; confirm it before care. Final responsibility may differ.')
            guidance.update(status='estimated', basis='office_copay',
                            reason='The applicable office-visit copay is collected once. Any additional service responsibility will follow the claim.')
    if amount is None and safe:
        reference = cash_reference_estimate(result)
        if reference:
            amount = reference['amount_cents']
            assumptions.extend(reference['assumptions'])
            guidance.update(status='estimated', basis='clinic_price_reference', reason=reference['reason'],
                range_min_cents=reference['amount_cents'], range_max_cents=reference['max_cents'])
    note = result.get('billing_note') or {}
    if (note and identity.get('status') != 'review' and not source_errors
            and not price.get('service_issues') and result.get('payment_mode') != 'cash'):
        office_only = bool(lines) and all(line.get('code', '').isdigit() and
            (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)
            and line.get('units') == 1 for line in lines) and (len(lines) == 1 or price.get('mode') == 'alternatives')
        copay_matches = all((line.get('benefits') or {}).get('copay_cents') in {None, note.get('amount_cents')} for line in lines)
        if note['kind'] == 'no_collection':
            amount = 0
            guidance.update(status='no_advance', basis='ecw_billing_note',
                reason='The eCW billing instruction for this visit says not to collect from the patient.')
        elif (amount is None and note['kind'] == 'office_copay' and office_only and copay_matches
                and not adverse and benefits.get('active') is not False
                and benefits.get('service_date_matches') is not False and not benefits.get('has_secondary')
                and benefits.get('network_status') != 'out_of_network'
                and all((line.get('benefits') or {}).get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                    and (line.get('benefits') or {}).get('deductible_remaining_cents') in {None, 0}
                    and (line.get('benefits') or {}).get('active') is not False for line in lines)):
            amount = note['amount_cents']
            guidance.update(status='estimated', basis='ecw_billing_note',
                reason='Office-visit copay recorded in the eCW billing note; the note records active coverage and a met deductible.')
        if guidance['basis'] == 'ecw_billing_note':
            guidance.pop('range_min_cents', None)
            guidance.pop('range_max_cents', None)
            guidance['billing_note'] = note
            assumptions.append('Source: the visit-linked eCW billing note, not a completed payer eligibility check. Final responsibility follows claim processing.')
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
            patient_message='No insurance advance is collected today. The insurer determines final responsibility when the claim is processed; a later bill may be due.')
    for line in lines:
        mapped = 'needs mapping' not in line.get('benefit_basis', '')
        terms = (line.get('benefits') or {}) if mapped else {}
        guidance['services'].append({'id': line.get('id'), 'code': line.get('code'),
            'benefit': (line.get('benefit_basis') or 'Applicable service benefits') if mapped else 'Benefit determined when claim is processed',
            'patient_cents': (line.get('calculation') or {}).get('patient_cents') if safe and amount is not None else None,
            'oop_remaining_cents': terms.get('oop_remaining_cents'),
            'copay_cents': terms.get('copay_cents'), 'coinsurance_percent': terms.get('coinsurance_percent'),
            'deductible_remaining_cents': terms.get('deductible_remaining_cents'),
            'selected_benefit_ids': list(dict.fromkeys([*(terms.get('selected_benefit_ids') or []),
                *[terms[key] for key in ('deductible_id', 'oop_id') if terms.get(key)]]))})
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('copay_limit_ids') or [])
        guidance['services'][-1]['selected_benefit_ids'].extend(terms.get('deductible_waiver_ids') or [])
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


def emg_option(result: dict, rates: list[dict], rules: dict, payload: dict | None,
               service_date: str, review: dict) -> dict:
    """Price complete office + NCS + limb EMG scenarios without choosing a CPT level."""
    from itertools import product
    from app.insurance import cpt_rules, visit_estimate

    option = {'requested': bool(review.get('emg_required')), 'additional_min_cents': None,
              'additional_max_cents': None, 'total_min_cents': None, 'total_max_cents': None,
              'message': 'Select EMG / nerve testing to calculate its additional patient cost.', 'scenario_count': 0}
    codes = result.get('codes') or []
    testing = [row for row in codes if row['code'] in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS]
    option['included'] = any(row['code'] in cpt_rules.EMG_ADD_ONS for row in testing)
    if not option['requested'] and not option['included']:
        return option
    baseline = [row for row in codes if row not in testing]
    if not baseline:
        option['message'] = 'An office consultation is needed as the comparison for additional testing cost.'
        return option
    alternatives = result.get('price_range', {}).get('mode') == 'alternatives'
    baselines = [[row] for row in baseline] if alternatives else [baseline]
    nerves = [row for row in testing if row['code'] in cpt_rules.NCS_CODES]
    needles = [row for row in testing if row['code'] in cpt_rules.EMG_ADD_ONS]
    def preview(code, units=1):
        return {'id': 'emg-preview:' + code, 'code': code, 'units': units, 'source': 'Additional testing cost scenario'}
    if needles:
        extras = [testing]
        option['message'] = 'Additional patient cost for the testing already selected below.'
    else:
        # At most 224 combinations: four office levels, seven NCS levels,
        # limited/complete limb EMG, and one through four limbs. No order is created.
        extras = [[nerve, preview(code, units)] for nerve, code, units in product(
            nerves or [preview(code) for code in sorted(cpt_rules.NCS_CODES)], ('95885', '95886'), range(1, 5))]
        option['message'] = 'Range includes one nerve-conduction study level and limited or complete needle EMG of 1–4 limbs. Specify the testing below to narrow the amount. This is a cost scenario, not a procedure order.'
    calculation_benefits = dict(result.get('benefits') or {})
    if result.get('pricing_assumptions') and calculation_benefits.get('network_status') != 'out_of_network':
        calculation_benefits['network_status'] = 'in_network'
    def amount_for(lines):
        priced = visit_estimate.price_lines(lines, rates, calculation_benefits, rules, payload=payload,
            service_date=service_date, reviews=review.get('lines'))
        # A copay-only advance cannot establish the incremental price of EMG.
        candidate = {**result, 'price_range': priced,
            'arrival_collection': visit_estimate.arrival_collection(priced, result['benefits'], review),
            'planning_estimate': visit_estimate.planning_estimate(priced, result['benefits'], result.get('identity') or {})}
        guidance = build(candidate, review)
        return guidance['amount_cents'] if guidance['status'] != 'deferred_billing' and (priced.get('complete') or guidance['basis'] == 'out_of_pocket_limit') else None
    differences, totals = [], []
    for office in baselines:
        base_amount = amount_for(office)
        if base_amount is None:
            option['message'] = 'The applicable office and testing benefits do not yet support an additional patient-cost calculation.'
            return option
        for extra in extras:
            total = amount_for([*office, *extra])
            if total is None:
                option['message'] = 'The returned benefits or clinic rates do not cover every testing option. Specify the nerve study and EMG scope below to calculate the additional cost.'
                return option
            differences.append(max(0, total - base_amount))
            totals.append(total)
    if totals:
        option.update(additional_min_cents=min(differences), additional_max_cents=max(differences),
                      total_min_cents=min(totals), total_max_cents=max(totals), scenario_count=len(totals))
    return option


def cash_reference_estimate(result: dict) -> dict | None:
    """A disclosed planning estimate, never a fabricated contracted allowance."""
    from app.insurance import commercial, cpt_rules
    lines = (result.get('price_range') or {}).get('lines') or []
    if not lines or any(line.get('allowed_cents') is not None for line in lines):
        return None
    terms = [line.get('benefits') or {} for line in lines]
    if not all(t.get('active') is True and t.get('service_date_matches') is not False
               and t.get('coverage_level') == 'individual' and not t.get('has_secondary') for t in terms):
        return None
    # A whole-package reference cannot be allocated across different benefit terms.
    keys = ('copay_cents', 'coinsurance_percent', 'deductible_remaining_cents', 'oop_remaining_cents', 'copay_id', 'deductible_id', 'oop_id')
    if any(tuple(t.get(k) for k in keys) != tuple(terms[0].get(k) for k in keys) for t in terms):
        return None
    t = terms[0]
    if not (t.get('network_independent') or result['benefits'].get('network_status') == 'in_network' or result.get('pricing_assumptions')):
        return None
    from app.insurance.visit_estimate import _workflow_condition
    if any(any(not _workflow_condition(reason) for reason in item.get('review_reasons', [])) for item in terms):
        return None
    cash = result.get('cash_quote') or {}
    low, high = cash.get('min_cents'), cash.get('max_cents')
    if not cash.get('complete'):
        codes = {line['code'] for line in lines}
        packages = (result.get('cash_schedule') or {}).get('packages') or []
        if not packages or not codes <= cpt_rules.NCS_CODES | {'95885', '95886'}:
            return None
        limbs = sum(line.get('units') or 0 for line in lines if line['code'] in {'95885', '95886'})
        candidates = [package['amount_cents'] for package in packages if package['max_limbs'] >= limbs]
        if not candidates:
            return None
        low, high = min(candidates), max(candidates)
    if low is None or high is None:
        return None
    calculation = {**t, 'network_status': 'in_network', 'requires_review': False, 'review_reasons': []}
    try:
        amounts = [commercial.estimate([{'code': 'planning reference', 'units': 1, 'allowed_cents': fee}], calculation,
                                      {'cost_share': 'payer_returned'})['patient_cents'] for fee in (low, high)]
    except commercial.CommercialError:
        return None
    amount, maximum = min(amounts), max(amounts)
    share = f"{t['coinsurance_percent']}% coinsurance" if t.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'} else 'the returned copay'
    return {'amount_cents': amount, 'max_cents': maximum,
        'reason': f"Advance planning estimate using {share} and the clinic's ${low / 100:,.2f}–${high / 100:,.2f} published price range. Estimated patient share: ${amount / 100:,.2f}–${maximum / 100:,.2f}; collect the lower estimate now. Final responsibility uses the insurer's allowed amount.",
        'assumptions': ['No contracted rate is available for this payer. The clinic published price is used only as an estimated pricing basis; it is not an insurer-contracted allowance.',
                        'Final testing scope and the insurer’s allowed amount can change this estimate.',
                        *(['The returned cost sharing applies both in and out of network; provider participation is not inferred.'] if t.get('network_independent') else []),
                        'Any required authorization or referral must apply to the performed services; unknown requirements are not confirmed approvals.']}
