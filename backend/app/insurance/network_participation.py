"""Provider tier follows scoped evidence or NMCT's explicit clinic policy."""

import re


NMCT_PRACTICE_ID = 'primary'
NMCT_REGISTERED_PRACTICE_ID = 'f378bc13-af1d-4ea5-bfbd-8cd102e42ddc'


def is_nmct_practice(practice_id: str) -> bool:
    return practice_id in {NMCT_PRACTICE_ID, NMCT_REGISTERED_PRACTICE_ID}


def nmct_contract_roster(payer: str, card_check: dict | None = None) -> bool | None:
    """Clinic-confirmed contracted insurers; None means the insurer is unidentified."""
    if (str(payer).casefold() == 'health plans incorporated' and
            isinstance(card_check, dict) and card_check.get('status') == 'filled_missing' and
            'payer' in (card_check.get('fields_added') or []) and not card_check.get('conflicts') and
            card_check.get('network_label')):
        payer = card_check['network_label']
    name = re.sub(r'[^a-z0-9]+', ' ', str(payer or '').casefold()).strip()
    if not name:
        return None
    # shortcut: this dated clinic roster is code-owned; replace it when a validated contract inventory is available.
    patterns = (r'\baetna\b', r'\bhumana\b', r'\bblue\s*cross\b|\bblue\s*shield\b|\bbcbs(?:tx)?\b',
                r'\bunited\s*health(?:care)?\b|\buhc\b', r'\bmedicare (?:of )?(?:texas|tx)\b',
                r'\bhealthsmart\b', r'\bwellpoint\b', r'\bcigna\b', r'\b(?:multiplan|phcs)\b')
    matches = sum(bool(re.search(pattern, name)) for pattern in patterns)
    wellcare = bool(re.search(r'\bwell\s*care\b', name))
    if matches > 1 or matches and wellcare:
        return None
    return not wellcare and matches == 1


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
                                                   'payer_participation_message', 'clinic_nmct_policy'}
            and not participation_conflict(benefits))


def verified_out_of_network(benefits: dict) -> bool:
    return (benefits.get('network_status') == 'out_of_network'
            and benefits.get('network_source') in {'payer_participation_message', 'clinic_nonparticipation_rule',
                                                  'clinic_contract_inventory', 'clinic_no_blue_contract',
                                                  'clinic_nmct_noncontracted'}
            and not participation_conflict(benefits))


def outcome(benefits: dict, evidence: dict) -> str:
    """Keep group contract support distinct from a priced provider network tier."""
    if participation_conflict(benefits):
        return 'UNRESOLVED'
    if evidence.get('decision') == 'in_network_contract_supported':
        return 'IN_NETWORK_CONTRACT_SUPPORTED'
    if evidence.get('decision') == 'in_network_by_clinic_policy' and verified_in_network(benefits):
        return 'IN_NETWORK_BY_CLINIC_POLICY'
    if verified_in_network(benefits):
        return 'IN_NETWORK_VERIFIED'
    if verified_out_of_network(benefits):
        return ('OUT_OF_NETWORK_CONTRACT_SUPPORTED'
                if benefits.get('network_source') == 'clinic_contract_inventory'
                else 'OUT_OF_NETWORK_VERIFIED')
    return 'UNRESOLVED'
