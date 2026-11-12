"""Prepare network and out-of-pocket evidence for collection decisions."""

from datetime import date, timedelta

from app.insurance.network_participation import verified_in_network
from app.insurance.identity import patient_benefits_bound


def uhc_choice_plus_zero_candidate(result: dict) -> bool:
    """Recognize the one-office-visit Choice Plus family-cap deferral."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    line_benefits = (lines[0].get('benefits') or {}) if len(lines) == 1 else {}
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    identity = result.get('identity') or benefits.get('identity') or {}
    options = benefits.get('reported_options') or []
    card = result.get('card_check') or {}
    office_copays = [row for row in options if row.get('kind') == 'copay'
                     and row.get('service_type_code') == 'BY'
                     and row.get('coverage_level') == 'INDIVIDUAL'
                     and row.get('network') == 'IN_NETWORK']
    family_deductibles = [row for row in options if row.get('kind') == 'deductible'
                          and row.get('service_type_code') == '30'
                          and row.get('coverage_level') == 'FAMILY'
                          and row.get('time_period') in {'REMAINING', '29'}
                          and row.get('network') == 'IN_NETWORK']
    return (str(result.get('payer') or '').casefold().replace(' ', '') == 'unitedhealthcare'
            and names == ['POS CHOICE PLUS']
            and result.get('payment_mode') != 'cash'
            and result.get('service_date') == str(result.get('checked_at') or '')[:10]
            and (result.get('appointment_context') or {}).get('place_of_service') == '11'
            and price.get('mode') == 'services' and not price.get('service_issues')
            and len(lines) == 1 and lines[0].get('code') == '99203'
            and lines[0].get('units') == 1 and not lines[0].get('modifiers')
            and benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and line_benefits.get('active') is True
            and line_benefits.get('service_date_matches') is not False
            and line_benefits.get('network_status') != 'out_of_network'
            and not line_benefits.get('coverage_status_conflict')
            and line_benefits.get('family_oop_zero_applies') is True
            and benefits.get('family_oop_zero_applies') is True
            and benefits.get('family_oop_remaining_cents') == 0
            and not benefits.get('separate_copay_limit')
            and not benefits.get('has_secondary') and not benefits.get('network_conflict')
            and not benefits.get('coverage_status_conflict') and not benefits.get('coverage_dates_invalid')
            and not benefits.get('historical_only') and not benefits.get('source_errors')
            and card.get('status') not in {'conflict', 'mismatch'}
            and not card.get('conflicts') and not card.get('differences')
            and result.get('refresh_requires_selection') is not True
            and patient_benefits_bound(identity) and verified_in_network(benefits)
            and office_copays and all(row.get('amount_cents') == 0 for row in office_copays)
            and family_deductibles and all(row.get('amount_cents') == 0 for row in family_deductibles))


def apply_uhc_choice_plus_family_oop_deferral(result: dict, guidance: dict, *,
                                               adverse: bool, same_policy_conflict: bool,
                                               source_errors: list) -> None:
    if (not uhc_choice_plus_zero_candidate(result) or adverse or same_policy_conflict
            or source_errors or guidance.get('amount_cents') not in {None, 0}):
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0 at check-in for the proposed CPT 99203 office visit. UHC reports a $0 office copay '
                'and $0 remaining on the family in-network deductible and out-of-pocket maximum. '
                'The BCBS Texas fee is a price reference, not a UHC allowance. Confirm that the performed '
                'service is covered and counts toward the family maximum; final responsibility follows the claim.'),
        patient_message='No payment is requested at arrival. Final responsibility follows the processed insurance claim.',
        collection_rule={'mode': 'uhc_choice_plus_family_oop_deferral',
                         'reference': 'UHC Choice Plus office and family medical benefits'})
    guidance.pop('policy_setup_required', None)


def prepare_benefit_evidence(benefits: dict, lines: list[dict]) -> tuple[dict, bool]:
    # An old in-network label needs current product/provider evidence.
    collection_benefits = (benefits if benefits.get('network_status') != 'in_network'
                           or verified_in_network(benefits) else {**benefits, 'network_status': 'unknown'})
    unconfirmed_zero_oop = (benefits.get('oop_remaining_cents') is None and
        all((line.get('benefits') or {}).get('oop_remaining_cents') is None for line in lines) and
        any(row.get('kind') == 'out_of_pocket' and row.get('service_type_code') == '30'
            and row.get('coverage_level') in {'INDIVIDUAL', 'IND'}
            and row.get('time_period') in {'REMAINING', '29'}
            and row.get('network') in {'IN_NETWORK', 'Y'}
            and row.get('plan_association') in {'unassigned', 'medical_candidate'}
            and row.get('amount_cents') == 0
            for row in benefits.get('reported_options') or []))
    return collection_benefits, unconfirmed_zero_oop


def preview_zero_individual_oop_applies(benefits: dict, price: dict, lines: list[dict]) -> bool:
    """A CPT choice is immaterial when every priced option has the same capped $0 share."""
    return (price.get('mode') == 'alternatives'
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


def apply_aetna_choice_family_oop_deferral(result: dict, guidance: dict, *,
                                           member_bound: bool, source_errors: list,
                                           adverse: bool, same_policy_conflict: bool) -> None:
    """Give a numeric check-in decision for the scoped Choice POS II EMG plan."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    reported = benefits.get('reported_options') or []
    family_rows = [row for row in reported
                   if row.get('kind') == 'out_of_pocket'
                   and row.get('coverage_level') in {'FAMILY', 'FAM'}
                   and row.get('network') in {'IN_NETWORK', 'Y'}
                   and row.get('time_period') in {'REMAINING', '29'}
                   and row.get('plan_association') == 'medical'
                   and not row.get('procedure_code')]
    returned = (benefits.get('returned_service_dates') or {}).get('start')
    try:
        prior_day_response = (date.fromisoformat(result['service_date'])
                              - date.fromisoformat(returned) == timedelta(days=1))
    except (KeyError, TypeError, ValueError):
        prior_day_response = False
    if (guidance.get('amount_cents') is not None or not member_bound or source_errors or adverse
            or same_policy_conflict or benefits.get('active') is not True
            or (benefits.get('service_date_matches') is not True and not prior_day_response)
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('network_conflict')
            or benefits.get('network_status') == 'out_of_network'
            or result.get('refresh_requires_selection') is True
            or result.get('payment_mode') == 'cash'
            or 'aetna' not in str(result.get('payer') or '').casefold()
            or names != ['Aetna Choice POS II']
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 2 or {line.get('code') for line in lines} != {'95908', '95886'}
            or any(line.get('units') != 1 or line.get('modifiers') for line in lines)
            or any((line.get('benefits') or {}).get('active') is not True for line in lines)
            or any('COPAY MAX' in str(row.get('payer_note') or '').upper() for row in reported)
            or not family_rows or any(type(row.get('amount_cents')) is not int
                                   or row['amount_cents'] != 0 for row in family_rows)):
        return
    deductible_rows = [row for row in reported if row.get('kind') == 'deductible'
                       and row.get('network') in {'IN_NETWORK', 'Y'}
                       and row.get('time_period') in {'REMAINING', '29'}
                       and row.get('plan_association') == 'medical']
    coinsurance_rows = [row for row in reported if row.get('kind') == 'coinsurance'
                        and row.get('network') in {'IN_NETWORK', 'Y'}
                        and row.get('service_type_code') == '73']
    deductible_term = ('$0 deductible' if deductible_rows and
                       all(row.get('amount_cents') == 0 for row in deductible_rows)
                       else 'any applicable deductible')
    coinsurance_term = ('0% × Aetna allowed amount' if coinsurance_rows and
                        all(str(row.get('percent')) in {'0', '0.0', '0.00'} for row in coinsurance_rows)
                        else 'any applicable coinsurance')
    # shortcut: a one-day payer echo or unresolved provider tier/order gets no advance; claims settle liability.
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0 at check-in. The Aetna response reports $0 remaining on the medical '
                f'family in-network out-of-pocket limit: min(any applicable copay + {deductible_term} '
                f'+ {coinsurance_term}, $0 family in-network remaining) = $0.00 '
                'for covered in-network services. The payer may echo the day before the visit; provider tier '
                'and payer order may also change final liability. '
                'Bill any balance after the processed claims.'),
        patient_message='No payment is requested at arrival. Final responsibility follows the processed insurance claims.',
        collection_rule={'mode': 'aetna_choice_family_oop_deferral',
                         'reference': 'Aetna medical family in-network out-of-pocket balance; exact or prior-day service echo'})
    guidance.pop('policy_setup_required', None)


def apply_family_oop_deferrals(result: dict, guidance: dict, *, member_bound: bool,
                               source_errors: list, adverse: bool,
                               same_policy_conflict: bool) -> None:
    apply_aetna_choice_family_oop_deferral(result, guidance, member_bound=member_bound,
        source_errors=source_errors, adverse=adverse, same_policy_conflict=same_policy_conflict)
    apply_uhc_choice_plus_family_oop_deferral(result, guidance, adverse=adverse,
        same_policy_conflict=same_policy_conflict, source_errors=source_errors)


def apply_trs_hd_office_refundable_deposit(result: dict, guidance: dict, *,
                                          member_bound: bool, source_errors: list,
                                          adverse: bool, same_policy_conflict: bool,
                                          review: dict | None) -> None:
    """Use the scoped clinic fee as a refundable deposit, not a patient share."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    line_benefits = (lines[0].get('benefits') or {}) if len(lines) == 1 else {}
    reference = result.get('temporary_rate_reference') or {}
    if (guidance.get('amount_cents') is not None or guidance.get('unconfirmed_zero_oop')
            or not member_bound or source_errors
            or adverse or same_policy_conflict or benefits.get('active') is not True
            or benefits.get('service_date_matches') is False
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('network_status') == 'out_of_network'
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or result.get('refresh_requires_selection') is True
            or result.get('payment_mode') == 'cash'
            or not any(name in str(result.get('payer') or '').casefold()
                       for name in ('blue cross', 'bcbstx'))
            or plan.get('names') != ['PREFERRED PROVIDER OPTION MEDICAL']
            or str(plan.get('group_number') or '') != '385000'
            or not str((result.get('insurance_details') or {}).get('member_id') or '').upper().startswith('T2S')
            or not '2026-09-01' <= str(result.get('service_date') or '') <= '2027-08-31'
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or lines[0].get('code') != '99203'
            or lines[0].get('units') != 1 or lines[0].get('modifiers')
            or benefits.get('deductible_remaining_cents') is not None
            or benefits.get('oop_remaining_cents') is not None
            or line_benefits.get('copay_cents') is not None
            or line_benefits.get('deductible_remaining_cents') is not None
            or line_benefits.get('oop_remaining_cents') is not None
            or str(line_benefits.get('coinsurance_percent') or '')
            not in {'30', '30.0', '30.00'}):
        return
    fee_rows = reference.get('lines') or []
    fee = (fee_rows[0].get('unit_reference_cents') if reference.get('complete') is True
           and len(fee_rows) == 1 and fee_rows[0].get('id') == lines[0].get('id')
           and fee_rows[0].get('code') == '99203' and fee_rows[0].get('units') == 1
           and not fee_rows[0].get('modifiers')
           and str(fee_rows[0].get('source_reference') or '').startswith('library:')
           else None)
    if fee is None and (lines[0].get('allowed_basis') == 'clinic_contract'
            and (lines[0].get('rate_resolution') or {}).get('status') == 'RESOLVED'):
        fee = lines[0].get('allowed_cents')
    if type(fee) is not int or not 0 < fee <= 100_000_000:
        return
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, fee - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=fee,
        reason=('Collect a refundable deposit based on the scoped BCBSTX clinic CPT 99203 fee reference. '
                'The payer omitted the remaining deductible and out-of-pocket balances. This deposit is '
                'not the insured allowed amount or patient share; apply it to the processed claim and '
                'refund any excess within 30 days.'),
        patient_message=('This is a refundable clinic deposit, not your final insurance share. '
                         'Any excess will be refunded after your claim is processed.'),
        collection_rule={'mode': 'trs_hd_office_refundable_deposit',
                         'reference': 'Scoped BCBSTX clinic CPT 99203 fee reference'})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
