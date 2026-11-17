"""Published requirements for the exact 2026 UHC Texas QMB office product."""

from app.insurance.published_plan_benefits import UHC_TX_QMB_H4514_024_2026


REFERRAL_SOURCE = {
    'label': '2026 UHC Dual Complete Texas quick reference, specialist referrals',
    'url': 'https://www.uhcprovider.com/content/dam/provider/docs/public/health-plans/medicare/2026/qrg/2026-UHC-Dual-Complete-TX-QRG-H2406-H4514.pdf',
    'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
}

AUTHORIZATION_SOURCE = {
    'label': 'WellMed July 2026 Prior Authorization List',
    'url': 'https://www.wellmedhealthcare.com/wp-content/uploads/2026/05/WellMed-PAL-July-2026.pdf',
    'effective_from': '2026-07-01', 'effective_to': '2026-12-31',
}

UHC_TX_MA_REFERRAL_SOURCE = {
    'label': 'UnitedHealthcare Texas Medicare Advantage PPO specialist referrals',
    'url': 'https://www.uhcprovider.com/en/health-plans-by-state/texas-health-plans/tx-medicare-plans/tx-med-adv.html',
    'source_version': 'Texas MA plan description, checked 2026-10-21',
    'verified_at': '2026-10-21', 'effective_from': '2026-01-01',
    'effective_to': '2026-12-31',
}


def decision(kind: str, benefits: dict, codes: set[str], context: dict) -> tuple | None:
    if (benefits.get('published_plan_rule') != UHC_TX_QMB_H4514_024_2026
            or codes != {'99213'} or str(context.get('pos') or '') != '11'):
        return None
    if kind == 'referral':
        return ('Required', 'The 2026 Texas UHC Dual Complete quick reference requires a PCP referral for specialist visits. Confirm a valid referral on file.', REFERRAL_SOURCE)
    return ('Verification required', 'CPT 99213 is absent from the July 2026 WellMed list; confirm this member’s delegated group and any current authorization rule.', AUTHORIZATION_SOURCE)
