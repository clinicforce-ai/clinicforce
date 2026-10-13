"""One plain sentence for the doctor's collection amount and its evidence basis."""
from __future__ import annotations

from app.insurance import visit_estimate


def _money(cents: int) -> str:
    return f'${cents // 100:,}.{cents % 100:02d}'


def build(result: dict, guidance: dict, review: dict | None = None) -> dict:
    price = result.get('price_range') or {}
    benefits = result.get('benefits') or {}
    lines = price.get('lines') or []
    failure = result.get('eligibility_failure') or {}
    card = result.get('card_check') or {}
    recovery = result.get('recovery') or {}
    paid = bool(((review or {}).get('payments') or {}).get('reference'))
    network = benefits.get('network_status') or 'unknown'
    unpriced = [line['code'] for line in lines if line.get('calculation') is None and
                not (guidance.get('basis') == 'office_copay' and visit_estimate.clinical_group(line['code']) == 'visit')]
    groups = {visit_estimate.clinical_group(code) for code in unpriced}
    missing = ('nerve testing' if 'nerve' in groups or 'muscle' in groups else
               'other planned care' if len(unpriced) != 1 else f'CPT {unpriced[0]}')
    notice = None
    if recovery.get('outcome') == 'recovered' and card.get('status') == 'conflict':
        notice = 'The card and eCW differed; the payer confirmed the card-based check.'
    elif (benefits.get('policy_match') or {}).get('status') == 'verified':
        notice = 'The payer confirmed this patient is covered as a dependent; eCW was not changed.'
    elif (result.get('identity') or benefits.get('identity') or {}).get('reconciliation_rule') == 'cigna_member_suffix' and benefits.get('active') is True:
        notice = 'The payer returned the member ID with a two-digit suffix; eCW was not changed.'
    elif card.get('differences') and recovery.get('outcome') == 'recovered':
        notice = 'The card and eCW differed; the payer check confirmed coverage.'

    basis = guidance.get('basis') or 'insufficient_evidence'
    amount = guidance.get('amount_cents')
    if result.get('payment_mode') == 'cash':
        named_package = (result.get('cash_quote') or {}).get('selection') and (result.get('cash_quote') or {}).get('complete')
        if not result.get('cash_fallback'):
            cause, code = 'cash was selected for the planned care', 'cash_selected'
        elif card.get('status') == 'conflict' and recovery.get('outcome') != 'recovered':
            field = 'member ID' if 'member_id' in card.get('conflicts', []) else 'group number' if 'group_number' in card.get('conflicts', []) else 'insurer'
            cause = f'the {field} on the card differs from eCW and coverage could not be confirmed'
            code = 'card_conflict'
        elif network == 'out_of_network':
            cause, code = 'the plan did not give an allowed price for out-of-network care', 'out_of_network_allowance_missing'
        elif benefits.get('active') is False:
            cause, code = 'the plan reported no active coverage for this visit', 'inactive_coverage'
        elif failure.get('kind') == 'provider_enrollment':
            cause, code = 'the clinic could not check this plan with the insurer', 'provider_enrollment'
        elif failure.get('codes') and set(failure['codes']) & {'65', '67', '71', '72', '73', '75', '76'}:
            cause, code = 'the insurer could not match the member details', 'member_identity_unverified'
        elif unpriced and benefits.get('active') is True and network == 'in_network':
            cause, code = f'the insurance price for {missing} is missing', 'contract_rate_missing'
        elif recovery.get('state') == 'indeterminate' or (result.get('previsit') or {}).get('request_state') == 'pending':
            cause, code = 'the insurance check has not finished', 'check_pending'
        else:
            cause, code = 'the plan did not confirm an amount for the planned care', 'insured_amount_unknown'
        sentence = (f"No payment is due now because the recorded payment covers NMCT’s cash price{'; cash was used because ' + cause if code != 'cash_selected' else ''}"
                    if amount == 0 and paid else
                    f"This is NMCT’s nerve-testing package price{' because ' + cause if code != 'cash_selected' else ' for the planned care'}{', after the recorded payment' if paid else ''}; the focused visit is included" if named_package and amount is not None else
                    f"This is NMCT’s cash price{', after the recorded payment' if paid else ''} for the planned care"
                    if code == 'cash_selected' and amount is not None else
                    f"This is NMCT’s cash price{', after the recorded payment' if paid else ''} because {cause}"
                    if amount is not None else f"NMCT’s cash price is not available because {cause}")
    elif amount is not None:
        if basis in {'office_copay', 'ecw_billing_note'} and (amount > 0 or basis == 'office_copay'):
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
                sentence += '; the office deductible does not apply'
            elif deductible_met:
                sentence += '; the office deductible is already met'
            elif deductible_unknown:
                sentence += '; any office deductible is still unknown'
            if unpriced:
                sentence += f'; {missing} is excluded because its patient cost is unknown'
            elif network == 'out_of_network':
                sentence += '; any extra doctor charge is unknown'
            code = 'office_copay_partial' if unpriced else 'office_copay'
        elif basis == 'out_of_pocket_limit':
            sentence, code = 'No payment is due now because the plan says the covered care has no remaining patient share', 'oop_limit_met'
        elif basis == 'ecw_billing_note':
            sentence, code = 'No payment is due now because the visit’s eCW billing note says not to collect', 'ecw_no_collection'
        elif basis == 'practice_cash_price':
            sentence, code = 'This is NMCT’s cash price for the planned care', 'practice_cash_price'
        else:
            calculations = [line.get('calculation') for line in lines]
            if price.get('mode') == 'services' and price.get('complete') and calculations and all(calculations):
                components = [(label, sum(row.get(key, 0) for row in calculations)) for key, label in (
                    ('copay_cents', 'copay'), ('deductible_cents', 'deductible'), ('coinsurance_cents', 'coinsurance'))]
                parts = [f'{_money(value)} {label}' for label, value in components if value]
                if amount == 0 and paid:
                    sentence = 'No payment is due now because the recorded payment covers the calculated patient share'
                elif amount == 0 and sum(row.get('oop_adjustment_cents', 0) for row in calculations):
                    sentence = 'No payment is due now because the plan limits the patient share for this care to zero'
                elif parts:
                    pieces = parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]
                    sentence = f'This {_money(amount)} advance includes {pieces}'
                    adjustment = sum(row.get('oop_adjustment_cents', 0) for row in calculations)
                    if adjustment:
                        sentence += f', reduced by {_money(adjustment)} under plan limits'
                    sentence += (' based on the payer’s out-of-network CPT prices' if network == 'out_of_network'
                                 else ' based on the clinic’s contracted CPT prices' if network == 'in_network'
                                 else ' for the planned care')
                    if paid:
                        sentence += ', after the recorded payment'
                    if network == 'out_of_network':
                        sentence += '; extra doctor charges are still unknown'
                else:
                    sentence = 'No payment is due now under the applicable plan benefits'
            else:
                sentence = ('This estimate uses the plan’s copay, deductible, and share of NMCT’s contract prices for the planned care'
                            if network == 'in_network' else
                            'This estimate uses the payer’s out-of-network allowed price; any extra doctor charge is still unknown'
                            if network == 'out_of_network' else
                            'This is the known payment for the planned care; other costs are still unknown')
                if paid:
                    sentence += ', after the recorded payment'
            code = 'insured_services'
    else:
        if recovery.get('state') in {'pending', 'indeterminate'}:
            sentence, code = 'No amount is ready because the insurance check has not finished', 'check_pending'
        elif network == 'out_of_network':
            percent = next((row.get('benefits', {}).get('coinsurance_percent') for row in lines
                            if row.get('benefits', {}).get('coinsurance_percent') is not None), None)
            term = f'{percent}% out-of-network share' if percent is not None else 'out-of-network benefits'
            sentence = f'The plan lists a {term}, but its allowed price for this care is unknown'
            code = 'out_of_network_allowance_missing'
        elif card.get('status') == 'conflict':
            sentence, code = 'No amount is ready because the card and eCW policy details differ', 'card_conflict'
        elif benefits.get('active') is False:
            sentence, code = 'No insured amount is ready because the plan reported no active coverage for this visit', 'inactive_coverage'
        elif unpriced:
            sentence, code = f'No insured amount is ready because the price for {missing} is unknown', 'service_price_missing'
        else:
            sentence, code = 'No amount is ready because the plan did not confirm the patient share for this visit', 'insured_amount_unknown'
    return {'sentence': sentence + '.', 'reason_code': code, 'network_tier': network,
            'pricing_basis': basis, 'unpriced_codes': unpriced,
            'known_amount_only': bool(result.get('payment_mode') != 'cash' and amount is not None and unpriced),
            'notice': notice}
