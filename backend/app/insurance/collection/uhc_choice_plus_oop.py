"""Defer an unpriced Choice Plus office collection when the individual limit is ambiguous."""

from datetime import date

from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, *, safe: bool, unconfirmed_zero_oop: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('kind') == 'out_of_pocket' and row.get('service_type_code') == '30'
            and row.get('coverage_level') == 'INDIVIDUAL' and row.get('time_period') == 'REMAINING'
            and row.get('network') == 'IN_NETWORK' and row.get('amount_cents') == 0
            and row.get('plan_association') in {'unassigned', 'medical_candidate'}]
    family = {kind: [row for row in benefits.get('reported_options') or []
                     if row.get('kind') == kind and row.get('service_type_code') == '30'
                     and row.get('coverage_level') == 'FAMILY'
                     and row.get('time_period') == 'REMAINING'
                     and row.get('network') == 'IN_NETWORK']
              for kind in ('deductible', 'out_of_pocket')}
    try:
        check_age = (date.fromisoformat(str(result.get('service_date') or ''))
                     - date.fromisoformat(str(result.get('checked_at') or '')[:10])).days
    except ValueError:
        check_age = None
    if (not safe or not unconfirmed_zero_oop or guidance.get('amount_cents') is not None
            or guidance.get('integrity_error') or result.get('payment_mode') == 'cash'
            or str(result.get('payer') or '').casefold().replace(' ', '') != 'unitedhealthcare'
            or len(names) != 1 or str(names[0]).upper() not in
               {'CHOICE PLUS', 'POS CHOICE PLUS', 'UNITEDHEALTHCARE CHOICE PLUS'}
            or check_age not in {0, 1, 2}
            or not verified_in_network(benefits) or benefits.get('oop_remaining_conflict_ids')
            or any(len(family[kind]) != 1 or type(family[kind][0].get('amount_cents')) is not int
                   or family[kind][0]['amount_cents'] <= 0 for kind in family)
            or benefits.get('copay_cents') is not None
            or (benefits.get('service_date_matches') is not True
                and benefits.get('returned_service_dates'))
            or any(row.get('kind') == 'out_of_pocket' and row.get('service_type_code') == '30'
                   and row.get('coverage_level') == 'INDIVIDUAL'
                   and row.get('time_period') == 'REMAINING'
                   and row.get('network') == 'IN_NETWORK' and row not in rows
                   for row in benefits.get('reported_options') or [])
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or price.get('mode') != 'services' or price.get('complete') is True
            or len(lines) != len(codes) or len(lines) != 1 or len(rows) != 1):
        return
    line, code = lines[0], codes[0]
    if (line.get('id') != code.get('id') or line.get('code') != code.get('code')
            or line.get('code') != '99214'
            or line.get('units') != code.get('units') or line.get('units') != 1
            or line.get('modifiers') or code.get('modifiers')
            or line.get('allowed_cents') is not None or (line.get('benefits') or {}).get('active') is False
            or (line.get('benefits') or {}).get('copay_cents') is not None):
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='claim_after_service',
        reason=('Collect $0 at check-in for the proposed office visit. UHC reports $0 remaining on an '
                'individual in-network out-of-pocket limit, but its application to this medical service '
                'is unconfirmed and the contracted rate is unavailable. Submit the claim to determine '
                'any patient balance; this is a collection deferral, not a $0 patient-share estimate.'),
        patient_message='No payment is requested at arrival. Any patient balance follows the processed insurance claim.',
        collection_rule={'mode': 'uhc_choice_plus_unassigned_oop_deferral',
                         'reference': rows[0].get('id') or 'UHC individual in-network out-of-pocket row'})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
    guidance.pop('unconfirmed_zero_oop', None)
