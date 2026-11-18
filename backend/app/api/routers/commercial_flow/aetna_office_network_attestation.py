"""Dated clinic statement for one Aetna office participation scope."""
from datetime import date, timedelta

from app.insurance import network_participation
from app.insurance.identity import patient_benefits_bound


def apply_nmct_aetna_office_attestation(result: dict, benefits: dict, practice_id: str,
                                        inputs: dict, source: dict, plan: dict) -> bool:
    """Use the clinic's scoped Aetna office participation statement for 2026."""
    identity = result.get('identity') or {}
    visit_day = str(inputs.get('service_date') or '')
    returned = str((benefits.get('returned_service_dates') or {}).get('start') or '')
    try:
        day_applies = (benefits.get('service_date_matches') is True or
                       date.fromisoformat(visit_day) - date.fromisoformat(returned) == timedelta(days=1))
    except ValueError:
        day_applies = False
    rendering = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    source_npi = str((source.get('rendering_provider') or {}).get('npi') or '')
    if (not network_participation.is_nmct_practice(practice_id)
            or str(result.get('payer') or '').casefold() != 'aetna'
            or plan.get('names') != ['Aetna Choice POS II']
            or str(plan.get('group_number') or '') != '047925802400006'
            or not '2026-11-17' <= visit_day <= '2026-12-31' or not day_applies
            or str(inputs.get('place_of_service') or '') != '11'
            or rendering != '1720615453' or (source_npi and source_npi != rendering)
            or benefits.get('active') is not True or not patient_benefits_bound(identity)
            or not patient_benefits_bound(benefits.get('identity'))
            or identity.get('binding') != benefits['identity'].get('binding')
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('source_errors')
            or benefits.get('network_conflict') or result.get('agreement_exclusion')
            or benefits.get('network_source') == 'payer_participation_message'
            or network_participation.verified_out_of_network(benefits)):
        return False
    benefits.update(network_status='in_network', network_source='clinic_attested_aetna_office',
                    network_evidence_ids=[], network_provider_npi=None)
    result['network_evidence'] = {
        'decision': 'in_network_by_clinic_attestation',
        'source': 'clinic_attested_aetna_office',
        'reference': 'clinic-aetna-choice-office-participation-2026-11-17',
        'reason': ('NMCT reports this Aetna Choice POS II group and office rendering provider '
                   'in network for 2026. The payer response does not independently confirm the provider tier.')}
    return True
