"""Referral and authorization status shown with collection guidance."""


def _cleared_with_source(item: dict) -> bool:
    return (item.get('required') == 'required' and item.get('cleared') is True
            and item.get('status') == 'valid'
            and bool(item.get('reference') and item.get('verified_at')))


def _cpt_rule_conflicts(result: dict, kind: str) -> list[str]:
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []
    lines = (result.get('price_range') or {}).get('lines') or []
    conflicts = []
    for line in lines:
        line_id, code = str(line.get('id') or ''), str(line.get('code') or '')
        exact = [row for row in procedures if line_id and row.get('id') == line_id]
        candidates = exact or [row for row in procedures if row.get('code') == code
                               and (not line_id or not row.get('id') or row.get('id') == code)]
        if len(candidates) != 1:
            continue
        decision = (candidates[0].get(kind) or {}).get('status')
        stated = ((line.get('benefits') or {}).get('requirements') or {}).get(kind, {}).get('required')
        if (decision == 'Required' and stated == 'not_required'
                or decision == 'Not required' and stated == 'required'):
            conflicts.append(code)
    return conflicts


def _cpt_approval_conflicts(result: dict) -> list[str]:
    """A saved unverified CPT decision cannot borrow a cleared line reference."""
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []
    conflicts = []
    for line in (result.get('price_range') or {}).get('lines') or []:
        line_id, code = str(line.get('id') or ''), str(line.get('code') or '')
        exact = [row for row in procedures if line_id and row.get('id') == line_id]
        candidates = exact or [row for row in procedures if row.get('code') == code
                               and (not line_id or not row.get('id') or row.get('id') == code)]
        if len(candidates) != 1:
            continue
        decision = candidates[0].get('authorization') or {}
        approval = ((line.get('benefits') or {}).get('requirements') or {}).get('authorization') or {}
        if (decision.get('status') == 'Required'
                and decision.get('approval_status') not in {None, 'Verified'}
                and _cleared_with_source(approval)):
            conflicts.append(code)
    return list(dict.fromkeys(conflicts))


def _unresolved(result: dict, kind: str, requirements: list[dict]) -> bool:
    """Only an explicit adverse payer decision prevents collection.

    Missing or pending verification remains visible in the CPT status, but it
    does not change the arithmetic or withhold the suggested visit amount.
    """
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []
    if any(str(item.get('status') or '').lower() in {'denied', 'expired'} for item in requirements):
        return True
    if kind == 'authorization' and any(
            str((row.get(kind) or {}).get('pa_approval') or '').upper() in {'DENIED', 'EXPIRED'}
            for row in procedures):
        return True
    documented = ((result.get('requirement_evidence') or {}).get(kind) or {})
    return kind == 'authorization' and str(documented.get('pa_approval') or '').upper() in {'DENIED', 'EXPIRED'}


def requirement_blocker(result: dict) -> tuple[str, str, str] | None:
    lines = (result.get('price_range') or {}).get('lines') or []
    required = [kind for kind in ('referral', 'authorization') if _unresolved(
        result, kind, [line.get('benefits', {}).get('requirements', {}).get(kind, {}) for line in lines])]
    if not required:
        if result.get('payment_mode') == 'cash' and (result.get('benefits') or {}).get('referral_signal') == 'required':
            return ('the payer reports a referral requirement for the planned specialist visit',
                    'referral_required', 'obtain the primary care referral for this specialist visit before the service')
        return None
    selected = list(dict.fromkeys(str(code) for code in
                                  (result.get('service_plan') or {}).get('selected_codes') or [] if code))
    if not selected and (result.get('price_range') or {}).get('mode') == 'services':
        selected = list(dict.fromkeys(str(line.get('code')) for line in lines if line.get('code')))
    procedures = (result.get('requirement_evidence') or {}).get('procedures') or []

    def service(kind: str, conflicting_codes: list[str] | None = None) -> str:
        specific = conflicting_codes or list(dict.fromkeys(str(row.get('code')) for row in procedures
                                       if str(row.get('code')) in selected
                                       and (row.get(kind) or {}).get('status') == 'Required'))
        if not specific:
            specific = list(dict.fromkeys(str(line.get('code')) for line in lines
                                               if line.get('code') and (line.get('benefits') or {}).get(
                                                   'requirements', {}).get(kind, {}).get('required') == 'required'))
        codes = specific or selected
        return ' for ' + ', '.join('CPT ' + code for code in codes) if codes else ' for the selected care'

    cpt_conflicts = {kind: _cpt_rule_conflicts(result, kind) for kind in required}
    conflicts = [kind for kind in required if cpt_conflicts[kind] or
                 ((result.get('requirement_evidence') or {}).get(kind) or {}).get('status') == 'Not required'
                 and any((line.get('benefits') or {}).get('requirements', {}).get(kind, {}).get('required') == 'required'
                         for line in lines)]
    if conflicts:
        labels = {'referral': 'referral', 'authorization': 'prior authorization'}
        affected = ' and '.join(labels[kind] + service(kind, cpt_conflicts[kind]) for kind in conflicts)
        code = ('referral_authorization_required' if len(conflicts) == 2 else
                f'{conflicts[0]}_required')
        payer = result.get('payer') or 'payer'
        cause = (f'the saved CPT rule and service line disagree about {affected}'
                 if any(cpt_conflicts.values()) else
                 f'the saved visit summary conflicts with the CPT-level {affected} decision')
        question = ' and '.join(labels[kind] + ' rule' + service(kind, cpt_conflicts[kind])
                                for kind in conflicts)
        return (cause,
                code, f'ask {payer} which {question} {"apply" if len(conflicts) > 1 else "applies"}, '
                      'correct the clinic evidence, and rerun the insured estimate')

    approval_conflicts = _cpt_approval_conflicts(result) if 'authorization' in required else []
    if approval_conflicts:
        affected = ', '.join('CPT ' + code for code in approval_conflicts)
        payer = result.get('payer') or 'the payer'
        return (f'the saved approval status for {affected} conflicts with its authorization reference',
                'authorization_required',
                f'check the authorization decision for {affected} with {payer}, correct the approval record, and rerun the insured estimate')

    if len(required) == 1:
        kind = required[0]
        if any((line.get('benefits') or {}).get('requirements', {}).get(kind, {}).get('required') == 'required'
               and (line.get('benefits') or {}).get('requirements', {}).get(kind, {}).get('cleared') is True
               and not _cleared_with_source((line.get('benefits') or {}).get('requirements', {}).get(kind, {}))
               for line in lines):
            label = 'referral' if kind == 'referral' else 'prior authorization'
            return (f'the saved {label} clearance lacks a valid reference or verification time' + service(kind),
                    f'{kind}_required',
                    f'check the {label} with the payer and record its reference and verification time' + service(kind))

    if len(required) == 2:
        if service('referral') == service('authorization'):
            return ('the required referral and prior authorization are not cleared' + service('referral'),
                    'referral_authorization_required',
                    'obtain the referral and payer authorization approval' + service('referral') + ' before the service')
        return ('the required referral' + service('referral') + ' and prior authorization' + service('authorization') + ' are not cleared',
                'referral_authorization_required',
                'obtain the referral' + service('referral') + ' and payer authorization approval' + service('authorization') + ' before the service')
    if required[0] == 'referral':
        return ('the required referral is not cleared' + service('referral'), 'referral_required',
                'obtain the referring clinician’s referral' + service('referral') + ' before the service')
    return ('the required prior authorization is not approved' + service('authorization'), 'authorization_required',
            'obtain payer authorization approval and record its reference' + service('authorization') + ' before the service')


def summarize_requirements(result: dict, lines: list[dict], guidance: dict, actions: list[str]) -> bool:
    adverse = False
    for kind in ('referral', 'authorization'):
        requirements = [line.get('benefits', {}).get('requirements', {}).get(kind, {}) for line in lines]
        guidance['requirement_sources'][kind] = (result.get('benefit_source') or 'Stedi') + ' / clinic evidence'
        if (((result.get('requirement_evidence') or {}).get(kind) or {}).get('status') == 'Not required'
                and any(item.get('required') == 'required' for item in requirements)):
            guidance['requirements'][kind] = 'Conflicting CPT rule · shown per CPT'
        elif _unresolved(result, kind, requirements):
            adverse = True
            guidance['requirements'][kind] = 'Denied or expired · shown per CPT'
            actions.append('Contact the payer about the denied or expired referral for this service.' if kind == 'referral' else
                           'Contact the payer about the denied or expired authorization for this service.')
        elif requirements and all(item.get('cleared') for item in requirements):
            guidance['requirements'][kind] = 'On file' if any(item.get('required') == 'required' for item in requirements) else 'Not required'
            if kind == 'authorization' and any('absent indicator' in line.get('benefits', {}).get('authorization_basis', '') for line in lines):
                guidance['requirements'][kind] = 'No requirement indicated'
                guidance['requirement_sources'][kind] = 'Stedi: no authorization indicator'
        elif (any(item.get('required') == 'required' for item in requirements)
              or any((row.get(kind) or {}).get('status') == 'Required'
                     for row in (result.get('requirement_evidence') or {}).get('procedures') or [])
              or ((result.get('requirement_evidence') or {}).get(kind) or {}).get('status') == 'Required'):
            guidance['requirements'][kind] = 'Required · status shown per CPT'
        else:
            explicit_unknown = kind == 'authorization' and any(line.get('benefits', {}).get('authorization_basis') == 'Payer indicator' for line in lines)
            guidance['requirements'][kind] = 'Unknown in payer response' if explicit_unknown else 'No requirement reported'
            guidance['requirement_sources'][kind] = 'Stedi' if explicit_unknown else 'eCW + Stedi'
            if kind == 'referral' and result.get('referral_evidence'):
                guidance['requirements'][kind] = 'Referral record on file'
                guidance['requirement_sources'][kind] = 'eCW; applicability to this service not established'
    return adverse
