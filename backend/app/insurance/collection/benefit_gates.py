"""Prepare network and out-of-pocket evidence for collection decisions."""

from app.insurance.network_participation import verified_in_network


def prepare_benefit_evidence(benefits: dict, lines: list[dict]) -> tuple[dict, bool]:
    # An old in-network label needs current product/provider evidence.
    collection_benefits = (benefits if benefits.get('network_status') != 'in_network'
                           or verified_in_network(benefits) else {**benefits, 'network_status': 'unknown'})
    unconfirmed_zero_oop = (benefits.get('oop_remaining_cents') is None and
        all((line.get('benefits') or {}).get('oop_remaining_cents') is None for line in lines) and
        any(row.get('kind') == 'out_of_pocket' and row.get('service_type_code') == '30'
            and row.get('coverage_level') in {'INDIVIDUAL', 'IND'}
            and row.get('time_period') in {'REMAINING', '29'}
            and row.get('network') in {'IN_NETWORK', 'Y'}
            and row.get('plan_association') in {'unassigned', 'medical_candidate'}
            and row.get('amount_cents') == 0
            for row in benefits.get('reported_options') or []))
    return collection_benefits, unconfirmed_zero_oop
