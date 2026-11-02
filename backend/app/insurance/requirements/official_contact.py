"""Official provider contact fallback; never decides a member's requirement."""
from __future__ import annotations

from datetime import date
import re


# Official payer pages checked 2026-11-02. These are general commercial provider
# routes. Delegated programs and member products can require a different route.
CONTACTS = {
    'bcbstx': {
        'authorization': ('Provider customer service', '800-972-8088',
            'https://eauth.bcbstx.com/provider/contact-us'),
        'referral': ('Provider customer service', '800-972-8088',
            'https://eauth.bcbstx.com/provider/contact-us'),
    },
    'cigna': {
        'authorization': ('Precertification', '800-882-4462',
            'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'),
        'referral': ('Specialist referral', '866-494-2111',
            'https://www.cigna.com/health-care-providers/coverage-and-claims/precertification'),
    },
    'aetna': {
        'authorization': ('Precertification', '888-632-3862',
            'https://www.aetna.com/about-us/contact-aetna.html'),
        'referral': ('Provider contact center', '888-632-3862',
            'https://www.aetna.com/about-us/contact-aetna.html'),
    },
    'uhc': {
        'authorization': ('Provider Services', '877-842-3210',
            'https://www.uhcprovider.com/en/prior-auth-advance-notification.html'),
        'referral': ('Provider Services', '877-842-3210',
            'https://www.uhcprovider.com/en/prior-auth-advance-notification.html'),
    },
}


def payer_key(names: set[str]) -> str | None:
    joined = ' '.join(names).lower()
    matches = []
    if re.search(r'bcbstx|bluecross.*texas|blueshield.*texas', joined):
        matches.append('bcbstx')
    if 'cigna' in joined:
        matches.append('cigna')
    if 'aetna' in joined:
        matches.append('aetna')
    if re.search(r'united\s*health|\buhc\b', joined):
        matches.append('uhc')
    return matches[0] if len(matches) == 1 else None


def fallback(names: set[str], kind: str, code: str, service_date: str) -> dict | None:
    """Give staff a cited starting point, never an authorization or referral decision."""
    key = payer_key(names)
    if key is None or kind not in CONTACTS[key]:
        return None
    department, phone, url = CONTACTS[key][kind]
    script = (f'Please verify whether CPT {code} on {service_date} requires '
              f'{"prior authorization" if kind == "authorization" else "a referral"} '
              'for this member, exact product, provider, network tier and place of service. '
              'Please confirm the delegated administrator, current status, effective dates '
              'and a call reference. Have the member ID, provider NPI, diagnosis, CPT, '
              'service date and location available.')
    return {'contact': f'{department}: {phone}', 'contact_department': department,
            'contact_phone': phone, 'contact_source': {'label': 'Official payer provider contact',
                'url': url, 'retrieved_at': date(2026, 11, 2).isoformat()},
            'call_script': script, 'delegation_unverified': True}


def attach(output: dict, names: set[str], service_date: str) -> None:
    """Add an action only where a selected service still needs confirmation."""
    for procedure in output.get('procedures') or []:
        for kind in ('authorization', 'referral'):
            decision = procedure[kind]
            if (decision['status'] == 'Verification required' or
                    decision['status'] == 'Required' and
                    (decision.get('approval_status') != 'Verified' if kind == 'authorization'
                     else decision.get('referral_obtained') != 'YES')):
                contact = fallback(names, kind, procedure['code'], service_date)
                if contact:
                    decision.update(contact)
