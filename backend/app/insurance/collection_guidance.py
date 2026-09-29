"""Advance collection decisions; a deferred collection never means free care."""
from __future__ import annotations

import re


def build(result: dict, review: dict | None = None) -> dict:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    strict = result.get('arrival_collection') or {}
    planning = result.get('planning_estimate') or {}
    assumptions = list(result.get('pricing_assumptions') or [])
    actions = []
    guidance = {'amount_cents': 0, 'status': 'deferred', 'basis': 'deferred_billing',
                'reason': 'An advance charge cannot be calculated from the available benefits. Collect $0 now; bill any patient responsibility after the insurer processes the claim.',
                'patient_message': 'No payment is requested today. This does not mean your care is free; you may receive a bill after your insurer processes the claim.',
                'assumptions': assumptions, 'patient_actions': actions, 'services': [],
                'network': 'In network' if benefits.get('network_status') == 'in_network' else
                           'Out of network' if benefits.get('network_status') == 'out_of_network' else
                           'In network · assumed' if assumptions else 'Determined when claim is processed',
                'requirements': {}}
    plans = ' '.join((result.get('plan_details') or {}).get('stedi', {}).get('names') or []).upper()
    routine_ppo = (bool(re.search(r'\bPPO\b|PREFERRED PROVIDER OPTION', plans))
                   and not re.search(r'\bHMO\b|HEALTHSELECT|\bERS\b', plans)
                   and bool(lines) and all(re.fullmatch(r'992(?:0[2-5]|1[2-5])', line.get('code', '')) for line in lines))
    adverse = False
    for kind, label in [('referral', 'referral'), ('authorization', 'prior authorization')]:
        requirements = [line.get('benefits', {}).get('requirements', {}).get(kind, {}) for line in lines]
        if any(item.get('status') in {'denied', 'expired', 'pending'} or
               item.get('required') == 'required' and not item.get('cleared') for item in requirements):
            adverse = True
            guidance['requirements'][kind] = 'Required · patient action below'
            actions.append('Ask your referring doctor to send the referral for this visit.' if kind == 'referral' else
                           'Contact your plan about the authorization requirement before the service; the treating office may need to submit clinical information.')
        elif requirements and all(item.get('cleared') for item in requirements):
            guidance['requirements'][kind] = 'On file' if any(item.get('required') == 'required' for item in requirements) else 'Not required'
            if kind == 'authorization' and any('absent indicator' in line.get('benefits', {}).get('authorization_basis', '') for line in lines):
                guidance['requirements'][kind] = 'Not required · assumed'
                assumptions.append('No authorization indicator was returned for the applicable service; assumed not required under Stedi guidance.')
        else:
            guidance['requirements'][kind] = ('Plan has not confirmed · assumed covered' if kind == 'authorization' and any(line.get('benefits', {}).get('authorization_basis') == 'Payer indicator' for line in lines) else 'No requirement reported') if lines else 'Determined by plan'
            if kind == 'referral' and routine_ppo:
                guidance['requirements'][kind] = 'Not required · assumed'
                assumptions.append('Routine office care under the returned PPO product is assumed to allow direct specialist access. An explicit payer referral requirement would override this assumption.')
            elif lines:
                assumptions.append(f'No confirmed {label} requirement is available. The estimate assumes covered care; this is not an approval.')
    identity = result.get('identity') or benefits.get('identity') or {}
    safe = (benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and benefits.get('network_status') != 'out_of_network' and not benefits.get('has_secondary')
            and identity.get('status') != 'review' and not price.get('service_issues') and not adverse
            and not any('evidence' in str(reason).lower() or 'date' in str(reason).lower()
                        for reason in benefits.get('review_reasons', [])))
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
                        reason='The payer reports $0 remaining in-network out-of-pocket responsibility for the selected covered services. No advance payment is needed.')
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
        if (terms and (benefits.get('network_status') == 'in_network' or result.get('pricing_assumptions'))
                and (len(office) == 1 or price.get('mode') == 'alternatives')
                and all(line.get('units') == 1 for line in office)
                and all(t.get('active') is True and t.get('copay_cents') is not None
                        and t.get('copay_period') in {'VISIT', '27', 'DAY', '7'}
                        and (t.get('deductible_applies') is False or t.get('deductible_remaining_cents') == 0)
                        and t.get('coinsurance_percent') in {None, '0', '0.0'}
                        and not any(any(word in r.lower() for word in ('conditional', 'limit', 'date', 'identity', 'evidence'))
                                    for r in t.get('review_reasons', [])) for t in terms)
                and len({t['copay_cents'] for t in terms}) == 1):
            amount = min(terms[0]['copay_cents'], terms[0].get('oop_remaining_cents') if terms[0].get('oop_remaining_cents') is not None else terms[0]['copay_cents'])
            guidance.update(status='estimated', basis='office_copay',
                            reason='The applicable office-visit copay is collected once. Any additional service responsibility will follow the claim.')
    failure_codes = set((result.get('eligibility_failure') or {}).get('codes') or [])
    if failure_codes & {'72', '73', '65', '67', '71', '75', '76'}:
        actions.append('Contact your insurer to confirm the member ID, policyholder name and date of birth; bring the corrected insurance details.')
    if amount is not None:
        payments = (review or {}).get('payments') or {}
        if strict.get('amount_cents') is None and payments.get('reference'):
            amount = max(0, amount - payments['amount_cents'])
        guidance['amount_cents'] = max(0, amount)
        guidance['patient_message'] = 'This is an advance estimate for the planned care. The insurer determines final responsibility when it processes the claim.'
        if amount > 0 and not payments.get('reference') and strict.get('payments_received_cents') is None:
            assumptions.append('No prior payments or credits were deducted because the clinic ledger is unavailable. Tell us if you already paid for this visit and show your receipt to avoid paying twice.')
        elif payments.get('reference') or strict.get('payments_received_cents') is not None:
            guidance['reason'] += ' Recorded payments have been deducted.'
    else:
        guidance.update(status='deferred', basis='deferred_billing')
        if benefits.get('active') is False:
            guidance['reason'] = 'The payer reports inactive coverage for this visit. Collect $0 now and defer billing while the patient resolves coverage.'
            actions.append('Contact your insurer to resolve inactive coverage or provide your current insurance card.')
        elif identity.get('status') == 'review':
            guidance['reason'] = 'The returned policy does not match the patient details reliably. Collect $0 now and defer billing.'
            actions.append('Contact your insurer to correct your member details and bring the updated card.')
        elif adverse:
            guidance['reason'] = 'A referral or authorization issue affects the selected care. Collect $0 now; an advance payment would not resolve the coverage requirement.'
        elif benefits.get('has_secondary'):
            guidance['reason'] = 'More than one plan may pay for this visit. Collect $0 now and bill after the plans coordinate benefits.'
        elif result.get('eligibility_failure') or benefits.get('active') is not True:
            guidance['reason'] = 'Current payer benefits are unavailable. Collect $0 now and bill after the insurer processes the claim.'
        elif benefits.get('service_date_matches') is False:
            guidance['reason'] = 'The payer response covers a different date. Collect $0 now and bill after benefits for this visit are established.'
        elif price.get('service_issues'):
            guidance['reason'] = 'The selected services do not form a complete billable plan. Collect $0 now; final documented services will determine the claim.'
        else:
            guidance['reason'] = 'The returned benefits or clinic rates do not establish an advance charge for all selected services. Collect $0 now and bill after the insurer processes the claim.'
    for line in lines:
        terms = line.get('benefits') or {}
        guidance['services'].append({'id': line.get('id'), 'code': line.get('code'),
            'benefit': line.get('benefit_basis') or 'Applicable service benefits',
            'patient_cents': (line.get('calculation') or {}).get('patient_cents') if safe and amount is not None else None,
            'oop_remaining_cents': terms.get('oop_remaining_cents'),
            'copay_cents': terms.get('copay_cents'), 'coinsurance_percent': terms.get('coinsurance_percent'),
            'deductible_remaining_cents': terms.get('deductible_remaining_cents'),
            'selected_benefit_ids': list(dict.fromkeys([*(terms.get('selected_benefit_ids') or []),
                *[terms[key] for key in ('deductible_id', 'oop_id') if terms.get(key)]]))})
        if guidance['basis'] == 'out_of_pocket_limit':
            guidance['services'][-1]['patient_cents'] = 0
    guidance['assumptions'] = list(dict.fromkeys(assumptions))
    return guidance
