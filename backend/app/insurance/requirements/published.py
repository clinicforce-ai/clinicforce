"""Verified published payer rules used by the shared requirement evaluator.

Workbook rows remain research leads; this catalogue is scoped to the checked
source version and date, never member approval.
"""
from __future__ import annotations

from app.insurance import cpt_rules, stedi_cpt_authorization
from app.insurance.identity import generic_plan_label, payer_identity_verified
from app.insurance.library_network_exclusions import product_kind
from app.insurance.requirements.evidence import _key

BCBSTX_PAYER_NAMES = {'bcbstx', 'bcbstexas', 'bluecrossblueshieldoftexas',
                      'bluecrossandblueshieldoftexas', 'bluecrossblueshieldtx',
                      'bluecrossblueshieldtexas'}

# Saved estimates re-evaluate requirement decisions when a published rule changes.
PUBLISHED_RULESET_VERSION = '2026-10-26-bcbstx-ppo-99213'

UHC_TEXAS_CHOICE_PLUS_SOURCE = {
    'label': 'UnitedHealthcare Texas Choice Plus plan description',
    'url': 'https://www.uhcprovider.com/en/health-plans-by-state/texas-health-plans/tx-commercial-plans.html',
    'source_version': 'Texas commercial plan description, checked 2026-10-21',
    'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
UHC_MA_PPO_REFERRAL_SOURCE = {
    'label': '2026 UnitedHealthcare Care Provider Administrative Guide, Group Medicare Advantage PPO referrals',
    'url': 'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf',
    'source_version': '2026 administrative guide', 'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
UHC_MA_2026_AUTH_SOURCE = {
    'label': 'UnitedHealthcare Medicare Advantage prior authorization requirements, effective October 1, 2026',
    'url': 'https://www.uhcprovider.com/content/dam/provider/docs/public/prior-auth/pa-requirements/medicare/Med-Adv-Dual-Eff-10-1-26.pdf',
    'source_version': 'PCA-4-26-00416, effective 2026-10-01',
    'verified_at': '2026-10-21',
    'effective_from': '2026-10-01', 'effective_to': '2026-12-31',
}

UHC_2026_GUIDE = {
    'label': 'UnitedHealthcare 2026 administrative guide, commercial product and Medicare PPO referral tables',
    'url': 'https://www.uhcprovider.com/content/dam/provider/docs/public/admin-guides/2026-UHC-Administrative-Guide.pdf',
    'source_version': '2026 administrative guide', 'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
UHC_COMMERCIAL_2026_PA = {
    'label': 'UnitedHealthcare commercial prior authorization list, transplant evaluation row',
    'url': 'https://www.uhcprovider.com/content/dam/provider/docs/public/prior-auth/pa-requirements/commercial/UHC-Commercial-PA-Requirements-10-2026.pdf',
    'source_version': 'PCA-1-26-00657, effective 2026-10-01',
    'verified_at': '2026-10-21', 'effective_from': '2026-10-01',
    'effective_to': '2026-12-31',
}
CIGNA_REFERRAL_SOURCE = {
    'label': 'Cigna plan and specialist referral guidance',
    'url': 'https://www.cigna.com/legal/members/member-rights-and-responsibilities/cigna-health-care-policies',
    'source_version': 'Provider plan guidance, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-01-01',
    'effective_to': '2026-12-31',
}
BCBSTX_2026_REFERRAL_SOURCE = {
    'label': 'BCBSTX 2026 Blue Choice PPO and Blue Essentials specialist referral comparison',
    'url': 'https://www.bcbstx.com/docs/provider/tx/standards/manuals/ppo-provider-manual/ppo-secd-referral.pdf',
    'source_version': 'Blue Choice PPO provider manual referral section, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-07-01',
    'effective_to': '2027-06-30',
}
BCBSTX_PPO_REFERRAL_SOURCE = {
    'label': 'BCBSTX PPO referral guidance',
    'url': 'https://connect.bcbstx.com/ask-bcbstx/w/frequently-asked-questions/69/how-to-know-if-you-need-a-referral-and-how-to-get-one',
    'source_version': 'PPO referral guidance, checked 2026-10-26',
    'verified_at': '2026-10-26', 'effective_from': '2026-10-26',
    'effective_to': '2026-12-31',
}
BCBSTX_PPO_99213_AUTH_SOURCE = {
    'label': 'Clinic-confirmed BCBSTX PPO CPT 99213 office authorization rule',
    'source_version': 'Clinic confirmation received 2026-10-26',
    'verified_at': '2026-10-26', 'effective_from': '2026-10-26',
    'effective_to': '2026-12-31',
}
UHC_TX_MA_REFERRAL_SOURCE = {
    'label': 'UnitedHealthcare Texas Medicare Advantage PPO specialist referrals',
    'url': 'https://www.uhcprovider.com/en/health-plans-by-state/texas-health-plans/tx-medicare-plans/tx-med-adv.html',
    'source_version': 'Texas MA plan description, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-01-01',
    'effective_to': '2026-12-31',
}
CIGNA_2026_PA_SOURCE = {
    'label': 'Cigna Master Precertification List, J0585, 64612 and 64615',
    'url': 'https://www.cigna.com/static/www-cigna-com/docs/master-precertification-list-for-providers.pdf',
    'source_version': 'July 2026 Master Precertification List', 'verified_at': '2026-10-21',
    'effective_from': '2026-07-01', 'effective_to': '2026-12-31',
}
AETNA_OPEN_ACCESS_SOURCE = {
    'label': 'Aetna benefits product guide, Open Access Managed Choice POS referrals',
    'url': 'https://www.aetna.com/document-library/healthcare-professionals/documents-forms/benefits-product-guide.pdf',
    'source_version': 'Product guide dated September 2025, checked 2026-10-23',
    'verified_at': '2026-10-23', 'effective_from': '2026-10-01', 'effective_to': '2026-12-31',
}
MEDICARE_REFERRAL_SOURCE = {
    'label': 'Medicare.gov Original Medicare specialist access',
    'url': 'https://www.medicare.gov/basics/get-started-with-medicare/get-more-coverage/your-coverage-options/compare-original-medicare-medicare-advantage',
    'source_version': 'Original Medicare coverage comparison, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
MEDICARE_OPD_PA_SOURCE = {
    'label': 'CMS Original Medicare hospital outpatient department botulinum toxin prior authorization program',
    'url': 'https://www.cms.gov/files/document/opd-services-require-prior-authorization.pdf',
    'source_version': 'CMS OPD services list, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
MEDICARE_EMG_LCD_SOURCE = {
    'label': 'CMS EMG and nerve conduction study LCD L34859, medical necessity and frequency',
    'url': 'https://www.cms.gov/medicare-coverage-database/view/lcd.aspx?lcdid=34859',
    'source_version': 'LCD L34859, checked 2026-10-21', 'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
BCBSTX_2026_PA_SOURCE = {
    'label': 'BCBSTX 2026 commercial prior authorization code list, product and ASO exceptions',
    'url': 'https://www.bcbstx.com/docs/provider/tx/claims/prior-authorization/pa-codes-fully-insured-certain-aso-01012026-tx.pdf',
    'source_version': '2026 code list, updated October 2026', 'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}
FEP_PSHB_2026_SOURCE = {
    'label': '2026 OPM PSHB Service Benefit Plan brochure, Sections 3 and 5(a)',
    'url': 'https://www.opm.gov/healthcare-insurance/healthcare/plan-information/plans/BrochureJson?brochureNumber=71-020&year=2026',
    'source_version': '2026 brochure 71-020', 'verified_at': '2026-10-21',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}


def _published_rule(kind: str, result: dict, context: dict, plans: list[str], codes: set[str]) -> dict | None:
    """Promote only independently checked, exact-scope research from the matrix."""
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    day = str(context.get('service_date') or '')
    payer_names = context.get('payer_names') or set()
    same_day_bcbstx_visit = (
        (kind == 'referral' or kind == 'authorization' and codes == {'99213'}
         and str(context.get('pos') or '') == '11')
        and benefits.get('service_date_matches') is None
        and benefits.get('network_status') == 'in_network'
        and (str(result.get('checked_at') or '')[:10] == day
             or stedi_cpt_authorization.visit_coverage_bound(benefits, day))
        and bool(payer_names & BCBSTX_PAYER_NAMES)
    )
    if (not codes or not '2026-01-01' <= day <= '2026-12-31'
            or benefits.get('active') is not True
            or benefits.get('service_date_matches') is not True and not same_day_bcbstx_visit
            or not payer_identity_verified(identity) or not result.get('benefit_source')
            or not context.get('member_id')):
        return None
    exact = {_key(plan).removeprefix('unitedhealthcare').removeprefix('uhc')
             for plan in plans if not generic_plan_label(plan)}
    product = next(iter(exact), '') if len(exact) <= 1 else ''
    names = context.get('payer_names') or set()
    in_network = benefits.get('network_status') == 'in_network'
    family = {cpt_rules.service_definition(code)['family'] for code in codes}
    office = family == {'office_consultation'}
    def rule(status: str, reason: str, source: dict, *, check: bool = False,
             source_type: str = 'official_published_rule') -> dict:
        return {'status': status, 'reason': reason, 'sources': [{**source, 'source_type': source_type}],
                'member_specific_check': check,
                'applicable_scope': {'payer_product': next((p for p in plans if _key(p).endswith(product)), product),
                                     'codes': sorted(codes), 'service_categories': sorted(family),
                                     'units_by_code': {str(row.get('code') or '').upper(): row.get('units') for row in result.get('codes') or []},
                                     'indication': context.get('indication'), 'diagnoses': context.get('diagnoses'),
                                     'network_tier': benefits.get('network_status'),
                                     'rendering_npi': context.get('rendering_npi'),
                                     'billing_npi': context.get('billing_npi'), 'tin': context.get('tin'),
                                     'location_id': context.get('location_id'),
                                     'place_of_service': context.get('pos'),
                                     'service_date': day}}
    bcbstx_ppo_follow_up = (exact <= {'bluechoiceppo'} and in_network and office and day >= '2026-10-26'
            and names & BCBSTX_PAYER_NAMES
            and 'preferredprovideroptionmedical' in {_key(plan) for plan in plans}
            and all(generic_plan_label(plan) or _key(plan) == 'bluechoiceppo' for plan in plans))
    if bcbstx_ppo_follow_up and not exact and kind == 'referral' and codes <= {'99212', '99213', '99214', '99215'}:
        product = 'preferredprovideroptionmedical'
        return rule('Not required', 'BCBSTX PPO plans do not require a referral for an in-network specialist office visit.', BCBSTX_PPO_REFERRAL_SOURCE)
    if (bcbstx_ppo_follow_up and kind == 'authorization' and codes == {'99213'}
            and str(context.get('pos') or '') == '11'):
        product = next(iter(exact), 'preferredprovideroptionmedical')
        return rule('Not required', 'Clinic-confirmed BCBSTX PPO rule: CPT 99213 alone does not require prior authorization for an in-network physician-office visit.',
                    BCBSTX_PPO_99213_AUTH_SOURCE, source_type='clinic_confirmation')
    if len(exact) != 1:
        return None
    if product == 'fepbluebasicpshb':
        if kind == 'referral':
            return rule('Not required', 'The 2026 PSHB Service Benefit Plan does not require a PCP referral to see a specialist.', FEP_PSHB_2026_SOURCE)
        if kind == 'authorization' and office and codes <= {'99212', '99213', '99214', '99215'} and str(context.get('pos') or '') == '11':
            return rule('Not required', 'The 2026 PSHB brochure lists services requiring prior approval; a routine office E/M visit alone is outside those listed categories. Additional services require their own decision.', FEP_PSHB_2026_SOURCE)
        if kind == 'authorization':
            return rule('Verification required', 'The 2026 PSHB brochure requires prior approval for selected services and drugs. Match each planned code and indication to its rule.', FEP_PSHB_2026_SOURCE, check=True)
    if 'aetna' in names and in_network and day >= '2026-10-01' and product in {
            'openaccessmc', 'openaccessmanagedchoice', 'aetnaopenaccessmanagedchoice',
            'aetnaopenaccessmanagedchoicepos'}:
        if kind == 'referral' and family <= {'office_consultation', 'nerve_conduction'}:
            return rule('Not required', 'Aetna Open Access Managed Choice permits in-network specialist care without a PCP referral.', AETNA_OPEN_ACCESS_SOURCE)
    if {'unitedhealthcare', 'uhc'} & names:
        if 'wellmed' in names or any('wellmed' in _key(plan) for plan in plans):
            return None
        if kind == 'referral':
            if product in {'regionalmedicareadvantageppo', 'localmedicareadvantageppo'}:
                return rule('Not required', 'UnitedHealthcare Texas Medicare Advantage PPO permits specialist care without a referral. Confirm the exact member product.', UHC_TX_MA_REFERRAL_SOURCE)
            if in_network and product in {'nexusacooa', 'nexusacooap'}:
                return rule('Not required', 'NexusACO OA/OAP permits access to network specialists without a PCP referral.', UHC_2026_GUIDE)
            if in_network and product in {'navigate', 'nexusacor'}:
                return rule('Required', 'The PCP must submit an electronic referral before this member sees another network specialist. Check whether the provider is in the PCP group and confirm the referral on file.', UHC_2026_GUIDE, check=True)
            if in_network and product in {'navigatebalanced', 'navigateplus', 'nexusacorb', 'nexusacorp'}:
                return rule('Verification required', 'A PCP referral is needed for the highest specialist benefit tier; the published plan permits a reduced-benefit route without one. Confirm the member tier and PCP group.', UHC_2026_GUIDE, check=True)
        if kind == 'authorization' and day >= '2026-10-01':
            if product in {'regionalmedicareadvantageppo', 'localmedicareadvantageppo',
                           'groupmedicareadvantageppo'} and 'J0585' in codes and in_network:
                return rule('Required', 'The October 2026 Medicare Advantage list requires prior authorization for J0585 Botox. The injection procedure is a separate code; verify the member and any delegated group protocol. Approval is not established.', UHC_MA_2026_AUTH_SOURCE, check=True)
            if '99205' in codes and office and context.get('indication') == 'transplant_evaluation' and not any(product_kind(plan) for plan in plans):
                return rule('Required', 'CPT 99205 requires authorization here only when it is a transplant evaluation. Verify the member and obtain the approval.', UHC_COMMERCIAL_2026_PA, check=True)
            if '99205' in codes and office:
                source = UHC_MA_2026_AUTH_SOURCE if any(product_kind(plan) for plan in plans) else UHC_COMMERCIAL_2026_PA
                return rule('Verification required', 'The published 99205 entry is limited to transplant evaluation; it does not establish PA for a routine neurology consultation. Check the member and indication.', source, check=True)
            if product in {'choiceplus', 'poschoiceplus'} and codes & {'J0585', '64612', '64615'}:
                return rule('Verification required', 'The commercial list does not settle authorization for this Botox drug or injection code. Check the member-specific drug and procedure requirements separately.', UHC_COMMERCIAL_2026_PA, check=True)
            if product in {'regionalmedicareadvantageppo', 'localmedicareadvantageppo'} and codes & {'64612', '64615'}:
                return rule('Verification required', 'The Medicare Advantage list does not establish a procedure-code authorization answer here. Check the member and any delegated rules.', UHC_MA_2026_AUTH_SOURCE, check=True)
            source = UHC_MA_2026_AUTH_SOURCE if any(product_kind(plan) for plan in plans) else UHC_COMMERCIAL_2026_PA
            return rule('Verification required', 'The published code list does not establish authorization for this selected service and member. Check the exact product, code and any delegated rule.', source, check=True)
    if {'traditionalmedicare', 'originalmedicare', 'medicare'} & names and product in {'originalmedicare', 'traditionalmedicare'}:
        if kind == 'referral':
            return rule('Not required', 'Original Medicare generally permits specialist visits without a PCP referral.', MEDICARE_REFERRAL_SOURCE)
        if (kind == 'authorization' and str(context.get('pos') or '') in {'19', '22'}
                and codes & {'J0585', '64612', '64615'}):
            return rule('Required', 'CMS requires prior authorization for this listed botulinum toxin code in a hospital outpatient department. Verify the member, service and approval; this rule does not apply to a physician office.', MEDICARE_OPD_PA_SOURCE, check=True)
        if kind == 'authorization' and codes & (cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS):
            return rule('Verification required', 'The CMS EMG/NCS LCD addresses medical necessity and frequency; it does not itself require prior authorization. Check for an applicable PA program.', MEDICARE_EMG_LCD_SOURCE, check=True)
        if kind == 'authorization':
            return rule('Verification required', 'The CMS hospital outpatient PA list does not establish a requirement for this selected service in this setting. Check any other applicable Medicare program.', MEDICARE_OPD_PA_SOURCE, check=True)
    if kind == 'authorization' and 'cigna' in names and in_network and day >= '2026-07-01':
        if product in {'cignappo', 'openaccessplus', 'cignaopenaccessplus', 'cignahmo'} and codes & {'J0585', '64612', '64615'}:
            return rule('Required', 'Cigna lists this selected drug or chemodenervation code separately for precertification. Confirm this member’s applicable care management rules and obtain approval.', CIGNA_2026_PA_SOURCE, check=True)
        return rule('Verification required', 'The Cigna list does not establish authorization for this selected service under the member’s exact plan. Check the member-specific precertification tool.', CIGNA_2026_PA_SOURCE, check=True)
    if (kind == 'authorization' and 'bluecard' not in product
            and names & BCBSTX_PAYER_NAMES):
        if codes & {'J0585', '64612', '64615'}:
            return rule('Verification required', 'BCBSTX lists J0585 for some 2026 products, but the list has product and ASO exceptions and does not establish authorization for this member or the separate injection code.', BCBSTX_2026_PA_SOURCE, check=True)
        return rule('Verification required', 'The BCBSTX published list does not establish authorization for this member and selected code; product and ASO exceptions require a member-specific check.', BCBSTX_2026_PA_SOURCE, check=True)
    if kind != 'referral' or not in_network:
        return None
    if 'cigna' in names:
        if product in {'cignappo', 'openaccessplus', 'cignaopenaccessplus'}:
            return rule('Not required', 'Cigna PPO/Open Access Plus permits in-network specialist visits without a PCP referral.', CIGNA_REFERRAL_SOURCE)
        if product == 'cignahmo':
            return rule('Required', 'Cigna HMO requires a PCP specialist referral for this neurology office visit; verify the member and referral record.', CIGNA_REFERRAL_SOURCE, check=True)
    if names & BCBSTX_PAYER_NAMES and day >= '2026-07-01':
        if product in {'bluechoiceppo', 'bluechoiceppobasic'}:
            return rule('Not required', 'BCBSTX Blue Choice PPO does not require an in-network specialist referral; confirm the member uses this network.', BCBSTX_2026_REFERRAL_SOURCE)
        if product in {'blueessentials', 'blueadvantagehmo'}:
            return rule('Required', 'This BCBSTX HMO product requires a PCP specialist referral; verify the member and referral record.', BCBSTX_2026_REFERRAL_SOURCE, check=True)
    return None


def _uhc_choice_plus_referral(result: dict, context: dict, plans: list[str]) -> dict | None:
    """Use the published Texas open-access rule for an identified Choice Plus member."""
    names = context.get('payer_names') or set()
    if not any(name in {'unitedhealthcare', 'uhc'} for name in names) or 'wellmed' in names:
        return None
    products = {_key(plan) for plan in plans if not generic_plan_label(plan)}
    if not products or not products <= {'choiceplus', 'poschoiceplus',
                                        'unitedhealthcarechoiceplus', 'uhcchoiceplus'}:
        return None
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (not '2026-01-01' <= str(context.get('service_date') or '') <= '2026-12-31'
            or benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not payer_identity_verified(identity)
            or not result.get('benefit_source') or not context.get('member_id')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'
            or not result.get('codes')):
        return None
    return {'status': 'Not required',
            'reason': 'UnitedHealthcare Texas Choice Plus permits specialist care without a referral.',
            'sources': [{**UHC_TEXAS_CHOICE_PLUS_SOURCE, 'source_type': 'official_published_rule'}],
            'member_specific_check': False,
            'applicable_scope': {'payer_product': next(plan for plan in plans if not generic_plan_label(plan)),
                                 'codes': sorted(row['code'] for row in result['codes']),
                                 'units_by_code': {row['code']: row.get('units') for row in result['codes']},
                                 'diagnoses': context.get('diagnoses'),
                                 'rendering_npi': context.get('rendering_npi'),
                                 'billing_npi': context.get('billing_npi'), 'tin': context.get('tin'),
                                 'location_id': context.get('location_id'),
                                 'network_tier': benefits.get('network_status'), 'place_of_service': context.get('pos'),
                                 'service_date': context.get('service_date')}}


def _uhc_ma_ppo_referral(result: dict, context: dict, plans: list[str]) -> dict | None:
    """The 2026 guide clears referrals for Group MA PPO, not every MA PPO."""
    if not {'unitedhealthcare', 'uhc'} & (context.get('payer_names') or set()):
        return None
    specific = [plan for plan in plans if not generic_plan_label(plan)]
    if not specific or any(_key(plan) not in {
            'unitedhealthcaregroupmedicareadvantageppo',
            'uhcgroupmedicareadvantageppo'} for plan in specific):
        return None
    if not '2026-01-01' <= str(context.get('service_date') or '') <= '2026-12-31':
        return None
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (benefits.get('active') is not True or benefits.get('service_date_matches') is not True
            or not payer_identity_verified(identity) or not result.get('benefit_source')
            or not context.get('member_id') or not result.get('codes')
            or (result.get('recovery') or {}).get('reason_code') == 'multiple_active_policies'):
        return None
    return {'status': 'Not required',
            'reason': 'The 2026 UnitedHealthcare Group Medicare Advantage PPO policy does not require referrals.',
            'sources': [{**UHC_MA_PPO_REFERRAL_SOURCE, 'source_type': 'official_published_rule'}],
            'applicable_scope': {'payer_product': specific[0], 'codes': sorted(row['code'] for row in result['codes']),
                                 'units_by_code': {row['code']: row.get('units') for row in result['codes']},
                                 'diagnoses': context.get('diagnoses'),
                                 'rendering_npi': context.get('rendering_npi'),
                                 'billing_npi': context.get('billing_npi'), 'tin': context.get('tin'),
                                 'location_id': context.get('location_id'),
                                 'network_tier': benefits.get('network_status'), 'place_of_service': context.get('pos'),
                                 'service_date': context.get('service_date')}}
