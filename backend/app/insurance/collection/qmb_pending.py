"""Zero check-in request while Medicare Advantage QMB status is unverified."""

from app.insurance.network_participation import verified_in_network


def apply(result: dict, guidance: dict, *, unresolved: bool, protected: bool,
          member_bound: bool, source_errors: list, same_policy_conflict: bool,
          unresolved_order: bool) -> None:
    benefits = result.get('benefits') or {}
    if (guidance['amount_cents'] is not None or not unresolved or protected
            or benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or not member_bound or not verified_in_network(benefits)
            or any((benefits.get('has_secondary'), benefits.get('network_conflict'),
                    benefits.get('coverage_status_conflict'), benefits.get('coverage_dates_invalid'),
                    source_errors, same_policy_conflict, unresolved_order))):
        return
    guidance.update(amount_cents=0, status='zero_advance', basis='qmb_status_pending',
        reason='Collect $0 at arrival while Medicare Advantage QMB protection is unverified. The payer-reported copay and final patient share remain separate; verify QMB status before any later charge.',
        patient_message='No payment is requested today while Medicare cost-sharing protection is checked. Final responsibility follows the processed claim and verified member protections.')
