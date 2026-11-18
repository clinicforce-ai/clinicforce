"""Scope the published Choice POS II office rule to a dated member response."""
from datetime import date

from app.insurance.identity import generic_plan_label
from app.insurance.requirements.evidence import _key


def prior_date_office(result: dict, context: dict, plans: list[str], codes: set[str]) -> bool:
    benefits = result.get('benefits') or {}
    reference = result.get('reference_benefits') or benefits
    try:
        lag = (date.fromisoformat(str(context.get('service_date') or '')) - date.fromisoformat(str(
            (reference.get('returned_service_dates') or {}).get('start') or ''))).days
    except ValueError:
        return False
    return (1 <= lag <= 7 and 'aetna' in (context.get('payer_names') or set())
            and benefits.get('active') is True and benefits.get('service_date_matches') is False
            and reference.get('active') is True
            and {_key(plan) for plan in plans if not generic_plan_label(plan)} == {'aetnachoiceposii'}
            and codes in ({'99203'}, {'99213'}) and str(context.get('pos') or '') == '11'
            and benefits.get('network_status') != 'out_of_network'
            and not benefits.get('network_conflict') and not benefits.get('coverage_status_conflict')
            and not benefits.get('source_errors'))
