"""Collect nothing now when a member's individual OOP balance is exhausted."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network
from app.insurance import aetna_fees


def apply(result: dict, guidance: dict, lines: list[dict], planning: dict) -> bool:
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    selected = result.get('codes') or []
    esa_office_date_exception = (
        benefits.get('service_date_matches') is False
        and aetna_fees.is_aetna_payer(str(result.get('payer') or ''))
        and len(names) == 1 and isinstance(names[0], str)
        and (benefits.get('plan_details') or {}).get('names') == names
        and ''.join(char for char in names[0].casefold() if char.isalnum())
            in {'medicarev01esappoplan', 'aetnamedicarev01esappoplan'}
        and (result.get('appointment_context') or {}).get('place_of_service') == '11'
        and len(selected) == 1 and selected[0].get('code') in
            {str(code) for code in (*range(99202, 99206), *range(99212, 99216))}
        and selected[0].get('units') == 1 and not selected[0].get('modifiers')
        and benefits.get('network_status') in {'unknown', 'in_network'}
        and not benefits.get('network_conflict') and not benefits.get('separate_copay_limit'))
    if not (type(benefits.get('oop_remaining_cents')) is int
            and benefits['oop_remaining_cents'] == 0 and benefits.get('oop_id')
            and benefits.get('active') is True
            and (esa_office_date_exception or benefits.get('service_date_matches') is not False
                 and (benefits.get('service_date_matches') is True
                      or not benefits.get('returned_service_dates')))
            and patient_benefits_bound(identity) and not benefits.get('historical_only')
            and not benefits.get('coverage_status_conflict')
            and not benefits.get('coverage_dates_invalid')
            and not benefits.get('oop_remaining_conflict_ids')
            and not benefits.get('oop_medical_candidate_ids')
            and not benefits.get('source_errors')
            and result.get('refresh_requires_selection') is not True):
        return False
    price = result.get('price_range') or {}
    priced_zero = (planning.get('basis') == 'out_of_pocket_limit'
                   and verified_in_network(benefits) and not price.get('service_issues'))
    guidance.update(amount_cents=0, status='zero_advance',
        basis='out_of_pocket_limit' if priced_zero else 'claim_after_service',
        reason='The payer reports $0 remaining on the individual out-of-pocket limit. Collect $0 at this visit; final responsibility follows the processed claim.',
        patient_message='No payment is requested at arrival. Final responsibility follows the processed insurance claim.',
        collection_rule={'mode': 'individual_oop_zero_deferral', 'reference': benefits['oop_id']})
    guidance['services'] = [{
        'id': line.get('id'), 'code': line.get('code'),
        'benefit': line.get('benefit_basis') or 'Applicable service benefits',
        'benefit_source': line.get('benefit_source') or result.get('benefit_source'),
        'patient_cents': 0 if priced_zero else None,
        'oop_remaining_cents': (line.get('benefits') or {}).get('oop_remaining_cents'),
        'copay_cents': (line.get('benefits') or {}).get('copay_cents'),
        'coinsurance_percent': (line.get('benefits') or {}).get('coinsurance_percent'),
        'deductible_remaining_cents': (line.get('benefits') or {}).get('deductible_remaining_cents'),
        'deductible_annual_cents': (line.get('benefits') or {}).get('deductible_annual_cents'),
        'deductible_applicability': (line.get('benefits') or {}).get('deductible_applicability', 'UNKNOWN'),
        'selected_benefit_ids': list(dict.fromkeys([
            *((line.get('benefits') or {}).get('selected_benefit_ids') or []),
            *((line.get('benefits') or {}).get(key) for key in ('deductible_id', 'oop_id')
              if (line.get('benefits') or {}).get(key)),
        ])),
    } for line in lines]
    return True
