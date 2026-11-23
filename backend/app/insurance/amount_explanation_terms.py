"""Payer-reported office terms used in the amount explanation."""
from app.insurance.identity import patient_benefits_bound


def _money(cents: int) -> str:
    return f'${cents // 100:,}.{cents % 100:02d}'


def missing_planned_cpt(result: dict, benefits: dict) -> bool:
    plan = result.get('service_plan') or {}
    return (benefits.get('active') is True and not plan.get('selected_codes') and not plan.get('alternatives')
            and not (result.get('price_range') or {}).get('lines') and not result.get('codes'))


def identity_or_date_reason(reason: str | None, benefits: dict, identity: dict) -> bool:
    return reason in {'identity_or_coverage_review', 'subscriber_relationship_conflict', 'returned_identity_or_coverage_mismatch'} or (
        reason not in {'no_coverage_found', 'upstream_no_policies'}
        and benefits.get('active') is True and benefits.get('service_date_matches') is False
        and patient_benefits_bound(identity))


def identity_or_date_cause(benefits: dict, identity: dict) -> tuple[str, str]:
    if benefits.get('active') is True and patient_benefits_bound(identity) and benefits.get('service_date_matches') is False:
        return 'the payer response date differs from the scheduled visit date', 'payer_service_date_mismatch'
    return 'the returned policy did not match the patient and subscriber details for this visit', 'policy_identity_mismatch'


def choice_plus_reported_terms(benefits: dict) -> str | None:
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('service_type_code') == '98' and row.get('network') in {'Y', 'N'}]
    terms = {(kind, tier): {row.get(field) for row in rows
                            if row.get('kind') == kind and row.get('network') == tier}
             for kind, field in (('copay', 'amount_cents'), ('coinsurance', 'percent'))
             for tier in ('Y', 'N')}
    if (terms['copay', 'Y'] == terms['copay', 'N'] == {0}
            and len(terms['coinsurance', 'Y']) == len(terms['coinsurance', 'N']) == 1):
        in_rate = next(iter(terms['coinsurance', 'Y']))
        out_rate = next(iter(terms['coinsurance', 'N']))
        if in_rate is not None and out_rate is not None:
            return (f'The payer reported a $0.00 professional office copay, '
                    f'{in_rate}% in-network and {out_rate}% out-of-network coinsurance. ')
    return None


def medicare_primary_sentence(amount: int, line: dict, guidance: dict) -> tuple[str, str]:
    sentence = (f'Collect {_money(amount)} at arrival: Original Medicare Part B reports a met deductible, '
                f'and 20% of the clinic Medicare fee {_money(line.get("allowed_cents") or 0)} '
                f'for CPT {line.get("code") or "office visit"} is '
                f'{_money(guidance.get("primary_medicare_share_cents") or 0)} before recorded payments. '
                'eCW lists Medicare first; any secondary payment and final patient balance follow the claims')
    return 'medicare_primary_coinsurance_advance', sentence
