"""Scope the 2026 Aetna Value Plus PPO routine office requirement rule."""
from datetime import date

from app.insurance.identity import generic_plan_label, patient_benefits_bound
from app.insurance.requirements.evidence import _key


def office_99213(result: dict, context: dict, plans: list[str], codes: set[str]) -> bool:
    benefits = result.get('benefits') or {}
    plan = benefits.get('plan_details') or ((result.get('plan_details') or {}).get('stedi') or {})
    stedi_plan = ((result.get('plan_details') or {}).get('stedi') or {})
    identity = result.get('identity') or benefits.get('identity') or {}
    selected = result.get('codes') or []
    plan_names = (stedi_plan.get('names') or plans) if patient_benefits_bound(identity) else plans
    exact_plans = {_key(name) for name in plan_names if name and not generic_plan_label(name)}
    if ('aetna' not in (context.get('payer_names') or set())
            or exact_plans != {'aetnamedicarevalueplusppo'}
            or plan.get('group_number') != '000003-TX'
            or plan.get('plan_number') != '000003-TX000083'
            or codes != {'99213'} or len(selected) != 1
            or selected[0].get('units') != 1 or selected[0].get('modifiers')
            or str(context.get('pos') or '') != '11'
            or benefits.get('active') is not True
            or benefits.get('network_status') not in {'in_network', 'unknown'}
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('historical_only')
            or benefits.get('source_errors')):
        return False
    if benefits.get('service_date_matches') is not False:
        return True
    try:
        visit = date.fromisoformat(str(context.get('service_date') or ''))
        returned = date.fromisoformat(str((benefits.get('returned_service_dates') or {}).get('start') or ''))
    except ValueError:
        return False
    return (visit - returned).days == 1


def esa_ppo_office_referral(result: dict, context: dict, plans: list[str], codes: set[str]) -> bool:
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    stedi_names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names')
                   or (benefits.get('plan_details') or {}).get('names') or [])
    names = stedi_names if patient_benefits_bound(identity) and stedi_names else plans
    selected = result.get('codes') or []
    if ('aetna' not in (context.get('payer_names') or set())
            or {_key(name) for name in names if name and not generic_plan_label(name)}
               not in ({'medicarev01esappoplan'}, {'aetnamedicarev01esappoplan'})
            or len(selected) != 1 or codes != {str(selected[0].get('code'))}
            or not codes <= {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
            or selected[0].get('units') != 1 or selected[0].get('modifiers')
            or str(context.get('pos') or '') != '11' or benefits.get('active') is not True
            or benefits.get('network_status') not in {'in_network', 'unknown'}
            or benefits.get('network_conflict') or benefits.get('coverage_status_conflict')
            or benefits.get('coverage_dates_invalid') or benefits.get('historical_only')
            or benefits.get('source_errors')):
        return False
    if benefits.get('service_date_matches') is True:
        return True
    if benefits.get('service_date_matches') is not False:
        return False
    try:
        visit = date.fromisoformat(str(context.get('service_date') or ''))
        returned = date.fromisoformat(str((benefits.get('returned_service_dates') or {}).get('start') or ''))
    except ValueError:
        return False
    return (visit - returned).days == 1
