"""Resolve an eCW payer against Stedi's live Payer Network before sending PHI."""
from __future__ import annotations

import re
from typing import Any

import httpx


SEARCH_URL = "https://payers.us.stedi.com/2024-04-01/payers/search"
_FILLER = {"and", "of", "the", "inc", "company", "insurance"}
_STATE_ABBREVIATIONS = dict(zip(
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada newhampshire newjersey newmexico newyork northcarolina northdakota ohio oklahoma oregon pennsylvania rhodeisland southcarolina southdakota tennessee texas utah vermont virginia washington westvirginia wisconsin wyoming districtofcolumbia".split(),
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
))


def _words(value: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", value.casefold()) if word not in _FILLER]


def _same_name(observed: str, candidate: str, operating_states: list[str]) -> bool:
    left, right = _words(observed), _words(candidate)
    if left == right and left:
        return True
    # A payer that operates in Texas may still be named for another state.
    # Match the state in the payer's actual name, then check its listed scope.
    two_word_state = _STATE_ABBREVIATIONS.get("".join(right[-2:]))
    state = two_word_state or _STATE_ABBREVIATIONS.get(right[-1] if right else "")
    return (len(left) >= 3 and len(right) >= 3 and left[:-1] == right[:-(2 if two_word_state else 1)]
            and left[-1].upper() == state and state in operating_states)


def matching_payers(items: list[dict[str, Any]], name: str, identifiers: list[str]) -> list[dict]:
    """Retain exact supported matches, including genuine routing ambiguity."""
    eligible = [row.get("payer", {}) for row in items if isinstance(row, dict)]
    eligible = [payer for payer in eligible if payer.get("transactionSupport", {}).get("eligibilityCheck") in {"SUPPORTED", "ENROLLMENT_REQUIRED"}
                and (not payer.get("coverageTypes") or "medical" in payer["coverageTypes"])]
    observed_ids = {value.casefold().strip() for value in identifiers if isinstance(value, str) and value.strip()}
    by_id = [payer for payer in eligible if observed_ids.intersection(
        str(value).casefold() for value in [payer.get("primaryPayerId"), payer.get("stediId"), *payer.get("aliases", [])] if value)]
    by_name = [payer for payer in eligible if _same_name(name, payer.get("displayName", ""), payer.get("operatingStates") or [])]
    matches = by_id or by_name or [payer for payer in eligible if any(_same_name(name, candidate, payer.get("operatingStates") or [])
        for candidate in [payer.get("displayName", ""), *payer.get("names", [])] if isinstance(candidate, str))]
    unique = {payer.get("stediId"): payer for payer in matches if isinstance(payer.get("stediId"), str)}
    return list(unique.values())


def select_payer(items: list[dict[str, Any]], name: str, identifiers: list[str]) -> dict | None:
    matches = matching_payers(items, name, identifiers)
    return matches[0] if len(matches) == 1 else None


async def search_payer(name: str, identifiers: list[str], api_key: str,
                       *, transport: httpx.AsyncBaseTransport | None = None, plan: str | None = None,
                       candidates: list[dict] | None = None) -> dict | None:
    if not api_key or not (name or identifiers):
        return None
    # Original Medicare Part B uses CMS/HETS for eligibility, including state
    # MAC labels. Never route Medicare Advantage or a supplement to CMS.
    if plan == 'MB' and re.fullmatch(r'Medicare(?: of)? (?:Texas|TX)(?: Part B)?', name, re.I):
        name, identifiers = 'CMS', ['CMSMED']
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), transport=transport) as client:
        # Search exact eCW payer IDs first; a name search may omit their record.
        by_id = []
        for identifier in dict.fromkeys(value.strip() for value in identifiers if isinstance(value, str) and value.strip()):
            response = await client.get(SEARCH_URL, params={"query": identifier[:200], "pageSize": 100},
                                        headers={"Authorization": api_key})
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                by_id.extend(match for row in payload['items']
                             if (match := select_payer([row], '', [identifier])))
        by_id = list({row['stediId']: row for row in by_id}.values())
        if len(by_id) != 0:
            if candidates is not None:
                candidates.extend({**row, 'match_basis': 'identifier'} for row in by_id)
            return by_id[0] if len(by_id) == 1 else None
        if not name:
            return None
        response = await client.get(SEARCH_URL, params={"query": name[:200], "pageSize": 100},
                                    headers={"Authorization": api_key})
        response.raise_for_status()
        payload = response.json()
        matches = matching_payers(payload['items'], name, []) if isinstance(payload, dict) and isinstance(payload.get('items'), list) else []
        if candidates is not None:
            if matches:
                candidates.extend({**row, 'match_basis': 'name_or_alias'} for row in matches)
            elif isinstance(payload, dict):
                # Directory suggestions are explicitly unverified and can only
                # be used after clinician selection; never auto-select one.
                suggestions = [row.get('payer') or {} for row in payload.get('items') or [] if isinstance(row, dict)]
                suggestions = {row['stediId']: row for row in suggestions if row.get('stediId')
                    and row.get('transactionSupport', {}).get('eligibilityCheck') in {'SUPPORTED', 'ENROLLMENT_REQUIRED'}
                    and (not row.get('coverageTypes') or 'medical' in row['coverageTypes'])}
                candidates.extend({**row, 'match_basis': 'directory_suggestion'} for row in list(suggestions.values())[:5])
        return matches[0] if len(matches) == 1 else None


async def eligibility_enrolled(payer_id: str, npi: str, api_key: str, *, transport=None) -> bool:
    """Read enrollment before a paid request; never create an enrollment."""
    return (await eligibility_enrollment(payer_id, npi, api_key, transport=transport))['status'] == 'LIVE'


async def eligibility_enrollment(payer_id: str, npi: str, api_key: str, *, transport=None) -> dict:
    """Report exact provider/payer eligibility state, including read failures.

    No status filter: non-live enrollment must not look like an absent record.
    Pagination uses only the documented token on the fixed Stedi endpoint.
    """
    statuses = set()
    params = {'payerIds': payer_id, 'providerNpis': npi, 'transactions': 'eligibilityCheck', 'pageSize': 100}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), transport=transport) as client:
        try:
            seen = set()
            for _ in range(10):
                response = await client.get('https://enrollments.us.stedi.com/2024-09-01/enrollments',
                    params=params, headers={'Authorization': api_key})
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
                    raise ValueError('Invalid enrollment response')
                for row in payload['items']:
                    if (row.get('payer', {}).get('stediPayerId') == payer_id
                            and row.get('provider', {}).get('npi') == npi
                            and row.get('transactions', {}).get('eligibilityCheck', {}).get('enroll') is True):
                        statuses.add(row.get('status'))
                if 'LIVE' in statuses:
                    return {'status': 'LIVE', 'reason_code': 'enrollment_live'}
                token = payload.get('nextPageToken')
                if not token:
                    break
                if not isinstance(token, str) or token in seen:
                    raise ValueError('Invalid enrollment pagination')
                seen.add(token)
                params['pageToken'] = token
            else:
                raise ValueError('Incomplete enrollment search')
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return {'status': 'LOOKUP_FAILED', 'reason_code': 'enrollment_lookup_failed'}
    status = next((value for value in ('PROVIDER_ACTION_REQUIRED', 'STEDI_ACTION_REQUIRED', 'PROVISIONING',
                                      'SUBMITTED', 'DRAFT', 'REJECTED', 'CANCELED') if value in statuses),
                  'UNKNOWN' if statuses else 'MISSING')
    reason = ('enrollment_pending' if status in {'STEDI_ACTION_REQUIRED', 'PROVISIONING', 'SUBMITTED'}
              else 'enrollment_' + status.lower())
    return {'status': status, 'reason_code': reason}
