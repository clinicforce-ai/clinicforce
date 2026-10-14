"""Referral and authorization status shown with collection guidance."""


def summarize_requirements(result: dict, lines: list[dict], guidance: dict, actions: list[str]) -> bool:
    adverse = False
    for kind in ('referral', 'authorization'):
        requirements = [line.get('benefits', {}).get('requirements', {}).get(kind, {}) for line in lines]
        guidance['requirement_sources'][kind] = (result.get('benefit_source') or 'Stedi') + ' / clinic evidence'
        if any(item.get('status') in {'denied', 'expired', 'pending'} or
               item.get('required') == 'required' and not item.get('cleared') for item in requirements):
            adverse = True
            guidance['requirements'][kind] = 'Required · patient action below'
            actions.append('Ask your referring doctor to send the referral for this visit.' if kind == 'referral' else
                           'Contact your plan about the authorization requirement before the service; the treating office may need to submit clinical information.')
        elif requirements and all(item.get('cleared') for item in requirements):
            guidance['requirements'][kind] = 'On file' if any(item.get('required') == 'required' for item in requirements) else 'Not required'
            if kind == 'authorization' and any('absent indicator' in line.get('benefits', {}).get('authorization_basis', '') for line in lines):
                guidance['requirements'][kind] = 'No requirement indicated'
                guidance['requirement_sources'][kind] = 'Stedi: no authorization indicator'
        else:
            explicit_unknown = kind == 'authorization' and any(line.get('benefits', {}).get('authorization_basis') == 'Payer indicator' for line in lines)
            guidance['requirements'][kind] = 'Unknown in payer response' if explicit_unknown else 'No requirement reported'
            guidance['requirement_sources'][kind] = 'Stedi' if explicit_unknown else 'eCW + Stedi'
            if kind == 'referral' and result.get('referral_evidence'):
                guidance['requirements'][kind] = 'Referral record on file'
                guidance['requirement_sources'][kind] = 'eCW; applicability to this service not established'
    return adverse
