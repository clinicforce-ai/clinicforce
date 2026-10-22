"""Price selected visit lines from explicit payer and clinic evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from app.insurance import commercial, cpt_rules, identity
from app.insurance import published_plan_benefits
from app.insurance.network_participation import verified_out_of_network


def _workflow_condition(reason: str) -> bool:
    return 'authorization' in reason.lower() or 'referral' in reason.lower()


def service_type(code: str, rules: dict) -> str | None:
    configured = (rules.get('service_type_by_code') or {}).get(code)
    if configured:
        return str(configured)
    return cpt_rules.service_definition(code)['service_type']



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
        stc = service_type(code['code'], rules)
        clinical_role = cpt_rules.service_definition(code['code'])['role']
        medical_line = clinical_role in {'drug', 'administration'} or cpt_rules.service_definition(code['code'])['family'] == 'chemodenervation'
        selection_valid = review_valid(selection, service_date, units)
        invalid_category = False
        if medical_line and selection_valid:
            category = str(selection.get('service_type_code') or '')
            options = {row['id']: row for row in commercial.reported_stedi_options(payload or {}, category)}
            picked = [options.get(key) for key in selection.get('selected_ids') or []]
            rows_match = bool(picked) and all(row and category in
                str(row.get('service_type_code') or '').replace(' ', '').split(',')
                and row.get('procedure_code') in {None, '', code['code']} for row in picked)
            exact = rows_match and all(row.get('procedure_code') == code['code'] for row in picked)
            invalid_category = not (rows_match and cpt_rules.benefit_category_compatible(
                code['code'], category, procedure_specific=exact))
            selection_valid = not invalid_category
        if medical_line and selection_valid:
            stc = selection.get('service_type_code') or stc
        raw_benefits = commercial.normalize_stedi_271(payload, stc or ('30' if medical_line else '98'), benefits.get('network_status') == 'in_network',
            benefits.get('has_secondary', False), procedure_code=code['code'], service_date=service_date,
            provider_out_of_network_verified=verified_out_of_network(benefits),
            selected_ids=selection.get('selected_ids') if selection_valid else None,
            office_service_types=rules.get('office_service_types'), place_of_service=rules.get('place_of_service'),
            visit_context=rules.get('visit_context'), provider_npi=rules.get('provider_npi')) if payload is not None else deepcopy(benefits)
        if benefits.get('network_status') in {'in_network', 'out_of_network'} and benefits.get('network_source'):
            raw_benefits['network_source'] = benefits['network_source']
            raw_benefits['network_evidence_ids'] = benefits.get('network_evidence_ids', [])
        raw_benefits = published_plan_benefits.apply_office_terms(
            raw_benefits, benefits, rules, service_date, code['code'])
        stc = (raw_benefits.get('resolved_service_type') or stc) if stc else None
        # Name differences remain on the case for billing. They do not stop
        # arithmetic for an explicitly conditional estimate of this policy.
        integrity = [reason for reason in benefits.get('review_reasons', []) if 'evidence' in reason or ('identity' in reason and not name_difference)]
        if name_difference:
            raw_benefits['review_reasons'] = [reason for reason in raw_benefits.get('review_reasons', []) if 'identity' not in reason]
        direct = any(commercial._procedure(row) == code['code'] and row.get('code') in {'A', 'B'}
                     for row in commercial._stedi_entries(payload or {}))
        if medical_line:
            raw_benefits['authorization_signal'] = 'unknown'
        raw_benefits['review_reasons'] = list(dict.fromkeys([*raw_benefits.get('review_reasons', []), *integrity]))
        raw_benefits['requires_review'] = bool(raw_benefits['review_reasons'])
        applied = resolve_requirements(raw_benefits, review, service_date, units)
        line_rules = dict(rules)
        if applied.get('cost_share'):
            line_rules['cost_share'] = applied['cost_share']
        if selection_valid:
            line_rules['cost_share'] = selection['cost_share']
            applied['cost_share'] = selection['cost_share']
            applied['deductible_applies'] = selection['deductible_applies']
            applied['deductible_applicability'] = ('DOES_NOT_APPLY' if not selection['deductible_applies'] else
                'ALREADY_MET' if applied.get('deductible_remaining_cents') == 0 else 'APPLIES')
            if not selection['deductible_applies']:
                applied['deductible_remaining_cents'] = 0
                applied['review_reasons'] = [reason for reason in applied.get('review_reasons', []) if 'remaining individual deductible' not in reason]
                applied['requires_review'] = bool(applied['review_reasons'])
        out_of_network = benefits.get('network_status') == 'out_of_network'
        matches = [rate for rate in rates if rate['code'] == code['code']
                   and (rate.get('modifier') or '') == ','.join(code.get('modifiers') or [])
                   and (not rate.get('line_id') or rate['line_id'] == line_id)
                   and (rate.get('line_id') or rate.get('basis') not in
                        {'payer_out_of_network', 'cms_original_medicare_estimate'})
                   and (rate.get('units') is None or rate['units'] == units)
                   and (not rate.get('product_binding') or rate['product_binding'] == rules.get('product_binding'))
                   and (not rate.get('cms_locality') or rate['cms_locality'] == rules.get('cms_locality'))
                   and (not rate.get('cms_qp_status') or rate['cms_qp_status'] == rules.get('cms_qp_status'))
                   and (not out_of_network or
                        rate.get('basis') in {'payer_out_of_network', 'cms_original_medicare_estimate'}
                        and rate.get('id') and rate.get('source_reference')
                        and (rate.get('verified_at') if rate.get('basis') == 'payer_out_of_network'
                             else rate.get('source_sha256') and rate.get('effective_from') <= service_date <= rate.get('effective_to'))
                        and rate.get('service_date') == service_date
                        and rate.get('provider_npi') == rules.get('provider_npi') and rules.get('provider_npi')
                        and rate.get('place_of_service') == rules.get('place_of_service') and rules.get('place_of_service')
                        and rate.get('patient_binding') == rules.get('patient_binding') and rules.get('patient_binding'))]
        rate = matches[0] if len(matches) == 1 else {}
        rate_status = ('RATE_CONFLICT' if len(matches) > 1 else 'RESOLVED'
                       if rate.get('allowed_cents') is not None else 'RATE_UNAVAILABLE')
        line = {**code, 'id': line_id, 'units': units, 'benefits': applied,
                'unit_cash_cents': rate.get('cash_cents'),
                'cash_cents': rate['cash_cents'] * units if rate.get('cash_cents') is not None and units else None,
                'cash_source': rate.get('source_reference') if rate.get('cash_cents') is not None else None,
                'unit_allowed_cents': rate.get('allowed_cents'),
                'allowed_cents': rate['allowed_cents'] * units if rate.get('allowed_cents') is not None and units else None,
                'allowed_basis': rate.get('basis') or 'clinic_contract',
                'rate_source': rate.get('source_reference') or ('Payer out-of-network allowance needed' if out_of_network else 'Clinic contract'), 'rate_version': rate.get('version_id'),
                'rate_source_sha256': rate.get('source_sha256'),
                'cms_locality': rate.get('cms_locality'), 'cms_qp_status': rate.get('cms_qp_status'),
                'rate_row_number': rate.get('row_number'),
                'rate_effective_from': rate.get('effective_from'),
                'rate_resolution': {'status': rate_status,
                                    'matched_candidate_count': len(matches),
                                    'source_checked': 'payer_out_of_network_rate_candidates' if out_of_network else 'scoped_active_contract_rate_candidates',
                                    'provider_network_verified': benefits.get('network_status') in {'in_network', 'out_of_network'}},
                'benefit_source': '2026 OPM PSHB brochure' if applied.get('published_plan_rule') else
                                  benefits.get('benefit_source') or (payload or {}).get('benefit_source') or ('Stedi' if payload is not None else None),
                'benefit_verification': selection if selection_valid else None,
                'benefit_basis': applied.get('benefit_label') or f'Service category {stc}' if stc else 'Service category needs mapping',
                'conditions': [],
                'issues': [reason for reason in applied.get('review_reasons', []) if not _workflow_condition(reason)], 'calculation': None}
        if name_difference:
            line['conditions'].append('Estimated using the returned policy; patient names differ between eCW and the payer.')
        if direct:
            line['benefit_basis'] = 'Procedure-specific payer benefit'
        if medical_line and not direct and not selection_valid:
            line['issues'].append('Verify a medical-benefit cost-share row for this exact drug or procedure; office-visit terms do not apply.')
        if invalid_category:
            line['issues'].append('The selected payer benefit category or source row does not apply to this drug or procedure.')
        if not stc and not direct and payload is not None:
            line['issues'].append('Map this service to a payer-supported benefit category in the clinic estimate profile.')
        if code.get('needs_confirmation') and not code.get('confirmed'):
            line['issues'].append('This CPT appears in multiple eCW records. Confirm which represent separate planned services.')
        if not units:
            line['issues'].append('Confirm the number of units for this service.')
        if rules.get('provider_npi_conflict'):
            line['issues'].append('Resolve the conflicting eCW rendering provider NPI before using this price.')
        if line['allowed_cents'] is None:
            line['issues'].append('Get the payer allowed amount for this out-of-network CPT and visit.' if out_of_network else
                                  'Add an active clinic contract rate matching this code, modifier and visit.')
        if benefits.get('network_status') == 'unknown':
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
            if any(deductible_used.values()) and deductible_key not in deductible_used and applied.get('deductible_remaining_cents') and applied.get('deductible_applies') is not False:
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
                if out_of_network:
                    line_rules['allowed_basis'] = rate['basis']
                calc = commercial.estimate([{'code': code['code'], 'units': units,
                    'allowed_cents': rate['allowed_cents'], 'rate_id': rate.get('id'),
                    'allowed_source': rate.get('basis')}], calculation_benefits, line_rules)
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
                                    'reason': 'Use this conditional estimate for planning. Resolve the benefit conditions before collecting.'}
    result['scenarios'] = [{'code': line['code'], 'allowed_cents': line['allowed_cents'], **(line['calculation'] or {})} for line in lines]
    if service_issues:
        result['collection'] = {'action': 'complete_services', 'amount_cents': None, 'reason': service_issues[0]}
    return result
