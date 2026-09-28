"""Explainable visit-line calculations over protected payer and clinic evidence."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import re
from datetime import date

from app.insurance import commercial, cpt_rules, plan_benefits, identity


def clinical_group(code: str) -> str:
    if code.isdigit() and 99202 <= int(code) <= 99215:
        return 'visit'
    if code in cpt_rules.NCS_CODES:
        return 'nerve'
    if code in cpt_rules.EMG_ADD_ONS:
        return 'muscle'
    return 'other'


def is_emg_visit(inputs: dict) -> bool:
    if any(row.get('service_status') == 'scheduled' and row.get('code') in cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS
           for row in inputs.get('lines', [])):
        return True
    reason = str(inputs.get('reason') or '')
    procedure = r'\b(?:emg|ncs|electromyograph\w*|nerve conduction)\b'
    # A possible future test is not today's booked procedure. Explicit orders win above.
    optional = r'\b(?:possible|potential|consider\w*|discuss\w*|may need|may require|if needed|if indicated|no|not)\b'
    for clause in re.split(r'[.;\n]', reason):
        match = re.search(procedure, clause, re.I)
        if (match and not re.search(optional, clause[:match.start()], re.I)
                and not re.search(r'\b(?:if needed|if indicated|only if|as needed|may be needed)\b', clause[match.end():], re.I)):
            return True
    return False


def service_plan(available: list[dict], inputs: dict, source: dict) -> dict:
    """Describe planning choices; past care is a suggestion, never a new order."""
    emg = is_emg_visit(inputs)
    order = ['nerve', 'muscle', 'visit', 'other'] if emg else ['visit', 'nerve', 'muscle', 'other']
    labels = {'visit': 'Office consultation', 'nerve': 'Nerve conduction study', 'muscle': 'Needle EMG', 'other': 'Other planned services'}
    descriptions = {'visit': 'Choose one anticipated visit level. Final coding follows the documented care.',
                    'nerve': 'Choose the range for the total planned nerve studies. These levels are alternatives.',
                    'muscle': 'Add muscle testing to the nerve study. Specify the limbs being examined.',
                    'other': 'Include only services expected at this appointment.'}
    groups = [{'id': key, 'label': labels[key], 'description': descriptions[key],
               'selection_mode': 'single' if key in {'visit', 'nerve'} else 'multiple',
               'role': 'Main service' if key == ('nerve' if emg else 'visit') else 'Additional services',
               'ids': [row['id'] for row in available if clinical_group(row['code']) == key]}
              for key in order if any(clinical_group(row['code']) == key for row in available)]
    scheduled = [row['id'] for row in available if row['source'] == 'eCW scheduled service']
    recommended = list(scheduled)
    basis = 'Planned services on this eCW appointment' if scheduled else ''
    for group in groups:
        if group['selection_mode'] == 'single' and len(set(scheduled) & set(group['ids'])) > 1:
            recommended = [key for key in recommended if key not in group['ids']]
            basis = 'eCW contains multiple records for alternative services. Choose the intended procedure for this visit.'
    if not scheduled:
        # Only use this patient's comparable completed visits. An empty or tied
        # history cannot establish a "most common" code or a clinical quantity.
        history = [row for row in source.get('recent_completed_appointments', [])
                   if (emg and any(code in cpt_rules.NCS_CODES for code in row.get('coded_services', [])))
                   or (not emg and inputs.get('visit_type') and row.get('visit_type') == inputs['visit_type'])]
        for group in groups:
            if group['id'] not in ({'nerve', 'muscle'} if emg else {'visit'}):
                continue
            counts = Counter(code for visit in history for code in set(visit.get('coded_services', [])))
            rows = [row for row in available if row['id'] in group['ids'] and counts[row['code']] > 0]
            if rows:
                highest = max(counts[row['code']] for row in rows)
                winners = [row for row in rows if counts[row['code']] == highest]
                if len(winners) == 1:
                    recommended.append(winners[0]['id'])
        if recommended:
            basis = f'Most frequent matching services in {len(history)} recent eCW visit(s) for this patient. Review for today’s plan.'
    return {'kind': 'emg_ncs' if emg else 'office' if inputs.get('visit_type') else 'other',
            'groups': groups, 'recommended_ids': recommended, 'recommendation_basis': basis,
            'history_count': len(source.get('recent_completed_appointments', []))}


def service_type(code: str, rules: dict) -> str | None:
    configured = (rules.get('service_type_by_code') or {}).get(code)
    if configured:
        return str(configured)
    # Stedi's documented neurology and office/telehealth mappings. Clinic overrides win.
    if code.isdigit() and (95700 <= int(code) <= 96020 or 99202 <= int(code) <= 99215):
        return '98'
    return None


def _workflow_condition(reason: str) -> bool:
    return 'authorization' in reason.lower() or 'referral' in reason.lower()


def review_valid(review: dict, service_date: str, units: int | None) -> bool:
    try:
        return (bool(review.get('reference')) and bool(review.get('verified_at'))
                and date.fromisoformat(review['valid_from']) <= date.fromisoformat(service_date) <= date.fromisoformat(review['valid_to'])
                and units is not None and review.get('units', 0) >= units)
    except (ValueError, KeyError, TypeError):
        return False


def resolve_requirements(benefits: dict, review: dict, service_date: str, units: int | None) -> dict:
    value = deepcopy(benefits)
    requirements = {}
    for kind in ('referral', 'authorization'):
        signal = value.get(kind + '_signal', 'unknown')
        evidence = review.get(kind) or {}
        valid = review_valid(evidence, service_date, units)
        status = evidence.get('status') if valid else 'expired' if evidence else 'not_verified'
        cleared = (signal == 'not_required' and not (valid and status in {'pending', 'denied'})) or (valid and status == 'valid') or (valid and status == 'not_required' and signal != 'required')
        requirements[kind] = {'required': 'not_required' if valid and status == 'not_required' and signal != 'required' else signal, 'payer_signal': signal, 'status': 'not_applicable' if signal == 'not_required' and cleared else status,
                              'reference': evidence.get('reference'), 'verified_at': evidence.get('verified_at'),
                              'verified_by': evidence.get('verified_by'), 'valid_to': evidence.get('valid_to'), 'cleared': cleared}
        if not cleared and signal == 'not_required':
            value.setdefault('review_reasons', []).append(f"Resolve the conflicting {kind} verification before pricing this service.")
        if cleared:
            phrase = 'authorization' if kind == 'authorization' else 'referral'
            value['review_reasons'] = [reason for reason in value.get('review_reasons', [])
                                       if phrase not in reason.lower()]
    value['requirements'] = requirements
    value['requires_review'] = bool(value.get('review_reasons'))
    return value


def price_lines(codes: list[dict], rates: list[dict], benefits: dict, rules: dict, *, payload: dict | None = None,
                service_date: str = '', reviews: dict | None = None, alternatives: bool = False) -> dict:
    """Allocate shared balances once; alternatives start from the same balance."""
    reviews = reviews or {}
    lines = []
    deductible_used: dict[str, int] = {}
    oop_used: dict[str, int] = {}
    copays_applied: set[str] = set()
    prior_unresolved = False
    name_difference = benefits.get('identity', {}).get('status') == 'review' and identity.name_difference_only(benefits.get('identity', {}))
    for index, code in enumerate(codes):
        line_id = code.get('id') or f"{code['code']}:{index}"
        review = reviews.get(line_id) or {}
        selection = review.get('benefits') or {}
        try:
            units = commercial._integer(code.get('units'), 'Service units')
        except commercial.CommercialError:
            units = None
        selection_valid = review_valid(selection, service_date, units)
        stc = service_type(code['code'], rules)
        raw_benefits = commercial.normalize_stedi_271(payload, stc or '98', benefits.get('network_status') == 'in_network',
            benefits.get('has_secondary', False), procedure_code=code['code'], service_date=service_date,
            selected_ids=selection.get('selected_ids') if selection_valid else None,
            office_service_types=rules.get('office_service_types')) if payload is not None else deepcopy(benefits)
        stc = raw_benefits.get('resolved_service_type') or stc
        # Name differences remain on the case for billing. They do not stop
        # arithmetic for an explicitly conditional estimate of this policy.
        integrity = [reason for reason in benefits.get('review_reasons', []) if 'evidence' in reason or ('identity' in reason and not name_difference)]
        if name_difference:
            raw_benefits['review_reasons'] = [reason for reason in raw_benefits.get('review_reasons', []) if 'identity' not in reason]
        raw_benefits['review_reasons'] = list(dict.fromkeys([*raw_benefits.get('review_reasons', []), *integrity]))
        raw_benefits['requires_review'] = bool(raw_benefits['review_reasons'])
        raw_benefits = plan_benefits.apply(raw_benefits, payload or {}, code['code'], service_date)
        applied = resolve_requirements(raw_benefits, review, service_date, units)
        line_rules = dict(rules)
        if applied.get('cost_share'):
            line_rules['cost_share'] = applied['cost_share']
        if selection_valid:
            line_rules['cost_share'] = selection['cost_share']
            applied['cost_share'] = selection['cost_share']
            applied['deductible_applies'] = selection['deductible_applies']
            if not selection['deductible_applies']:
                applied['deductible_remaining_cents'] = 0
                applied['review_reasons'] = [reason for reason in applied.get('review_reasons', []) if 'remaining individual deductible' not in reason]
                applied['requires_review'] = bool(applied['review_reasons'])
        matches = [rate for rate in rates if rate['code'] == code['code']
                   and (rate.get('modifier') or '') == ','.join(code.get('modifiers') or [])]
        rate = matches[0] if len(matches) == 1 else {}
        line = {**code, 'id': line_id, 'units': units, 'benefits': applied,
                'unit_cash_cents': rate.get('cash_cents'),
                'cash_cents': rate['cash_cents'] * units if rate.get('cash_cents') is not None and units else None,
                'cash_source': rate.get('source_reference') if rate.get('cash_cents') is not None else None,
                'unit_allowed_cents': rate.get('allowed_cents'),
                'allowed_cents': rate['allowed_cents'] * units if rate.get('allowed_cents') is not None and units else None,
                'rate_source': rate.get('source_reference') or 'Clinic contract', 'rate_version': rate.get('version_id'),
                'rate_effective_from': rate.get('effective_from'), 'benefit_source': 'Stedi', 'benefit_verification': selection if selection_valid else None,
                'benefit_basis': applied.get('benefit_label') or f'Service category {stc}' if stc else 'Service category needs mapping',
                'conditions': [reason for reason in applied.get('review_reasons', []) if _workflow_condition(reason)],
                'issues': [reason for reason in applied.get('review_reasons', []) if not _workflow_condition(reason)], 'calculation': None}
        if name_difference:
            line['conditions'].append('Estimated using the returned policy; patient names differ between eCW and the payer.')
        direct = any(commercial._procedure(row) == code['code'] and row.get('code') in {'A', 'B'} for row in commercial._stedi_entries(payload or {}))
        if direct:
            line['benefit_basis'] = 'Procedure-specific payer benefit'
        if not stc and not direct and payload is not None:
            line['issues'].append('Map this service to a payer-supported benefit category in the clinic estimate profile.')
        if code.get('needs_confirmation') and not code.get('confirmed'):
            line['issues'].append('This CPT appears in multiple eCW records. Confirm which represent separate planned services.')
        if not units:
            line['issues'].append('Confirm the number of units for this service.')
        if line['allowed_cents'] is None:
            line['issues'].append('Add an active clinic contract rate matching this code, modifier and visit.')
        if benefits.get('network_status') != 'in_network':
            line['issues'].append('Verify provider participation for this plan and visit location.')
        if benefits.get('requires_review') and not benefits.get('review_reasons'):
            line['issues'].append('Saved payer evidence needs verification before pricing.')
        period = str(applied.get('copay_period') or '').upper()
        if applied.get('copay_cents') is not None and period not in {'', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'}:
            line['issues'].append('The returned copay period needs a supported calculation rule.')
        if not alternatives and len(codes) > 1 and applied.get('copay_cents') is not None and not period:
            line['issues'].append('Confirm whether this copay applies once per visit or to each service.')
        deductible_key = applied.get('deductible_id') or 'encounter'
        oop_key = applied.get('oop_id') or 'encounter'
        if not alternatives:
            if deductible_used and deductible_key not in deductible_used and applied.get('deductible_remaining_cents'):
                line['issues'].append('Confirm how the different service deductibles share an accumulator before totaling the visit.')
            if oop_used and oop_key not in oop_used:
                line['issues'].append('Confirm how the different out-of-pocket limits apply together.')
        if prior_unresolved and not alternatives:
            line['issues'].append('Resolve the preceding service before allocating the remaining visit balances.')
        if applied.get('oop_remaining_cents') == 0:
            line['issues'] = [reason for reason in line['issues'] if reason not in {
                'Confirm the remaining individual deductible that applies to this visit.',
                'Confirm which copay or coinsurance term applies, including any visit-count conditions.',
            }]
        if not line['issues']:
            calculation_benefits = deepcopy(applied)
            calculation_benefits['review_reasons'] = []
            calculation_benefits['requires_review'] = False
            if not alternatives:
                if calculation_benefits.get('deductible_remaining_cents') is not None:
                    calculation_benefits['deductible_remaining_cents'] = max(0, calculation_benefits['deductible_remaining_cents'] - deductible_used.get(deductible_key, 0))
                if calculation_benefits.get('oop_remaining_cents') is not None:
                    calculation_benefits['oop_remaining_cents'] = max(0, calculation_benefits['oop_remaining_cents'] - oop_used.get(oop_key, 0))
                copay_key = applied.get('copay_id') or '|'.join(applied.get('selected_benefit_ids', [])) or 'visit'
                if period in {'VISIT', '27', 'DAY', '7'} and copay_key in copays_applied:
                    calculation_benefits['copay_cents'] = 0
            try:
                calc = commercial.estimate([{'code': code['code'], 'units': units,
                    'allowed_cents': rate['allowed_cents'], 'rate_id': rate.get('id')}], calculation_benefits, line_rules)
                line['calculation'] = calc
                if not alternatives:
                    deductible_used[deductible_key] = deductible_used.get(deductible_key, 0) + min(calc['deductible_cents'], calc['patient_cents'])
                    oop_used[oop_key] = oop_used.get(oop_key, 0) + calc['patient_cents']
                    if period in {'VISIT', '27', 'DAY', '7'}:
                        copays_applied.add(copay_key)
            except commercial.CommercialError as exc:
                line['issues'].append(str(exc))
        lines.append(line)
        prior_unresolved = prior_unresolved or line['calculation'] is None
    service_issues = [] if alternatives else cpt_rules.service_selection_issues([line['code'] for line in lines])
    if not alternatives and sum(line.get('units') or 0 for line in lines if line['code'] in {'95885', '95886'}) > 4:
        service_issues.append('Limited and complete needle examinations must cover at most four distinct limbs.')
    complete = bool(lines) and not service_issues and all(line['calculation'] is not None for line in lines)
    all_rates = bool(lines) and all(line['allowed_cents'] is not None for line in lines)
    result = {'mode': 'alternatives' if alternatives else 'services', 'lines': lines, 'complete': complete,
              'service_issues': service_issues,
              'conditional': any(line['conditions'] for line in lines),
              'collection': {'action': 'verify_before_collecting', 'amount_cents': None,
                             'reason': 'Resolve the listed items and verify payments already received before collecting.'}}
    cash = [line['cash_cents'] for line in lines]
    result['cash_complete'] = bool(cash) and all(value is not None for value in cash) and not service_issues
    if result['cash_complete']:
        result.update(cash_min_cents=min(cash) if alternatives else sum(cash),
                      cash_max_cents=max(cash) if alternatives else sum(cash))
    if all_rates:
        allowed = [line['allowed_cents'] for line in lines]
        result.update(allowed_min_cents=min(allowed) if alternatives else sum(allowed),
                      allowed_max_cents=max(allowed) if alternatives else sum(allowed))
    if complete:
        amounts = [line['calculation']['patient_cents'] for line in lines]
        result.update(patient_min_cents=min(amounts) if alternatives else sum(amounts),
                      patient_max_cents=max(amounts) if alternatives else sum(amounts))
        plan_amounts = [line['calculation']['plan_cents'] for line in lines]
        result.update(plan_min_cents=min(plan_amounts) if alternatives else sum(plan_amounts),
                      plan_max_cents=max(plan_amounts) if alternatives else sum(plan_amounts))
        result['collection'] = {'action': 'confirm_services' if alternatives else 'confirm_payments', 'amount_cents': None,
                                'reason': 'Confirm the final planned service before collecting.' if alternatives else
                                          'Estimate ready. Confirm payments already received before collecting this amount.'}
        if result['patient_max_cents'] == 0:
            result['collection'] = {'action': 'no_collection', 'amount_cents': 0, 'reason': 'No estimated patient responsibility for these covered services.'}
        if result['conditional']:
            result['collection'] = {'action': 'verify_before_collecting', 'amount_cents': None,
                                    'reason': 'Use this conditional estimate for planning. Resolve the benefit and approval conditions before collecting.'}
    result['scenarios'] = [{'code': line['code'], 'allowed_cents': line['allowed_cents'], **(line['calculation'] or {})} for line in lines]
    if service_issues:
        result['collection'] = {'action': 'complete_services', 'amount_cents': None, 'reason': service_issues[0]}
    return result


def arrival_collection(price: dict, benefits: dict, review: dict) -> dict:
    """At-service patient obligations, separate from the insurer's later payment."""
    result = {'amount_cents': None, 'estimated_cents': None, 'verified_copay_cents': None, 'status': 'verify', 'basis': 'patient_obligations',
              'prior_balance_cents': None, 'noncovered_cents': None, 'payments_received_cents': None,
              'reason': 'Verify coverage, planned services, benefits and approval requirements before collecting.'}
    if benefits.get('active') is not True or benefits.get('network_status') != 'in_network' or benefits.get('has_secondary') or benefits.get('service_date_matches') is False or any('identity' in reason for reason in benefits.get('review_reasons', [])):
        return result
    if price.get('conditional') or price.get('mode') in {'alternatives', 'options'}:
        result['estimated_cents'] = price.get('patient_max_cents')
        result['reason'] = 'This is a planning amount. Confirm the services and resolve benefit or approval conditions before collection.'
        return result
    office = [line for line in price.get('lines', []) if str(line.get('code')) in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}]
    if len(office) == 1:
        terms = office[0]['benefits']
        if (office[0].get('units') == 1 and not terms.get('requires_review') and not office[0].get('conditions')
                and terms.get('cost_share') == 'copay' and terms.get('deductible_applies') is False
                and terms.get('copay_period') in {'VISIT', '27', 'DAY', '7'}
                and terms.get('copay_cents') is not None and terms.get('oop_remaining_cents') is not None):
            result['verified_copay_cents'] = min(terms['copay_cents'], terms['oop_remaining_cents'])
    if not price.get('complete') and result['verified_copay_cents'] is None:
        return result
    payments = review.get('payments') or {}
    result['estimated_cents'] = price.get('patient_max_cents') if price.get('complete') else None
    amount = result['estimated_cents'] if price.get('complete') else result['verified_copay_cents']
    if payments.get('reference'):
        result['payments_received_cents'] = payments['amount_cents']
        amount = max(0, amount - payments['amount_cents'])
    result.update(amount_cents=amount, status='estimate', reason=(
        'Copay, applicable deductible and coinsurance for this visit, less verified payments. Prior balances and non-covered charges are not included unless separately verified.' if payments.get('reference') else
        'Copay, applicable deductible and coinsurance for this visit, before payments or credits. Check the clinic ledger for prior balances and amounts already collected; those values were not returned.'))
    if not price.get('complete'):
        result.update(status='copay_only', reason='Only the verified office copay is included. Additional deductible and coinsurance remain for estimation or post-adjudication billing. ' +
                      ('Verified payments have been subtracted.' if payments.get('reference') else 'Before payments or credits; check the clinic ledger.'))
    return result


def planning_estimate(price: dict, benefits: dict, comparison: dict) -> dict | None:
    """A conditional planning result never authorizes collection or clears evidence."""
    lines = price.get('lines') or []
    if (not lines or benefits.get('active') is not True or benefits.get('has_secondary')
            or benefits.get('network_status') == 'out_of_network'
            or benefits.get('service_date_matches') is False or price.get('service_issues')
            or (comparison.get('status') == 'review' and not identity.name_difference_only(comparison))
            or any('evidence' in r.lower() or 'date' in r.lower() for r in benefits.get('review_reasons', []))
            or any('needs mapping' in line.get('benefit_basis', '') for line in lines)
            or any(requirement.get('status') in {'denied', 'expired', 'pending'}
                   for line in lines for requirement in line.get('benefits', {}).get('requirements', {}).values())):
        return None
    assumptions = []
    if comparison.get('status') == 'review':
        assumptions.append('Calculated using the returned patient coverage. eCW and payer patient names differ; both are retained for billing.')
    if benefits.get('network_status') != 'in_network':
        assumptions.append('The provider and visit location participate in this plan’s network.')
    assumptions.append('The selected services are covered and any required referral or authorization is in place.')
    if all(line.get('units') and line['benefits'].get('active') is True
           and line['benefits'].get('network_status') != 'out_of_network'
           and line['benefits'].get('oop_remaining_cents') == 0
           and line['benefits'].get('service_date_matches') is not False
           and not line['benefits'].get('has_secondary')
           and not any('limit' in r.lower() or 'date' in r.lower() for r in line['benefits'].get('review_reasons', []))
           for line in lines):
        return {'amount_cents': 0, 'collectable': False, 'basis': 'out_of_pocket_limit',
                'explanation': 'The payer reports $0 remaining in-network out-of-pocket responsibility. Under the assumptions below, the selected covered services have an estimated patient cost of $0.',
                'assumptions': assumptions}
    if price.get('complete') and price.get('patient_min_cents') == price.get('patient_max_cents'):
        return {'amount_cents': price['patient_max_cents'], 'collectable': False, 'basis': 'conditional_services',
                'explanation': 'Estimated patient responsibility for the selected services, before payments and credits.',
                'assumptions': assumptions}
    return None
