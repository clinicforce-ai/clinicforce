"""Single office visit copay when a complete visit estimate is unavailable."""

from app.insurance.benefit_review import annual_oop_conflict_with_known_remaining
from app.insurance.library_network_exclusions import product_kind


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
            and (benefits.get('network_status') in {'in_network', 'out_of_network'}
                 or all(t.get('network_independent') for t in terms))
            and all(t.get('active') is True and t.get('copay_cents') is not None
                    and t.get('copay_period') in {'', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'}
                    and (t.get('deductible_applies') is not True or t.get('deductible_remaining_cents') == 0)
                    and (benefits.get('network_status') != 'out_of_network' or
                         t.get('deductible_applies') is False or t.get('deductible_remaining_cents') == 0)
                    and (not t.get('separate_copay_limit') or t.get('copay_remaining_cents') is not None)
                    and t.get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                    and t.get('cost_share') not in {'coinsurance', 'cumulative'}
                    and not any(not annual_oop_conflict_with_known_remaining(r, t)
                                and any(word in r.lower() for word in ('conditional', 'limit', 'date', 'identity', 'evidence'))
                                for r in t.get('review_reasons', [])) for t in terms)
            and len({t['copay_cents'] for t in terms}) == 1):
        payable = set()
        for line, terms_for_line in zip(office, terms):
            cap = (terms_for_line.get('copay_remaining_cents') if terms_for_line.get('separate_copay_limit')
                   else terms_for_line.get('oop_remaining_cents'))
            payable.add(min(terms_for_line['copay_cents'],
                            cap if cap is not None else terms_for_line['copay_cents'],
                            line['allowed_cents'] if line.get('allowed_cents') is not None else terms_for_line['copay_cents']))
        if len(payable) != 1:
            return None
        amount = payable.pop()
        if any(t.get('deductible_remaining_cents') != 0 and t.get('deductible_applies') is not False for t in terms):
            assumptions.append('The payer returned a fixed office copay but did not establish whether the deductible applies to it. This advance includes the copay only; additional deductible responsibility may follow the claim.')
        if any(t.get('separate_copay_limit') for t in terms):
            assumptions.append('The payer reports a separate remaining copay maximum. The office copay uses that balance, while the general out-of-pocket balance remains separate.')
        if any(not t.get('copay_period') for t in terms):
            assumptions.append('The payer did not specify a copay period. This advance applies one copay to the single office consultation.')
        if benefits.get('network_status') == 'out_of_network':
            names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
            assumptions.append(
                'This is only the verified out-of-network office copay. Apply Medicare Advantage member billing protections.'
                if len(names) == 1 and product_kind(str(names[0])) else
                'This is only the verified out-of-network office copay. The payer allowed amount and possible additional charges are unknown.')
        guidance.update(status='estimated', basis='office_copay',
                        reason='The applicable office-visit copay is collected once. Any additional service responsibility will follow the claim.')
    return amount
