"""Visit-bound payer out-of-network allowances documented by clinic administrators."""
from __future__ import annotations

import hashlib
import json


def product_binding(plan: dict | None) -> str:
    """Bind a manual payer quote to the product identifiers returned with benefits."""
    if not isinstance(plan, dict):
        return ''
    scope = {key: sorted(str(value).strip().casefold() for value in plan.get(key) or [] if value)
             for key in ('names', 'network_names')}
    scope.update({key: str(plan.get(key) or '').strip().casefold()
                  for key in ('plan_number', 'group_number')})
    if not any(scope.values()):
        return ''
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def quoted_rates(review: dict, selected: list[dict], *, payer_id: str, service_date: str,
                 provider_npi: str, place_of_service: str, patient_binding: str,
                 product_binding: str) -> list[dict]:
    """Return only quotes that still match the exact selected visit context."""
    quotes = review.get('payer_allowances') or {}
    if not isinstance(quotes, dict) or not all((payer_id, service_date, provider_npi,
                                               place_of_service, patient_binding, product_binding)):
        return []
    rates = []
    for line in selected:
        quote = quotes.get(line.get('id'))
        if not isinstance(quote, dict):
            continue
        modifiers = ','.join(line.get('modifiers') or [])
        units = line.get('units')
        amount = quote.get('unit_allowed_cents')
        if (not quote.get('id') or not quote.get('reference') or not quote.get('verified_at')
                or quote.get('channel') not in {'payer_portal', 'payer_phone', 'payer_document'}
                or type(amount) is not int or not 0 < amount <= 100_000_000
                or quote.get('payer_id') != payer_id or quote.get('code') != line.get('code')
                or quote.get('modifiers') != modifiers or quote.get('units') != units
                or quote.get('service_date') != service_date
                or str(quote.get('valid_through') or '') < service_date
                or quote.get('provider_npi') != provider_npi
                or quote.get('place_of_service') != place_of_service
                or quote.get('patient_binding') != patient_binding
                or quote.get('product_binding') != product_binding):
            continue
        rates.append({'id': quote['id'], 'line_id': line['id'], 'code': line['code'], 'modifier': modifiers,
                      'units': units, 'product_binding': product_binding,
                      'allowed_cents': amount, 'basis': 'payer_out_of_network',
                      'source_reference': 'Payer quote ' + quote['reference'],
                      'verified_at': quote['verified_at'], 'service_date': service_date,
                      'provider_npi': provider_npi, 'place_of_service': place_of_service,
                      'patient_binding': patient_binding})
    return rates
