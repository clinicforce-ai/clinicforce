"""Clinic check-in decision when an insured visit has no supported advance."""
from __future__ import annotations


def apply(result: dict, guidance: dict) -> dict:
    if (result.get('payment_mode') == 'cash'
            or result.get('nmct_fixed_office_copay_advance_enabled') is not True
            or guidance.get('amount_cents') is not None):
        return guidance
    unresolved = guidance.get('reason') or 'The insured patient share is unresolved.'
    return {**guidance,
        'amount_cents': 0,
        'status': 'zero_advance',
        'basis': 'claim_after_service',
        'reason': ('Collect $0 at arrival under the clinic no-upfront-collection default. '
                   'The final patient share is not calculated; any lawful balance follows '
                   'coverage reconciliation and claim processing or applicable self-pay terms.'),
        'unresolved_collection_reason': unresolved,
        'patient_message': ('No payment is requested at arrival. This does not mean the visit is free; '
                            'any later balance depends on coverage, the processed claim or self-pay terms, '
                            'and applicable billing protections.'),
        'collection_rule': {'mode': 'clinic_zero_arrival_default',
                            'reference': 'NMCT default: no insured advance without supported evidence'},
        'policy_setup_required': False}
