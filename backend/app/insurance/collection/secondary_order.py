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


def apply_part_b_deferral(result: dict, guidance: dict, source_errors: list,
                          member_bound: bool, ma_ppo: bool) -> None:
    """CMS MSP office collection decision; never resolves final claim order."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    if (benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and member_bound and not ma_ppo
            and (benefits.get('medicare_part_b_secondary') or {}).get('status') == 'reported'
            and not source_errors and not benefits.get('coverage_status_conflict')
            and not benefits.get('coverage_dates_invalid') and not benefits.get('historical_only')
            and (result.get('recovery') or {}).get('reason_code') != 'same_policy_evidence_conflict'
            and not benefits.get('network_conflict')
            and result.get('refresh_requires_selection') is not True
            and price.get('mode') == 'services' and len(lines) == 1
            and lines[0].get('code') in {'99202', '99203', '99204', '99205',
                                        '99212', '99213', '99214', '99215'}
            and lines[0].get('units') == 1):
        guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
            reason=('The payer reports Original Medicare Part B as secondary for this patient. '
                    'Collect no office copay at check-in; submit the claims and use the remittances '
                    'to determine any later patient balance.'),
            patient_message=('No payment is requested at arrival. Final responsibility follows '
                             'the primary and Medicare secondary claims.'))
        guidance.pop('policy_setup_required', None)
        guidance.pop('positive_advance_policy_missing', None)
        guidance['collection_rule'] = {'mode': 'cms_msp_part_b_deferral',
            'reference': 'CMS Medicare Secondary Payer Manual, Chapter 5, section 40.7.6'}
