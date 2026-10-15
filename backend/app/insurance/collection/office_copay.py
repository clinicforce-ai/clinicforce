"""Single office visit copay when a complete visit estimate is unavailable."""


def office_copay_amount(result: dict, benefits: dict, price: dict, lines: list[dict],
                       guidance: dict, assumptions: list[str]) -> int | None:
    amount = None
    # A unique fixed office copay can stand alone without an allowed rate.
    office = [line for line in lines if line.get('code', '').isdigit() and
              (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)]
    terms = [line.get('benefits') or {} for line in office]
    if (terms
            and (len(office) == 1 or price.get('mode') == 'alternatives')
            and all(line.get('units') == 1 for line in office)
            and all(t.get('active') is True and t.get('copay_cents') is not None
                    and t.get('copay_period') in {'', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'}
                    and (t.get('deductible_applies') is not True or t.get('deductible_remaining_cents') == 0)
                    and (benefits.get('network_status') != 'out_of_network' or
                         t.get('deductible_applies') is False or t.get('deductible_remaining_cents') == 0)
                    and (not t.get('separate_copay_limit') or t.get('copay_remaining_cents') is not None)
                    and t.get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                    and t.get('cost_share') not in {'coinsurance', 'cumulative'}
                    and not any(any(word in r.lower() for word in ('conditional', 'limit', 'date', 'identity', 'evidence'))
                                for r in t.get('review_reasons', [])) for t in terms)
            and len({t['copay_cents'] for t in terms}) == 1):
        cap = terms[0].get('copay_remaining_cents') if terms[0].get('separate_copay_limit') else terms[0].get('oop_remaining_cents')
        amount = min(terms[0]['copay_cents'], cap if cap is not None else terms[0]['copay_cents'])
        if all(line.get('allowed_cents') is not None for line in office):
            amount = min(amount, min(line['allowed_cents'] for line in office))
        if any(t.get('deductible_remaining_cents') != 0 and t.get('deductible_applies') is not False for t in terms):
            assumptions.append('The payer returned a fixed office copay but did not establish whether the deductible applies to it. This advance includes the copay only; additional deductible responsibility may follow the claim.')
        if any(t.get('separate_copay_limit') for t in terms):
            assumptions.append('The payer reports a separate remaining copay maximum. The office copay uses that balance, while the general out-of-pocket balance remains separate.')
        if any(not t.get('copay_period') for t in terms):
            assumptions.append('The payer did not specify a copay period. This advance applies one copay to the single office consultation.')
        if benefits.get('network_status') not in {'in_network', 'out_of_network'} and not result.get('pricing_assumptions') and not all(t.get('network_independent') for t in terms):
            assumptions.append('This provisional office advance uses the returned in-network specialist copay. The payer did not establish the member’s exact provider network; final responsibility may differ.')
        if benefits.get('network_status') == 'out_of_network':
            assumptions.append('This is only the verified out-of-network office copay. The payer allowed amount and possible additional charges are unknown.')
        guidance.update(status='estimated', basis='office_copay',
                        reason='The applicable office-visit copay is collected once. Any additional service responsibility will follow the claim.')
    return amount
