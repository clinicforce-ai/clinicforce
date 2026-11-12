"""Bind FEP Preferred office terms to the exact patient and clinic provider."""
from app.insurance.network_participation import verified_in_network


def provider_verified(benefits: dict, source: dict, inputs: dict,
                      rules: dict, evidence: dict | None) -> bool:
    product = (benefits.get('plan_details') or {}).get('identified_product')
    npi = rules.get('provider_npi')
    if (product not in {'FEP Blue Basic PSHB', 'FEP Blue Standard FEHB'}
            or source.get('payer_plan_bound') is not True
            or source.get('payer_plan_names') != [product]
            or not verified_in_network(benefits) or not npi):
        return False
    if benefits.get('network_source') == 'payer_participation_message':
        return benefits.get('network_provider_npi') == npi
    evidence = evidence or {}
    return (product == 'FEP Blue Standard FEHB'
            and benefits.get('network_source') == 'clinic_group_agreement'
            and evidence.get('decision') == 'in_network_contract_supported'
            and evidence.get('product_network_source') == 'official_payer_provider_manual'
            and bool(evidence.get('document_id') and evidence.get('document_sha256'))
            and evidence.get('product') == product
            and evidence.get('rendering_npi') == npi
            and evidence.get('billing_npi') == (source.get('billing_provider') or {}).get('npi')
            and evidence.get('location_id') == source.get('location_id')
            and evidence.get('place_of_service') == rules.get('place_of_service')
            and evidence.get('service_date') == inputs.get('service_date'))
