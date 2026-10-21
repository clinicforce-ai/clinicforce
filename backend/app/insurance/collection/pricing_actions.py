"""Specific payer-term action when a selected CPT already has an allowed price."""


def priced_payer_gap(result: dict, payer: str) -> tuple[str, str, str] | None:
    for line in (result.get('price_range') or {}).get('lines') or []:
        code = str(line.get('code') or '')
        if not code or line.get('calculation') is not None or line.get('allowed_cents') is None:
            continue
        cpt = f'CPT {code}'
        issues = set(line.get('issues') or [])
        if 'Confirm which copay or coinsurance term applies, including any visit-count conditions.' in issues:
            return (f'the applicable copay or coinsurance term for {cpt} is unresolved',
                    'cost_share_term_unresolved',
                    f'ask {payer} which term applies to {cpt} at this visit and rerun the insured estimate')
        if 'Conflicting deductible applicability evidence requires review before calculation.' in issues:
            return (f'the payer gave conflicting deductible applicability for {cpt}',
                    'deductible_applicability_conflict',
                    f'ask {payer} whether the deductible applies to {cpt} and rerun the insured estimate')
        if 'Confirm the remaining individual deductible that applies to this visit.' in issues:
            return (f'the applicable individual deductible balance for {cpt} is unresolved',
                    'individual_deductible_unresolved',
                    f'obtain that balance from {payer} for {cpt} and rerun the insured estimate')
        if 'Confirm the remaining individual out-of-pocket maximum.' in issues:
            return (f'the applicable individual out-of-pocket remaining for {cpt} is unresolved',
                    'individual_oop_unresolved',
                    f'obtain that balance from {payer} for {cpt} and rerun the insured estimate')
    return None


def priced_line_issue(result: dict) -> tuple[str, str] | None:
    for line in (result.get('price_range') or {}).get('lines') or []:
        if line.get('calculation') is not None or line.get('allowed_cents') is None:
            continue
        issue = next((str(item).strip() for item in line.get('issues') or [] if str(item).strip()), None)
        if issue:
            return f"CPT {line['code']}" if line.get('code') else 'the selected service', issue
    return None
