"""Clinic refundable advance when Cigna returns no financial terms."""
from __future__ import annotations

import re


def advance_patient_message(guidance: dict) -> str:
    """Explain the collected advance without presenting it as final liability."""
    if guidance['basis'] == 'cigna_refundable_office_copay':
        return 'This is a refundable advance based on the Cigna office copay. The clinic will apply it to the claim and refund any excess after processing.'
    if (guidance.get('collection_rule') or {}).get('mode') == 'blue_ppo_family_deductible_quote':
        return 'This is a refundable Blue PPO visit quote based on matched clinic fees and reported family benefits. The clinic will apply it to the claim and refund any excess after processing.'
    if (guidance.get('collection_rule') or {}).get('mode') == 'cigna_ppo_deductible_quote':
        return 'This is a refundable Cigna PPO office quote based on a clinic fee reference and reported benefits. The clinic will apply it to the claim and refund any excess after processing.'
    if (guidance.get('collection_rule') or {}).get('mode') == 'bluecard_family_office_refundable_deposit':
        return 'This is a refundable BlueCard office deposit based on a clinic fee reference, not your final insurance share. The clinic will apply it to the claim and refund any excess after processing.'
    if guidance['basis'] == 'bcbstx_refundable_advance':
        return 'This is a refundable advance based on a Blue Cross Texas fee reference, not your Cigna benefit or final bill. The clinic will apply it to the claim and refund any excess after processing.'
    return 'This is an advance estimate for the planned care. The insurer determines final responsibility when it processes the claim.'


def eligible_amount(result: dict, lines: list[dict]) -> int | None:
    """Use the clinic's exact CPT fee reference as a deposit only when no financial terms exist."""
    reference = result.get('temporary_rate_reference') or {}
    codes = result.get('codes') or []
    card = result.get('card_check') or {}
    if (result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or not re.search(r'\bcigna\b', str(result.get('payer') or ''), re.I)
            or not result.get('checked_at') or result.get('payment_mode') == 'cash'
            or (result.get('billing_protection') or {}).get('status') == 'qmb'
            or card.get('status') in {'conflict', 'mismatch'}
            or reference.get('complete') is not True
            or (result.get('price_range') or {}).get('mode') != 'services'
            or not codes or len(codes) != len(lines) or len(lines) != len(reference.get('lines') or [])):
        return None
    terms = [result.get('benefits') or {}, *(line.get('benefits') or {} for line in lines),
             *((result.get('cpt_authorization') or {}).get('financial_terms') or {}).values()]
    financial = ('copay_cents', 'deductible_remaining_cents', 'deductible_annual_cents',
                 'coinsurance_percent', 'oop_remaining_cents', 'oop_annual_cents',
                 'deductible_applies', 'published_preferred_office_copay_cents')
    if (any(term.get(key) is not None for term in terms for key in financial)
            or any(row.get('kind') in {'copay', 'coinsurance', 'deductible', 'out_of_pocket'}
                   and (row.get('amount_cents') is not None or row.get('percent') is not None)
                   for term in terms for row in term.get('reported_options') or [])
            or any(card.get(key) is not None
                   for key in ('office_copay_cents', 'published_office_copay_cents'))
            or any(value is not None for key, value in (card.get('cigna_card_reference') or {}).items()
                   if key.endswith('_copay_cents'))):
        return None
    by_id = {row.get('id'): row for row in reference['lines']}
    selected = {row.get('id'): row for row in codes}
    if len(by_id) != len(lines) or len(selected) != len(lines):
        return None
    total = 0
    for line in lines:
        row, code = by_id.get(line.get('id')) or {}, selected.get(line.get('id')) or {}
        units = line.get('units')
        amount = row.get('unit_reference_cents')
        if (not code or row.get('code') != line.get('code') or code.get('code') != line.get('code')
                or row.get('units') != units or code.get('units') != units
                or (row.get('modifiers') or []) != (line.get('modifiers') or [])
                or (code.get('modifiers') or []) != (line.get('modifiers') or [])
                or type(units) is not int or not 1 <= units <= 100
                or type(amount) is not int or not 0 < amount <= 100_000_000
                or not str(row.get('source_reference') or '').startswith('library:')):
            return None
        total += amount * units
    return total if 0 < total <= 100_000_000 else None


def apply(result: dict, lines: list[dict], guidance: dict, assumptions: list[str], allowed: bool) -> int | None:
    """Set deposit language while keeping final liability unknown."""
    amount = eligible_amount(result, lines) if allowed else None
    if amount is not None:
        guidance.update(status='estimated', basis='bcbstx_refundable_advance', reference_total_cents=amount,
            reason='Refundable clinic advance using the selected CPTs’ BCBS Texas fee references because the payer returned no financial terms. The Cigna allowed amount and patient liability remain unknown.',
            collection_rule={'mode': 'clinic_refundable_bcbstx_advance',
                             'reference': 'Clinic-directed BCBS Texas fee-schedule deposit for missing payer financial terms'})
        assumptions.append('Apply the advance to the processed claim and refund any amount above the adjudicated patient responsibility.')
    return amount
