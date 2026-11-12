"""Dated, clinic-directed refundable office deposit for Aetna Choice POS II."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network


OFFICE_REFERENCE_CENTS = 7553
REFERENCE_DATE = '2026-11-12'


def apply(result: dict, guidance: dict, review: dict | None, *, safe: bool,
          adverse: bool, source_errors: list, same_policy_conflict: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    plan_names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    if (guidance.get('amount_cents') is not None or not safe or adverse or source_errors
            or same_policy_conflict or result.get('payment_mode') == 'cash'
            or result.get('refresh_requires_selection') is True
            or str(result.get('payer') or '').strip().casefold() != 'aetna'
            or plan_names != ['Aetna Choice POS II']
            or result.get('service_date') != REFERENCE_DATE
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not verified_in_network(benefits) or not patient_benefits_bound(identity)
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('source_errors')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or len(codes) != 1 or len(result.get('selected_ids') or []) != 1):
        return
    line, code = lines[0], codes[0]
    terms = line.get('benefits') or {}
    family_remaining = terms.get('family_oop_remaining_cents')
    if (line.get('id') != code.get('id') or line.get('id') != result['selected_ids'][0]
            or line.get('code') != code.get('code') or line.get('code') != '99213'
            or line.get('units') != 1 or code.get('units') != 1
            or line.get('modifiers') or code.get('modifiers')
            or line.get('allowed_cents') is not None
            or terms.get('active') is not True or terms.get('service_date_matches') is False
            or terms.get('has_secondary') or terms.get('source_errors')
            or terms.get('coverage_status_conflict') or terms.get('coverage_dates_invalid')
            or terms.get('copay_cents') is not None or terms.get('coinsurance_percent') is not None
            or terms.get('deductible_applies') is not False or terms.get('oop_remaining_cents') == 0
            or benefits.get('copay_cents') is not None or benefits.get('coinsurance_percent') is not None
            or benefits.get('oop_remaining_cents') == 0 or benefits.get('family_oop_zero_applies') is True
            or type(family_remaining) is not int or family_remaining < OFFICE_REFERENCE_CENTS):
        return
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents') if payments.get('reference') else None
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, OFFICE_REFERENCE_CENTS - paid), status='estimated',
        basis='aetna_office_refundable_deposit', reference_total_cents=OFFICE_REFERENCE_CENTS,
        reason=('Collect a refundable clinic deposit for one office CPT 99213 using the dated '
                'Availity MPPO Office fee maximum supplied by the clinic. MPPO has not been '
                'verified as this Choice POS II member’s contracted allowance. Final patient '
                'responsibility follows the processed Aetna claim; refund any excess.'),
        patient_message=('This is a refundable clinic deposit, not your final Aetna share. '
                         'Any excess will be refunded after your claim is processed.'),
        collection_rule={'mode': 'aetna_choice_office_reference_deposit',
                         'reference': 'Clinic-supplied Availity MPPO Office CPT 99213 lookup, 2026-11-12'})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
