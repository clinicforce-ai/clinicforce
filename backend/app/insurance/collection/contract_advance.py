"""Exact clinic-rate advance eligibility."""

from app.insurance.network_participation import verified_in_network


def applies(benefits: dict, price: dict, lines: list[dict],
            basis: str, policy_approved: bool, published_copay: bool) -> bool:
    return (not policy_approved and not published_copay and verified_in_network(benefits)
            and basis in {'patient_obligations', 'anticipated_services'}
            and price.get('complete') is True and not price.get('conditional')
            and price.get('mode') == 'services' and bool(lines)
            and price.get('patient_min_cents') == price.get('patient_max_cents')
            and all(line.get('rate_resolution', {}).get('status') == 'RESOLVED'
                    and line.get('allowed_basis') == 'clinic_contract'
                    and line.get('allowed_cents') is not None for line in lines))
