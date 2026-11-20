"""Prepare network and out-of-pocket evidence for collection decisions."""

from datetime import date, timedelta

from app.insurance.network_participation import verified_in_network
from app.insurance.identity import patient_benefits_bound
from app.insurance import aetna_fees
from app.insurance.collection.bcbstx_botox_zero import apply_bcbstx_botox_family_oop_deferral
from app.insurance.collection.uhc_choice_plus_oop import apply as apply_uhc_choice_plus_unassigned_oop_deferral


def uhc_choice_plus_zero_candidate(result: dict) -> bool:
    """Recognize a bounded Choice Plus office check-in deferral."""
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
    individual_oop = [row for row in options if row.get('kind') == 'out_of_pocket'
                      and row.get('service_type_code') == '30'
                      and row.get('coverage_level') == 'INDIVIDUAL'
                      and row.get('time_period') in {'REMAINING', '29'}
                      and row.get('network') == 'IN_NETWORK']
    family_oop = [row for row in options if row.get('kind') == 'out_of_pocket'
                  and row.get('service_type_code') == '30'
                  and row.get('coverage_level') == 'FAMILY'
                  and row.get('time_period') in {'REMAINING', '29'}
                  and row.get('network') == 'IN_NETWORK']
    code = lines[0].get('code') if len(lines) == 1 else None
    try:
        check_age = (date.fromisoformat(str(result.get('service_date') or ''))
                     - date.fromisoformat(str(result.get('checked_at') or '')[:10])).days
    except (TypeError, ValueError):
        check_age = None
    vision_names = (benefits.get('plan_details') or {}).get('supplementary_names') or []
    specialist_copays = [row for row in options if row.get('kind') == 'copay'
                         and row.get('service_type_code') == '96'
                         and row.get('coverage_level') == 'INDIVIDUAL'
                         and row.get('network') == 'IN_NETWORK']
    mixed_vision_followup = (code == '99213' and check_age in {0, 1}
        and bool(vision_names) and all('vision' in str(name).casefold() or 'spectera' in str(name).casefold()
                                       for name in vision_names)
        and (benefits.get('plan_details') or {}).get('names') == names
        and individual_oop and family_oop
        and all(type(row.get('plan_index')) is int for row in
                [*office_copays, *family_deductibles, *individual_oop, *family_oop])
        and len({row.get('plan_index') for row in
                 [*office_copays, *family_deductibles, *individual_oop, *family_oop]}) == 1
        and all(row.get('plan_association') == 'unassigned' and row.get('amount_cents') == 0
                for row in [*individual_oop, *family_oop]))
    return (str(result.get('payer') or '').casefold().replace(' ', '') == 'unitedhealthcare'
            and names == ['POS CHOICE PLUS']
            and result.get('payment_mode') != 'cash'
            and (result.get('service_date') == str(result.get('checked_at') or '')[:10]
                 or mixed_vision_followup)
            and (result.get('appointment_context') or {}).get('place_of_service') == '11'
            and price.get('mode') == 'services' and not price.get('service_issues')
            and len(lines) == 1 and code in {'99203', '99213'}
            and lines[0].get('units') == 1 and not lines[0].get('modifiers')
            and benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and line_benefits.get('active') is True
            and line_benefits.get('service_date_matches') is not False
            and line_benefits.get('network_status') != 'out_of_network'
            and not line_benefits.get('coverage_status_conflict')
            and ((code in {'99203', '99213'} and line_benefits.get('family_oop_zero_applies') is True
                  and benefits.get('family_oop_zero_applies') is True
                  and benefits.get('family_oop_remaining_cents') == 0)
                 or mixed_vision_followup)
            and not benefits.get('separate_copay_limit')
            and not benefits.get('has_secondary') and not benefits.get('network_conflict')
            and not benefits.get('coverage_status_conflict') and not benefits.get('coverage_dates_invalid')
            and not benefits.get('historical_only') and not benefits.get('source_errors')
            and card.get('status') not in {'conflict', 'mismatch'}
            and card.get('independent_ecw_check') is not True
            and not card.get('conflicts') and not card.get('differences')
            and result.get('refresh_requires_selection') is not True
            and patient_benefits_bound(identity) and verified_in_network(benefits)
            and office_copays and all(row.get('amount_cents') == 0 for row in office_copays)
            and (code != '99213' or all(row.get('amount_cents') == 0 for row in specialist_copays))
            and family_deductibles and all(row.get('amount_cents') == 0 for row in family_deductibles))


def apply_uhc_choice_plus_family_oop_deferral(result: dict, guidance: dict, *,
                                               adverse: bool, same_policy_conflict: bool,
                                               source_errors: list) -> None:
    if (not uhc_choice_plus_zero_candidate(result) or adverse or same_policy_conflict
            or source_errors or guidance.get('amount_cents') not in {None, 0}):
        return
    code = (result['price_range']['lines'][0]['code'])
    mixed_vision = code == '99213' and (result.get('benefits') or {}).get('family_oop_zero_applies') is not True
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=(f'Collect $0 at check-in for the proposed CPT {code} office visit. UHC reports a $0 office copay '
                'and $0 remaining on in-network individual and family out-of-pocket rows, but their medical '
                'applicability is unconfirmed alongside a vision plan. Confirm coverage and the applicable '
                'medical limit; final responsibility follows the claim.' if mixed_vision else
                f'Collect $0 at check-in for the proposed CPT {code} office visit. UHC reports a $0 office copay '
                'and $0 remaining on the family in-network deductible and out-of-pocket maximum. '
                'The BCBS Texas fee is a price reference, not a UHC allowance. Confirm that the performed '
                'service is covered and counts toward the family maximum; final responsibility follows the claim.'),
        patient_message='No payment is requested at arrival. Final responsibility follows the processed insurance claim.',
        collection_rule={'mode': 'uhc_choice_plus_family_oop_deferral',
                         'reference': ('UHC Choice Plus office and unassigned in-network STC 30 balances'
                                       if mixed_vision else 'UHC Choice Plus office and family medical benefits')})
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
                               same_policy_conflict: bool, safe: bool,
                               unconfirmed_zero_oop: bool) -> None:
    apply_bcbstx_botox_family_oop_deferral(result, guidance, member_bound=member_bound,
        source_errors=source_errors, adverse=adverse, same_policy_conflict=same_policy_conflict)
    apply_aetna_choice_family_oop_deferral(result, guidance, member_bound=member_bound,
        source_errors=source_errors, adverse=adverse, same_policy_conflict=same_policy_conflict)
    apply_aetna_office_individual_oop_deferral(result, guidance, member_bound=member_bound,
        source_errors=source_errors, adverse=adverse, same_policy_conflict=same_policy_conflict)
    apply_uhc_choice_plus_family_oop_deferral(result, guidance, adverse=adverse,
        same_policy_conflict=same_policy_conflict, source_errors=source_errors)
    apply_uhc_choice_plus_unassigned_oop_deferral(result, guidance, safe=safe,
        unconfirmed_zero_oop=unconfirmed_zero_oop)



def apply_aetna_televisit_oop_deferral(result: dict, guidance: dict, *, member_bound: bool,
                                      source_errors: list, adverse: bool,
                                      same_policy_conflict: bool) -> None:
    """Defer a single Aetna video follow-up when the dated medical OOP reference is zero."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    setting = result.get('telehealth') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    oop_id = benefits.get('oop_id')
    line_terms = (lines[0].get('benefits') or {}) if len(lines) == 1 else {}
    medical_oop = bool(oop_id) and any(row.get('id') == oop_id and row.get('kind') == 'out_of_pocket'
                      and row.get('amount_cents') == 0
                      and row.get('coverage_level') in {'INDIVIDUAL', 'IND'}
                      and row.get('time_period') in {'REMAINING', '29'}
                      and row.get('network') in {'IN_NETWORK', 'Y'}
                      and row.get('plan_association') == 'medical'
                      for row in benefits.get('reported_options') or [])
    if (guidance.get('amount_cents') is not None or result.get('payment_mode') == 'cash'
            or not member_bound or not result.get('checked_at') or source_errors or adverse
            or same_policy_conflict or aetna_fees.category(str(result.get('payer') or ''), names) != 'commercial'
            or benefits.get('active') is not True or benefits.get('has_secondary')
            or not patient_benefits_bound(benefits.get('identity'))
            or not benefits['identity'].get('binding')
            or benefits['identity'].get('binding') != (result.get('identity') or {}).get('binding')
            or (benefits.get('plan_details') or {}).get('names') != names
            or benefits.get('network_status') == 'out_of_network' or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('oop_remaining_cents') != 0
            or benefits.get('oop_remaining_conflict_ids') or not medical_oop
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or (result.get('recovery') or {}).get('reason_code') in {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or result.get('refresh_requires_selection') is True
            or setting.get('place_of_service') not in {'10', '02'}
            or setting.get('modality') != 'audio_video'
            or (result.get('appointment_context') or {}).get('place_of_service') != setting['place_of_service']
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(codes) != 1 or len(lines) != 1
            or codes[0].get('id') != lines[0].get('id')
            or codes[0].get('code') != lines[0].get('code') or lines[0].get('code') != '99213'
            or lines[0].get('units') != 1 or lines[0].get('modifiers')
            or line_terms.get('active') is not True or line_terms.get('has_secondary')
            or line_terms.get('coverage_status_conflict') or line_terms.get('network_conflict')
            or line_terms.get('historical_only')
            or (line_terms.get('identity') and
                (not patient_benefits_bound(line_terms['identity'])
                 or line_terms['identity'].get('binding') != benefits['identity'].get('binding')))):
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0 at check-in for the proposed CPT 99213 video visit. Aetna reported $0 remaining '
                'on an individual in-network medical out-of-pocket limit in the saved response. '
                'This is a clinic deferral, not a confirmed patient share: provider tier, telehealth billing '
                'and final liability follow the processed claim.'),
        patient_message='No payment is requested at arrival. Final responsibility follows the processed Aetna claim.',
        collection_rule={'mode': 'aetna_televisit_oop_deferral',
                         'reference': 'Patient-bound Aetna medical out-of-pocket reference; claim-after-service deferral'})
    guidance.pop('policy_setup_required', None)


def apply_aetna_office_individual_oop_deferral(result: dict, guidance: dict, *,
                                               member_bound: bool, source_errors: list,
                                               adverse: bool, same_policy_conflict: bool) -> None:
    """Use a dated clinic fee to explain a covered office visit's $0 check-in."""
    benefits = result.get('benefits') or {}
    plan = result.get('aetna_office_planning') or {}
    product = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    identity = result.get('identity') or {}
    rows = benefits.get('reported_options') or []
    oop = [row for row in rows if row.get('id') == benefits.get('oop_id')
           and row.get('kind') == 'out_of_pocket' and row.get('service_type_code') == '30'
           and row.get('coverage_level') == 'INDIVIDUAL' and row.get('network') == 'IN_NETWORK'
           and row.get('time_period') == 'REMAINING' and row.get('plan_association') == 'medical'
           and row.get('amount_cents') == 0]
    annual = [row for row in rows if row.get('kind') == 'out_of_pocket'
              and row.get('service_type_code') == '30' and row.get('coverage_level') == 'INDIVIDUAL'
              and row.get('network') == 'IN_NETWORK' and row.get('time_period') == 'CALENDAR_YEAR'
              and row.get('plan_association') == 'medical'
              and row.get('amount_cents') == benefits.get('oop_annual_cents')]
    copays = [row for row in rows if row.get('kind') == 'copay'
              and row.get('network') == 'IN_NETWORK'
              and '98' in str(row.get('service_type_code') or '').split(',')
              and 'Specialist Visit or Evaluation' in str(row.get('payer_note') or '')
              and (plan.get('coinsurance_scope') == 'telemedicine_reference'
                   or not any(word in str(row.get('payer_note') or '').casefold()
                              for word in ('telemedicine', 'telehealth')))
              and not row.get('procedure_code') and not row.get('diagnosis_scoped')]
    try:
        prior_day = date.fromisoformat(str(result.get('service_date'))) - date.fromisoformat(
            str((benefits.get('returned_service_dates') or {}).get('start'))) == timedelta(days=1)
    except (TypeError, ValueError):
        prior_day = False
    if (guidance.get('amount_cents') not in {None, 0} or result.get('payment_mode') == 'cash'
            or not member_bound or source_errors or adverse or same_policy_conflict
            or result.get('reference_benefits')
            or not (prior_day or benefits.get('service_date_matches') is True)
            or result.get('refresh_requires_selection') is True
            or (result.get('recovery') or {}).get('reason_code') in
                {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or aetna_fees.category(str(result.get('payer') or ''), product) != 'commercial'
            or product != ['Aetna Choice POS II']
            or (benefits.get('plan_details') or {}).get('names') != product
            or benefits.get('active') is not True
            or (benefits.get('service_date_matches') is not False and not
                (benefits.get('service_date_matches') is True and
                 (benefits.get('returned_service_dates') or {}).get('start') == result.get('service_date')))
            or benefits.get('network_status') == 'out_of_network'
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('network_conflict') or benefits.get('historical_only')
            or benefits.get('has_secondary') or benefits.get('qmb_status') == 'protected'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or benefits.get('separate_copay_limit') or benefits.get('copay_remaining_cents') is not None
            or benefits.get('oop_remaining_cents') != 0
            or type(benefits.get('oop_annual_cents')) is not int or benefits['oop_annual_cents'] <= 0
            or benefits.get('oop_remaining_conflict_ids') or benefits.get('deductible_remaining_cents') != 0
            or benefits.get('deductible_remaining_conflict_ids')
            or not patient_benefits_bound(benefits.get('identity'))
            or (benefits['identity'].get('binding') != identity.get('binding'))
            or not oop or len(annual) != 1 or not copays
            or any(row.get('amount_cents') != 0 for row in copays)
            or any(row.get('plan_index') is not None and oop[0].get('plan_index') is not None
                   and row['plan_index'] != oop[0]['plan_index'] for row in annual)
            or any(row.get('kind') == 'copay' and row.get('network') == 'IN_NETWORK'
                   and row.get('service_type_code') == '98' and row.get('amount_cents') != 0
                   for row in rows)
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or len(codes) != 1
            or codes[0].get('id') != lines[0].get('id')
            or codes[0].get('code') != lines[0].get('code')
            or lines[0].get('code') not in {'99212', '99213', '99214', '99215'}
            or lines[0].get('units') != 1 or lines[0].get('modifiers')
            or (lines[0].get('benefits') or {}).get('active') is not True
            or plan.get('status') != 'planning_only' or plan.get('code') != lines[0].get('code')
            or plan.get('service_date') != result.get('service_date')
            or plan.get('payer_response_date') != (benefits.get('returned_service_dates') or {}).get('start')
            or plan.get('oop_remaining_cents') != 0 or plan.get('deductible_remaining_cents') != 0
            or type(plan.get('allowed_cents')) is not int or plan['allowed_cents'] <= 0
            or type(plan.get('gross_coinsurance_cents')) is not int
            or not 0 <= plan['gross_coinsurance_cents'] <= plan['allowed_cents']
            or plan.get('deductible_waived_cents') != 0 or plan.get('deductible_applies_cents') != 0):
        return
    fee = plan['allowed_cents'] / 100
    gross = plan['gross_coinsurance_cents'] / 100
    scope_note = (' The 20% row is marked telemedicine by Aetna, so the gross figure is an '
                  'illustration, not a confirmed in-person office rate; the $0 medical cap '
                  'drives the check-in decision.' if plan.get('coinsurance_scope') == 'telemedicine_reference' else '')
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=(f'Collect $0 at check-in for proposed CPT {plan["code"]}. The clinic Aetna fee '
                f'reference is ${fee:,.2f}; {plan["coinsurance_percent"]}% × ${fee:,.2f} '
                f'= ${gross:,.2f} gross reference coinsurance.{scope_note} Aetna reports $0 remaining on the '
                'individual in-network medical out-of-pocket maximum, so '
                f'min(${gross:,.2f}, $0.00) = $0.00 for covered in-network care. '
                'The fee is a clinic reference; the claim determines Aetna’s final allowance.'),
        patient_message='No payment is requested at arrival for the proposed covered office visit.',
        collection_rule={'mode': 'aetna_office_individual_oop_deferral',
                         'reference': 'Patient-bound Aetna office and medical out-of-pocket benefits; clinic fee schedule'})
    guidance.pop('policy_setup_required', None)


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
