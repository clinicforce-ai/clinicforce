"""Classify payer-sourced medical products separately from eligibility routes."""
from __future__ import annotations

import re


def classify(names: list[str] | None, *, payer_id: str = '',
             insurance_types: list[str] | None = None) -> str:
    """Return a supported family; generic labels cannot establish an MA product."""
    labels = [re.sub(r'[^a-z0-9]+', ' ', str(value).casefold()).strip()
              for value in names or [] if str(value).strip()]
    if not labels:
        return 'original_medicare' if payer_id in {'CMS', 'CMSMED'} else 'unknown'
    medical = [label for label in labels if not re.search(
        r'\b(?:spectera|vision|dental|pharmacy|prescription|drug|hearing)\b', label)]
    if not medical:
        return 'supplementary'
    if payer_id in {'CMS', 'CMSMED'} and not any('advantage' in label or 'dsnp' in label
                                                 or 'd snp' in label for label in medical):
        return 'original_medicare'
    if len(set(medical)) != 1:
        return 'unknown'
    label = medical[0]
    ma = bool(re.search(r'\bmedicare\s+advantage\b|\b(?:d\s*snp|dual\s+special\s+needs)\b', label)
              or re.search(r'\b(?:rppo|lppo)\b', label) and 'complete care' in label)
    if ma:
        if re.search(r'\b(?:d\s*snp|dual\s+special\s+needs)\b', label):
            return 'medicare_advantage_dsnp'
        if re.search(r'\bgroup\s+medicare\s+advantage\s+pp\b|\bgroup\b.*\bppo\b|\bppo\b.*\bgroup\b', label):
            return 'medicare_advantage_group_ppo'
        if re.search(r'\b(?:regional\s+(?:medicare\s+advantage\s+)?ppo|rppo)\b', label):
            return 'medicare_advantage_regional_ppo'
        if re.search(r'\b(?:local\s+(?:medicare\s+advantage\s+)?ppo|lppo)\b', label):
            return 'medicare_advantage_local_ppo'
        if re.search(r'\bhmo\b', label):
            return 'medicare_advantage_hmo'
        return 'medicare_advantage_other'
    if 'medicaid' in label or 'chip' in label:
        return 'medicaid'
    if re.search(r'\b(?:original|traditional)\s+medicare\b|\bmedicare\s+part\s+[ab]\b', label):
        return 'original_medicare'
    if any(str(value).upper() in {'MA', 'MB'} for value in insurance_types or []):
        # Part A/B in a commercial-payer 271 can describe MA enrollment.
        return 'unknown'
    return 'commercial' if label not in {'medicare', 'ppo', 'hmo'} else 'unknown'


def is_medicare_advantage(names: list[str] | None, *, payer_id: str = '') -> bool:
    return classify(names, payer_id=payer_id).startswith('medicare_advantage_')
