"""Bind a dated payer Part B report to the selected previsit patient."""

from app.insurance import identity
from app.insurance.stedi_benefits.payer_order import reported_medicare_part_b_secondary


def bind(payload: dict, coverage: dict, service_date: str, benefits: dict) -> dict:
    reported = reported_medicare_part_b_secondary(payload, service_date) if payload else None
    # This exception binds only the matched dependent; positive collection
    # remains blocked by the separate multi-policy collection gate.
    comparison_coverage = ({**coverage, 'active_coverages': 1}
        if reported and coverage.get('active_coverages', 0) > 1 and coverage.get('coverage_id')
        else coverage)
    comparison = identity.compare(payload, comparison_coverage)
    benefits['identity'] = comparison
    if reported and identity.patient_benefits_bound(comparison):
        benefits['medicare_part_b_secondary'] = reported
    return comparison
