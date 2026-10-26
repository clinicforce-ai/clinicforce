"""Sourced visit copays when a complete patient share is unavailable."""

import re

from app.insurance.benefit_review import annual_oop_conflict_with_known_remaining
from app.insurance.cpt_rules import EMG_ADD_ONS, NCS_CODES
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
                    and t.get('copay_period') in {'', 'VISIT', '27', 'DAY', '7', 'EPISODE', 'SERVICE', 'PROCEDURE'}
                    and (t.get('deductible_applies') is not True or t.get('deductible_remaining_cents') == 0)
                    and (benefits.get('network_status') != 'out_of_network' or
                         t.get('deductible_applies') is False or t.get('deductible_remaining_cents') == 0)
                    and (not t.get('separate_copay_limit') or t.get('copay_remaining_cents') is not None)
                    and t.get('coinsurance_percent') in {None, '0', '0.0', '0.00'}
                    and t.get('cost_share') not in {'coinsurance', 'cumulative'}
                    and not any(not annual_oop_conflict_with_known_remaining(r, t)
                                and 'unassigned out-of-pocket rows are retained' not in r.lower()
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
        oop_met = (amount == 0 and len(office) == len(lines)
                   and all(t.get('oop_remaining_cents') == 0 and not t.get('separate_copay_limit')
                           for t in terms))
        if not oop_met and any(t.get('deductible_remaining_cents') != 0 and t.get('deductible_applies') is not False for t in terms):
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
        guidance.update(status='estimated', basis='out_of_pocket_limit' if oop_met else 'office_copay',
                        reason=('The payer reports $0 remaining on the applicable individual in-network out-of-pocket maximum for this covered office visit.'
                                if oop_met else 'The applicable office-visit copay is collected once. Any additional service responsibility will follow the claim.'))
    return amount


def diagnostic_day_copay_amount(price: dict, lines: list[dict], guidance: dict,
                                assumptions: list[str]) -> int | None:
    """Collect one diagnostic copay when all planned testing shares its payer row."""
    if price.get('mode') != 'services' or not lines or not all(
            line.get('code') in NCS_CODES | EMG_ADD_ONS
            and line.get('allowed_basis') == 'clinic_contract'
            and (line.get('rate_resolution') or {}).get('status') == 'RESOLVED'
            and type(line.get('allowed_cents')) is int and line['allowed_cents'] > 0
            for line in lines):
        return None
    terms = [line.get('benefits') or {} for line in lines]
    amounts = {term.get('copay_cents') for term in terms}
    sources = {term.get('copay_id') for term in terms}
    periods = {term.get('copay_period') for term in terms}
    if (len(amounts) != 1 or len(sources) != 1 or len(periods) != 1
            or not next(iter(sources)) or next(iter(periods)) not in {'DAY', 'VISIT', '7', '27'}
            or type(next(iter(amounts))) is not int or next(iter(amounts)) <= 0):
        return None
    source_id = next(iter(sources))
    for term in terms:
        payer_row = next((row for row in term.get('reported_options') or []
                          if row.get('id') == source_id and row.get('kind') == 'copay'), None)
        note = str((payer_row or {}).get('payer_note') or '')
        if (not payer_row or payer_row.get('amount_cents') != term['copay_cents']
                or not re.search(r'\b(?:diagnostic medical(?: procedure)?|nerve conduction|electrodiagnostic|EMG|NCS)\b', note, re.I)
                or term.get('active') is not True
                or term.get('deductible_applies') is True and term.get('deductible_remaining_cents') != 0
                or term.get('oop_remaining_cents') == 0
                or term.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'}
                or term.get('cost_share') in {'coinsurance', 'cumulative'}
                or any(not annual_oop_conflict_with_known_remaining(reason, term)
                       and 'unassigned out-of-pocket rows are retained' not in reason.lower()
                       and any(word in reason.lower() for word in ('conflict', 'conditional', 'limit', 'date', 'identity', 'evidence'))
                       for reason in term.get('review_reasons') or [])):
            return None
    amount = min(next(iter(amounts)), sum(line['allowed_cents'] for line in lines))
    known_caps = [term['oop_remaining_cents'] for term in terms
                  if type(term.get('oop_remaining_cents')) is int]
    if known_caps:
        amount = min(amount, *known_caps)
    assumptions.append('The payer reported one diagnostic-medical copay per day. Deductible applicability and the final patient share remain unresolved; additional responsibility may follow the claim.')
    guidance.update(status='estimated', basis='diagnostic_day_copay',
                    reason='Collect one payer-reported diagnostic-medical copay for the selected testing today. Other patient responsibility follows the claim.')
    return amount
