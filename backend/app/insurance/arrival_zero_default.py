"""Clinic check-in decision when an insured visit has no supported advance."""
from __future__ import annotations


def apply(result: dict, guidance: dict) -> dict:
    amount = guidance.get('amount_cents')
    if (result.get('nmct_fixed_office_copay_advance_enabled') is not True
            or (type(amount) is int and 0 <= amount <= 100_000_000)):
        return guidance
    cash = result.get('payment_mode') == 'cash'
    unresolved = guidance.get('reason') or ('The cash price is unresolved.' if cash else 'The insured patient share is unresolved.')
    return {**guidance,
        'amount_cents': 0,
        'status': 'zero_advance',
        'basis': 'cash_price_pending' if cash else 'claim_after_service',
        'reason': ('Collect $0 at arrival under the clinic no-upfront-collection default. '
                   'The cash price is still unresolved; provide the self-pay estimate '
                   'and settle only the agreed lawful balance later.' if cash else
                   'Collect $0 at arrival under the clinic no-upfront-collection default. '
                   'The final patient share is not calculated; any lawful balance follows '
                   'coverage reconciliation and claim processing or applicable self-pay terms.'),
        'unresolved_collection_reason': unresolved,
        'patient_message': ('No payment is requested at arrival. The cash price has not been settled.'
                            if cash else 'No payment is requested at arrival. This does not mean the visit is free; '
                            'any later balance depends on coverage, the processed claim or self-pay terms, '
                            'and applicable billing protections.'),
        'collection_rule': {'mode': 'clinic_zero_arrival_default',
                            'reference': 'NMCT default: no advance without supported evidence'},
        'policy_setup_required': False}
