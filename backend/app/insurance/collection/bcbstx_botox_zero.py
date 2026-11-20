"""BCBS Texas Botox zero check-in decision from patient-bound office injection terms."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network


def apply_bcbstx_botox_family_oop_deferral(result: dict, guidance: dict, *,
                                           member_bound: bool, source_errors: list,
                                           adverse: bool, same_policy_conflict: bool) -> None:
    """Defer check-in for a patient-bound, met-family-cap office injection preview."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    policy = result.get('collection_policy') or {}
    card = result.get('card_check') or {}
    rows = benefits.get('reported_options') or []
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    if (guidance.get('amount_cents') is not None or not member_bound or source_errors
            or adverse or same_policy_conflict or result.get('payment_mode') != 'insurance'
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or names != ['PREFERRED PROVIDER OPTION MEDICAL']
            or (result.get('service_plan') or {}).get('kind') != 'botox'
            or result.get('service_selection_status') != 'preview'
            or result.get('codes') or result.get('selected_ids') or price.get('lines')
            or result.get('separate_em_planned') or result.get('reference_benefits')
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or '98' not in (result.get('requested_service_types') or [])
            or not result.get('checked_at') or not result.get('service_date')
            or benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or not verified_in_network(benefits) or benefits.get('has_secondary')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('network_conflict')
            or benefits.get('oop_remaining_conflict_ids')
            or benefits.get('family_oop_remaining_cents') not in (None, 0)
            or result.get('refresh_requires_selection') is True
            or (result.get('recovery') or {}).get('reason_code') in
               {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or card.get('status') in {'conflict', 'mismatch'} or card.get('conflicts')
            or card.get('independent_ecw_check') is True
            or not patient_benefits_bound(benefits.get('identity'))
            or benefits['identity'].get('binding') != identity.get('binding')
            or policy.get('mode') != 'collect_supported_advance'
            or not all(policy.get(key) for key in ('approved_by', 'approved_at', 'reference'))):
        return
    try:
        age = date.fromisoformat(result['service_date']) - date.fromisoformat(result['checked_at'][:10])
    except (KeyError, TypeError, ValueError):
        return
    if not timedelta(0) <= age <= timedelta(days=31):
        return

    def injection(row: dict) -> bool:
        return (row.get('network') in {'IN_NETWORK', 'Y'}
                and row.get('plan_association') == 'medical'
                and bool(re.search(r'\bINJECTIONS\s*-\s*PROFESSIONAL\b',
                                   str(row.get('payer_note') or ''), re.I)))

    coinsurance = [row for row in rows if injection(row) and row.get('kind') == 'coinsurance'
                   and row.get('service_type_code') == 'BY'
                   and row.get('coverage_level') == 'INDIVIDUAL'
                   and '11' in (row.get('places_of_service') or [])]
    family = {kind: [row for row in rows if injection(row) and row.get('kind') == kind
                     and row.get('service_type_code') == '30'
                     and row.get('coverage_level') == 'FAMILY'
                     and row.get('time_period') == 'REMAINING']
              for kind in ('deductible', 'out_of_pocket')}
    copays = [row for row in rows if row.get('kind') == 'copay'
              and row.get('network') in {'IN_NETWORK', 'Y'}
              and row.get('service_type_code') in {'BY', '98', '30'}
              and row.get('plan_association') in {None, 'medical'}
              and (not row.get('places_of_service') or '11' in row['places_of_service'])]
    if (len(coinsurance) != 1 or not all(len(items) == 1 for items in family.values())
            or any(row.get('amount_cents') != 0 for items in family.values() for row in items)
            or any(row.get('amount_cents') != 0 for row in copays)
            or any(injection(row) and row.get('coverage_level') == 'INDIVIDUAL'
                   and row.get('kind') in {'deductible', 'out_of_pocket'}
                   and row.get('time_period') == 'REMAINING'
                   and row.get('amount_cents') not in (None, 0) for row in rows)
            or benefits.get('separate_copay_limit')):
        return
    try:
        if Decimal(str(coinsurance[0]['percent'])) != 0:
            return
    except (KeyError, InvalidOperation, TypeError):
        return
    plan_indexes = {row.get('plan_index') for row in
                    [coinsurance[0], *family['deductible'], *family['out_of_pocket']]}
    if len(plan_indexes) != 1:
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0.00 at check-in for the planned office injection. The patient-bound BCBS Texas '
                'response reports 0% injection coinsurance and $0 remaining on the medical family '
                'in-network deductible and out-of-pocket maximum, with no applicable injection copay. '
                'The billable procedure, drug units and insurer allowance remain unconfirmed; final '
                'responsibility follows the processed claim.'),
        patient_message='No payment is requested at arrival. Final responsibility follows the processed insurance claim.',
        collection_rule={'mode': 'bcbstx_botox_family_oop_deferral',
                         'reference': 'BCBS Texas office injection benefit and medical family accumulators'})
    guidance.pop('policy_setup_required', None)
