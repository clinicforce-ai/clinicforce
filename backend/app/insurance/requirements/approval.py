"""Keep published requirements separate from member-specific clearance evidence."""
from __future__ import annotations


def attach_approval_status(output: dict, result: dict, codes: set[str]) -> None:
    authorization = output['authorization']
    referral = output['referral']
    approval_lines = [line for line in (result.get('price_range') or {}).get('lines') or []
                      if str(line.get('code') or '').upper() in codes]
    referral_verified = bool(approval_lines) and len(approval_lines) == len(result.get('codes') or []) and all(
        (line.get('benefits') or {}).get('requirements', {}).get('referral', {}).get('status') == 'valid'
        and (line.get('benefits') or {}).get('requirements', {}).get('referral', {}).get('cleared') is True
        and (line.get('benefits') or {}).get('requirements', {}).get('referral', {}).get('reference')
        and (line.get('benefits') or {}).get('requirements', {}).get('referral', {}).get('verified_at')
        for line in approval_lines)
    approval_verified = bool(approval_lines) and len(approval_lines) == len(result.get('codes') or []) and all(
        (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('status') == 'valid'
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('cleared') is True
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('reference')
        and (line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('verified_at')
        for line in approval_lines)
    authorization['approval_status'] = ('Verified' if approval_verified else 'Not verified') if authorization['status'] == 'Required' else 'Unknown' if authorization['status'] == 'Verification required' else 'Not needed under the stated rule'
    referral['referral_obtained'] = ('YES' if referral_verified else 'UNKNOWN') if referral['status'] == 'Required' else ('NOT APPLICABLE' if referral['status'] == 'Not required' else 'UNKNOWN')
    if referral['status'] == 'Required' and not referral_verified and any(
            (line.get('benefits') or {}).get('requirements', {}).get('referral', {}).get('status') in {'pending', 'denied', 'expired'}
            for line in approval_lines):
        referral['referral_obtained'] = 'NO'
    pa_states = {(line.get('benefits') or {}).get('requirements', {}).get('authorization', {}).get('status')
                 for line in approval_lines}
    authorization['pa_approval'] = (
        'NOT APPLICABLE' if authorization['status'] == 'Not required' else
        'APPROVED' if approval_verified and authorization['status'] == 'Required' else
        'DENIED' if authorization['status'] == 'Required' and 'denied' in pa_states else
        'PENDING' if authorization['status'] == 'Required' and 'pending' in pa_states else
        'EXPIRED' if authorization['status'] == 'Required' and 'expired' in pa_states else
        'UNVERIFIED')
    if approval_verified and len(approval_lines) == 1:
        approval = approval_lines[0]['benefits']['requirements']['authorization']
        authorization['approval_reference'] = approval['reference']
        authorization['approval_verified_at'] = approval['verified_at']
    if authorization['status'] == 'Required' and authorization['approval_status'] != 'Verified' and 'approval' not in authorization['reason'].lower():
        authorization['reason'] += ' Authorization approval is a separate check.'
