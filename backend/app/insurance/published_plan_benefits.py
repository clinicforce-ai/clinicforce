"""Date-effective published plan terms, applied only to a bound product and tier."""

from app.insurance.identity import patient_benefits_bound
from app.insurance.network_participation import verified_in_network


FEP_PSHB_2026 = {
    'label': '2026 OPM PSHB Service Benefit Plan brochure, Section 5(a)',
    'url': 'https://www.opm.gov/healthcare-insurance/healthcare/plan-information/plans/BrochureJson?brochureNumber=71-020&year=2026',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
    'office_specialist_copay_cents': 5000,
}

AETNA_SIGNATURE_TX_2026 = {
    'label': '2026 Aetna Medicare Signature (PPO) Evidence of Coverage, diagnostic tests',
    'url': 'https://content.medicareadvantage.com/2026/Aetna-Y0001-H3288-006-PP48-EOC2026-C-SF20251014.pdf',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
    'diagnostic_copay_cents': 5000,
}


def aetna_signature_diagnostic(benefits: dict, service_date: str, code: str,
                               *, payer_verified: bool) -> bool:
    """Use the published diagnostic benefit only for the exact member product."""
    plan = benefits.get('plan_details') or {}
    return (payer_verified and code in {'95908', '95886'}
            and plan.get('names') == ['Aetna Medicare Signature (PPO)']
            and plan.get('group_number') == '000003-TX'
            and plan.get('plan_number') == '000003-TX000006'
            and AETNA_SIGNATURE_TX_2026['effective_from'] <= service_date <= AETNA_SIGNATURE_TX_2026['effective_to']
            and benefits.get('active') is True and verified_in_network(benefits)
            and patient_benefits_bound(benefits.get('identity') or {})
            and not benefits.get('has_secondary')
            and benefits.get('service_date_matches') is not False)


def apply_plan_terms(benefits: dict, plan: dict, service_date: str, patient_bound: bool) -> None:
    """A confirmed Basic plan has no annual medical deductible at any provider tier."""
    if (not patient_bound or plan.get('identified_product') != 'FEP Blue Basic PSHB'
            or not FEP_PSHB_2026['effective_from'] <= service_date <= FEP_PSHB_2026['effective_to']):
        return
    benefits['published_plan_rule'] = FEP_PSHB_2026
    benefits['published_preferred_office_copay_cents'] = FEP_PSHB_2026['office_specialist_copay_cents']
    if benefits.get('deductible_remaining_cents') not in {None, 0} or benefits.get('deductible_applies') is True:
        benefits.setdefault('review_reasons', []).append(
            'The member-specific deductible conflicts with the published FEP Basic no-deductible rule.')
    else:
        benefits['deductible_applies'] = False
        benefits['deductible_applicability'] = 'DOES_NOT_APPLY'
        benefits['deductible_remaining_cents'] = 0
        benefits['review_reasons'] = [reason for reason in benefits.get('review_reasons') or []
                                      if reason != 'Confirm the remaining individual deductible that applies to this visit.']
    benefits['requires_review'] = bool(benefits.get('review_reasons')) or benefits.get('requires_review') is True


def preferred_fep_office(benefits: dict, rules: dict, service_date: str, code: str) -> bool:
    plan = benefits.get('plan_details') or {}
    provider_bound = (rules.get('fep_preferred_verified') is True or
                      benefits.get('network_source') == 'payer_participation_message'
                      and verified_in_network(benefits)
                      and bool(rules.get('provider_npi'))
                      and benefits.get('network_provider_npi') == rules.get('provider_npi'))
    return (plan.get('identified_product') == 'FEP Blue Basic PSHB'
            and provider_bound and verified_in_network(benefits)
            and benefits.get('active') is True
            and benefits.get('service_date_matches') is True
            and patient_benefits_bound(benefits.get('identity') or {})
            and not benefits.get('has_secondary')
            and FEP_PSHB_2026['effective_from'] <= service_date <= FEP_PSHB_2026['effective_to']
            and str(rules.get('place_of_service') or '') == '11'
            and rules.get('provider_office_role') == 'specialist'
            and code in {'99212', '99213', '99214', '99215'})


def apply_office_terms(raw: dict, case_benefits: dict, rules: dict,
                       service_date: str, code: str) -> dict:
    """Fill missing office terms; retain conflicting member-specific evidence."""
    if (rules.get('aetna_eoc_verified') is True
            and str(rules.get('place_of_service') or '') == '11' and
            aetna_signature_diagnostic(case_benefits, service_date, code,
                                       payer_verified=rules.get('aetna_payer_verified') is True)):
        copay = AETNA_SIGNATURE_TX_2026['diagnostic_copay_cents']
        if (raw.get('copay_cents') not in {None, copay}
                or raw.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'}
                or raw.get('deductible_remaining_cents') not in {None, 0}):
            return {**raw, 'review_reasons': [*(raw.get('review_reasons') or []),
                'The member-specific diagnostic cost share conflicts with the published Aetna plan rule.'],
                'requires_review': True}
        reasons = [reason for reason in raw.get('review_reasons') or [] if reason not in {
            'Confirm the remaining individual deductible that applies to this visit.',
            'Confirm which copay or coinsurance term applies, including any visit-count conditions.',
        }]
        return {**raw, 'copay_cents': copay, 'copay_period': 'PROCEDURE',
                'coinsurance_percent': '0', 'deductible_applies': False,
                'deductible_applicability': 'DOES_NOT_APPLY', 'deductible_remaining_cents': 0,
                'cost_share': 'copay', 'published_plan_rule': AETNA_SIGNATURE_TX_2026,
                'review_reasons': reasons, 'requires_review': bool(reasons)}
    if not preferred_fep_office(case_benefits, rules, service_date, code):
        return raw
    copay = FEP_PSHB_2026['office_specialist_copay_cents']
    if raw.get('copay_cents') not in {None, copay} or raw.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'}:
        return {**raw, 'review_reasons': [*(raw.get('review_reasons') or []),
            'The member-specific office cost share conflicts with the published FEP Basic Preferred specialist rule.']}
    if raw.get('deductible_remaining_cents') not in {None, 0} or raw.get('deductible_applies') is True:
        return {**raw, 'review_reasons': [*(raw.get('review_reasons') or []),
            'The member-specific deductible conflicts with the published FEP Basic no-deductible rule.']}
    reasons = [reason for reason in raw.get('review_reasons') or [] if reason not in {
        'Confirm the remaining individual deductible that applies to this visit.',
        'Confirm which copay or coinsurance term applies, including any visit-count conditions.',
        "Confirm the plan's referral requirement and, if required, that a referral is on file.",
    }]
    return {**raw, 'copay_cents': copay, 'copay_period': 'VISIT',
            'deductible_applies': False, 'deductible_applicability': 'DOES_NOT_APPLY',
            'deductible_remaining_cents': 0, 'cost_share': 'copay',
            'referral_signal': 'not_required' if raw.get('referral_signal') != 'required' else 'required',
            'published_plan_rule': FEP_PSHB_2026,
            'review_reasons': reasons, 'requires_review': bool(reasons)}
