"""Clinic-approved refundable deposit for a tightly scoped BlueCard office visit."""

from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, review: dict | None, *, safe: bool,
          qmb_protected: bool, qmb_unresolved: bool) -> None:
    policy = result.get('collection_policy') or {}
    benefits = result.get('benefits') or {}
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    identity = result.get('identity') or {}
    auth = result.get('cpt_authorization') or {}
    verifier = (((result.get('plan_details') or {}).get('stedi') or {})
                .get('verification_payer') or {})
    reference = result.get('temporary_rate_reference') or {}
    codes = result.get('codes') or []
    if (guidance.get('amount_cents') is not None or not safe or qmb_protected or qmb_unresolved
            or policy.get('mode') != 'collect_supported_advance'
            or policy.get('allow_bluecard_office_refundable_deposit') is not True
            or not all(policy.get(key) for key in ('approved_by', 'approved_at', 'reference'))
            or result.get('reference_benefits') or result.get('payment_mode') == 'cash'
            or not any(name in str(result.get('payer') or '').casefold()
                       for name in ('blue cross', 'bcbstx', 'blue shield'))
            or (result.get('stedi_payer') or {}).get('id') != 'QDTRP'
            or not verifier.get('payer_id') or verifier['payer_id'] == 'QDTRP'
            or not verifier.get('evidence_path')
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not verified_in_network(benefits) or benefits.get('has_secondary')
            or benefits.get('network_conflict') or benefits.get('historical_only')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or price.get('mode') != 'services' or price.get('service_issues')
            or len(lines) != 1 or len(codes) != 1 or len(result.get('selected_ids') or []) != 1
            or auth.get('state') != 'complete' or auth.get('pos') != '11'
            or auth.get('network') != 'in_network' or not identity.get('binding')
            or auth.get('patient_binding') != identity['binding']
            or reference.get('complete') is not True
            or not any(name in str(reference.get('source') or '').casefold()
                       for name in ('blue cross', 'bcbstx'))
            or len(reference.get('lines') or []) != 1):
        return
    line, code, fee_row = lines[0], codes[0], reference['lines'][0]
    terms = line.get('benefits') or {}
    if (line.get('code') != '99203' or line.get('units') != 1 or line.get('modifiers')
            or line.get('allowed_cents') is not None
            or result['selected_ids'][0] != line.get('id')
            or code.get('id') != line.get('id') or code.get('code') != '99203'
            or code.get('units') != 1 or code.get('modifiers')
            or terms.get('active') is not True or terms.get('network_status') != 'in_network'
            or terms.get('service_date_matches') is False or terms.get('source_errors')
            or terms.get('has_secondary') or terms.get('coverage_status_conflict')
            or terms.get('coverage_dates_invalid')
            or terms.get('copay_cents') not in (None, 0)
            or terms.get('coinsurance_percent') not in (None, '20', '20.0', '20.00')
            or fee_row.get('id') != line.get('id') or fee_row.get('code') != '99203'
            or fee_row.get('units') != 1 or fee_row.get('modifiers')
            or not str(fee_row.get('source_reference') or '').startswith('library:')
            or type(fee_row.get('unit_reference_cents')) is not int
            or not 0 < fee_row['unit_reference_cents'] <= 100_000_000):
        return
    options = benefits.get('reported_options') or []
    medical = lambda row: row.get('network') == 'IN_NETWORK' and row.get('plan_association') == 'medical'
    family = [row for row in options if medical(row) and row.get('kind') == 'deductible'
              and row.get('service_type_code') == '30' and row.get('coverage_level') == 'FAMILY'
              and row.get('time_period') == 'REMAINING'
              and type(row.get('amount_cents')) is int and row['amount_cents'] >= 0]
    specialist = [row for row in options if medical(row) and row.get('kind') == 'coinsurance'
                  and row.get('service_type_code') == '98'
                  and row.get('coverage_level') == 'INDIVIDUAL'
                  and str(row.get('payer_note') or '').strip().upper() == 'BENEFITCLASS-SPECIALIST'
                  and str(row.get('percent')) in {'20', '20.0', '20.00'}]
    if len(family) != 1 or len(specialist) != 1:
        return
    plan_index = family[0].get('plan_index')
    if plan_index is None or specialist[0].get('plan_index') != plan_index:
        return
    scoped = [row for row in options if medical(row) and row.get('plan_index') == plan_index]
    copays = [row for row in scoped if row.get('kind') == 'copay'
              and row.get('service_type_code') == '98'
              and (not row.get('payer_note') or
                   str(row['payer_note']).strip().upper() == 'BENEFITCLASS-SPECIALIST')]
    oop = [row for row in scoped if row.get('kind') == 'out_of_pocket'
           and row.get('service_type_code') == '30'
           and row.get('coverage_level') == 'INDIVIDUAL'
           and row.get('time_period') == 'REMAINING']
    if (len(copays) != 1 or type(copays[0].get('amount_cents')) is not int
            or copays[0]['amount_cents'] != 0
            or len(oop) != 1 or type(oop[0].get('amount_cents')) is not int
            or oop[0]['amount_cents'] != benefits.get('oop_remaining_cents')
            or any(row.get('kind') == 'deductible'
                   and ((row.get('service_type_code') == '30'
                         and row.get('coverage_level') == 'INDIVIDUAL'
                         and row.get('time_period') == 'REMAINING')
                        or (row.get('service_type_code') == '98' and
                            str(row.get('payer_note') or '').upper().startswith('BENEFITCLASS-SPECIALIST')))
                   for row in scoped)):
        return
    fee = fee_row['unit_reference_cents']
    deductible = min(fee, family[0]['amount_cents'])
    # Twenty percent of whole cents, rounded to the nearest cent.
    coinsurance = (fee - deductible + 2) // 5
    target = min(deductible + coinsurance, oop[0]['amount_cents'])
    if target <= 0:
        return
    payments = (review or {}).get('payments') or {}
    paid = (payments.get('amount_cents') if payments.get('reference') else
            (result.get('arrival_collection') or {}).get('payments_received_cents'))
    if type(paid) is not int or paid < 0:
        paid = 0
    guidance.update(amount_cents=max(0, target - paid), status='estimated',
        basis='bcbstx_refundable_advance', reference_total_cents=target,
        clinic_fee_reference_cents=fee,
        reason=('Collect a refundable BlueCard office deposit using the scoped BCBSTX clinic fee '
                'and the reported family deductible, specialist coinsurance and out-of-pocket balance. '
                'The fee is not the member-specific allowed amount. Apply the deposit to the processed '
                'claim and refund any excess within 30 days.'),
        patient_message=('This is a refundable clinic deposit, not your final insurance share. '
                         'Any excess will be refunded after your claim is processed.'),
        collection_rule={'mode': 'bluecard_family_office_refundable_deposit',
                         'reference': policy['reference'], 'approved_at': policy['approved_at']})
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
