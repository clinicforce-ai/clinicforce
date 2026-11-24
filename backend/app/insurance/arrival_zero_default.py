"""Clinic check-in decision when an insured visit has no supported advance."""
from __future__ import annotations

from app.insurance.collection import stedi_office_copay


def apply(result: dict, guidance: dict, review: dict | None = None) -> dict:
    guidance = stedi_office_copay.apply(result, guidance, review)
    if result.get('eligibility_request_state') in {'pending', 'indeterminate'}:
        return {**guidance, 'amount_cents': None, 'status': 'action_required',
                'basis': 'eligibility_pending',
                'reason': 'The payer eligibility check has not completed; the visit collection amount is pending.',
                'patient_message': 'The visit payment amount will be available after the payer check completes.'}
    identity = result.get('identity') or (result.get('benefits') or {}).get('identity') or {}
    unresolved_identity = identity.get('status') == 'review' and (
        identity.get('differences') or identity.get('policy_differences'))
    rechecked_without_advance = (identity.get('reconciliation_rule') == 'payer_name_recheck'
        and guidance.get('amount_cents') is None)
    if result.get('payment_mode') != 'cash' and (unresolved_identity or rechecked_without_advance):
        pending = {**guidance, 'amount_cents': None, 'status': 'action_required',
                   'basis': 'identity_recheck_required' if unresolved_identity else 'collection_review_required',
                   'reason': ('The payer and eCW member details differ. Reconcile the member and plan with the payer before setting a visit collection amount.'
                              if unresolved_identity else
                              'The payer-name recheck matched this member, but the visit collection requirements remain unresolved. Review the sourced office benefit and planned service before collecting.'),
                   'patient_message': 'The visit payment amount is pending a member-detail check.'}
        pending.pop('collection_rule', None)
        potential = stedi_office_copay.potential(result, review)
        if potential:
            pending['potential_office_collection'] = potential
        return pending
    amount = guidance.get('amount_cents')
    if result.get('nmct_fixed_office_copay_advance_enabled') is not True:
        return guidance
    if type(amount) is int and 0 <= amount <= 100_000_000:
        if amount == 0:
            potential = stedi_office_copay.potential(result, review)
            if potential:
                return {**guidance, 'potential_office_collection': potential}
        return guidance
    pending = {**guidance}
    if amount is not None:
        pending.update(amount_cents=None, status='action_required', basis='invalid_collection_amount',
                       reason='The visit collection amount is invalid. Recalculate the estimate before collecting.',
                       patient_message='The visit payment amount is pending a corrected estimate.')
        pending.pop('collection_rule', None)
    potential = stedi_office_copay.potential(result, review)
    if potential:
        pending['potential_office_collection'] = potential
    return pending
