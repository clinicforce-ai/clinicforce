"""Plan-specific research from NMCT's three-sheet workbook, never payer approval."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.insurance import cpt_rules

from app.insurance.plan_matrix_source import (
    WORKBOOK,
    SHEETS,
    PAYER_ALIASES,
    CODE,
    _key,
    _urls,
    _names,
    _sheet,
    parse,
    bundled,
    uploaded,
)


def _payer(row: dict, names: set[str]) -> bool:
    payer = row['Payer']
    aliases = PAYER_ALIASES.get(payer) or {_key(payer)}
    return any(name == alias or len(alias) >= 6 and name.startswith(alias)
               for name in names for alias in aliases)


def _portal_reference(data: dict, row: dict, plan: str) -> dict:
    payer = row['Payer']
    return data['portals'].get('UnitedHealthcare (WellMed)' if 'wellmed' in plan and payer == 'UnitedHealthcare' else payer) or {}


def _portal(data: dict, row: dict, plan: str, kind: str) -> str | None:
    payer = row['Payer']
    reference = _portal_reference(data, row, plan)
    # The contact sheet identifies the portal; the matrix supplies a fully qualified URL.
    portal_name = _key(reference.get('Primary Portal'))
    matrix_urls = _urls(row.get('Eligibility Portal URL'))
    reference_urls = _urls(' '.join(url if url.startswith('https://') else 'https://' + url
                                    for url in re.split(r'\s+and\s+', str(reference.get('Portal URL') or '')) if url))
    urls = reference_urls or matrix_urls
    if kind == 'authorization' and payer == 'Humana Medicare (Pending)':
        return next((url for url in matrix_urls if 'humana.com' in urlparse(url).netloc), None) or (urls[0] if urls else None)
    if 'wellmed' in plan and 'wellmed' in portal_name:
        return next((url for url in urls if 'wellmed' in urlparse(url).netloc), None)
    return urls[0] if urls else None


def _research_source(data: dict, label: str) -> dict:
    return {'label': label, **({'document_id': data['document_id']} if data.get('document_id') else {})}


def _official_ppo_source(row: dict, family: str | None) -> list[dict]:
    if row['Payer'] == 'Blue Cross Blue Shield of Texas' and family == 'PPO':
        return [{'label': 'BCBS PPO manual',
                 'url': 'https://www.bcbstx.com/docs/provider/tx/standards/manuals/ppo-provider-manual/ppo-secd-referral.pdf'}]
    return []


def _patient_evidence(result: dict, context: dict, steps: dict) -> list[str]:
    description = steps['Eligibility check']['Exact Fields ClinicForce Must Pull'].casefold()
    benefits = result.get('benefits') or {}
    identity = benefits.get('identity') or result.get('identity') or {}
    gaps = []
    if 'coverage status' in description and benefits.get('active') is not True:
        gaps.append('active coverage')
    period = context.get('coverage_period') or {}
    visit = str(context.get('service_date') or '')[:10]
    start, end = str(period.get('start') or '')[:10], str(period.get('end') or '')[:10]
    if 'effective date' in description and (benefits.get('service_date_matches') is False or
            start and start > visit or end and end < visit or not
            (benefits.get('service_date_matches') is True or start or end)):
        gaps.append('coverage dates')
    if 'member id' in description and not context.get('member_id'):
        gaps.append('member ID')
    if 'group number' in description and not context.get('group_number') and context.get('payer') != 'Traditional Medicare':
        gaps.append('group number')
    if not context.get('rendering_npi'):
        gaps.append('rendering provider NPI')
    if context.get('payer') != 'Traditional Medicare' and benefits.get('network_status') != 'in_network':
        gaps.append('provider network')
    if identity.get('status') not in {'matched', 'reconciled'}:
        gaps.append('patient identity')
    if not result.get('benefit_source'):
        gaps.append('current payer eligibility source')
    return gaps


def evaluate(result: dict, context: dict, data: dict) -> dict | None:
    """Return independent referral/auth research for an exact plan, or cautious lookup guidance."""
    payer_names = {_key(value) for value in context.get('payer_names') or [] if value}
    payer_rows = [(number, row) for number, row in data['rows'] if _payer(row, payer_names)]
    if not payer_rows:
        return None
    reported_plans = [str(value) for value in context.get('plans') or [] if value]
    plans = {_key(value) for value in reported_plans if _key(value) not in {'', 'ci', 'commercial', 'ppo', 'hmo'}}
    if any('bluecard' in _key(value) for value in reported_plans) and any(
            row['Payer'] == 'Blue Cross Blue Shield of Texas' for _, row in payer_rows):
        payer_rows += [(number, row) for number, row in data['rows']
                       if row['Payer'] == 'Blue Cross Blue Shield (out-of-state BlueCard)']
    matched = [(number, row) for number, row in payer_rows
               if plans & _names(row["Specific Plan / Network Names on NMCT's Accepted List"])]
    if len(matched) != 1:
        families = set()
        if any('bluecard' in _key(value) for value in reported_plans):
            families.add('BlueCard')
        if any(re.search(r'\bHMO\b|health maintenance organization', value, re.I) for value in reported_plans):
            families.add('HMO')
        if any(re.search(r'\bPPO\b|preferred provider (?:option|organization)', value, re.I) for value in reported_plans):
            families.add('PPO')
        family = next(iter(families)) if len(families) == 1 else None
        family_row = next(((number, row) for number, row in payer_rows
                           if (row['Payer'] == 'Blue Cross Blue Shield (out-of-state BlueCard)' and family == 'BlueCard')
                           or (row['Payer'] == 'Blue Cross Blue Shield of Texas' and family in {'PPO', 'HMO'}
                               and row['Plan Group (shared rule set)'].startswith(family))), None)
        if not matched and family_row:
            number, row = family_row
            label = f"{data['filename']} · Payer Plan Matrix, row {number} ({family} group research; product not matched)"
            referral = (f'Reported {family} description; the matrix says no referral generally for its PPO group, '
                        'but the exact patient product and member rule need payer confirmation.' if family == 'PPO' else
                        f'Reported {family} description; the matrix says HMO referrals generally apply, '
                        'but the exact patient product and member rule need payer confirmation.' if family == 'HMO' else
                        'Reported BlueCard description; the home plan controls referrals and must be confirmed.')
            codes = ', '.join(str(code.get('code')) for code in result.get('codes') or [] if code.get('code')) or 'planned services'
            authorization = (f'The {family} matrix group does not establish authorization for CPT {codes}; '
                             'check the member plan and current code rules with the payer.')
            return {kind: {'status': 'Verification required', 'reason': reason,
                           'sources': [_research_source(data, label), *_official_ppo_source(row, family)],
                           'verify_url': _portal(data, row, '', kind), 'contact': None}
                    for kind, reason in (('referral', referral), ('authorization', authorization))}
        row = payer_rows[0][1]
        label = f"{data['filename']} · Payer Plan Matrix, rows {', '.join(str(n) for n, _ in payer_rows)} (Excel research)"
        reason = ('Patient plan matches multiple matrix groups; confirm the exact product.' if matched else
                  'Exact patient plan/product is not identified in the matrix; confirm it with the payer.')
        return {kind: {'status': 'Verification required', 'reason': reason,
                       'sources': [_research_source(data, label)],
                       'verify_url': _portal(data, row, '', kind), 'contact': None} for kind in ('referral', 'authorization')}
    number, row = matched[0]
    plan = next((value for value in context.get('plans') or [] if _key(value) in _names(row["Specific Plan / Network Names on NMCT's Accepted List"])), '')
    source = _research_source(data, f"{data['filename']} · Payer Plan Matrix, row {number} · {plan} (Excel research)")
    gaps = _patient_evidence(result, {**context, 'payer': row['Payer']}, data['steps'])
    uncertain = any(code.get('needs_confirmation') or code.get('source') == 'conditional office E/M alternative'
                    for code in result.get('codes') or [])
    rental = row['Payer'] in {'HealthSmart', 'MultiPlan / PHCS (Claritev)', 'Blue Cross Blue Shield (out-of-state BlueCard)'}
    referral = str(row['Referral Required Before NMCT Visit'] or '').strip().casefold()
    if referral.startswith('yes for hmo'):
        referral = 'yes' if 'hmo' in _key(plan) else 'no' if any(term in _key(plan) for term in ('ppo', 'pffs')) else 'varies'
    referral_status = 'Required' if referral == 'yes' else 'Not required' if referral == 'no' else 'Verification required'
    conditional_hmo = row['Payer'] == 'Blue Cross Blue Shield of Texas' and _key(plan) in {'blueessentials', 'blueadvantageplus'}
    pending_contract = 'pending' in str(row['Plan Group (shared rule set)']).casefold()
    if rental or gaps or uncertain or conditional_hmo or not result.get('codes') or pending_contract and referral == 'no':
        referral_status = 'Verification required'
    detail = str(row['Referral Rule Detail (which CPT/services trigger it)'] or '').strip()
    detail_key = detail.casefold()
    contradictory_referral = (row['Payer'] != 'Humana Medicare (Pending)' and
        (referral == 'yes' and any(term in detail_key for term in ('no referral', 'without a referral')) or
         referral == 'no' and re.search(r'(?:requires?|must submit).{0,35}referral', detail_key)))
    if rental:
        referral_reason = 'The underlying payer or employer plan, not this rental network, controls referrals.'
    elif contradictory_referral:
        referral_status = 'Verification required'
        referral_reason = 'The matrix referral answer conflicts with its explanation; confirm with the payer.'
    elif conditional_hmo:
        referral_reason = 'This HMO usually needs a PCP referral, but its same-group or self-directed exception needs patient-specific review.'
    elif pending_contract and referral == 'no':
        referral_reason = 'This plan type usually lets members self-refer, but NMCT must confirm its contract is active and the patient’s network.'
    elif gaps:
        referral_reason = f"Matrix says {'referral required' if referral == 'yes' else 'no referral generally' if referral == 'no' else 'plan-dependent referral'}; verify {', '.join(gaps)} for this patient."
    elif uncertain:
        referral_reason = 'Confirm the planned service before applying this plan’s referral rule.'
    else:
        referral_reason = detail.split(';')[0][:160] or 'Confirm this plan’s referral rule.'
    referral_fields = data['steps']['Referral check']['Exact Fields ClinicForce Must Pull'].casefold()
    if referral_status == 'Required' and all(field in referral_fields for field in ('referring pcp', 'number of visits', 'start and end date')):
        referral_reason += ' Referral on file, PCP, visit count and dates still need confirmation.'
    if row['Payer'] == 'Humana Medicare (Pending)':
        referral_reason += ' Confirm NMCT contract effective date separately.'
    codes = {str(code.get('code') or '').upper() for code in result.get('codes') or [] if CODE.fullmatch(str(code.get('code') or '').upper())}
    trigger_text = str(row['Prior Authorization Needed For'] or '')
    explicit_codes = set(CODE.findall(trigger_text.upper()))
    triggered = codes & explicit_codes
    emg_codes = codes & (cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS) if 'EMG/NCS' in trigger_text else set()
    auth_status = 'Verification required'
    if triggered and not (rental or gaps or uncertain):
        # Research names these CPTs directly; approval remains a separate payer check.
        auth_status = 'Required' if row['Payer'] in {'Aetna', 'Blue Cross Blue Shield of Texas'} else 'Verification required'
    if rental:
        auth_reason = 'The underlying payer or employer plan controls authorization; check its ID card and plan document.'
    elif not codes or uncertain:
        auth_reason = 'Confirm the planned CPT codes before checking authorization.'
    elif gaps:
        auth_reason = f"Verify {', '.join(gaps)} before applying CPT-specific authorization research."
    elif triggered:
        auth_reason = f"Matrix flags CPT {', '.join(sorted(triggered))} for authorization review."
    elif emg_codes:
        auth_reason = f"Matrix flags EMG/NCS code {', '.join(sorted(emg_codes))} for a current payer lookup; the exact requirement is unconfirmed."
    else:
        auth_reason = 'Matrix does not settle authorization for every planned CPT; check the payer’s current code lookup.'
    auth_fields = data['steps']['Prior authorization check']['Exact Fields ClinicForce Must Pull'].casefold()
    claim_fields = data['steps']['Claim-ready confirmation']['Exact Fields ClinicForce Must Pull'].casefold()
    if triggered and all(field in auth_fields for field in ('pa status', 'tracking number', 'approved unit', 'expiration date')):
        auth_reason += ' Approval status, number, units and expiry need a separate check.'
    if triggered and 'cpt codes actually performed' in claim_fields:
        auth_reason += ' Match final billed CPTs before claim submission.'
    reference = _portal_reference(data, row, _key(plan))
    contact_source = (_research_source(data, f"{data['filename']} · Portal & Contact Reference, row {reference['_row_number']}")
                      if reference.get('_row_number') else None)
    def evidence(kind: str, status: str, reason: str) -> dict:
        contact = str(reference.get('Precert / PA Phone' if kind == 'authorization' else 'Provider Phone') or '').strip()
        if contact.casefold().startswith('same number'):
            contact = f"{reference.get('Provider Phone') or ''} ({contact})".strip()
        return {'status': status, 'reason': reason,
                'sources': [source, *([contact_source] if contact and contact_source else []),
                            *_official_ppo_source(row, 'PPO' if 'ppo' in _key(plan) or 'bluechoice' in _key(plan) else None)],
                'verify_url': _portal(data, row, _key(plan), kind),
                'verification': (str(row.get('Prior Auth Source / Submission Channel') or '').strip()
                                 if kind == 'authorization' else
                                 str(data['steps']['Referral check'].get('Where It Comes From') or '').strip()),
                'contact': contact or None}
    return {
        'referral': evidence('referral', referral_status, referral_reason),
        'authorization': evidence('authorization', auth_status, auth_reason),
    }
