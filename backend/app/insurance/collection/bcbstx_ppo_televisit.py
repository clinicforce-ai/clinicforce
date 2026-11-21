"""Blue PPO home televisit check-in deferral with unresolved final share."""

from datetime import date, timedelta

from app.insurance.collection.requirements import requirement_blocker
from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network

OFFICE_CODES = {'99202', '99203', '99204', '99205', '99212', '99213', '99214', '99215'}


def apply(result: dict, guidance: dict) -> None:
    """Choose no advance for an unpriced, insured Blue PPO home office visit."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    context = result.get('appointment_context') or {}
    policy = result.get('collection_policy') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    codes = result.get('codes') or []
    lines = price.get('lines') or []
    card = result.get('card_check') or {}
    if (guidance.get('amount_cents') is not None
            or result.get('payment_mode') != 'insurance'
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or names != ['PREFERRED PROVIDER OPTION MEDICAL']
            or context.get('appointment_type') != 'Televisit'
            or context.get('place_of_service') != '10'
            or (result.get('service_plan') or {}).get('kind') != 'office'
            or result.get('separate_em_planned') or result.get('reference_benefits')
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or '98' not in (result.get('requested_service_types') or [])
            or not result.get('checked_at') or not result.get('service_date')
            or price.get('complete') is True or len(codes) > 1 or len(lines) > 1
            or any(code.get('code') not in OFFICE_CODES or code.get('units') != 1
                   or code.get('modifiers') for code in codes)
            or (lines and (len(codes) != 1 or lines[0].get('code') != codes[0].get('code')
                           or lines[0].get('id') != codes[0].get('id')))
            or benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or benefits.get('copay_cents') is not None
            or not verified_in_network(benefits) or benefits.get('has_secondary')
            or benefits.get('source_errors')
            or any((line.get('benefits') or {}).get('source_errors') for line in lines)
            or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('network_conflict')
            or benefits.get('historical_only') or result.get('refresh_requires_selection') is True
            or (result.get('recovery') or {}).get('reason_code') in
               {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or card.get('status') in {'conflict', 'mismatch'} or card.get('conflicts')
            or card.get('independent_ecw_check') is True
            or not patient_benefits_bound(identity)
            or not patient_benefits_bound(benefits.get('identity'))
            or identity.get('binding') != benefits['identity'].get('binding')
            or requirement_blocker(result)):
        return
    try:
        age = date.fromisoformat(result['service_date']) - date.fromisoformat(result['checked_at'][:10])
    except (TypeError, ValueError):
        return
    if not timedelta(0) <= age <= timedelta(days=31):
        return
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('network') in {'IN_NETWORK', 'Y'}
            and row.get('plan_association') == 'medical']
    office = [row for row in rows if row.get('service_type_code') == '98'
              and '10' in (row.get('places_of_service') or [])]
    coinsurance = [row for row in office if row.get('kind') == 'coinsurance']
    if (len(coinsurance) != 1 or str(coinsurance[0].get('percent')) not in {'20', '20.0', '20.00'}
            or any(row.get('kind') == 'copay' for row in office)
            or any(row.get('kind') == 'copay' and row.get('network') in {'IN_NETWORK', 'Y'}
                   and row.get('plan_association') in {None, 'medical', 'unassigned'}
                   and row.get('service_type_code') in {'30', '98'}
                   and (not row.get('places_of_service') or '10' in row['places_of_service'])
                   for row in benefits.get('reported_options') or [])
            or any(row.get('kind') == 'coinsurance'
                   and row.get('service_type_code') in {'30', '98'}
                   and row.get('plan_index') == coinsurance[0].get('plan_index')
                   and (not row.get('places_of_service') or '10' in row['places_of_service'])
                   and str(row.get('percent')) not in {'20', '20.0', '20.00'} for row in rows)):
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0.00 at check-in for the planned home televisit. BCBS Texas reports '
                '20% in-network office coinsurance, but no matched telehealth allowance or fixed '
                'office copay establishes a dollar advance. Submit the claim and bill any later '
                'patient share only after adjudication. This is not a $0 final patient balance.'),
        patient_message='No payment is requested at check-in. Final responsibility follows the processed insurance claim.',
        collection_rule={'mode': 'bcbstx_ppo_televisit_deferral',
                         'reference': 'BCBS Texas office benefit and clinic claim-after-service decision'})
    guidance.pop('policy_setup_required', None)


