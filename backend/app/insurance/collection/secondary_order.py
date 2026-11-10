"""Collection gates when a visit has a named secondary payer."""

from app.insurance.stedi_benefits.payer_order import verified_primary_order


def state(result: dict) -> tuple[bool, bool, bool, bool]:
    recovery = result.get('recovery') or {}
    benefits = result.get('benefits') or {}
    unresolved = recovery.get('reason_code') == 'multiple_active_policies'
    conflict = recovery.get('reason_code') == 'same_policy_evidence_conflict'
    verified = verified_primary_order(result) and not conflict
    secondary_name = str((benefits.get('primary_secondary_order') or {}).get('secondary_payer') or '')
    medicare = verified and secondary_name.strip().casefold().startswith('medicare')
    return unresolved, conflict, verified and not medicare, medicare


def allowed_advance(amount: int | None, medicare_secondary: bool) -> int | None:
    return None if medicare_secondary and amount is not None and amount > 0 else amount


def action(medicare_secondary: bool) -> str:
    if medicare_secondary:
        return ('eCW and the primary payer list Medicare as secondary. Verify Medicare eligibility, '
                'submit the primary claim, then the Medicare secondary claim; collect any patient '
                'balance only after the remittance.')
    return 'More than one plan may pay for this visit. The collection amount depends on how those plans coordinate benefits.'
