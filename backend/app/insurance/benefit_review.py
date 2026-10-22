"""Distinguish an annual OOP disclosure conflict from a visit-price blocker."""

from app.insurance.identity import patient_benefits_bound


def annual_oop_conflict_with_known_remaining(reason: str, benefits: dict) -> bool:
    return (reason.lower().startswith('the payer returned a conflicting annual individual out-of-pocket maximum')
            and type(benefits.get('oop_remaining_cents')) is int
            and not benefits.get('oop_remaining_conflict_ids')
            and benefits['oop_remaining_cents'] >= 0)


def selected_service_blocker(network: str, patient_identity: dict, lines: list[dict]) -> tuple[str, str] | None:
    """Return the first explicit CPT benefit conflict; absent legacy fields add no fact."""
    for line in lines:
        terms = line.get('benefits') or {}
        code = str(line.get('code') or '')
        if terms.get('active') is False:
            return 'service_inactive', code
        if terms.get('service_date_matches') is False:
            return 'service_date_mismatch', code
        if terms.get('coverage_status_conflict'):
            return 'coverage_status_conflict', code
        if terms.get('coverage_dates_invalid'):
            return 'coverage_date_invalid', code
        if terms.get('historical_only'):
            return 'service_benefits_historical', code
        if terms.get('has_secondary'):
            return 'service_secondary_coverage', code
        line_identity = terms.get('identity')
        if terms.get('active') is True and line_identity and (not patient_benefits_bound(line_identity)
                or line_identity.get('binding') != patient_identity.get('binding')):
            return 'service_identity_mismatch', code
        line_network = terms.get('network_status')
        if (terms.get('network_conflict') or network in {'in_network', 'out_of_network'}
                and line_network in {'in_network', 'out_of_network'} and line_network != network):
            return 'service_network_conflict', code
    return None


def selected_service_detail(blocker: tuple[str, str]) -> tuple[str, str]:
    kind, code = blocker
    service = f'CPT {code}' if code else 'the selected service'
    return {
        'service_inactive': (f'the payer marked {service} inactive or noncovered for this visit',
                             f'ask the payer whether {service} is covered on the visit date and rerun the insured estimate'),
        'service_date_mismatch': (f'the payer benefit for {service} applies to a different service date',
                                  f'get the payer benefit for {service} on this visit date and rerun the insured estimate'),
        'coverage_status_conflict': (f'the payer returned conflicting coverage status for {service}',
                                     f'ask the payer which coverage status applies to {service} on this visit date and rerun the insured estimate'),
        'coverage_date_invalid': (f'the payer returned an unreadable coverage date for {service}',
                                  f'get the correct coverage dates for {service} from the payer and rerun the insured estimate'),
        'service_benefits_historical': (f'the saved payer benefit for {service} cannot be reopened',
                                        f'rerun the payer check for {service} before collecting'),
        'service_secondary_coverage': (f'the payer reports other coverage affecting {service}',
                                        f'establish payer order and the secondary benefit for {service} before collecting'),
        'service_identity_mismatch': (f'the payer benefit for {service} does not match this patient',
                                      f'reconcile patient and subscriber identity with the payer before using the {service} benefit'),
        'service_network_conflict': (f'the payer benefit tier for {service} conflicts with the clinic network result',
                                     f'ask the payer which provider network tier applies to {service} on this visit date'),
    }[kind]
