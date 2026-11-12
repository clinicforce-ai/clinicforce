"""Automatic refundable office quote when a Blue PPO returns family-only balances."""

from app.insurance.collection.requirements import requirement_blocker
from app.insurance.network_participation import verified_in_network

OFFICE_CODES = {'99202', '99203', '99204', '99205', '99212', '99213', '99214', '99215'}
DIAGNOSTIC_CODES = {'95908', '95886'}


def apply(result: dict, guidance: dict, review: dict | None, assumptions: list[str], *, safe: bool) -> None:
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    codes = result.get('codes') or []
    authorization = result.get('cpt_authorization') or {}
    binding = (result.get('identity') or {}).get('binding')
    policy = result.get('collection_policy') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    appointment_pos = (result.get('appointment_context') or {}).get('place_of_service')
    visit_pos = authorization.get('pos') or appointment_pos
    if (guidance.get('amount_cents') is not None or not safe
            or result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or len(names) != 1 or str(names[0]).strip().upper() not in {
                'PREFERRED PROVIDER OPTION MEDICAL', 'BLUE CHOICE PPO'}
            or result.get('payment_mode') == 'cash' or result.get('reference_benefits')
            or visit_pos != '11' or appointment_pos not in (None, '11')
            or authorization.get('network', 'in_network') != 'in_network'
            or not binding or authorization.get('patient_binding', binding) != binding
            or authorization.get('service_date', result.get('service_date')) != result.get('service_date')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or policy.get('mode') not in (None, 'collect_supported_advance')
            or benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or not verified_in_network(benefits) or benefits.get('has_secondary')
            or benefits.get('deductible_remaining_cents') is not None
            or benefits.get('oop_remaining_cents') is not None
            or price.get('mode') != 'services' or price.get('service_issues')
            or not lines or len(lines) != len(codes) or requirement_blocker(result)):
        return
    by_id = {line.get('id'): line for line in lines}
    selected = result.get('selected_ids') or list(by_id)
    selected_codes = {line.get('code') for line in lines}
    office_codes = selected_codes & OFFICE_CODES
    diagnostic_codes = selected_codes & DIAGNOSTIC_CODES
    if (len(by_id) != len(lines) or set(selected) != set(by_id) or len(selected) != len(lines)
            or len(office_codes) > 1 or selected_codes != office_codes | diagnostic_codes
            or (diagnostic_codes and diagnostic_codes != DIAGNOSTIC_CODES)
            or len(selected_codes) != len(lines)):
        return
    reference = result.get('temporary_rate_reference') or {}
    fee_rows = reference.get('lines') or []
    references = {row.get('id'): row for row in fee_rows} if reference.get('complete') is True else {}
    if len(references) != len(fee_rows):
        return
    fee = 0
    for code in codes:
        line = by_id.get(code.get('id'))
        if not line or line.get('code') != code.get('code') or line.get('units') != code.get('units'):
            return
        units = code.get('units')
        terms = line.get('benefits') or {}
        if (type(units) is not int or not 1 <= units <= (4 if code.get('code') == '95886' else 1)
                or line.get('modifiers') or code.get('modifiers')
                or terms.get('active') is not True or terms.get('network_status') != 'in_network'
                or terms.get('service_date_matches') is False or terms.get('deductible_applies') is False
                or terms.get('deductible_remaining_cents') is not None
                or terms.get('oop_remaining_cents') is not None
                or terms.get('copay_cents') not in (None, 0)
                or str(terms.get('coinsurance_percent')) not in {'20', '20.0', '20.00'}):
            return
        row = references.get(code['id'])
        unit_fee = (row.get('unit_reference_cents') if row and row.get('code') == code['code']
                    and row.get('units') == units and not row.get('modifiers')
                    and str(row.get('source_reference') or '').startswith('library:') else None)
        line_fee = unit_fee * units if type(unit_fee) is int else None
        if (line.get('allowed_basis') == 'clinic_contract'
                and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
                and type(line.get('allowed_cents')) is int):
            if line_fee is not None and line_fee != line['allowed_cents']:
                return
            line_fee = line['allowed_cents']
        if type(line_fee) is not int or not 0 < line_fee <= 100_000_000:
            return
        fee += line_fee
    if fee > 100_000_000:
        return
    options = [*(benefits.get('reported_options') or []),
               *(row for line in lines for row in (line.get('benefits') or {}).get('reported_options') or [])]
    medical = lambda row: row.get('network') == 'IN_NETWORK' and row.get('plan_association') == 'medical'
    family = {kind: {(row.get('plan_index'), row.get('amount_cents')) for row in options
                     if medical(row) and row.get('kind') == kind
                     and row.get('service_type_code') == '30'
                     and row.get('coverage_level') == 'FAMILY'
                     and row.get('time_period') == 'REMAINING'
                     and type(row.get('amount_cents')) is int}
              for kind in ('deductible', 'out_of_pocket')}
    if (any(len(rows) != 1 for rows in family.values())
            or any(next(iter(rows))[0] is None or next(iter(rows))[1] < 0
                   for rows in family.values())):
        return
    plan_index, remaining_deductible = next(iter(family['deductible']))
    oop_plan, remaining_oop = next(iter(family['out_of_pocket']))
    if plan_index != oop_plan:
        return
    scoped = [row for row in options if medical(row) and row.get('plan_index') == plan_index]
    if any(row.get('kind') in {'deductible', 'out_of_pocket'}
           and row.get('coverage_level') == 'INDIVIDUAL'
           and row.get('time_period') == 'REMAINING' for row in scoped):
        return
    for line in lines:
        terms = line.get('benefits') or {}
        selected_ids = set(terms.get('selected_benefit_ids') or [])
        selected_ids.update(filter(None, (terms.get('coinsurance_id'), terms.get('copay_id'))))
        def applies(row: dict) -> bool:
            return (row.get('procedure_code') == line['code']
                    or bool(row.get('id') and row['id'] in selected_ids)
                    or line['code'] in OFFICE_CODES and row.get('service_type_code') == '98'
                    and str(row.get('payer_note') or '').strip().upper() in {
                        '', 'BENEFITCLASS-SPECIALIST'})
        coinsurance = [row for row in scoped if row.get('kind') == 'coinsurance' and applies(row)]
        if (not coinsurance or any(str(row.get('percent')) not in {'20', '20.0', '20.00'}
                                   for row in coinsurance)
                or any(row.get('kind') == 'copay' and applies(row)
                       and row.get('amount_cents') not in (None, 0) for row in scoped)
                or any(row.get('kind') == 'deductible' and applies(row)
                       and ('DOES NOT APPLY' in str(row.get('payer_note') or '').upper()
                            or 'WAIVED' in str(row.get('payer_note') or '').upper())
                       for row in scoped)):
            return
    applied_deductible = min(fee, remaining_deductible)
    target = min(applied_deductible + (fee - applied_deductible + 2) // 5, remaining_oop)
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, target - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=target,
        clinic_fee_reference_cents=fee,
        reason=('Collect the clinic’s refundable Blue PPO visit quote. It assumes the reported family '
                'deductible applies before 20% coinsurance and that the unreturned individual out-of-pocket '
                'balance does not lower this amount. Apply it to the claim and refund any excess.'),
        patient_message=('This is an estimated visit payment. The clinic will apply it to your '
                         'insurance claim and refund any excess.'),
        collection_rule={'mode': 'blue_ppo_family_deductible_quote',
                         'reference': 'Current matched clinic CPT fee and family medical benefits'})
    assumptions.append('The family deductible is assumed to apply to the selected services; any lower individual '
                       'deductible or out-of-pocket balance may reduce the final patient share.')
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
