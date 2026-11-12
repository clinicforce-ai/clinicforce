"""Narrow payer-supported BlueCard EPO office copay collection rule."""

from app.insurance.network_participation import verified_in_network


OFFICE_CODES = {str(code) for code in (*range(99202, 99206), *range(99212, 99216))}


def neurology_office_copay(result: dict, benefits: dict, price: dict,
                          lines: list[dict], amount: int | None,
                          policy_approved: bool, basis: str) -> bool:
    network = result.get('network_evidence') or {}
    context = result.get('appointment_context') or {}
    rendering = context.get('rendering_provider') or {}
    card = result.get('card_check') or {}
    line = lines[0] if len(lines) == 1 else {}
    return (not policy_approved and basis == 'office_copay' and amount == 7000
            and verified_in_network(benefits)
            and network.get('product_network_source') == 'signed_bluecard_agreement'
            and (result.get('plan_details') or {}).get('stedi', {}).get('names') ==
                ['BCBS EPO, NATIONAL MED/SURG']
            and 'highmark' in str(network.get('home_plan') or '').casefold()
            and context.get('place_of_service') == '11'
            and any('neurolog' in str(role).casefold() for role in rendering.get('roles') or [])
            and price.get('mode') == 'services' and not price.get('conditional')
            and line.get('code') in OFFICE_CODES and line.get('units') == 1
            and not line.get('modifiers')
            and (line.get('benefits') or {}).get('copay_cents') == 7000
            and not card.get('conflicts') and card.get('status') not in {'conflict', 'mismatch'})
