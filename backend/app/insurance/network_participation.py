"""Provider tier follows scoped contract or payer evidence."""

import re


NMCT_PRACTICE_ID = 'primary'
NMCT_REGISTERED_PRACTICE_ID = 'f378bc13-af1d-4ea5-bfbd-8cd102e42ddc'


def is_nmct_practice(practice_id: str) -> bool:
    return practice_id in {NMCT_PRACTICE_ID, NMCT_REGISTERED_PRACTICE_ID}


def nmct_library_contract(documents: list[dict], payer: str, service_date: str) -> dict | None:
    """A current Library insurer contract covers NMCT's HMO, PPO and Medicare products."""
    def payer_key(value: str) -> str:
        name = str(value or '').casefold()
        matches = []
        if re.search(r'\b(?:bcbs\s*(?:of\s*)?(?:tx|texas)|blue\s+cross(?:\s+and)?\s+blue\s+shield\s+(?:of\s+)?(?:tx|texas))\b', name):
            matches.append('bcbstx')
        if re.search(r'\bunited\s*health(?:care)?\b|\buhc\b', name):
            matches.append('unitedhealthcare')
        for brand in ('wellpoint', 'healthsmart', 'cigna', 'humana', 'multiplan', 'aetna', 'wellcare'):
            if re.search(r'\b' + brand + r'\b', name):
                matches.append(brand)
        return matches[0] if len(matches) == 1 else '' if matches else re.sub(r'[^a-z0-9]', '', name)

    key = payer_key(payer)
    if not key or not service_date:
        return None
    for row in documents:
        if (row.get('kind') != 'contract' or not row.get('id')
                or row.get('effective_from') and row['effective_from'] > service_date
                or row.get('effective_to') and row['effective_to'] < service_date):
            continue
        document_payer = row.get('payer') or (
            'UnitedHealthcare' if re.fullmatch(r'United\s*Health(?:care)?\s+contract',
                                               str(row.get('label') or ''), re.I) else '')
        if document_payer and payer_key(document_payer) == key:
            return row
    return None


FEP_BLUE_CHOICE_SOURCE = (
    'https://www.bcbstx.com/docs/provider/tx/standards/manuals/ppo-provider-manual/ppo-sech-fep.pdf'
)


def product_network_relationship(payer: str, product: str, reported_networks: list[str],
                                 contract_network: str, service_date: str) -> dict | None:
    """Identify a product's network without inferring any clinician's participation."""
    normalized = lambda value: re.sub(r'[^a-z0-9]', '', str(value).casefold())
    network = normalized(contract_network)
    reported = {normalized(name) for name in reported_networks if name}
    if network and reported == {network}:
        return {'source': 'patient_bound_payer_network', 'network_name': contract_network}
    if (network == 'bluechoiceppo' and normalized(product) == 'fepbluebasicpshb'
            and re.search(r'blue\s+cross.*blue\s+shield.*(?:texas|\btx\b)|bcbs\s*(?:of\s*)?(?:texas|tx)\b|bcbstx', payer, re.I)
            and '2026-01-01' <= service_date <= '2026-12-31' and not reported):
        return {'source': 'official_payer_provider_manual', 'reference': FEP_BLUE_CHOICE_SOURCE,
                'network_name': contract_network, 'effective_from': '2026-01-01',
                'effective_to': '2026-12-31'}
    return None


def participation_conflict(benefits: dict) -> bool:
    return (benefits.get('network_conflict') is True
            or any('provider-network evidence reports nonparticipation' in str(reason).lower()
                   for reason in benefits.get('review_reasons') or []))


def verified_in_network(benefits: dict) -> bool:
    return (benefits.get('network_status') == 'in_network'
            and benefits.get('network_source') in {'clinic_library_contract', 'clinic_group_agreement',
                                                   'payer_participation_message', 'clinic_attested_phcs_roster'}
            and not participation_conflict(benefits))


def verified_out_of_network(benefits: dict) -> bool:
    return (benefits.get('network_status') == 'out_of_network'
            and benefits.get('network_source') in {'payer_participation_message', 'clinic_nonparticipation_rule',
                                                  'clinic_contract_inventory', 'clinic_no_blue_contract',
                                                  'clinic_no_insurer_contract'}
            and not participation_conflict(benefits))


def outcome(benefits: dict, evidence: dict) -> str:
    """Keep group contract support distinct from a priced provider network tier."""
    if participation_conflict(benefits):
        return 'UNRESOLVED'
    if evidence.get('decision') == 'in_network_contract_supported':
        return 'IN_NETWORK_CONTRACT_SUPPORTED'
    if verified_in_network(benefits):
        return 'IN_NETWORK_VERIFIED'
    if verified_out_of_network(benefits):
        return ('OUT_OF_NETWORK_CONTRACT_SUPPORTED'
                if benefits.get('network_source') == 'clinic_contract_inventory'
                else 'OUT_OF_NETWORK_VERIFIED')
    return 'UNRESOLVED'
