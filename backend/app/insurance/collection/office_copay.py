"""Sourced visit copays when a complete patient share is unavailable."""

import re

from app.insurance.benefit_review import annual_oop_conflict_with_known_remaining
from app.insurance.cpt_rules import EMG_ADD_ONS, NCS_CODES
from app.insurance.library_network_exclusions import product_kind
from app.insurance.network_participation import verified_in_network
from app.insurance.provider_specialty import office_role


def preview_office_copay_applies(benefits: dict, price: dict, lines: list[dict]) -> bool:
    """A common office copay can be independent of the eventual E/M level."""
    return (price.get('mode') == 'alternatives'
        and benefits.get('network_source') != 'clinic_contract_inventory'
        and bool(lines) and all(str(line.get('code')) in
            {str(n) for n in (*range(99202, 99206), *range(99212, 99216))} for line in lines)
        and len({(line.get('benefits') or {}).get('copay_cents') for line in lines}) == 1
        and (lines[0].get('benefits') or {}).get('copay_cents') is not None)


def uhc_choice_plus_registration_copay(result: dict, benefits: dict, price: dict,
                                       lines: list[dict], amount: int | None,
                                       basis: str) -> bool:
    """Allow a payer-bound specialist copay despite a bounded eCW name typo."""
    identity = result.get('identity') or benefits.get('identity') or {}
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    provider = (result.get('appointment_context') or {}).get('rendering_provider') or {}
    telehealth = result.get('telehealth') or {}
    card = result.get('card_check') or {}
    line = lines[0] if len(lines) == 1 else {}
    terms = line.get('benefits') or {}
    selected = [row for row in terms.get('reported_options') or benefits.get('reported_options') or []
                if row.get('id') == terms.get('copay_id')]
    return (identity.get('reconciliation_rule') == 'payer_registration_variant'
            and str(result.get('payer') or '').casefold().replace(' ', '') == 'unitedhealthcare'
            and [re.sub(r'[^a-z]', '', str(name).casefold()) for name in names] == ['choiceplus']
            and telehealth.get('place_of_service') == '10'
            and telehealth.get('modality') == 'audio_video'
            and office_role(provider.get('roles') or []) == 'specialist'
            and verified_in_network(benefits)
            and basis == 'office_copay' and type(amount) is int and 0 < amount <= 100_000
            and price.get('mode') == 'services' and not price.get('service_issues')
            and all('patient names differ between eCW and the payer' in condition
                    for condition in line.get('conditions') or [])
            and line.get('code') in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
            and line.get('units') == 1 and not line.get('modifiers')
            and terms.get('active') is True and terms.get('copay_period') in {'', 'VISIT', '27'}
            and terms.get('deductible_applies') is not True
            and terms.get('coinsurance_percent') in {'0', '0.0', '0.00'}
            and type(terms.get('copay_cents')) is int and amount <= terms['copay_cents']
            and len(selected) == 1 and selected[0].get('service_type_code') == 'BY'
            and re.fullmatch(r'provider role other', str(selected[0].get('payer_note') or '').strip(), re.I) is not None
            and selected[0].get('amount_cents') == terms['copay_cents']
            and card.get('status') not in {'conflict', 'mismatch'} and not card.get('conflicts'))


def cigna_oap_televisit_office_copay(result: dict, benefits: dict, price: dict,
                                     lines: list[dict], guidance: dict,
                                     assumptions: list[str]) -> int | None:
    """Apply Cigna's specialist office benefit to a regular-provider video visit."""
    card = result.get('card_check') or {}
    names = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
    telehealth = result.get('telehealth') or {}
    provider = (result.get('appointment_context') or {}).get('rendering_provider') or {}
    if not (
        'cigna' in str(result.get('payer') or '').casefold()
        and len(names) == 1 and re.sub(r'[^a-z]', '', str(names[0]).casefold()) == 'openaccessplus'
        and telehealth.get('place_of_service') == '10'
        and telehealth.get('modality') == 'audio_video'
        and office_role(provider.get('roles') or []) == 'specialist'
        and verified_in_network(benefits)
        and price.get('mode') == 'services' and not price.get('conditional')
        and len(lines) == 1 and lines[0].get('code') in
            {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
        and lines[0].get('units') == 1
        and (lines[0].get('benefits') or {}).get('active') is True
        and not card.get('conflicts') and not card.get('differences')
        and card.get('status') != 'conflict'
    ):
        return None
    service_date = str(result.get('service_date') or '')
    def current(row: dict) -> bool:
        dates = re.findall(r'\d{4}-\d{2}-\d{2}', ' '.join(
            str(value) for value in (row.get('effective_dates') or {}).values()))
        return (not dates or len(dates) <= 2 and dates[0] <= service_date
                and (len(dates) == 1 or service_date <= dates[1]))
    rows = benefits.get('reported_options') or []
    def office_rows(kind: str) -> list[dict]:
        return [row for row in rows if row.get('kind') == kind
                and row.get('service_type_code') == '98'
                and row.get('coverage_level') == 'INDIVIDUAL'
                and row.get('network') == 'IN_NETWORK'
                and '11' in (row.get('places_of_service') or [])
                and row.get('plan_association') in {'medical', 'medical_candidate', 'unassigned'}
                and current(row)]
    copays = [row for row in office_rows('copay')
              if re.search(r'\bspecialist\b', str(row.get('payer_note') or ''), re.I)
              and not re.search(r'\b(?:vendor|teladoc|telemedicine|virtual|diagnos|mental|substance)\b',
                                str(row.get('payer_note') or ''), re.I)
              and row.get('time_period') in {'VISIT', '27'}]
    if (len(copays) != 1 or type(copays[0].get('amount_cents')) is not int
            or not 0 < copays[0]['amount_cents'] <= 100_000
            or not any(row.get('amount_cents') == 0 and
                       re.search(r'\bspecialist\b', str(row.get('payer_note') or ''), re.I)
                       for row in office_rows('deductible'))
            or not any(row.get('percent') in {'0', '0.0', '0.00'} and
                       re.search(r'\bspecialist\b', str(row.get('payer_note') or ''), re.I)
                       for row in office_rows('coinsurance'))):
        return None
    card_amount = card.get('office_copay_cents')
    if card_amount is not None and (card.get('status') not in {'matched', 'filled_missing'}
            or card.get('source') not in {'clinicforce_card', 'ecw_card'}
            or card.get('reference_only') is True or card_amount != copays[0]['amount_cents']):
        return None
    amount = copays[0]['amount_cents']
    oop_remaining = benefits.get('oop_remaining_cents')
    if type(oop_remaining) is int:
        amount = min(amount, oop_remaining)
    allowed = lines[0].get('allowed_cents')
    if type(allowed) is int:
        amount = min(amount, allowed)
    guidance['payer_reported_office_copay_cents'] = copays[0]['amount_cents']
    assumptions.append('The current payer response reports one in-network specialist office copay, $0 office deductible and 0% office coinsurance. A separately listed contracted-vendor telehealth benefit does not apply to this regular-provider visit. Final responsibility follows the claim.')
    guidance.update(status='estimated', basis='office_copay',
                    reason='Collect the payer-reported specialist copay for this audio-video visit. The CPT allowed amount and any later claim adjustment remain unresolved.')
    return amount


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
                    and (t.get('copay_period') in {'', 'VISIT', '27', 'DAY', '7', 'EPISODE', 'SERVICE', 'PROCEDURE'}
                         or t.get('copay_period') == 'CONTRACT' and any(
                             row.get('id') == t.get('copay_id') and re.search(
                                 r'\b(?:ONLY )?1 COPAYMENT, PER DATE OF SERVICE, PER PROVIDER\b',
                                 str(row.get('payer_note') or ''), re.I)
                             for row in t.get('reported_options') or []))
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
    return amount if amount is not None else cigna_refundable_office_copay(
        result, benefits, price, lines, guidance, assumptions)


def cigna_refundable_office_copay(result: dict, benefits: dict, price: dict,
                                  lines: list[dict], guidance: dict,
                                  assumptions: list[str]) -> int | None:
    """Collect one payer-reported office copay as a refundable advance when tier is unknown."""
    visit = result.get('appointment_context') or {}
    card = result.get('card_check') or {}
    if (result.get('nmct_bcbstx_refundable_advance_enabled') is not True
            or str(result.get('payer') or '').casefold() != 'cigna'
            or (result.get('network_evidence') or {}).get('decision') != 'lacking_applicable_network_evidence'
            or benefits.get('network_status') != 'unknown'
            or visit.get('place_of_service') != '11'
            or office_role((visit.get('rendering_provider') or {}).get('roles') or []) != 'specialist'
            or card.get('status') in {'conflict', 'mismatch'} or card.get('conflicts')
            or card.get('differences')
            or result.get('service_date') != str(result.get('checked_at') or '')[:10]
            or len((((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])) != 1
            or price.get('mode') != 'services' or price.get('conditional')
            or len(lines) != 1 or lines[0].get('code') not in
                {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
            or lines[0].get('units') != 1):
        return None
    terms = lines[0].get('benefits') or {}
    copay = terms.get('copay_cents')
    if (terms.get('active') is not True or terms.get('network_status') == 'out_of_network'
            or type(copay) is not int or not 0 < copay <= 100_000
            or terms.get('copay_period') not in {'', 'VISIT', '27', 'DAY', '7'}
            or terms.get('deductible_applies') is True and terms.get('deductible_remaining_cents') != 0
            or terms.get('coinsurance_percent') not in {None, '0', '0.0', '0.00'}
            or terms.get('oop_remaining_cents') is not None and terms['oop_remaining_cents'] < copay
            or benefits.get('oop_remaining_cents') is not None and benefits['oop_remaining_cents'] < copay
            or any(row.get('kind') == 'out_of_pocket' and row.get('amount_cents') == 0
                   and row.get('service_type_code') == '30' and row.get('time_period') in {'REMAINING', '29'}
                   and row.get('network') in {'IN_NETWORK', 'Y'}
                   and str(row.get('coverage_level') or '').upper() in {'INDIVIDUAL', 'IND'}
                   for row in benefits.get('reported_options') or [])):
        return None
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('kind') == 'copay' and row.get('service_type_code') == '98'
            and str(row.get('coverage_level') or '').upper() in {'INDIVIDUAL', 'IND'}
            and row.get('network') in {'IN_NETWORK', 'Y'}
            and row.get('places_of_service') == ['11']
            and row.get('amount_cents') == copay
            and re.search(r'\bspecialist\b', str(row.get('payer_note') or ''), re.I)]
    if len(rows) != 1:
        return None
    guidance.update(status='estimated', basis='cigna_refundable_office_copay',
        payer_reported_office_copay_cents=copay,
        collection_rule={'mode': 'cigna_refundable_office_copay', 'reference': 'Clinic-directed refundable Cigna office copay advance'},
        reason='Collect the payer-reported specialist office copay as a refundable advance. Provider participation for this exact product is still unconfirmed; reconcile the processed claim and refund any excess.')
    assumptions.append('The payer returned this in-network POS 11 specialist copay for the member. Provider network participation and the final patient liability remain unconfirmed.')
    return copay


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
