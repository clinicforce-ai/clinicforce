"""Prepare network and out-of-pocket evidence for collection decisions."""

from datetime import date, timedelta

from app.insurance.network_participation import verified_in_network


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
