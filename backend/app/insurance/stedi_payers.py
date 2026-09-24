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
    if left == right and len(left) >= 2:
        return True
    # A payer that operates in Texas may still be named for another state.
    # Match the state in the payer's actual name, then check its listed scope.
    two_word_state = _STATE_ABBREVIATIONS.get("".join(right[-2:]))
    state = two_word_state or _STATE_ABBREVIATIONS.get(right[-1] if right else "")
    return (len(left) >= 3 and len(right) >= 3 and left[:-1] == right[:-(2 if two_word_state else 1)]
            and left[-1].upper() == state and state in operating_states)


def select_payer(items: list[dict[str, Any]], name: str, identifiers: list[str]) -> dict | None:
    """Accept one exact identifier/name match; never select a fuzzy first result."""
    eligible = [row.get("payer", {}) for row in items if isinstance(row, dict)]
    eligible = [payer for payer in eligible if payer.get("transactionSupport", {}).get("eligibilityCheck") == "SUPPORTED"
                and (not payer.get("coverageTypes") or "medical" in payer["coverageTypes"])]
    observed_ids = {value.casefold().strip() for value in identifiers if isinstance(value, str) and value.strip()}
    by_id = [payer for payer in eligible if observed_ids.intersection(
        str(value).casefold() for value in [payer.get("primaryPayerId"), payer.get("stediId"), *payer.get("aliases", [])] if value)]
    matches = by_id or [payer for payer in eligible if any(_same_name(name, candidate, payer.get("operatingStates") or [])
        for candidate in [payer.get("displayName", ""), *payer.get("names", [])] if isinstance(candidate, str))]
    unique = {payer.get("stediId"): payer for payer in matches if isinstance(payer.get("stediId"), str)}
    return next(iter(unique.values())) if len(unique) == 1 else None


async def search_payer(name: str, identifiers: list[str], api_key: str,
                       *, transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    if not api_key or not name:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), transport=transport) as client:
        response = await client.get(SEARCH_URL, params={"query": name[:200], "eligibilityCheck": "SUPPORTED", "pageSize": 100},
                                    headers={"Authorization": api_key})
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    return select_payer(payload["items"], name, identifiers)
