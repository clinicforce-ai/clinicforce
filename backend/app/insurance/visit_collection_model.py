"""Deterministic visit-level planning and policy-gated check-in collection."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance import cpt_rules
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network, verified_out_of_network
from app.insurance.collection.requirements import requirement_blocker

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


def _reference_for(line: dict, references: list[dict]) -> dict | None:
    matching = [row for row in references if row.get('id') == line.get('id')
                and row.get('code') == line.get('code')
                and row.get('units') == line.get('units')
                and (row.get('modifiers') or []) == (line.get('modifiers') or [])
                and row.get('reference_basis') == 'BCBSTX_CROSS_PAYER_REFERENCE'
                and row.get('rate_version')
                and str(row.get('source_reference') or '').startswith('library:')
                and type(row.get('unit_reference_cents')) is int and row['unit_reference_cents'] >= 0]
    return matching[0] if len(matching) == 1 else None


def _interaction(terms: dict) -> str:
    explicit = terms.get('interaction_rule')
    if explicit in {'COPAY_ONLY', 'DEDUCTIBLE_PLUS_COINSURANCE', 'NO_PATIENT_COST_SHARE'}:
        return explicit
    copay = terms.get('copay_cents')
    coinsurance = _percent(terms.get('coinsurance_percent'))
    deductible = terms.get('deductible_remaining_cents')
    if type(terms.get('oop_remaining_cents')) is int and terms['oop_remaining_cents'] == 0:
        return 'NO_PATIENT_COST_SHARE'
    if type(copay) is int and copay >= 0 and coinsurance in {None, Decimal(0)}:
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
              'cpt_source': result.get('service_selection_status'),
              'network_evidence': result.get('network_evidence'),
              'service_groups': [], 'remaining_actions': [],
              'research': result.get('insurance_research')}
    if result.get('payment_mode') == 'cash' or price.get('mode') != 'services' or not lines or price.get('service_issues'):
        output['remaining_actions'].append('Select one complete insured service plan.')
        return output
    benefits = result.get('benefits') or {}
    if (benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or benefits.get('coverage_dates_invalid') or benefits.get('coverage_status_conflict')
            or not patient_benefits_bound(benefits.get('identity') or result.get('identity') or {})):
        output['remaining_actions'].append('Verify this patient, policy and visit-date coverage.')
        return output
    references = (result.get('temporary_rate_reference') or {}).get('lines') or []
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
                        'benefit_evidence': list(dict.fromkeys(
                            key for term in terms for key in term.get('selected_benefit_ids') or [])),
                        'price_lines': []}
        output['service_groups'].append(group_result)
        if (len(patterns) != 1 or 'UNKNOWN_INTERACTION' in patterns
                or any(not term.get('selected_benefit_ids') for term in terms)
                or any(term.get('active') is not True or term.get('source_errors') or term.get('coverage_status_conflict')
                       or term.get('service_date_matches') is False for term in terms)):
            output['remaining_actions'].append(f'Confirm the cost-sharing interaction for {name}.')
            continue
        amounts = []
        for line in members:
            exact = (type(line.get('allowed_cents')) is int
                     and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                     and line.get('allowed_basis') in {'clinic_contract', 'payer_out_of_network'})
            reference = _reference_for(line, references) if not exact else None
            value = line['allowed_cents'] if exact else (
                reference['unit_reference_cents'] * line['units'] if reference and type(line.get('units')) is int else None)
            basis = line.get('allowed_basis') if exact else 'BCBSTX_CROSS_PAYER_REFERENCE' if reference else None
            group_result['price_lines'].append({'code': line.get('code'), 'units': line.get('units'),
                'extended_cents': value, 'pricing_basis': basis,
                'source': line.get('rate_source') if exact else reference.get('source_reference') if reference else None,
                'rate_version': line.get('rate_version') if exact else reference.get('rate_version') if reference else None})
            amounts.append(value)
            if basis and group_result['interaction_rule'] == 'DEDUCTIBLE_PLUS_COINSURANCE':
                bases.add(basis)
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
        else:
            if any(value is None for value in amounts):
                output['remaining_actions'].append(f'Acquire a matching CPT reference price for {name}.')
                continue
            deductible_values = {term.get('deductible_remaining_cents') for term in terms}
            percentages = {_percent(term.get('coinsurance_percent')) for term in terms}
            keys = {term.get('deductible_id') or 'visit' for term in terms}
            if len(deductible_values) != 1 or len(percentages) != 1 or len(keys) != 1 or None in percentages:
                output['remaining_actions'].append(f'Reconcile deductible and coinsurance evidence for {name}.')
                continue
            key = next(iter(keys))
            remaining = max(0, next(iter(deductible_values)) - deductible_used.get(key, 0))
            deductible = min(sum(amounts), remaining)
            deductible_used[key] = deductible_used.get(key, 0) + deductible
            coinsurance = int((Decimal(sum(amounts) - deductible) * next(iter(percentages)) / 100)
                              .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            patient = deductible + coinsurance
            group_result.update(deductible_cents=deductible, coinsurance_cents=coinsurance,
                                coinsurance_percent=str(next(iter(percentages))))
        oop_values = {(term.get('oop_id') or 'visit', term.get('oop_remaining_cents')) for term in terms}
        if len(oop_values) != 1:
            output['remaining_actions'].append(f'Reconcile out-of-pocket applicability for {name}.')
            continue
        oop_pairs.update(oop_values)
        group_result['estimated_patient_share_cents'] = patient
        total += patient
    if any(group['estimated_patient_share_cents'] is None for group in output['service_groups']):
        return output
    output['reference_schedule_versions'] = sorted({str(item['rate_version']) for group in output['service_groups']
        for item in group['price_lines'] if item['pricing_basis'] == 'BCBSTX_CROSS_PAYER_REFERENCE'
        and item['rate_version']})
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
    output['estimated_patient_responsibility_cents'] = total
    output['estimated_low_cents'] = total if oop_verified else 0
    output['estimated_high_cents'] = total
    if not oop_verified:
        output['remaining_actions'].append('Verify the applicable remaining out-of-pocket balance.')
    output['pricing_basis'] = next(iter(bases)) if len(bases) == 1 else 'MIXED' if bases else 'COPAY_ONLY'
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
          and policy.get('product_binding') == result.get('payer_product_binding')
          and bool(result.get('payer_product_binding'))
          and policy.get('network_tier') == benefits.get('network_status')
          and set(policy.get('service_groups') or []) == set(grouped)
          and oop_verified
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
        payment_review = (review or {}).get('payments') or {}
        payments = payment_review.get('amount_cents') if payment_review.get('reference') else (
            result.get('arrival_collection') or {}).get('payments_received_cents')
        if type(payments) is int and payments >= 0:
            cap = policy.get('max_cents')
            if cap is None or type(cap) is int and cap >= 0:
                output.update(collect_now_cents=max(0, min(total, cap if cap is not None else total) - payments),
                              collection_decision_type='APPROVED_VISIT_ADVANCE',
                              collection_policy_id=policy.get('id'), collection_policy_version=policy['version'])
    if output['collect_now_cents'] is None:
        output['collection_decision_type'] = 'POLICY_OR_EVIDENCE_REQUIRED'
        output['remaining_actions'].append('Verify policy scope, selected services, network and prior payments.')
    return output
