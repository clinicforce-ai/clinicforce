"""Scoped BCBSTX PPO office requirement evidence for the published rules."""
from app.insurance import stedi_cpt_authorization
from app.insurance.identity import generic_plan_label, patient_benefits_bound
from app.insurance.requirements.evidence import _key


PAYER_NAMES = {'bcbstx', 'bcbstexas', 'bluecrossblueshieldoftexas',
               'bluecrossandblueshieldoftexas', 'bluecrossblueshieldtx',
               'bluecrossblueshieldtexas'}

AUTH_SOURCE_99214 = {
    'label': 'Clinic-confirmed BCBSTX PPO CPT 99214 office authorization rule',
    'source_version': 'Clinic confirmation received 2026-11-17',
    'verified_at': '2026-11-17', 'effective_from': '2026-11-17',
    'effective_to': '2026-12-31',
}


def same_day_visit(kind: str, result: dict, benefits: dict, context: dict,
                   codes: set[str], payer_names: set[str]) -> bool:
    day = str(context.get('service_date') or '')
    return ((kind == 'referral' or kind == 'authorization' and codes in ({'99213'}, {'99214'})
             and str(context.get('pos') or '') == '11')
            and benefits.get('service_date_matches') is None
            and benefits.get('network_status') == 'in_network'
            and (str(result.get('checked_at') or '')[:10] == day
                 or stedi_cpt_authorization.visit_coverage_bound(benefits, day))
            and bool(payer_names & PAYER_NAMES))


def bound_plus_surname_warning(identity: dict, benefits: dict, context: dict,
                               plans: list[str], codes: set[str], payer_names: set[str]) -> bool:
    day = str(context.get('service_date') or '')
    plan_keys = {_key(plan) for plan in plans if plan}
    return (bool(payer_names & PAYER_NAMES)
            and codes == {'99214'} and str(context.get('pos') or '') == '11'
            and '2026-11-17' <= day <= '2026-12-31'
            and 'preferredprovideroptionplusmedical' in plan_keys
            and plan_keys <= {'preferredprovideroptionplusmedical', 'bluechoiceppo', 'ci'}
            and benefits.get('network_status') == 'in_network'
            and patient_benefits_bound(identity) and identity.get('status') == 'warning'
            and identity.get('reconciliation_rule') is None and identity.get('can_confirm') is True
            and [row.get('field') for row in identity.get('differences') or []] == ['last_name']
            and not identity.get('policy_differences') and not identity.get('missing_fields'))


def office_follow_up(exact: set[str], in_network: bool, office: bool, day: str,
                     names: set[str], plans: list[str]) -> bool:
    plan_keys = {_key(plan) for plan in plans}
    return (exact <= {'bluechoiceppo'} and in_network and office and day >= '2026-10-26'
            and bool(names & PAYER_NAMES)
            and (bool({'preferredprovideroptionmedical', 'preferredprovideroptionplusmedical'} & plan_keys)
                 or exact == {'bluechoiceppo'})
            and all(generic_plan_label(plan) or _key(plan) == 'bluechoiceppo' for plan in plans))


def member_product_key(plans: list[str], exact: set[str]) -> str:
    return next((_key(plan) for plan in plans if _key(plan).startswith('preferredprovideroption')),
                next(iter(exact), 'bluechoiceppo'))
