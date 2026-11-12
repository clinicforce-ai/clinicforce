"""Automatic refundable office quote when a Blue PPO returns family-only balances."""

from app.insurance.collection.requirements import requirement_blocker
from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, review: dict | None, assumptions: list[str], *, safe: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    authorization = result.get('cpt_authorization') or {}
    binding = (result.get('identity') or {}).get('binding')
    policy = result.get('collection_policy') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    if (guidance.get('amount_cents') is not None or not safe
            or result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or len(names) != 1 or str(names[0]).strip().upper() not in {
                'PREFERRED PROVIDER OPTION MEDICAL', 'BLUE CHOICE PPO'}
            or result.get('payment_mode') == 'cash' or result.get('reference_benefits')
            or authorization.get('pos') != '11' or authorization.get('network') != 'in_network'
            or not binding or authorization.get('patient_binding') != binding
            or authorization.get('service_date', result.get('service_date')) != result.get('service_date')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not verified_in_network(benefits) or benefits.get('has_secondary')
            or benefits.get('deductible_remaining_cents') is not None
            or benefits.get('oop_remaining_cents') is not None
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or len(codes) != 1 or requirement_blocker(result)):
        return
    line, code = lines[0], codes[0]
    terms = line.get('benefits') or {}
    if (line.get('id') != code.get('id') or line.get('code') != code.get('code')
            or (result.get('selected_ids') or [line.get('id')]) != [line.get('id')]
            or line.get('code') not in {'99202', '99203', '99204', '99205',
                                        '99212', '99213', '99214', '99215'}
            or line.get('units') != 1 or code.get('units') != 1
            or line.get('modifiers') or code.get('modifiers')
            or terms.get('active') is not True or terms.get('network_status') != 'in_network'
            or terms.get('service_date_matches') is False or terms.get('deductible_applies') is False
            or terms.get('deductible_remaining_cents') is not None
            or terms.get('oop_remaining_cents') is not None
            or terms.get('copay_cents') not in (None, 0)
            or str(terms.get('coinsurance_percent')) not in {'20', '20.0', '20.00'}):
        return
    reference = result.get('temporary_rate_reference') or {}
    fee_rows = reference.get('lines') or []
    fee = None
    if (reference.get('complete') is True and len(fee_rows) == 1
            and fee_rows[0].get('id') == line['id'] and fee_rows[0].get('code') == line['code']
            and fee_rows[0].get('units') == 1 and not fee_rows[0].get('modifiers')
            and str(fee_rows[0].get('source_reference') or '').startswith('library:')):
        fee = fee_rows[0].get('unit_reference_cents')
    if (line.get('allowed_basis') == 'clinic_contract'
            and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
            and type(line.get('allowed_cents')) is int):
        if fee is not None and fee != line['allowed_cents']:
            return
        fee = line['allowed_cents']
    if type(fee) is not int or not 0 < fee <= 100_000_000:
        return
    options = benefits.get('reported_options') or []
    medical = lambda row: row.get('network') == 'IN_NETWORK' and row.get('plan_association') == 'medical'
    deductible = [row for row in options if medical(row) and row.get('kind') == 'deductible'
                  and row.get('service_type_code') == '30' and row.get('coverage_level') == 'FAMILY'
                  and row.get('time_period') == 'REMAINING' and type(row.get('amount_cents')) is int]
    family_oop = [row for row in options if medical(row) and row.get('kind') == 'out_of_pocket'
                  and row.get('service_type_code') == '30' and row.get('coverage_level') == 'FAMILY'
                  and row.get('time_period') == 'REMAINING' and type(row.get('amount_cents')) is int]
    if (len(deductible) != 1 or len(family_oop) != 1
            or deductible[0].get('plan_index') is None
            or deductible[0].get('plan_index') != family_oop[0].get('plan_index')
            or min(deductible[0]['amount_cents'], family_oop[0]['amount_cents']) < 0):
        return
    plan_index = deductible[0]['plan_index']
    scoped = [row for row in options if medical(row) and row.get('plan_index') == plan_index]
    office = lambda row: (row.get('procedure_code') == line['code'] or
                          row.get('service_type_code') == '98' and
                          str(row.get('payer_note') or '').strip().upper() in {'', 'BENEFITCLASS-SPECIALIST'})
    coinsurance = [row for row in scoped if row.get('kind') == 'coinsurance' and office(row)]
    if (not coinsurance or any(str(row.get('percent')) not in {'20', '20.0', '20.00'}
                              for row in coinsurance)
            or any(row.get('kind') == 'copay' and office(row)
                   and row.get('amount_cents') not in (None, 0) for row in scoped)
            or any(row.get('kind') in {'deductible', 'out_of_pocket'}
                   and row.get('coverage_level') == 'INDIVIDUAL'
                   and row.get('time_period') == 'REMAINING' for row in scoped)
            or any(row.get('kind') == 'deductible' and office(row)
                   and ('DOES NOT APPLY' in str(row.get('payer_note') or '').upper()
                        or 'WAIVED' in str(row.get('payer_note') or '').upper())
                   for row in scoped)):
        return
    applied_deductible = min(fee, deductible[0]['amount_cents'])
    target = min(applied_deductible + (fee - applied_deductible + 2) // 5,
                 family_oop[0]['amount_cents'])
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, target - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=target,
        clinic_fee_reference_cents=fee,
        reason=('Collect the clinic’s refundable Blue PPO office quote. It assumes the reported family '
                'deductible applies before 20% coinsurance and that the unreturned individual out-of-pocket '
                'balance does not lower this amount. Apply it to the claim and refund any excess.'),
        patient_message=('This is an estimated office payment. The clinic will apply it to your '
                         'insurance claim and refund any excess.'),
        collection_rule={'mode': 'blue_ppo_family_deductible_quote',
                         'reference': 'Current matched clinic CPT fee and family medical benefits'})
    assumptions.append('The family deductible is assumed to apply to this office visit; any lower individual '
                       'deductible or out-of-pocket balance may reduce the final patient share.')
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
