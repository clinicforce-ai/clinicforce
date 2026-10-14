"""Attach explicit authorization approval state to visit requirement guidance."""
from __future__ import annotations


def attach_approval_status(output: dict, result: dict, codes: set[str]) -> None:
    authorization = output['authorization']
    approval_lines = [line for line in (result.get('price_range') or {}).get('lines') or []
                      if str(line.get('code') or '').upper() in codes]
    approval_verified = bool(approval_lines) and len(approval_lines) == len(result.get('codes') or []) and all(
        (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('status') == 'valid'
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('cleared') is True
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('reference')
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('verified_at')
        for line in approval_lines)
    authorization['approval_status'] = ('Verified' if approval_verified else 'Not verified') if authorization['status'] == 'Required' else 'Unknown' if authorization['status'] == 'Verification required' else 'Not needed under the stated rule'
    if approval_verified and len(approval_lines) == 1:
        approval = approval_lines[0]['benefits']['requirements']['authorization']
        authorization['approval_reference'] = approval['reference']
        authorization['approval_verified_at'] = approval['verified_at']
    if authorization['status'] == 'Required' and authorization['approval_status'] != 'Verified' and 'approval' not in authorization['reason'].lower():
        authorization['reason'] += ' Authorization approval is a separate check.'
