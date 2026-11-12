"""Refundable clinic quote for a single unpriced Cigna PPO office visit."""


def apply(result: dict, guidance: dict, review: dict | None, assumptions: list[str], *, safe: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    codes = result.get('codes') or []
    lines = price.get('lines') or []
    reference = result.get('temporary_rate_reference') or {}
    fee_rows = reference.get('lines') or []
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    policy = result.get('collection_policy') or {}
    authorization = result.get('cpt_authorization') or {}
    binding = (result.get('identity') or {}).get('binding')
    card = result.get('card_check') or {}
    if (not safe or guidance.get('amount_cents') is not None
            or result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or result.get('payer') != 'Cigna' or names != ['PPO']
            or not result.get('checked_at') or result.get('payment_mode') == 'cash'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or (result.get('appointment_context') or {}).get('place_of_service') != '11'
            or authorization.get('pos', '11') != '11'
            or authorization.get('network', 'unknown') not in {'unknown', 'in_network'}
            or not binding or authorization.get('patient_binding', binding) != binding
            or authorization.get('service_date', result.get('service_date')) != result.get('service_date')
            or card.get('status') in {'conflict', 'mismatch'} or card.get('conflicts')
            or card.get('office_copay_cents') is not None
            or any(value is not None for key, value in (card.get('cigna_card_reference') or {}).items()
                   if key.endswith('_copay_cents'))
            or benefits.get('network_status') != 'unknown' or benefits.get('has_secondary')
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(codes) != 1 or len(lines) != 1 or len(fee_rows) != 1
            or reference.get('complete') is not True):
        return
    code, line, fee_row = codes[0], lines[0], fee_rows[0]
    terms = line.get('benefits') or {}
    fee = fee_row.get('unit_reference_cents')
    if (code.get('code') != '99203' or code.get('units') != 1 or code.get('modifiers')
            or line.get('id') != code.get('id') or line.get('code') != code.get('code')
            or line.get('units') != 1 or line.get('modifiers')
            or (result.get('selected_ids') or [code['id']]) != [code['id']]
            or fee_row.get('id') != code['id'] or fee_row.get('code') != code['code']
            or fee_row.get('units') != 1 or fee_row.get('modifiers')
            or not str(fee_row.get('source_reference') or '').startswith('library:')
            or type(fee) is not int or not 0 < fee <= 100_000_000
            or terms.get('active') is not True
            or terms.get('network_status') not in {'unknown', 'in_network'}
            or terms.get('deductible_applies') is False
            or terms.get('copay_cents') is not None
            or str(terms.get('coinsurance_percent')) not in {'20', '20.0', '20.00'}
            or line.get('allowed_cents') is not None):
        return
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('network') == 'IN_NETWORK' and row.get('plan_association') == 'medical']
    balances = {}
    for kind in ('deductible', 'out_of_pocket'):
        for level in ('INDIVIDUAL', 'FAMILY'):
            values = {(row.get('plan_index'), row.get('amount_cents')) for row in rows
                if row.get('kind') == kind and row.get('coverage_level') == level
                and row.get('service_type_code') == '30' and row.get('time_period') == 'REMAINING'
                and type(row.get('amount_cents')) is int}
            if len(values) != 1:
                return
            balances[kind, level] = next(iter(values))
    if (len({plan for plan, _ in balances.values()}) != 1
            or next(iter(balances.values()))[0] is None
            or any(amount < fee for _, amount in balances.values())
            or benefits.get('deductible_remaining_cents') not in (None, balances['deductible', 'INDIVIDUAL'][1])
            or benefits.get('oop_remaining_cents') not in (None, balances['out_of_pocket', 'INDIVIDUAL'][1])
            or terms.get('deductible_remaining_cents') not in (None, balances['deductible', 'INDIVIDUAL'][1])
            or terms.get('oop_remaining_cents') not in (None, balances['out_of_pocket', 'INDIVIDUAL'][1])):
        return
    plan = next(iter(balances.values()))[0]
    office = [row for row in rows if row.get('plan_index') == plan
              and (row.get('service_type_code') == '98' or row.get('procedure_code') == '99203')]
    coinsurance = [row for row in office if row.get('kind') == 'coinsurance']
    if (not coinsurance or any(str(row.get('percent')) not in {'20', '20.0', '20.00'}
                               for row in coinsurance)
            or any(row.get('kind') == 'copay' and row.get('amount_cents') is not None for row in office)
            or any(row.get('kind') == 'deductible' and
                   ('DOES NOT APPLY' in str(row.get('payer_note') or '').upper()
                    or 'WAIVED' in str(row.get('payer_note') or '').upper()) for row in office)):
        return
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, fee - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=fee,
        clinic_fee_reference_cents=fee,
        reason=('Collect the clinic’s refundable Cigna PPO office quote using the matched BCBS Texas '
                'clinic fee reference. The reported individual and family deductible and out-of-pocket '
                'balances exceed this fee; the Cigna allowed amount and provider network tier remain '
                'unconfirmed. Apply the payment to the processed claim and refund any excess.'),
        patient_message=('This is a refundable clinic quote for the planned office visit. '
                         'The clinic will apply it to the insurance claim and refund any excess.'),
        collection_rule={'mode': 'cigna_ppo_deductible_quote',
                         'reference': 'Matched clinic CPT fee and same-plan medical benefit balances'})
    assumptions.append('The reported office deductible is assumed to apply before 20% coinsurance; the Cigna allowed amount and provider network tier are unconfirmed.')
    for key in ('cigna_estimate_required', 'policy_setup_required', 'positive_advance_policy_missing'):
        guidance.pop(key, None)
