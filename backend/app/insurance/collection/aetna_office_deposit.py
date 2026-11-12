"""Visit-approved refundable Aetna office deposit from a clinic fee reference."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, review: dict | None, *, safe: bool,
          adverse: bool, source_errors: list, same_policy_conflict: bool) -> None:
    quote = (review or {}).get('insurance_office_deposit') or {}
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    identity = result.get('identity') or benefits.get('identity') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    context = result.get('appointment_context') or {}
    if (guidance.get('amount_cents') is not None or not safe or adverse or source_errors
            or same_policy_conflict or result.get('payment_mode') == 'cash'
            or result.get('refresh_requires_selection') is True
            or str(result.get('payer') or '').strip().casefold() != 'aetna'
            or names != ['Aetna Choice POS II']
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not verified_in_network(benefits) or not patient_benefits_bound(identity)
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('source_errors')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or len(result.get('codes') or []) != 1
            or len(result.get('selected_ids') or []) != 1
            or context.get('place_of_service') != '11'
            or not all(quote.get(key) for key in ('reference', 'approved_at', 'actor_id'))
            or quote.get('context_token') != (review or {}).get('context_token')
            or quote.get('service_date') != result.get('service_date')
            or quote.get('patient_binding') != identity.get('binding')
            or quote.get('schedule') != 'MPPO'
            or type(quote.get('amount_cents')) is not int
            or not 0 < quote['amount_cents'] <= 100_000_000):
        return
    line, code = lines[0], result['codes'][0]
    terms = line.get('benefits') or {}
    if (line.get('id') != quote.get('line_id') or line.get('id') != code.get('id')
            or line.get('id') != result['selected_ids'][0]
            or line.get('code') != code.get('code') or line.get('code') != '99213'
            or line.get('units') != code.get('units') or line.get('units') != 1
            or line.get('modifiers') or code.get('modifiers')
            or line.get('allowed_cents') is not None
            or terms.get('active') is not True or terms.get('service_date_matches') is False
            or terms.get('has_secondary') or terms.get('source_errors')
            or terms.get('coverage_status_conflict') or terms.get('coverage_dates_invalid')
            or terms.get('copay_cents') is not None
            or terms.get('coinsurance_percent') is not None
            or terms.get('deductible_applies') is True
            or terms.get('oop_remaining_cents') == 0
            or (type(terms.get('family_oop_remaining_cents')) is int
                and terms['family_oop_remaining_cents'] < quote['amount_cents'])
            or benefits.get('copay_cents') is not None
            or benefits.get('coinsurance_percent') is not None
            or benefits.get('oop_remaining_cents') == 0
            or (type(benefits.get('family_oop_remaining_cents')) is int
                and benefits['family_oop_remaining_cents'] < quote['amount_cents'])
            or benefits.get('family_oop_zero_applies') is True):
        return
    payments = (review or {}).get('payments') or {}
    paid = payments.get('amount_cents')
    if not payments.get('reference') or type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, quote['amount_cents'] - paid), status='estimated',
        basis='aetna_office_refundable_deposit', reference_total_cents=quote['amount_cents'],
        reason=('Collect a refundable clinic deposit for one office CPT 99213 using the '
                'administrator-entered Availity MPPO Open Choice fee maximum. The MPPO schedule '
                'has not been verified as this Choice POS II member’s allowance, and the payer did '
                'not return a usable office copay or coinsurance. Apply the deposit to the processed '
                'claim and refund any excess after adjudication.'),
        patient_message=('This is a refundable clinic deposit, not your final Aetna share. '
                         'Any excess will be refunded after your claim is processed.'),
        collection_rule={'mode': 'visit_approved_aetna_office_deposit',
                         'reference': quote['reference'], 'approved_at': quote['approved_at']})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
