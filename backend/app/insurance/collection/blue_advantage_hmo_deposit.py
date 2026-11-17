"""Clinic-directed, capped refundable advance for a Blue Advantage HMO office visit."""

from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, review: dict | None, *, safe: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    reference = result.get('temporary_rate_reference') or {}
    fee_rows = reference.get('lines') or []
    policy = result.get('collection_policy') or {}
    if (guidance.get('amount_cents') is not None or not safe
            or result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or result.get('payment_mode') == 'cash' or result.get('reference_benefits')
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or 'blue cross' not in str(result.get('payer') or '').casefold()
            or ((result.get('plan_details') or {}).get('stedi') or {}).get('names') != ['Blue Advantage HMO']
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or not result.get('checked_at') or benefits.get('active') is not True
            or benefits.get('service_date_matches') is False or not verified_in_network(benefits)
            or benefits.get('has_secondary') or benefits.get('oop_remaining_conflict_ids')
            or benefits.get('oop_medical_candidate_ids') or benefits.get('oop_quarantined_ids')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != len(codes) or len(lines) != len(result.get('selected_ids') or [])
            or len(lines) != len(fee_rows) or len(lines) != 1
            or reference.get('complete') is not True):
        return
    line, code, fee_row = lines[0], codes[0], fee_rows[0]
    terms = line.get('benefits') or {}
    fee = fee_row.get('unit_reference_cents')
    oop = benefits.get('oop_remaining_cents')
    if (line.get('code') != '99203' or line.get('units') != 1 or line.get('modifiers')
            or line.get('allowed_cents') is not None
            or code.get('id') != line.get('id') or code.get('code') != '99203'
            or code.get('units') != 1 or code.get('modifiers')
            or result['selected_ids'] != [line.get('id')]
            or terms.get('active') is not True or terms.get('service_date_matches') is False
            or terms.get('network_status') != 'in_network' or terms.get('source_errors')
            or terms.get('copay_cents') not in (None, 0)
            or terms.get('deductible_remaining_cents') not in (None, 0)
            or benefits.get('deductible_remaining_cents') != 0
            or str(terms.get('coinsurance_percent')) not in {'50', '50.0', '50.00'}
            or type(oop) is not int or oop <= 0
            or terms.get('oop_remaining_cents') not in (None, oop)
            or fee_row.get('id') != line.get('id') or fee_row.get('code') != '99203'
            or fee_row.get('units') != 1 or fee_row.get('modifiers')
            or not str(fee_row.get('source_reference') or '').startswith('library:')
            or type(fee) is not int or not 0 < fee <= 100_000_000):
        return
    options = benefits.get('reported_options') or []
    def selected(identifier: object, kind: str, service: str) -> dict | None:
        return next((row for row in options if identifier and row.get('id') == identifier
                     and row.get('kind') == kind and row.get('service_type_code') == service
                     and row.get('network') == 'IN_NETWORK'
                     and row.get('coverage_level') == 'INDIVIDUAL'
                     and row.get('plan_association') == 'medical'
                     and not row.get('procedure_code')), None)
    deductible = selected(benefits.get('deductible_id'), 'deductible', '30')
    limit = selected(benefits.get('oop_id'), 'out_of_pocket', '30')
    coinsurance = selected(terms.get('coinsurance_id'), 'coinsurance', '98')
    if (not deductible or deductible.get('time_period') != 'REMAINING'
            or deductible.get('amount_cents') != 0
            or not limit or limit.get('time_period') != 'REMAINING'
            or limit.get('amount_cents') != oop
            or not coinsurance or str(coinsurance.get('percent')) not in {'50', '50.0', '50.00'}
            or deductible.get('plan_index') is None
            or len({row.get('plan_index') for row in (deductible, limit, coinsurance)}) != 1
            or any(row.get('kind') == 'copay' and row.get('service_type_code') == '98'
                   and row.get('amount_cents') not in (None, 0) for row in options)):
        return
    proxy_share = (fee + 1) // 2
    if oop > proxy_share:
        return
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    money = lambda cents: f'${cents // 100:,}.{cents % 100:02d}'
    guidance.update(amount_cents=max(0, oop - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=oop,
        clinic_fee_reference_cents=fee,
        reason=(f'Collect a refundable Blue Advantage HMO office advance: 50% of the '
                f'{money(fee)} clinic Blue Cross fee reference is {money(proxy_share)}, '
                f'capped by the payer-reported {money(oop)} remaining individual medical '
                'out-of-pocket balance. The fee is not the HMO allowed amount; apply the '
                'advance to the processed claim and refund any excess within 30 days.'),
        patient_message='This is a refundable office-visit advance, not your final insurance share. Any excess will be refunded after your claim is processed.',
        collection_rule={'mode': 'blue_advantage_hmo_oop_deposit',
                         'reference': 'Current clinic Blue Cross CPT reference and member office and medical benefits'})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
