"""Deterministic visit-level planning and policy-gated check-in collection."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance import cpt_rules
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network, verified_out_of_network
from app.insurance.collection.requirements import requirement_blocker
from app.insurance import canonical_evidence
from app.insurance.reference_price import select_reference as _reference_for

RULE_VERSION = 'visit-collection-1'


def _group(code: str) -> str:
    family = cpt_rules.service_definition(code)['family']
    if family == 'office_consultation':
        return 'specialist_office'
    if family in {'nerve_conduction', 'needle_emg', 'standalone_emg'}:
        return 'emg_ncs'
    if family == 'diagnostic_procedure' and code in {'11104', '11105'}:
        return 'skin_biopsy'
    return 'other'


def _percent(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and 0 <= number <= 100 else None


def _interaction(terms: dict) -> str:
    explicit = terms.get('interaction_rule')
    if explicit in {'DEDUCTIBLE_PLUS_COINSURANCE', 'NO_PATIENT_COST_SHARE'}:
        return explicit
    if (explicit in {'COPAY_ONLY', 'DEDUCTIBLE_THEN_COPAY', 'COPAY_PLUS_COINSURANCE',
                     'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'}
            and terms.get('interaction_evidence_id') in (terms.get('selected_benefit_ids') or [])):
        return explicit
    copay = terms.get('copay_cents')
    coinsurance = _percent(terms.get('coinsurance_percent'))
    deductible = terms.get('deductible_remaining_cents')
    if type(terms.get('oop_remaining_cents')) is int and terms['oop_remaining_cents'] == 0:
        return 'NO_PATIENT_COST_SHARE'
    if type(copay) is int and copay >= 0 and coinsurance == Decimal(0):
        if terms.get('deductible_applies') is False or deductible == 0:
            return 'COPAY_ONLY'
    if copay is None and coinsurance is not None and type(deductible) is int and deductible >= 0:
        return 'DEDUCTIBLE_PLUS_COINSURANCE'
    return 'UNKNOWN_INTERACTION'


def build(result: dict, review: dict | None = None) -> dict:
    """Return a planning estimate and a separate policy-authorized visit amount."""
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    output = {'rule_version': RULE_VERSION, 'collect_now_cents': None,
              'collection_decision_type': 'EVIDENCE_REQUIRED',
              'estimated_patient_responsibility_cents': None,
              'estimated_low_cents': None, 'estimated_high_cents': None,
              'pricing_basis': None, 'reference_schedule_versions': [],
              'reference_total_cents': None, 'selected_price_total_cents': None,
              'cpt_source': result.get('service_selection_status'),
              'network_evidence': result.get('network_evidence'),
              'service_groups': [], 'remaining_actions': [], 'price_acquisition': [],
              'research': result.get('insurance_research'),
              'canonical_evidence': result.get('canonical_evidence') or canonical_evidence.build(result, review)}
    if result.get('payment_mode') == 'cash' or price.get('mode') != 'services' or not lines or price.get('service_issues'):
        output['remaining_actions'].append('Select one complete insured service plan.')
        return output
    benefits = result.get('benefits') or {}
    references = (result.get('temporary_rate_reference') or {}).get('lines') or []

    def acquire_reference(line: dict) -> None:
        task = {'code': line.get('code'), 'modifiers': line.get('modifiers') or [],
                'place_of_service': line.get('place_of_service') or result.get('place_of_service'),
                'effective_date': result.get('service_date'),
                'price_source': 'NMCT_BCBSTX_REFERENCE',
                'action': 'Acquire and approve a scoped BCBSTX CPT rate in the Clinical Library.'}
        if task not in output['price_acquisition']:
            output['price_acquisition'].append(task)

    if (benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or benefits.get('coverage_dates_invalid') or benefits.get('coverage_status_conflict')
            or not patient_benefits_bound(benefits.get('identity') or result.get('identity') or {})):
        for line in lines:
            exact = (type(line.get('allowed_cents')) is int
                     and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                     and line.get('allowed_basis') in {'clinic_contract', 'payer_out_of_network'})
            if not exact and _reference_for(line, references, result.get('service_date'),
                                             result.get('pricing_scope') or {'place_of_service':
                                                 line.get('place_of_service') or result.get('place_of_service')}) is None:
                acquire_reference(line)
        output['remaining_actions'].append('Verify this patient, policy and visit-date coverage.')
        return output
    grouped: dict[str, list[dict]] = {}
    for line in lines:
        grouped.setdefault(_group(str(line.get('code') or '')), []).append(line)
    deductible_used: dict[str, int] = {}
    total = 0
    bases = set()
    oop_pairs = set()
    encounter_copays: dict[str, int] = {}
    for name, members in grouped.items():
        terms = [line.get('benefits') or {} for line in members]
        patterns = {_interaction(term) for term in terms}
        group_result = {'service_group': name,
                        'cpts': [{'code': row.get('code'), 'units': row.get('units'),
                                  'modifiers': row.get('modifiers') or []} for row in members],
                        'interaction_rule': next(iter(patterns)) if len(patterns) == 1 else 'UNKNOWN_INTERACTION',
                        'estimated_patient_share_cents': None,
                        'deductible_applies': (next(iter({term.get('deductible_applies') for term in terms}))
                            if len({term.get('deductible_applies') for term in terms}) == 1 else None),
                        'copay_frequency': (next(iter({term.get('copay_period') for term in terms}))
                            if len({term.get('copay_period') for term in terms}) == 1 else None),
                        'benefit_evidence': list(dict.fromkeys(
                            key for term in terms for key in term.get('selected_benefit_ids') or [])),
                        'price_lines': []}
        output['service_groups'].append(group_result)
        amounts = []
        for line in members:
            exact = (type(line.get('allowed_cents')) is int
                     and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                     and line.get('allowed_basis') in {'clinic_contract', 'payer_out_of_network'})
            reference = (_reference_for(line, references, result.get('service_date'),
                                        result.get('pricing_scope') or {'place_of_service':
                                            line.get('place_of_service') or result.get('place_of_service')})
                         if not exact else None)
            value = line['allowed_cents'] if exact else (
                reference['unit_reference_cents'] * line['units'] if reference and type(line.get('units')) is int else None)
            basis = line.get('allowed_basis') if exact else 'BCBSTX_CROSS_PAYER_REFERENCE' if reference else None
            group_result['price_lines'].append({'code': line.get('code'), 'units': line.get('units'),
                'extended_cents': value, 'pricing_basis': basis,
                'source': line.get('rate_source') if exact else reference.get('source_reference') if reference else None,
                'rate_version': line.get('rate_version') if exact else reference.get('rate_version') if reference else None})
            if not exact and not reference:
                acquire_reference(line)
            amounts.append(value)
            if basis and group_result['interaction_rule'] in {'DEDUCTIBLE_PLUS_COINSURANCE',
                    'DEDUCTIBLE_THEN_COPAY', 'COPAY_PLUS_COINSURANCE', 'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'}:
                bases.add(basis)
        if (len(patterns) != 1 or 'UNKNOWN_INTERACTION' in patterns
                or any(not term.get('selected_benefit_ids') for term in terms)
                or any(term.get('active') is not True or term.get('source_errors') or term.get('coverage_status_conflict')
                       or term.get('service_date_matches') is False for term in terms)):
            output['remaining_actions'].append(f'Confirm the cost-sharing interaction for {name}.')
            continue
        pattern = group_result['interaction_rule']
        if pattern == 'NO_PATIENT_COST_SHARE':
            patient = 0
        elif pattern == 'COPAY_ONLY':
            copays = {term.get('copay_cents') for term in terms}
            periods = {str(term.get('copay_period') or '').upper() for term in terms}
            copay_ids = {term.get('copay_id') for term in terms}
            # A day/visit copay covers this group once, even when several CPT rows repeat it.
            if len(copays) != 1 or type(next(iter(copays))) is not int or len(periods) != 1 or (
                    len(members) > 1 and next(iter(periods)) not in {'DAY', '7', 'VISIT', '27'}) or len(copay_ids) != 1:
                output['remaining_actions'].append(f'Confirm copay frequency for {name}.')
                continue
            patient = next(iter(copays))
            if all(type(value) is int and (row.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                   for value, row in zip(amounts, members)):
                patient = min(patient, sum(amounts))
            copay_id = next(iter(copay_ids))
            if copay_id and next(iter(periods)) in {'VISIT', '27'}:
                if copay_id in encounter_copays:
                    if encounter_copays[copay_id] != next(iter(copays)):
                        output['remaining_actions'].append(f'Reconcile conflicting encounter copays for {name}.')
                        continue
                    patient = 0
                    group_result['duplicate_encounter_copay'] = True
                encounter_copays[copay_id] = next(iter(copays))
            group_result['copay_cents'] = patient
        else:
            if any(value is None for value in amounts):
                output['remaining_actions'].append(f'Acquire a matching CPT reference price for {name}.')
                continue
            deductible_values = {term.get('deductible_remaining_cents') for term in terms}
            percentages = {_percent(term.get('coinsurance_percent')) for term in terms}
            keys = {term.get('deductible_id') or 'visit' for term in terms}
            copays = {term.get('copay_cents') for term in terms}
            frequency = {str(term.get('copay_period') or '').upper() for term in terms}
            if (len(keys) != 1 or len(percentages) != 1 or
                    pattern in {'DEDUCTIBLE_PLUS_COINSURANCE', 'DEDUCTIBLE_THEN_COPAY',
                                'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'} and
                    (len(deductible_values) != 1 or type(next(iter(deductible_values))) is not int or
                     next(iter(deductible_values)) < 0) or
                    pattern in {'DEDUCTIBLE_PLUS_COINSURANCE', 'COPAY_PLUS_COINSURANCE',
                                'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'} and None in percentages or
                    pattern in {'DEDUCTIBLE_THEN_COPAY', 'COPAY_PLUS_COINSURANCE',
                                'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'} and
                    (len(copays) != 1 or type(next(iter(copays))) is not int or next(iter(copays)) < 0
                     or len(frequency) != 1 or next(iter(frequency)) not in {'DAY', '7', 'VISIT', '27'})):
                output['remaining_actions'].append(f'Reconcile the source-backed cost-sharing formula for {name}.')
                continue
            key = next(iter(keys))
            price_total = sum(amounts)
            deductible_base = price_total
            if pattern == 'COPAY_PLUS_DEDUCTIBLE_COINSURANCE':
                component = {term.get('copay_component_price_cents') for term in terms}
                if len(component) != 1 or type(next(iter(component))) is not int or not 0 <= next(iter(component)) <= price_total:
                    output['remaining_actions'].append(f'Identify distinct copay and deductible portions for {name}.')
                    continue
                deductible_base -= next(iter(component))
            if pattern in {'DEDUCTIBLE_PLUS_COINSURANCE', 'DEDUCTIBLE_THEN_COPAY',
                           'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'}:
                remaining = max(0, next(iter(deductible_values)) - deductible_used.get(key, 0))
                deductible = min(deductible_base, remaining)
                deductible_used[key] = deductible_used.get(key, 0) + deductible
            else:
                deductible = 0
            percent = next(iter(percentages))
            if pattern == 'COPAY_PLUS_COINSURANCE':
                bases_for_percent = {term.get('coinsurance_base') for term in terms}
                if bases_for_percent not in ({'FULL_PRICE'}, {'AFTER_COPAY'}):
                    output['remaining_actions'].append(f'Confirm the coinsurance base for {name}.')
                    continue
                percent_base = price_total if bases_for_percent == {'FULL_PRICE'} else max(0, price_total - next(iter(copays)))
            else:
                percent_base = deductible_base - deductible
            coinsurance = (int((Decimal(percent_base) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                           if pattern != 'DEDUCTIBLE_THEN_COPAY' else 0)
            if pattern == 'DEDUCTIBLE_THEN_COPAY':
                if {term.get('copay_after_deductible') for term in terms} != {True}:
                    output['remaining_actions'].append(f'Confirm the post-deductible copay rule for {name}.')
                    continue
                copay_component = min(next(iter(copays)), max(0, price_total - deductible))
            elif pattern in {'COPAY_PLUS_COINSURANCE', 'COPAY_PLUS_DEDUCTIBLE_COINSURANCE'}:
                copay_component = next(iter(copays))
            else:
                copay_component = 0
            patient = min(price_total, deductible + coinsurance + copay_component)
            group_result.update(deductible_cents=deductible, coinsurance_cents=coinsurance,
                                copay_cents=copay_component,
                                coinsurance_percent=str(percent) if percent is not None else None)
        oop_values = {(term.get('oop_id') or 'visit', term.get('oop_remaining_cents')) for term in terms}
        if len(oop_values) != 1:
            output['remaining_actions'].append(f'Reconcile out-of-pocket applicability for {name}.')
            continue
        oop_pairs.update(oop_values)
        group_result['estimated_patient_share_cents'] = patient
        total += patient
    output['reference_schedule_versions'] = sorted({str(item['rate_version']) for group in output['service_groups']
        for item in group['price_lines'] if item['pricing_basis'] == 'BCBSTX_CROSS_PAYER_REFERENCE'
        and item['rate_version']})
    priced = [item for group in output['service_groups'] for item in group['price_lines']]
    if priced and all(type(item['extended_cents']) is int for item in priced):
        output['selected_price_total_cents'] = sum(item['extended_cents'] for item in priced)
        if all(item['pricing_basis'] == 'BCBSTX_CROSS_PAYER_REFERENCE' for item in priced):
            output['reference_total_cents'] = output['selected_price_total_cents']
    if any(group['estimated_patient_share_cents'] is None for group in output['service_groups']):
        if (output['selected_price_total_cents'] is not None
                and all((line.get('benefits') or {}).get('active') is True
                        and (line.get('benefits') or {}).get('selected_benefit_ids')
                        and not (line.get('benefits') or {}).get('source_errors') for line in lines)
                and (verified_in_network(benefits) or verified_out_of_network(benefits))):
            upper = sum(group['estimated_patient_share_cents'] if type(group['estimated_patient_share_cents']) is int
                        else sum(row['extended_cents'] for row in group['price_lines'])
                        for group in output['service_groups'])
            oop_values = {((line.get('benefits') or {}).get('oop_id'),
                           (line.get('benefits') or {}).get('oop_remaining_cents')) for line in lines}
            if len(oop_values) == 1:
                _, oop_remaining = next(iter(oop_values))
                if type(oop_remaining) is int and oop_remaining >= 0:
                    upper = min(upper, oop_remaining)
            output.update(estimated_low_cents=0, estimated_high_cents=upper,
                          estimate_assumptions=[
                              'Conditional planning range uses selected covered-service prices; '
                              'payer allowance and benefit interaction still require verification.'])
        return output
    oop_verified = False
    if len(oop_pairs) == 1:
        _, oop = next(iter(oop_pairs))
        if type(oop) is int and oop >= 0:
            oop_verified = True
            remaining = oop
            for group in output['service_groups']:
                before = group['estimated_patient_share_cents']
                group['estimated_patient_share_cents'] = min(before, remaining)
                group['oop_adjustment_cents'] = before - group['estimated_patient_share_cents']
                remaining -= group['estimated_patient_share_cents']
            total = sum(group['estimated_patient_share_cents'] for group in output['service_groups'])
    elif any(value is not None for _, value in oop_pairs):
        output['remaining_actions'].append('Reconcile distinct out-of-pocket accumulators.')
        return output
    if not oop_verified and benefits.get('family_oop_zero_applies') is True and benefits.get('family_oop_remaining_cents') == 0:
        oop_verified = True
        for group in output['service_groups']:
            group['oop_adjustment_cents'] = group['estimated_patient_share_cents']
            group['estimated_patient_share_cents'] = 0
        total = 0
    output['estimated_patient_responsibility_cents'] = total
    output['estimated_low_cents'] = total if oop_verified else 0
    output['estimated_high_cents'] = total
    if not oop_verified:
        output['remaining_actions'].append('Verify the applicable remaining out-of-pocket balance.')
    output['pricing_basis'] = next(iter(bases)) if len(bases) == 1 else 'MIXED' if bases else 'COPAY_ONLY'
    if (benefits.get('qmb_status') == 'protected'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'):
        output.update(collect_now_cents=0, collection_decision_type='PROHIBITED_COLLECTION')
        return output
    if total == 0 and (oop_verified or all(group['interaction_rule'] in {
            'COPAY_ONLY', 'NO_PATIENT_COST_SHARE'} for group in output['service_groups'])):
        output.update(collect_now_cents=0, collection_decision_type='DOCUMENTED_ZERO')
        return output
    configured = result.get('visit_collection_policies') or []
    matching = [item for item in configured if isinstance(item, dict)
                and item.get('product_binding') == result.get('payer_product_binding')
                and item.get('network_tier') == benefits.get('network_status')
                and set(item.get('service_groups') or []) == set(grouped)
                and str(item.get('effective_from') or '') <= str(result.get('service_date') or '')
                <= str(item.get('effective_to') or '')]
    if len(matching) > 1:
        output['collection_decision_type'] = 'POLICY_CONFLICT'
        output['remaining_actions'].append('Resolve overlapping approved NMCT visit policies.')
        return output
    policy = matching[0] if matching else result.get('collection_policy') or {}
    approved = (isinstance(policy, dict) and policy.get('approved_by') and policy.get('approved_at')
                and policy.get('reference'))
    if not approved:
        output['collection_decision_type'] = 'POLICY_APPROVAL_REQUIRED'
        output['remaining_actions'].append('Approve an NMCT policy for this combined visit amount.')
        return output
    if policy.get('mode') == 'defer_to_claim':
        output.update(collect_now_cents=0, collection_decision_type='DEFERRED_BILLING')
    elif (policy.get('mode') == 'collect_visit_model' and policy.get('version')
          and policy.get('disclosure')
          and policy.get('product_binding') == result.get('payer_product_binding')
          and bool(result.get('payer_product_binding'))
          and policy.get('network_tier') == benefits.get('network_status')
          and set(policy.get('service_groups') or []) == set(grouped)
          and (oop_verified or policy.get('allow_unknown_oop') is True)
          and str(policy.get('effective_from') or '') <= str(result.get('service_date') or '')
          <= str(policy.get('effective_to') or '')
          and (output['pricing_basis'] != 'BCBSTX_CROSS_PAYER_REFERENCE'
               or policy.get('allow_benchmark_advance') is True)
          and (verified_in_network(benefits) or verified_out_of_network(benefits))
          and patient_benefits_bound(benefits.get('identity') or result.get('identity') or {})
          and not benefits.get('has_secondary') and not benefits.get('network_conflict')
          and not benefits.get('coverage_status_conflict') and not benefits.get('source_errors')
          and not benefits.get('historical_only') and not result.get('refresh_requires_selection')
          and benefits.get('qmb_status') != 'protected'
          and (result.get('billing_protection') or {}).get('status') != 'qmb'
          and requirement_blocker(result) is None
          and not (result.get('service_selection_status') == 'preview'
                   and any(name != 'specialist_office' for name in grouped))):
        if any(row.get('code') in (policy.get('excluded_cpts') or []) for row in lines):
            output['collection_decision_type'] = 'POLICY_EXCLUSION'
            output['remaining_actions'].append('Selected CPT is excluded from this collection policy.')
            return output
        payment_review = (review if review is not None else result.get('visit_review') or {}).get('payments') or {}
        payments = payment_review.get('amount_cents') if payment_review.get('reference') else None
        if type(payments) is int and payments >= 0:
            cap = policy.get('max_cents')
            if cap is None or type(cap) is int and cap >= 0:
                formula = total
                if type(policy.get('fixed_cents')) is int:
                    formula = min(total, policy['fixed_cents'])
                elif _percent(policy.get('advance_percent')) is not None:
                    formula = min(total, int((Decimal(total) * _percent(policy['advance_percent']) / 100)
                                             .quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
                output.update(collect_now_cents=max(0, min(formula, cap if cap is not None else formula) - payments),
                              collection_decision_type='APPROVED_VISIT_ADVANCE',
                              collection_policy_id=policy.get('id'), collection_policy_version=policy['version'],
                              collection_disclosure=policy['disclosure'])
    if output['collect_now_cents'] is None:
        output['collection_decision_type'] = 'POLICY_OR_EVIDENCE_REQUIRED'
        output['remaining_actions'].append('Verify policy scope, selected services, network and prior payments.')
    return output
