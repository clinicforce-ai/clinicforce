"""Apply a verified visit-linked eCW billing instruction."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.benefit_review import selected_service_blocker


def apply_billing_note(result: dict, benefits: dict, price: dict, lines: list[dict],
                       guidance: dict, assumptions: list[str], amount: int | None,
                       identity: dict, source_errors: list[dict], adverse: bool) -> tuple[int | None, dict]:
    note = result.get('billing_note') or {}
    if (note and (identity.get('status') != 'review' or
                 (note.get('kind') == 'no_collection' and benefits.get('active') is not True)) and not source_errors
            and not price.get('service_issues') and result.get('payment_mode') != 'cash'):
        office_only = bool(lines) and all(line.get('code', '').isdigit() and
            (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)
            and line.get('units') == 1 for line in lines) and (len(lines) == 1 or price.get('mode') == 'alternatives')
        copay_matches = all((line.get('benefits') or {}).get('copay_cents') in {None, note.get('amount_cents')} for line in lines)
        if note['kind'] == 'no_collection':
            amount = 0
            guidance.update(status='no_advance', basis='ecw_billing_note',
                reason='The eCW billing instruction for this visit says not to collect from the patient.')
        elif (amount is None and note['kind'] == 'office_copay' and office_only and copay_matches
                and not adverse and benefits.get('active') is not False
                and benefits.get('service_date_matches') is not False and not benefits.get('has_secondary')
                and not benefits.get('coverage_status_conflict') and not benefits.get('coverage_dates_invalid')
                and not benefits.get('network_conflict') and not benefits.get('historical_only')
                and selected_service_blocker(benefits.get('network_status') or 'unknown', identity, lines) is None
                and (result.get('recovery') or {}).get('reason_code') != 'multiple_active_policies'
                and (benefits.get('active') is not True or patient_benefits_bound(identity))
                and benefits.get('network_status') != 'out_of_network'
                and (benefits.get('active') is not True or benefits.get('network_status') == 'in_network')
                and all((line.get('benefits') or {}).get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                    and (line.get('benefits') or {}).get('deductible_remaining_cents') in {None, 0}
                    and (line.get('benefits') or {}).get('active') is not False for line in lines)):
            amount = note['amount_cents']
            guidance.update(status='estimated', basis='ecw_billing_note',
                reason='Office-visit copay recorded in the eCW billing note; the note records active coverage and a met deductible.')
        if guidance['basis'] == 'ecw_billing_note':
            guidance.pop('range_min_cents', None)
            guidance.pop('range_max_cents', None)
            guidance['billing_note'] = note
            assumptions.append('Source: the visit-linked eCW billing note, not a completed payer eligibility check. Final responsibility follows claim processing.')
    return amount, note
