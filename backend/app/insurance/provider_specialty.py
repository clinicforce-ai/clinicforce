"""Optional public NPI specialty evidence; never sends patient or payer data."""
import asyncio
import re
import time
from datetime import datetime, timezone

import httpx


_cache: dict[str, tuple[float, dict]] = {}


def office_role(labels: list[str]) -> str | None:
    roles = set()
    for label in labels:
        label = label.strip().casefold()
        if re.fullmatch(r'family (?:medicine|practice)|general practice|internal medicine|pediatrics|primary care(?: physician)?', label):
            roles.add('primary_care')
        elif re.search(r'neurolog|cardiolog|dermatolog|endocrinolog|gastroenterolog|rheumatolog|urolog|ophthalmolog|otolaryngolog|orthop(?:edic|aedic)|pulmonary disease|nephrolog|hematolog|oncolog', label):
            roles.add('specialist')
    return next(iter(roles)) if len(roles) == 1 else None


async def lookup(npi: str) -> dict:
    """Require one active individual NPI and one primary taxonomy; fail closed."""
    if not re.fullmatch(r'\d{10}', npi):
        return {}
    cached = _cache.get(npi)
    if cached and cached[0] > time.monotonic():
        return dict(cached[1])
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
            response = await asyncio.wait_for(client.get('https://npiregistry.cms.hhs.gov/api/',
                params={'version': '2.1', 'number': npi}), timeout=3)
            response.raise_for_status()
            rows = response.json().get('results') or []
        if len(rows) != 1:
            return {}
        row = rows[0]
        if (str(row.get('number')) != npi or row.get('enumeration_type') != 'NPI-1'
                or (row.get('basic') or {}).get('status') != 'A'):
            return {}
        primary = [item for item in row.get('taxonomies', []) if item.get('primary') is True]
        if len(primary) != 1 or not office_role([str(primary[0].get('desc') or '')]):
            return {}
        evidence = {'npi': npi, 'roles': [primary[0]['desc']], 'office_role': office_role([primary[0]['desc']]),
                    'taxonomy_code': primary[0].get('code'), 'source': 'NPPES primary taxonomy',
                    'checked_at': datetime.now(timezone.utc).isoformat()}
        if len(_cache) >= 256:
            _cache.pop(next(iter(_cache)))
        _cache[npi] = (time.monotonic() + 86400, evidence)
        return dict(evidence)
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError, AttributeError, KeyError):
        return {}
