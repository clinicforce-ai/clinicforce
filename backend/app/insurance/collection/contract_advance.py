"""Exact clinic-rate advance eligibility."""

from app.insurance.network_participation import verified_in_network


def applies(benefits: dict, price: dict, lines: list[dict],
            basis: str, policy_approved: bool, published_copay: bool) -> bool:
    return (not policy_approved and not published_copay and verified_in_network(benefits)
            and basis in {'patient_obligations', 'anticipated_services', 'office_copay'}
            and price.get('complete') is True and not price.get('conditional')
            and price.get('mode') == 'services' and bool(lines)
            and price.get('patient_min_cents') == price.get('patient_max_cents')
            and all(line.get('rate_resolution', {}).get('status') == 'RESOLVED'
                    and line.get('allowed_basis') in {'clinic_contract', 'payer_fee_schedule', 'bcbstx_rate_fallback'}
                    and line.get('allowed_cents') is not None for line in lines))


def note_bcbstx_fallback(guidance: dict, assumptions: list[str], lines: list[dict]) -> None:
    if any(line.get('allowed_basis') == 'bcbstx_rate_fallback' for line in lines):
        assumptions.append('A matching BCBSTX clinic fee replaces a missing insurer fee for this provisional estimate; the insurer allowed amount follows the claim.')
        if guidance['amount_cents'] is not None and guidance['amount_cents'] > 0:
            guidance['reason'] += ' The estimate uses a BCBSTX fee for a CPT without an insurer-specific rate.'
    guidance['assumptions'] = list(dict.fromkeys(assumptions))
