"""Dated, product-specific published benefits supplement the member's live 271.

These rules never establish provider participation or a negotiated dollar rate.
The FEP rule applies across practices; NMCT cash pricing is a separate schedule.
"""
import re
from decimal import Decimal, InvalidOperation
from app.insurance import commercial, cpt_rules

FEP_2026 = 'https://www.fepblue.org/-/media/PDFs/Brochures/FEP-Blue-Focus-brochure-2026.pdf'
FEP_NETWORK = 'https://www.bcbstx.com/content/dam/hcsc/docs/provider/tx/standards/manuals/ppo-provider-manual/ppo-sech-fep.pdf'

def network_match(payer_id: str, products: set[str], network: str) -> bool:
    # BCBSTX provider manual section H: FEP utilizes Blue Choice PPO.
    # Provider participation and actual rate amounts still come from the practice.
    return (payer_id == 'QDTRP' and products == {'fepbluefocus'}
            and re.sub(r'[^a-z]', '', network.lower()) == 'bluechoiceppo')


FEP_REFERRALS = 'https://www.fepblue.org/our-plans/benefits-plan-overview'


def _message(row: dict) -> str:
    return ' '.join(str(x) for x in row.get('messages', [])).upper()


def apply(value: dict, payload: dict, code: str, service_date: str, *, coverage_active: bool | None = None) -> dict:
    names = commercial.plan_details(payload)['names']
    if (len(names) != 1 or re.sub(r'[^A-Z]', '', names[0].upper()) != 'FEPBLUEFOCUS'
            or not '2026-01-01' <= service_date <= '2026-12-31'
            or not (value.get('active') is True or value.get('active') is None and coverage_active is True) or value.get('has_secondary')):
        return value
    office = code in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
    neuro = code in cpt_rules.NCS_CODES | {'95885', '95886', '95887'}
    if not (office or neuro):
        return value
    result = {**value, 'active': True}
    result['policy_evidence'] = {'title': 'FEP Blue Focus · 2026 plan benefits', 'url': FEP_2026,
                                 'section': '5(a), diagnostic tests' if neuro else '5(a), professional visits',
                                 'referral_url': FEP_REFERRALS, 'effective_from': '2026-01-01', 'effective_to': '2026-12-31'}
    # A procedure-specific conflicting payer instruction outranks the general plan.
    exact = [row for row in commercial._stedi_entries(payload) if commercial._procedure(row) == code]
    if exact or neuro and any('73' in (row.get('serviceTypeCodes') or []) and row.get('code') in {'A', 'B'} for row in commercial._stedi_entries(payload)):
        return value
    # Do not override a returned requirement for this service (or an unscoped
    # requirement). Explicitly unrelated STC 98 rows, e.g. ABA, are not EMG.
    unrelated = ('APPLIED BEHAVIOR ANALYSIS', 'ACUPUNCTURE', 'MANIPULATIVE TREATMENT',
                 'DRUGS', 'MEDICATIONS', 'SUBSTANCE USE DISORDER VISITS BEGINNING')
    for row in commercial._stedi_entries(payload):
        required = str(row.get('priorAuthIndicator') or row.get('authOrCertIndicator') or '').upper() in {'Y', 'REQUIRED'}
        if required and set(row.get('serviceTypeCodes') or ['30']) & {'30', '98'} and not any(term in _message(row) for term in unrelated):
            return value
    result.update(referral_signal='not_required', authorization_signal='not_required',
                  authorization_basis='Published plan requirements for this service')
    reasons = [r for r in result.get('review_reasons', []) if not ('referral' in r.lower() or 'authorization' in r.lower())
               and not (coverage_active is True and value.get('active') is None and r == 'Confirm active coverage for the visit date with the payer.')]
    cost_reasons = {'Confirm which copay or coinsurance term applies, including any visit-count conditions.',
                    'Confirm which conditional benefit applies to the selected service.',
                    "Review the payer's benefit limits before estimating patient cost."}
    if neuro:
        # Office-visit, drug, acupuncture and ABA benefits in STC 98 do not
        # describe diagnostic EMG. Section 5(a) supplies its distinct rule.
        result.update(coinsurance_percent='30', copay_cents=None, cost_share='coinsurance',
                      deductible_applies=True, selected_benefit_ids=[], benefit_label='Neurological diagnostic testing',
                      benefit_summary='30% patient share after the remaining deductible; plan pays 70% of its allowance afterward.')
        reasons = [r for r in reasons if r not in cost_reasons]
    else:
        entries = [row for row in commercial._stedi_entries(payload)
                   if commercial._network_indicator(row) in {'Y', 'IN_NETWORK', 'W', 'IN_AND_OUT_OF_NETWORK'}
                   and str(row.get('coverageLevel') or row.get('coverageLevelCode') or '').upper() in {'IND', 'INDIVIDUAL'}
                   and '98' in (row.get('serviceTypeCodes') or [])
                   and 'FIRST TEN PROFESSIONAL MEDICAL' in _message(row)]
        remaining = [row for row in entries if row.get('code') == 'F'
                     and str(row.get('timeQualifierCode') or row.get('timeQualifier') or '').upper() in {'29', 'REMAINING'}]
        counts = set()
        for row in remaining:
            quantity = row.get('quantity') or {}
            try:
                if str(quantity.get('qualifier') or '').upper() not in {'VISITS', 'VISIT', 'VS'}:
                    continue
                amount = Decimal(str(quantity.get('value')))
                if amount == int(amount) and 0 <= amount <= 10:
                    counts.add(int(amount))
            except (InvalidOperation, TypeError, ValueError):
                pass
        result['benefit_label'] = 'Office consultation'
        result['benefit_summary'] = '$10 for each of the first 10 combined visits; 30% after the deductible from visit 11.'
        if len(counts) == 1:
            count = counts.pop()
            result['visits_remaining'] = count
            result['selected_benefit_ids'] = [row['evidence_path'] for row in remaining]
            if count > 0:
                result.update(copay_cents=1000, copay_period='VISIT', coinsurance_percent=None,
                              cost_share='copay', deductible_applies=False, deductible_remaining_cents=0,
                              benefit_summary=f'$10 office copay. {count} of the first 10 combined visits remain; deductible does not apply.')
            else:
                result.update(copay_cents=None, coinsurance_percent='30', cost_share='coinsurance', deductible_applies=True,
                              benefit_summary='The first 10 combined visits are used. Patient pays 30% after the remaining deductible.')
            reasons = [r for r in reasons if r not in cost_reasons]
            if count > 0:
                reasons = [r for r in reasons if 'remaining individual deductible' not in r]
    result.update(review_reasons=reasons, requires_review=bool(reasons))
    return result
