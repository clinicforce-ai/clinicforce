"""One plain sentence for the doctor's collection amount and its evidence basis."""
from __future__ import annotations

from app.insurance import visit_estimate
from app.insurance.identity import patient_benefits_bound
from app.insurance.benefit_review import selected_service_blocker, selected_service_detail
from app.insurance.network_participation import participation_conflict, verified_in_network, verified_out_of_network
from app.insurance.collection.enrollment_actions import ENROLLMENT_REASON_ACTION, ENROLLMENT_DEFAULT
from app.insurance.collection.amount_actions import _collection_action, _payer_label
from app.insurance.collection.pricing_actions import priced_line_issue, priced_payer_gap
from app.insurance.collection.requirements import requirement_blocker
from app.insurance.library_network_exclusions import product_kind
from app.insurance.insurance_product import is_medicare_advantage


def _money(cents: int) -> str:
    return f'${cents // 100:,}.{cents % 100:02d}'


def _choice_plus_reported_terms(benefits: dict) -> str | None:
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('service_type_code') == '98' and row.get('network') in {'Y', 'N'}]
    terms = {(kind, tier): {row.get(field) for row in rows
                            if row.get('kind') == kind and row.get('network') == tier}
             for kind, field in (('copay', 'amount_cents'), ('coinsurance', 'percent'))
             for tier in ('Y', 'N')}
    if (terms['copay', 'Y'] == terms['copay', 'N'] == {0}
            and len(terms['coinsurance', 'Y']) == len(terms['coinsurance', 'N']) == 1):
        in_rate = next(iter(terms['coinsurance', 'Y']))
        out_rate = next(iter(terms['coinsurance', 'N']))
        if in_rate is not None and out_rate is not None:
            return (f'The payer reported a $0.00 professional office copay, '
                    f'{in_rate}% in-network and {out_rate}% out-of-network coinsurance. ')
    return None


def _cash_cause(result: dict, failure: dict, recovery: dict, card: dict, network: str,
                benefits: dict, unpriced: list[str], missing: str,
                source_errors: list[dict] | None = None) -> tuple[str, str]:
    reason = recovery.get('reason_code')
    identity = result.get('identity') or benefits.get('identity') or {}
    active_benefits = benefits.get('active') is True and benefits.get('service_date_matches') is not False
    if reason == 'same_policy_evidence_conflict':
        return 'the same medical policy returned conflicting subscriber, date or office benefit evidence', 'same_policy_evidence_conflict'
    if not active_benefits and (failure.get('kind') == 'provider_enrollment' or reason in {'provider_enrollment_missing', 'enrollment_required'}):
        return ENROLLMENT_REASON_ACTION.get(failure.get('reason_code'), ENROLLMENT_DEFAULT)[0], 'provider_enrollment'
    if reason == 'multiple_active_policies' or benefits.get('has_secondary'):
        count = recovery.get('active_policy_count')
        if type(count) is not int:
            count = sum(row.get('active_medical') is True for row in recovery.get('candidates') or [])
        count_label = f'{count} active policies' if type(count) is int and count > 1 else 'more than one active policy'
        coordination = result.get('coordination') or {}
        if coordination.get('status') == 'primacy_reported':
            return (f'the insurance search returned {count_label} but the payer-reported roles '
                    'are not yet tied to this patient and all active policies'), 'multiple_active_policies'
        if coordination.get('status') == 'primacy_conflict':
            return (f'the insurance search returned {count_label} and UHC COB reported conflicting '
                    'medical payer roles'), 'multiple_active_policies'
        return (f'the insurance search returned {count_label} '
                'and did not identify which pays first'), 'multiple_active_policies'
    if reason in {'identity_or_coverage_review', 'subscriber_relationship_conflict', 'returned_identity_or_coverage_mismatch'}:
        return 'the returned policy did not match the patient and subscriber details for this visit', 'policy_identity_mismatch'
    if identity.get('missing_fields') and not (identity.get('differences') or identity.get('policy_differences')):
        return 'the payer and eCW records lack enough matching identifiers to bind these benefits to this patient', 'payer_identity_match_missing'
    if identity.get('status') == 'review':
        return 'the payer response did not match the patient and policyholder details in eCW', 'policy_identity_mismatch'
    if benefits.get('active') is True and not patient_benefits_bound(identity):
        return 'the saved payer response has no retained patient identity match for this visit', 'payer_identity_match_missing'
    lines = (result.get('price_range') or {}).get('lines') or []
    if benefits.get('coverage_status_conflict'):
        return 'the payer returned both active and inactive or noncovered status for the planned service', 'coverage_status_conflict'
    if benefits.get('coverage_dates_invalid'):
        return 'the payer returned a coverage date that cannot be interpreted for this visit', 'coverage_date_invalid'
    if reason in {'no_coverage_found', 'upstream_no_policies'} and not active_benefits:
        return 'the completed insurance search returned no policy for this patient', 'no_policy_returned'
    line_blocker = selected_service_blocker(network, identity, lines)
    if line_blocker:
        return selected_service_detail(line_blocker)[0], line_blocker[0]
    if participation_conflict(benefits):
        return 'provider participation evidence conflicts for this plan and provider', 'provider_network_conflict'
    if result.get('service_selection_status') == 'preview':
        return 'the planned CPTs have not been approved by the doctor for this visit', 'planned_cpt_unselected'
    exclusion = result.get('network_evidence') or {}
    if network == 'unknown' and (exclusion.get('status') == 'excluded_under_available_agreement'
                                 or exclusion.get('decision') == 'excluded_under_available_agreement'):
        product = ('Regional' if exclusion.get('product_kind') == 'regional_medicare_advantage_ppo' else 'Local')
        medical_names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
        named = (f'the medical plan {medical_names[0]} is a UHC {product} Medicare Advantage PPO product'
                 if len(medical_names) == 1 else f'the UHC {product} Medicare Advantage PPO product')
        return (f'{named} and is excluded from NMCT’s uploaded agreement, leaving this provider’s network tier and applicable cost-sharing basis unresolved'
                if len(medical_names) == 1 else
                f'{named} is excluded from NMCT’s uploaded agreement, leaving this provider’s network tier and applicable cost-sharing basis unresolved',
                'clinic_agreement_exclusion')
    if card.get('status') == 'conflict' and recovery.get('outcome') != 'recovered':
        field = 'member ID' if 'member_id' in card.get('conflicts', []) else 'group number' if 'group_number' in card.get('conflicts', []) else 'insurer'
        return f'the {field} on the card differs from eCW', 'card_conflict'
    if benefits.get('active') is False:
        return 'the payer reported no active coverage for this visit', 'inactive_coverage'
    if not active_benefits and failure.get('codes') and set(failure['codes']) & {'65', '67', '71', '72', '73', '75', '76'}:
        return 'the payer rejected the submitted member details', 'member_identity_unverified'
    if source_errors:
        if any(error.get('kind') == 'saved_raw_unavailable' for error in source_errors):
            return 'the protected saved payer response could not be reopened', 'saved_payer_response_unavailable'
        if any(error.get('kind') == 'invalid_calculation' for error in source_errors):
            return 'the calculated visit amount is invalid', 'financial_source_error'
        return 'the payer benefit response contains an invalid financial field', 'financial_source_error'
    issues = result.get('issues') or []
    if any('configuration timestamp for this saved estimate could not be checked' in str(issue).lower()
           for issue in issues):
        return 'the saved clinic pricing timestamp is invalid', 'saved_clinic_pricing_changed'
    if any('clinic plan or provider configuration changed since this saved estimate' in str(issue).lower()
           for issue in issues):
        return 'the clinic plan or provider configuration changed since this estimate', 'saved_clinic_pricing_changed'
    if any('saved estimate is no longer' in str(issue).lower() or 'changed since this saved estimate' in str(issue).lower()
           or 'saved estimate no longer matches current configuration' in str(issue).lower()
           for issue in issues):
        return 'the clinic contract or CPT rate changed since this saved estimate was calculated', 'saved_clinic_pricing_changed'
    if network != 'out_of_network' and 'The clinic fee schedule is not matched to the exact payer product.' in issues:
        return 'the clinic fee schedule is not matched to the exact payer product', 'clinic_product_mismatch'
    if network != 'out_of_network' and "Clinic pricing: this payer's clinic estimator connection is still a draft." in issues:
        return "the clinic's payer pricing connection is still a draft", 'clinic_payer_connection_draft'
    if network != 'out_of_network' and 'Clinic pricing: no single approved plan and provider contract is linked to this Stedi payer.' in issues:
        return 'clinic pricing is not linked to this plan and provider', 'clinic_contract_missing'
    if network != 'out_of_network' and benefits.get('network_source') == 'clinic_library_absent':
        return 'the Clinical Library contract is not linked to this plan and provider', 'clinic_contract_missing'
    if (result.get('price_range') or {}).get('mode') == 'alternatives':
        return 'the office visit still has multiple possible E/M CPT levels and no clinician-selected code', 'planned_cpt_unselected'
    if (benefits.get('active') is True
            and not (result.get('service_plan') or {}).get('selected_codes')
            and not (result.get('service_plan') or {}).get('alternatives')
            and not (result.get('price_range') or {}).get('lines') and not result.get('codes')):
        return 'no billable CPT is documented for this visit', 'planned_cpt_missing'
    if (result.get('price_range') or {}).get('service_issues'):
        if result.get('botox_review') is not None:
            return (str(result['price_range']['service_issues'][0]), 'botox_clinical_review_required')
        return 'the selected CPTs do not form a billable service combination', 'service_selection_invalid'
    if benefits.get('active') is True and network == 'unknown':
        return 'the exact plan and provider have no current clinic network link for this visit', 'provider_network_unresolved'
    medical_names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    if (is_medicare_advantage(medical_names)
            and (result.get('price_range') or {}).get('complete')
            and (result.get('collection_guidance') or {}).get('amount_cents') is None):
        protection = result.get('billing_protection') or {}
        if benefits.get('qmb_status') == 'protected' or protection.get('status') == 'qmb':
            return 'QMB protection prohibits collecting Medicare cost sharing from this member', 'qmb_protected'
        if protection.get('status') != 'not_qmb':
            return 'Medicare Advantage QMB cost-sharing protection has not been ruled out for this member', 'qmb_status_unresolved'
    if network == 'out_of_network':
        if lines and all(line.get('allowed_cents') is not None for line in lines):
            return ('the selected CPT prices are available but the payer did not establish all '
                    'out-of-network cost-sharing terms needed for an advance',
                    'cost_share_term_unresolved')
        return 'the plan did not give an allowed price for out-of-network care', 'out_of_network_allowance_missing'
    if unpriced and benefits.get('active') is True and network == 'in_network':
        lines = [line for line in (result.get('price_range') or {}).get('lines') or []
                 if line.get('calculation') is None]
        if any((line.get('rate_resolution') or {}).get('status') == 'RATE_CONFLICT' for line in lines):
            return f'more than one clinic contract rate matches {missing}', 'contract_rate_conflict'
        if any((line.get('rate_resolution') or {}).get('status') == 'RATE_UNAVAILABLE'
               or line.get('allowed_cents') is None for line in lines):
            return f'the clinic has no applicable contracted rate for {missing}', 'contract_rate_missing'
    payer_gap = priced_payer_gap(result, _payer_label(result, 'the payer'))
    if payer_gap:
        return payer_gap[:2]
    line_issue = priced_line_issue(result)
    if line_issue:
        return f'{line_issue[0]} has a recorded pricing issue', 'service_pricing_issue'
    requirement = requirement_blocker(result)
    if requirement:
        return requirement[:2]
    if recovery.get('state') == 'indeterminate' or (result.get('previsit') or {}).get('request_state') == 'pending':
        return 'the payer check has not finished', 'check_pending'
    return 'the estimate did not produce a collection calculation for the selected services', 'calculation_unavailable'


def fallback_cause(result: dict) -> str:
    """One concrete reason shared by cash guidance and the headline sentence."""
    lines = (result.get('price_range') or {}).get('lines') or []
    unpriced = [str(line.get('code')) for line in lines if line.get('calculation') is None and line.get('code')]
    groups = {visit_estimate.clinical_group(code) for code in unpriced}
    missing = ('nerve testing' if 'nerve' in groups or 'muscle' in groups else
               'other planned care' if len(unpriced) != 1 else f'CPT {unpriced[0]}')
    benefits = result.get('benefits') or {}
    network = benefits.get('network_status') or 'unknown'
    if ((network == 'in_network' and not verified_in_network(benefits)) or
            (network == 'out_of_network' and not verified_out_of_network(benefits))):
        network = 'unknown'
    return _cash_cause(result, result.get('eligibility_failure') or {}, result.get('recovery') or {},
                       result.get('card_check') or {}, network, benefits, unpriced, missing)[0]


def build(result: dict, guidance: dict, review: dict | None = None) -> dict:
    price = result.get('price_range') or {}
    benefits = result.get('benefits') or {}
    lines = price.get('lines') or []
    failure = result.get('eligibility_failure') or {}
    card = result.get('card_check') or {}
    recovery = result.get('recovery') or {}
    paid = bool(((review or {}).get('payments') or {}).get('reference'))
    network = benefits.get('network_status') or 'unknown'
    if ((network == 'in_network' and not verified_in_network(benefits)) or
            (network == 'out_of_network' and not verified_out_of_network(benefits))):
        network = 'unknown'
    unpriced = [line['code'] for line in lines if line.get('calculation') is None and
                not (guidance.get('basis') == 'office_copay' and visit_estimate.clinical_group(line['code']) == 'visit')]
    groups = {visit_estimate.clinical_group(code) for code in unpriced}
    missing = ('nerve testing' if 'nerve' in groups or 'muscle' in groups else
               'other planned care' if len(unpriced) != 1 else f'CPT {unpriced[0]}')
    notice = None
    if recovery.get('outcome') == 'recovered' and card.get('status') == 'conflict':
        notice = 'The payer confirmed the card-based check after the card and eCW differed.'
    elif (benefits.get('policy_match') or {}).get('status') == 'verified':
        notice = 'The payer confirmed dependent coverage without changing eCW.'
    elif (result.get('identity') or benefits.get('identity') or {}).get('reconciliation_rule') == 'cigna_member_suffix' and benefits.get('active') is True:
        notice = 'The payer returned the member ID with a two-digit suffix without changing eCW.'
    elif card.get('differences') and recovery.get('outcome') == 'recovered':
        notice = 'The payer check confirmed coverage after the card and eCW differed.'

    basis = guidance.get('basis') or 'insufficient_evidence'
    amount = guidance.get('amount_cents')
    medical_names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    ma_ppo = is_medicare_advantage(medical_names)
    if result.get('payment_mode') == 'cash':
        named_package = (result.get('cash_quote') or {}).get('selection') and (result.get('cash_quote') or {}).get('complete')
        if not result.get('cash_fallback'):
            cause, code = 'cash was selected for the planned care', 'cash_selected'
        else:
            cause, code = _cash_cause(result, failure, recovery, card, network, benefits, unpriced, missing)
        sentence = (f"No payment is due now because the recorded payment covers NMCT’s cash price{', which was used because ' + cause if code != 'cash_selected' else ''}"
                    if amount == 0 and paid else
                    f"This is NMCT’s nerve-testing package price{' because ' + cause if code != 'cash_selected' else ' for the planned care'}{', after the recorded payment' if paid else ''}, with the focused visit included" if named_package and amount is not None else
                    f"This is NMCT’s cash price{', after the recorded payment' if paid else ''} for the planned care"
                    if code == 'cash_selected' and amount is not None else
                    f"This is NMCT’s cash price{', after the recorded payment' if paid else ''} because {cause}"
                    if amount is not None else
                    f"NMCT has no cash rate for {', '.join(unpriced) if unpriced else 'the selected services'}, and insurance pricing is unavailable because {cause}")
    elif amount is not None:
        if basis == 'claim_after_service':
            cause, code = _cash_cause(result, failure, recovery, card, network, benefits, unpriced, missing,
                                      guidance.get('source_errors'))
            if guidance.get('positive_advance_policy_missing'):
                sentence = ('Collect $0 today under NMCT’s approved defer-to-claim policy because positive '
                            'out-of-network advance collection is not approved; final patient liability follows the processed claim')
                code = 'collection_policy_approval_required'
            elif code == 'clinic_agreement_exclusion':
                sentence = (f'Collect $0 today under NMCT’s approved defer-to-claim policy because {cause}; '
                            'final patient liability follows the processed claim')
            else:
                sentence = f'Collect $0 today: {cause}, so {_collection_action(code, result, guidance.get("source_errors"))}'
        elif basis in {'office_copay', 'ecw_billing_note'} and (amount > 0 or basis == 'office_copay'):
            office_terms = [(line.get('benefits') or {}) for line in lines
                            if visit_estimate.clinical_group(line.get('code') or '') == 'visit']
            deductible_unknown = basis == 'office_copay' and any(
                terms.get('deductible_applicability') == 'UNKNOWN'
                and terms.get('deductible_remaining_cents') != 0 for terms in office_terms)
            deductible_waived = bool(office_terms) and all(
                terms.get('deductible_applicability') == 'DOES_NOT_APPLY' for terms in office_terms)
            deductible_met = bool(office_terms) and all(
                terms.get('deductible_applicability') == 'ALREADY_MET' for terms in office_terms)
            if amount == 0 and paid:
                sentence = 'Collect $0 now: the recorded payment covers the office copay'
            elif amount == 0:
                sentence = 'Collect $0 now: no office copay remains'
            else:
                source = 'plan' if basis == 'office_copay' else 'visit billing note'
                sentence = f'Collect {_money(amount)} for the consultation: the {"out-of-network " if network == "out_of_network" else ""}{source} office copay'
                if paid:
                    sentence += ' after the recorded payment'
            if deductible_waived:
                sentence += ', and the office deductible does not apply'
            elif deductible_met:
                sentence += ', and the office deductible is already met'
            elif deductible_unknown:
                sentence += ', while the office deductible remains unknown'
            if unpriced:
                sentence += f', while {missing} is excluded because its patient cost is unknown'
            elif network == 'out_of_network' and not ma_ppo:
                sentence += ', while any extra doctor charge remains unknown'
            code = 'office_copay_partial' if unpriced else 'office_copay'
        elif basis == 'out_of_pocket_limit':
            sentence, code = 'No payment is due now because the plan says the covered care has no remaining patient share', 'oop_limit_met'
        elif basis == 'qmb_protection':
            sentence, code = ('Collect $0 now because confirmed QMB protection prohibits Medicare cost-sharing collection; '
                              'any other liability requires separate covered-service and claim evidence', 'qmb_protected')
        elif basis == 'ecw_billing_note':
            sentence, code = 'No payment is due now because the visit’s eCW billing note says not to collect', 'ecw_no_collection'
        elif basis == 'practice_cash_price':
            sentence, code = 'This is NMCT’s cash price for the planned care', 'practice_cash_price'
        else:
            calculations = [line.get('calculation') for line in lines]
            if price.get('mode') == 'services' and price.get('complete') and calculations and all(calculations):
                components = [(label, sum(row.get(key, 0) for row in calculations)) for key, label in (
                    ('copay_cents', 'copay'), ('deductible_cents', 'deductible'), ('coinsurance_cents', 'coinsurance'))]
                summary = result.get('encounter_summary') or {}
                parts = []
                for label, value in components:
                    if not value:
                        continue
                    part = f'{_money(value)} {label}'
                    if label == 'coinsurance':
                        percent = summary.get('coinsurance_percent')
                        base = summary.get('coinsurance_base_cents')
                        if percent is not None and type(base) is int:
                            part += f', calculated as {percent}% of {_money(base)}'
                    parts.append(part)
                if amount == 0 and paid:
                    sentence = 'No payment is due now because the recorded payment covers the calculated patient share'
                elif amount == 0 and sum(row.get('oop_adjustment_cents', 0) for row in calculations):
                    sentence = 'No payment is due now because the plan limits the patient share for this care to zero'
                elif parts:
                    pieces = parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]
                    sentence = f'Collect {_money(amount)} now: the patient owes {pieces}'
                    allowed_reduction = sum(row.get('allowed_cap_adjustment_cents', 0) for row in calculations)
                    oop_reduction = sum(row.get('oop_adjustment_cents', 0) for row in calculations)
                    if allowed_reduction:
                        sentence += f', reduced by {_money(allowed_reduction)} to the payer allowed amount'
                    if oop_reduction:
                        sentence += (f', reduced by {_money(oop_reduction)} under the out-of-pocket limit'
                                     if all('allowed_cap_adjustment_cents' in row for row in calculations) else
                                     f', reduced by {_money(oop_reduction)} under plan limits')
                    cms_based = any(line.get('allowed_basis') == 'cms_original_medicare_estimate' for line in lines)
                    sentence += (' based on CMS Original Medicare benchmark prices, not payer-confirmed allowances'
                                 if cms_based else
                                 ' based on the payer’s out-of-network CPT prices' if network == 'out_of_network'
                                 else ' based on the clinic’s contracted CPT prices' if network == 'in_network'
                                 else ' for the planned care')
                    office_lines = [line for line in lines
                                    if visit_estimate.clinical_group(line.get('code') or '') == 'visit']
                    if office_lines and all((line.get('benefits') or {}).get('copay_cents') == 0
                                            and (line.get('calculation') or {}).get('copay_cents') == 0
                                            for line in office_lines):
                        sentence += '; the plan reports a $0.00 office copay'
                        if all((line.get('benefits') or {}).get('deductible_applicability') == 'DOES_NOT_APPLY'
                               for line in office_lines):
                            sentence += ', and the office deductible does not apply'
                    if paid:
                        sentence += ', after the recorded payment'
                    if network == 'out_of_network' and not ma_ppo:
                        sentence += ', though extra doctor charges remain unknown'
                else:
                    sentence = 'No payment is due now under the applicable plan benefits'
            else:
                cms_based = any(line.get('allowed_basis') == 'cms_original_medicare_estimate' for line in lines)
                sentence = ('This estimate uses CMS Original Medicare benchmark prices, not payer-confirmed allowances'
                            if cms_based else
                            'This estimate uses the plan’s copay, deductible, and share of NMCT’s contract prices for the planned care'
                            if network == 'in_network' else
                            'This estimate uses the payer’s out-of-network allowed price under Medicare Advantage member billing protections'
                            if network == 'out_of_network' and ma_ppo else
                            'This estimate uses the payer’s out-of-network allowed price, though any extra doctor charge remains unknown'
                            if network == 'out_of_network' else
                            'This is the known payment for the planned care, while other costs remain unknown')
                if paid:
                    sentence += ', after the recorded payment'
            code = 'insured_services'
    else:
        cause, cause_code = _cash_cause(result, failure, recovery, card, network, benefits, unpriced, missing,
                                        guidance.get('source_errors'))
        if guidance.get('positive_advance_policy_missing'):
            sentence, code = ('The calculated out-of-network share is not approved for advance collection, so a clinic administrator must approve this payer’s collection policy before requesting payment',
                              'collection_policy_approval_required')
        elif basis == 'calculation_conflict':
            sentence, code = guidance.get('reason', 'Recalculate the insured estimate before collecting').rstrip('.'), 'calculation_conflict'
        elif recovery.get('state') in {'pending', 'indeterminate'}:
            sentence, code = 'No amount is ready because the insurance check has not finished', 'check_pending'
        elif cause_code in {'provider_enrollment', 'multiple_active_policies', 'policy_identity_mismatch',
                            'payer_identity_match_missing',
                            'coverage_status_conflict', 'coverage_date_invalid',
                            'service_inactive', 'service_date_mismatch', 'service_benefits_historical',
                            'service_secondary_coverage', 'service_identity_mismatch', 'service_network_conflict',
                            'no_policy_returned', 'card_conflict', 'member_identity_unverified',
                            'clinic_product_mismatch', 'clinic_contract_missing', 'clinic_payer_connection_draft',
                            'clinic_agreement_exclusion',
                            'qmb_status_unresolved',
                            'qmb_protected',
                            'provider_network_unresolved',
                            'provider_network_conflict', 'planned_cpt_unselected',
                            'planned_cpt_missing', 'service_selection_invalid', 'botox_clinical_review_required', 'service_pricing_issue',
                            'calculation_unavailable',
                            'financial_source_error',
                            'saved_payer_response_unavailable',
                            'contract_rate_missing', 'contract_rate_conflict',
                            'saved_clinic_pricing_changed', 'cost_share_term_unresolved',
                            'deductible_applicability_conflict', 'individual_deductible_unresolved',
                            'individual_oop_unresolved', 'referral_required', 'authorization_required', 'referral_authorization_required'}:
            sentence, code = (f'No insured amount is ready because {cause}, so '
                              f'{_collection_action(cause_code, result, guidance.get("source_errors"))}'), cause_code
        elif network == 'out_of_network':
            percent = next((row.get('benefits', {}).get('coinsurance_percent') for row in lines
                            if row.get('benefits', {}).get('coinsurance_percent') is not None), None)
            if lines and all(line.get('allowed_cents') is not None for line in lines):
                sentence = ('The selected CPT prices are available, but the payer did not establish all '
                            'out-of-network cost-sharing terms needed for an advance, so '
                            f'{_collection_action("cost_share_term_unresolved", result)}')
                code = 'cost_share_term_unresolved'
            else:
                term = f'a {percent}% out-of-network share' if percent is not None else 'out-of-network benefits'
                sentence = (f'The plan lists {term}, but its allowed price for this care is unknown, so '
                            f'{_collection_action("out_of_network_allowance_missing", result)}')
                code = 'out_of_network_allowance_missing'
        elif card.get('status') == 'conflict':
            sentence, code = 'No amount is ready because the card and eCW policy details differ', 'card_conflict'
        elif benefits.get('active') is False:
            sentence, code = 'No insured amount is ready because the plan reported no active coverage for this visit', 'inactive_coverage'
        elif unpriced:
            sentence, code = f'No insured amount is ready because the price for {missing} is unknown', 'service_price_missing'
        else:
            code = cause_code
            sentence = f'No insured amount is ready because {cause}'
    if (amount is None and benefits.get('active') is True and len(medical_names) == 1
            and 'CHOICE PLUS' in medical_names[0].upper()):
        reported = _choice_plus_reported_terms(benefits)
        if reported:
            sentence = (f'UHC reported active {medical_names[0]} medical coverage. '
                        f'{reported}{sentence}')
    inventory = result.get('network_evidence') or {}
    if inventory.get('decision') == 'out_of_network_by_clinic_contract_inventory':
        plan = medical_names[0] if len(medical_names) == 1 else 'UnitedHealthcare Regional Medicare Advantage PPO'
        sentence = (f'The patient has {plan}, which is excluded from NMCT’s UHC agreement, so NMCT is '
                    f'treating this visit as out of network under its certified contract inventory; '
                    f'{sentence[:1].lower()}{sentence[1:]}')
    elif network == 'out_of_network' and amount is not None and inventory.get('decision') in {
            'out_of_network_confirmed_by_payer',
            'out_of_network_confirmed_by_scoped_nonparticipation_record'}:
        plan_label = medical_names[0] if medical_names else result.get('payer') or 'The medical plan'
        source_reason = ('the payer confirmed this provider does not participate in that product'
                         if inventory['decision'] == 'out_of_network_confirmed_by_payer' else
                         'NMCT has a current provider- and product-scoped nonparticipation record')
        sentence = (f'{plan_label} is out of network for this visit because {source_reason}: '
                    f'{sentence[:1].lower()}{sentence[1:]}')
    elif code == 'insured_services' and (medical_names or result.get('payer')):
        tier = ('out-of-network' if network == 'out_of_network' else
                'in-network' if network == 'in_network' else 'network-unresolved')
        plan_label = medical_names[0] if medical_names else result['payer']
        sentence = f'{plan_label} is treated as {tier} for this visit: {sentence[:1].lower()}{sentence[1:]}'
    summary = result.get('encounter_summary') or {}
    return {'sentence': sentence + '.', 'reason_code': code, 'network_tier': network,
            'pricing_basis': basis, 'unpriced_codes': unpriced,
            'plan_name': medical_names[0] if len(medical_names) == 1 else None,
            'network_decision': inventory.get('decision'),
            'network_source': benefits.get('network_source'),
            'contract_evidence': {key: inventory.get(key) for key in
                                  ('document_id', 'page', 'locator', 'scope') if inventory.get(key) is not None},
            'selected_services': [{'line_id': line.get('id'), 'code': line.get('code'),
                                   'units': line.get('units'), 'allowed_cents': line.get('allowed_cents'),
                                   'allowed_basis': line.get('allowed_basis'),
                                   'rate_source': line.get('rate_source'),
                                   'rate_version': line.get('rate_version'),
                                   'benefit_ids': (line.get('benefits') or {}).get('selected_benefit_ids') or []}
                                  for line in lines],
            'payments_verified_cents': summary.get('payments_verified_cents'),
            'estimated_liability_cents': summary.get('estimated_total_patient_max_cents'),
            'collect_now_cents': amount, 'collection_policy_basis': basis,
            'financial_components': (result.get('encounter_summary') or {}).get('components'),
            'coinsurance_percent': (result.get('encounter_summary') or {}).get('coinsurance_percent'),
            'coinsurance_base_cents': (result.get('encounter_summary') or {}).get('coinsurance_base_cents'),
            'known_amount_only': bool(result.get('payment_mode') != 'cash' and amount is not None and unpriced),
            'notice': notice}
