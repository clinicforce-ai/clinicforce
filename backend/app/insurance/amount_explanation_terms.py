"""Payer-reported office terms used in the amount explanation."""


def choice_plus_reported_terms(benefits: dict) -> str | None:
    rows = [row for row in benefits.get('reported_options') or []
            if row.get('service_type_code') == '98' and row.get('network') in {'Y', 'N'}]
    terms = {(kind, tier): {row.get(field) for row in rows
                            if row.get('kind') == kind and row.get('network') == tier}
             for kind, field in (('copay', 'amount_cents'), ('coinsurance', 'percent'))
             for tier in ('Y', 'N')}
    if (terms['copay', 'Y'] == terms['copay', 'N'] == {0}
            and len(terms['coinsurance', 'Y']) == len(terms['coinsurance', 'N']) == 1):
        in_rate = next(iter(terms['coinsurance', 'Y']))
        out_rate = next(iter(terms['coinsurance', 'N']))
        if in_rate is not None and out_rate is not None:
            return (f'The payer reported a $0.00 professional office copay, '
                    f'{in_rate}% in-network and {out_rate}% out-of-network coinsurance. ')
    return None
