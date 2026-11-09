"""Pre-Medicaid office planning share from a BCBSTX reference for UHC D-SNP."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.insurance import commercial, insurance_product
from app.insurance.identity import patient_benefits_bound
from app.insurance.stedi_cpt_authorization import visit_coverage_bound


def estimate(result: dict, service_date: str) -> dict | None:
    """Calculate an office planning share before Medicaid, without authorizing collection."""
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    reference = result.get('temporary_rate_reference') or {}
    price = result.get('price_range') or {}
    codes = result.get('codes') or []
    if (result.get('service_selection_status') == 'preview' and not result.get('selected_ids')
            and codes and all(row.get('source') == 'conditional office E/M alternative'
                              for row in codes)):
        codes = []
    rates = reference.get('lines') or []
    if (result.get('payment_mode') == 'cash'
            or not commercial.is_united_healthcare_payer(result)
            or insurance_product.classify(names) != 'medicare_advantage_dsnp'
            or not (visit_coverage_bound(benefits, service_date) or
                    benefits.get('active') is True and benefits.get('service_date_matches') is None
                    and result.get('service_date') == service_date and result.get('checked_at'))
            or not patient_benefits_bound(identity)
            or benefits.get('identity') and benefits['identity'].get('binding') != identity.get('binding')
            or benefits.get('resolved_service_type') != '98'
            or benefits.get('has_secondary') or benefits.get('network_conflict')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('source_errors') or benefits.get('historical_only')
            or (result.get('recovery') or {}).get('reason_code') in {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or result.get('refresh_requires_selection') is True
            or reference.get('complete') is not True or len(rates) != 1
            or len(codes) > 1 or price.get('service_issues')
            or price.get('complete') and price.get('allowed_min_cents') is not None
            or benefits.get('copay_cents') != 0
            or benefits.get('deductible_remaining_cents') != 0
            or type(benefits.get('oop_remaining_cents')) is not int
            or benefits['oop_remaining_cents'] < 0):
        return None
    rate = rates[0]
    code = codes[0] if codes else None
    if (not codes and (result.get('visit_type') != 'new'
                       or not any(row.get('code') == '99203'
                                  for row in result.get('available_codes') or []))
            or code and (code.get('code') != rate.get('code')
                         or code.get('id') != rate.get('id')
                         or code.get('units') != 1 or code.get('modifiers'))
            or not code and rate.get('code') != '99203'
            or rate.get('code') not in {'99202', '99203', '99204', '99205'}
            or rate.get('units') != 1 or rate.get('modifiers')
            or type(rate.get('unit_reference_cents')) is not int
            or not 0 < rate['unit_reference_cents'] <= 100_000_000):
        return None
    try:
        percent = Decimal(str(benefits['coinsurance_percent']))
    except (KeyError, TypeError, InvalidOperation):
        return None
    if not percent.is_finite() or not 0 <= percent <= 100:
        return None
    fee = rate['unit_reference_cents']
    coinsurance = int((Decimal(fee) * percent / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    oop = benefits['oop_remaining_cents']
    share = min(coinsurance, oop)
    return {'basis': 'bcbstx_uhc_dsnp_office_proxy', 'status': 'provisional',
            'scenario_cpt': rate['code'], 'cpt_selected': bool(codes),
            'reference_total_cents': fee, 'coinsurance_percent': str(percent),
            'copay_cents': 0, 'deductible_cents': 0, 'coinsurance_cents': coinsurance,
            'oop_reduction_cents': coinsurance - share, 'oop_remaining_cents': oop,
            'patient_share_cents': share,
            'lines': [{'code': rate['code'], 'units': 1, 'reference_cents': fee,
                       'coinsurance_base_cents': fee, 'coinsurance_cents': coinsurance}],
            'rate_source': reference.get('source') or 'BCBSTX fee reference',
            'deductible_source': 'Stedi reported $0 remaining individual deductible',
            'message': ('Pre-Medicaid planning share from one office E/M CPT, payer-reported office '
                        'benefits and a BCBSTX fee reference. UHC allowance, rendering-provider '
                        'participation and member-specific dual cost-sharing protection are not '
                        'established. Do not collect this share at check-in.')}
