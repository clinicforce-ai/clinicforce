"""Practice-only, case-backed inventory of unresolved CPT prices."""
from __future__ import annotations

import json
import re


CODE = re.compile(r'^[A-Z0-9]{4,5}$')


def summarize(cases: list[dict], limit: int, connections: list[dict] | None = None) -> dict:
    # Only display configured payer labels. An eCW free-text payer field could
    # contain patient-supplied notes and must not become an admin inventory name.
    payers = {str(value).strip().casefold(): str(row['display_name'])[:200]
              for row in connections or [] for value in [row.get('display_name'), *(row.get('aliases') or [])]
              if value and row.get('display_name')}
    grouped: dict[tuple[str, str, str], dict] = {}
    for case in cases:
        if case.get('mode') in {'alternatives', 'options'}:
            continue  # A possible code family is not an encountered billed CPT.
        lines = case.get('lines') or []
        if isinstance(lines, str):
            try:
                lines = json.loads(lines)
            except ValueError:
                continue
        if not isinstance(lines, list):
            continue
        payer = payers.get(str(case.get('payer') or '').strip().casefold(), 'Unmapped payer')
        observed = str(case.get('calculated_at') or case.get('created_at') or '')[:35]
        unique: set[tuple[str, str, str]] = set()
        for line in lines:
            if not isinstance(line, dict):
                continue
            code = str(line.get('code') or '').upper()
            if not CODE.fullmatch(code):
                continue
            for kind, field in (('insured_allowed', 'allowed_cents'), ('clinic_cash', 'cash_cents')):
                if kind == 'insured_allowed' and case.get('payment_mode') == 'cash':
                    continue
                if line.get(field) is not None:
                    continue
                key = (payer, code, kind)
                if key in unique:
                    continue
                unique.add(key)
                item = grouped.setdefault(key, {'payer': payer, 'code': code, 'kind': kind,
                                                'estimate_count': 0, 'last_observed_at': ''})
                item['estimate_count'] += 1
                item['last_observed_at'] = max(item['last_observed_at'], observed)
    gaps = sorted(grouped.values(), key=lambda item: (-item['estimate_count'], item['payer'], item['code'], item['kind']))
    return {'gaps': gaps[:50], 'group_count': len(gaps), 'scanned_case_count': len(cases),
            'scan_limit': limit, 'scan_limit_reached': len(cases) >= limit}
