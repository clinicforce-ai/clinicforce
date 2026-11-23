"""Collection gates when a visit has a named secondary payer."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance.stedi_benefits.payer_order import verified_primary_order
from app.insurance.stedi_payers import original_medicare_route
from app.insurance.insurance_product import classify


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


def original_medicare_first_in_ecw(result: dict) -> bool:
    """Use the recorded policy sequence for a provisional advance, not final COB."""
    selected = (result.get('insurance_details') or {}).get('coverage_id')
    day = str(result.get('service_date') or '')[:10]
    rows = [row for row in result.get('coverage_records') or [] if isinstance(row, dict)
            and row.get('status') == 'active'
            and (not (row.get('period') or {}).get('start') or str(row['period']['start'])[:10] <= day)
            and (not (row.get('period') or {}).get('end') or day <= str(row['period']['end'])[:10])]
    if (not selected or not day or len(rows) != 2 or {row.get('order') for row in rows} != {1, 2}
            or not any(row.get('id') == selected and row.get('order') == 1 for row in rows)):
        return False
    coordination = result.get('coordination') or {}
    roles = coordination.get('payer_roles') or []
    if coordination.get('status') == 'primacy_conflict' or (
            coordination.get('status') == 'primacy_reported'
            and not any(row.get('role') == 'Primary Payer' and
                        any(name in str(row.get('payer_name') or '').casefold()
                            for name in ('medicare', 'cms')) for row in roles)) or any(
            any(name in str(row.get('payer_name') or '').casefold()
                for name in ('medicare', 'cms')) and row.get('role') != 'Primary Payer'
            for row in roles):
        return False
    return True


def original_medicare_office_share(result: dict, *, safe: bool, qmb_protected: bool) -> int | None:
    """Return a sourced Part B office share before any secondary payment."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    payer_id = (result.get('stedi_payer') or {}).get('id')
    names = ((benefits.get('plan_details') or {}).get('names') or
             ((result.get('plan_details') or {}).get('stedi') or {}).get('names'))
    original = (benefits.get('original_medicare') is True or
                original_medicare_route({'stediId': payer_id})
                and classify(names, payer_id=payer_id) == 'original_medicare')
    policy = result.get('collection_policy') if isinstance(result.get('collection_policy'), dict) else {}
    line = lines[0] if len(lines) == 1 else {}
    terms = line.get('benefits') or {}
    allowed = line.get('allowed_cents')
    patient = (line.get('calculation') or {}).get('patient_cents')
    secondary = benefits.get('has_secondary') is True
    if not (safe and not qmb_protected and original
            and not classify(names or [], payer_id=payer_id).startswith('medicare_advantage_')
            and not (benefits.get('medicare_part_b_secondary') or {}).get('status') == 'reported'
            and (not secondary and (result.get('recovery') or {}).get('reason_code') != 'multiple_active_policies'
                 or secondary and original_medicare_first_in_ecw(result))
            and policy.get('mode') != 'defer_to_claim'
            and price.get('mode') == 'services' and len(lines) == 1
            and not price.get('conditional') and not price.get('service_issues')
            and not line.get('conditions') and line.get('code') in {'99202', '99203', '99204', '99205',
                                                                     '99212', '99213', '99214', '99215'}
            and line.get('units') == 1 and line.get('allowed_basis') == 'medicare_fee_schedule'
            and type(allowed) is int and 0 < allowed <= 100_000_000
            and (terms.get('active') is True or terms.get('active') is None)
            and terms.get('service_date_matches') is not False
            and not any(terms.get(key) for key in ('source_errors', 'coverage_status_conflict',
                                                  'coverage_dates_invalid', 'network_conflict', 'historical_only'))
            and type(terms.get('deductible_remaining_cents')) is int
            and terms['deductible_remaining_cents'] == 0
            and terms.get('copay_cents') in {None, 0}):
        return None
    try:
        percent = Decimal(str(terms.get('coinsurance_percent')))
    except (InvalidOperation, ValueError, TypeError):
        return None
    expected = int((Decimal(allowed) / 5).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    if not percent.is_finite() or percent != 20:
        return None
    if secondary:
        if ((line.get('rate_resolution') or {}).get('status') != 'RESOLVED'
                or any(issue != 'Confirm which plan pays first and how additional coverage applies.'
                       for issue in line.get('issues') or [])):
            return None
        return expected
    return patient if (price.get('complete') is True and type(patient) is int
                       and price.get('patient_min_cents') == price.get('patient_max_cents') == patient
                       and patient == expected) else None


def apply_primary_medicare_advance(guidance: dict, share: int | None) -> int | None:
    if share is None:
        return None
    guidance.update(status='estimated', basis='medicare_primary_coinsurance_advance',
        primary_medicare_share_cents=share,
        reason=('Provisional Original Medicare Part B advance: 20% of the clinic Medicare fee for the selected office CPT after a $0 remaining deductible. '
                'eCW lists Medicare first; a second policy may reduce the final patient balance after both claims.'),
        collection_rule={'mode': 'original_medicare_office_coinsurance',
                         'reference': 'Clinic-approved Medicare fee schedule and dated Part B benefit'})
    return share


def apply_original_medicare_deferral(result: dict, guidance: dict, source_errors: list,
                                     member_bound: bool, adverse: bool) -> None:
    """Defer collection for a verified Original Medicare office visit, preserving liability."""
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    payer_id = (result.get('stedi_payer') or {}).get('id')
    names = ((benefits.get('plan_details') or {}).get('names') or
             ((result.get('plan_details') or {}).get('stedi') or {}).get('names'))
    if ((benefits.get('original_medicare') is True or
         original_medicare_route({'stediId': payer_id})
         and classify(names, payer_id=payer_id) == 'original_medicare')
            and benefits.get('active') is True and benefits.get('service_date_matches') is not False
            and member_bound and not source_errors and not adverse
            and not benefits.get('coverage_status_conflict') and not benefits.get('coverage_dates_invalid')
            and not benefits.get('historical_only') and not benefits.get('network_conflict')
            and result.get('refresh_requires_selection') is not True
            and not result.get('eligibility_failure')
            and price.get('mode') in {'services', 'alternatives'} and lines
            and guidance.get('amount_cents') is None
            and (price.get('mode') == 'alternatives' or benefits.get('has_secondary')
                 or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies')
            and all(line.get('code') in {'99202', '99203', '99204', '99205',
                                         '99212', '99213', '99214', '99215'}
                    and line.get('units') == 1 for line in lines)):
        guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
            reason='Clinic policy collects no advance for a verified Original Medicare office visit. Submit the claim and use the remittance to determine any later patient balance.',
            patient_message='No payment is requested at arrival. Final responsibility follows the processed Medicare claim and member protections.')
        guidance.pop('policy_setup_required', None)
        guidance.pop('positive_advance_policy_missing', None)
        guidance['collection_rule'] = {'mode': 'original_medicare_office_deferral',
            'reference': 'Clinic Original Medicare collection policy'}


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
