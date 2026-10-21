"""Provider participation must be backed by a scoped clinic or payer source."""


def participation_conflict(benefits: dict) -> bool:
    return (benefits.get('network_conflict') is True
            or any('provider-network evidence reports nonparticipation' in str(reason).lower()
                   for reason in benefits.get('review_reasons') or []))


def verified_in_network(benefits: dict) -> bool:
    return (benefits.get('network_status') == 'in_network'
            and benefits.get('network_source') in {'clinic_library_contract', 'payer_participation_message'}
            and not participation_conflict(benefits))


def verified_out_of_network(benefits: dict) -> bool:
    return (benefits.get('network_status') == 'out_of_network'
            and benefits.get('network_source') in {'payer_participation_message', 'clinic_nonparticipation_rule',
                                                  'clinic_contract_inventory'}
            and not participation_conflict(benefits))
