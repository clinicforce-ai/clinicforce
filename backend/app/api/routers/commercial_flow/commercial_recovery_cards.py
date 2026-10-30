"""Commercial recovery cards workflow."""
from __future__ import annotations

from app.api.routers import commercial_estimator as _api

def _compare_card_hints(card: dict, inputs: dict, source: dict) -> dict:
    """Fill absent eCW inquiry fields only when OCR names the linked patient/policy."""
    status = card['status']
    result = {'status': status, 'source': card.get('source'),
              'failure_reason': card.get('failure_reason'), 'fields_added': [], 'conflicts': [],
              'differences': [], 'alternative_supported': False}
    if status != 'read':
        return result
    fields = card['fields']
    if not fields.get('member_id'):
        result['status'] = 'unverified'
        return result
    for field in ('member_id', 'group_number'):
        if (inputs.get(field) and fields.get(field)
                and _api.stedi_discovery.normalized(inputs[field]) != _api.stedi_discovery.normalized(fields[field])):
            result['conflicts'].append(field)
    payer_difference = bool(source.get('payer') and fields.get('payer') and
        _api.stedi_discovery.normalized(source['payer']) != _api.stedi_discovery.normalized(fields['payer']))
    if payer_difference:
        result['differences'].append('payer_name')
        result['payer_comparison'] = {'ecw': source['payer'], 'card': fields['payer']}
    candidate = _api.stedi_discovery.card_candidate({**fields, 'payer': fields.get('payer') or source.get('payer')}, inputs, source)
    patient = inputs.get('patient') or {}
    subscriber = inputs.get('subscriber') or {}
    name_matches = any(_api.stedi_discovery.card_name_matches(fields.get('member_name'), person.get(first), person.get(last))
                       for person, first, last in ((patient, 'patient_first_name', 'patient_last_name'),
                                                   (subscriber, 'firstName', 'lastName')))
    expected_dobs = {_api.insurance_identity.birth_date(value) for value in
                     (patient.get('patient_birth_date'), subscriber.get('birthDate')) if value}
    card_dob = _api.insurance_identity.birth_date(fields['birth_date']) if fields.get('birth_date') else None
    dob_matches = bool(card_dob and card_dob in expected_dobs)
    group_matches = bool(inputs.get('group_number') and fields.get('group_number') and
                         _api.stedi_discovery.normalized(inputs['group_number']) == _api.stedi_discovery.normalized(fields['group_number']))
    member_matches = bool(inputs.get('member_id') and
        _api.stedi_discovery.normalized(inputs['member_id']) == _api.stedi_discovery.normalized(fields['member_id']))
    patient_bound = bool(card.get('count') in {1, 2} and source.get('_patient_id') and
                         source.get('coverage_id') and source.get('active_coverages') == 1)
    printed_copay = _api.re.fullmatch(r'\$\s*(\d{1,4})(?:\.(\d{2}))?', str(fields.get('specialist_copay') or ''))
    if (patient_bound and member_matches and name_matches and (not card_dob or dob_matches)
            and not result['conflicts'] and printed_copay):
        result['office_copay_cents'] = int(printed_copay[1]) * 100 + int(printed_copay[2] or 0)
    result['alternative_supported'] = bool(patient_bound and candidate and name_matches and
        (not card_dob or dob_matches) and (
            bool(result['conflicts']) and dob_matches and group_matches or
            payer_difference and member_matches and (group_matches or dob_matches)))
    if result['alternative_supported']:
        result['alternative_fingerprint'] = _api.hashlib.sha256(_api.json.dumps(fields, sort_keys=True).encode()).hexdigest()
    if result['conflicts']:
        result['status'] = 'conflict'
        return result
    if not candidate:
        result['status'] = 'unverified'
        result['differences'].append('member_name_or_birth_date')
        return result
    if payer_difference and result['alternative_supported']:
        result['status'] = 'conflict'
        return result
    for field in ('member_id', 'group_number'):
        if not inputs.get(field) and fields.get(field):
            inputs[field] = fields[field]
            result['fields_added'].append(field)
    if not source.get('payer') and fields.get('payer'):
        source['payer'] = fields['payer']
        source['payer_source'] = 'card_ocr'
        result['fields_added'].append('payer')
    if 'member_id' in result['fields_added']:
        source['subscriber_evidence'] = {**(source.get('subscriber_evidence') or {}), 'member_id_source': 'card_ocr'}
    if fields.get('birth_date') and not (inputs.get('subscriber') or {}).get('birthDate'):
        subscriber = inputs.get('subscriber') or {}
        if _api.stedi_discovery.card_name_matches(fields.get('member_name'), subscriber.get('firstName'), subscriber.get('lastName')):
            subscriber['birthDate'] = fields['birth_date']
            inputs['subscriber'] = subscriber
            result['fields_added'].append('subscriber_birth_date')
    result['status'] = 'filled_missing' if result['fields_added'] else 'matched'
    if fields.get('cms_plan_id'):
        result['cms_plan_id'] = fields['cms_plan_id']
        result['plan_name'] = fields.get('plan_name')
    return result


async def _recover_card(case: dict, session: dict, provider: dict) -> bool:
    """Read patient-bound stored/eCW cards once; OCR is never benefit evidence."""
    context, recovery = case['previsit_context'], case['previsit_discovery']
    patient_id = context['source'].get('_patient_id')
    if recovery.get('card_status'):
        return False
    recovery['card_status'] = 'reading'
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'coverage-card-started'))
    _api._recovery_event(case, session, 'insurance_card', 'started')
    unavailable = False
    count = 0
    try:
        card = await _api._read_card_hints(session, patient_id)
        count = card.get('count', 0)
        recovery['card_status'] = card['status']
        unavailable = card['status'] == 'unavailable'
        if unavailable:
            recovery['card_failure_reason'] = card.get('failure_reason') or 'card_unavailable'
        if card['status'] == 'read':
            fields, evidence = card['fields'], card['evidence']
            if (recovery.get('card_conflict') and
                    _api.hashlib.sha256(_api.json.dumps(fields, sort_keys=True).encode()).hexdigest() != recovery.get('card_fingerprint')):
                recovery.update(card_status='changed', request_state='complete', outcome='review_needed',
                                reason_code='card_changed_before_check')
                return True
            _api._recovery_event(case, session, 'card_download', 'complete', count=count)
            _api._recovery_event(case, session, 'card_ocr', 'complete', count=len(fields))
            recovery['card_evidence'] = evidence
            item = _api.stedi_discovery.card_candidate(fields, context['inputs'], context['source'])
            if item:
                payload = {'items': [item]}
                key = await _api.run_in_threadpool(_api.object_store.put_staff_document, session['practice_id'], case['id'],
                    'card-coverage-hints.json', _api.json.dumps(payload).encode(), 'application/json')
                recovery.update(card_status='read', card_object_key=key,
                    candidates=_api.stedi_discovery.candidates(payload, context['inputs'], context['source']))
                recovery['card_candidates'] = recovery['candidates']
                if recovery['candidates'] and context['source'].get('active_coverages', 0) <= 1:
                    recovery.update(selected_candidate_id=recovery['candidates'][0]['id'], stage='eligibility',
                                    request_state='pending', evidence_source='insurance_card')
                    case.update(await _api.run_in_threadpool(_api._save, case, session, 'coverage-card-eligibility-pending'))
                    recovery = case['previsit_discovery']
                    await _api._recheck_discovery_candidate(case, session, provider, discovery_payload=payload, inquiry_only=True)
                    if recovery.get('card_conflict') or recovery.get('outcome') == 'recovered' or recovery.get('request_state') == 'indeterminate':
                        return True
            recovery['card_status'] = 'read_no_verified_policy'
    except (_api.ecw_backend.EcwBackendError, _api.httpx.HTTPError, TimeoutError, ValueError, _api.HTTPException, _api.golden.OCRServiceError) as exc:
        unavailable = True
        recovery['card_status'] = 'unavailable'
        recovery['card_failure_reason'] = 'card_read_timeout' if isinstance(exc, (TimeoutError, _api.httpx.TimeoutException)) else 'card_unavailable'
    if recovery.get('card_status') == 'reading':
        recovery['card_status'] = 'not_found'
    _api._recovery_event(case, session, 'insurance_card', 'unavailable' if unavailable else 'complete',
                    reason=recovery['card_status'], count=count)
    recovery.update(request_state='pending', stage='discovery', outcome=None, selection_error=None,
                    candidates=[], selected_candidate_id=None, evidence_source='stedi_discovery')
    case.update(await _api.run_in_threadpool(_api._save, case, session, 'coverage-card-finished'))
    return False


def _recovery_stopped(case: dict) -> bool:
    recovery = case['previsit_discovery']
    return recovery.get('outcome') == 'recovered' or recovery.get('request_state') == 'indeterminate'


def _request_ip_chain(request: _api.Request) -> str | None:
    """Preserve upstream HETS traceability privately, never in public results."""
    forwarded = request.headers.get('x-forwarded-for', '')
    if len(forwarded) > 2048:
        return None
    try:
        hops = [str(_api.ipaddress.ip_address(part.strip())) for part in forwarded.split(',') if part.strip()]
        if request.client:
            peer = str(_api.ipaddress.ip_address(request.client.host))
            if peer not in hops:
                hops.append(peer)
        return ', '.join(hops) if 0 < len(hops) <= 20 else None
    except ValueError:
        return None


async def _recover_saved_policy(case: dict, session: dict, provider: dict, *, allow_query: bool = True) -> None:
    """Reuse protected evidence only from explicitly linked patient records."""
    context, recovery = case['previsit_context'], case['previsit_discovery']
    if recovery.get('steps', {}).get('saved_policy') or context['source'].get('active_coverages', 0) > 1:
        return
    recovery.setdefault('steps', {})['saved_policy'] = 'not_found'
    rows = await _api.run_in_threadpool(_api.store.patient_checks, session['practice_id'], session['patient_id'])
    policies = {}
    for row in rows[:10]:
        old = row['case']
        if old.get('id') == case['id'] or (old.get('previsit') or {}).get('request_state') != 'complete':
            continue
        key = old.get('previsit_raw_object_key')
        if not key:
            continue
        try:
            item = _api.json.loads(await _api.run_in_threadpool(_api.object_store.get_object_bytes, key))
            identity = _api.stedi_discovery.candidate_identity(item, context['inputs'])
            payer = item.get('payer') or {}
            if not identity or not (payer.get('name') or payer.get('lastName')):
                continue
            if context['inputs'].get('member_id') and _api.stedi_discovery.normalized(identity['member_id']) != _api.stedi_discovery.normalized(context['inputs']['member_id']):
                continue
            # Retain distinct policies; never choose the newest policy as primary.
            token = (str(payer.get('payorIdentification') or payer.get('name') or payer.get('lastName')),
                     identity['member_id'])
            policies.setdefault(token, (old, item))
        except (ValueError, TypeError, KeyError):
            continue
    if len(policies) != 1:
        recovery['steps']['saved_policy'] = 'multiple_policies' if policies else 'not_found'
        _api._recovery_event(case, session, 'saved_policy', 'complete', count=len(policies))
        return
    old, item = next(iter(policies.values()))
    previous = old.get('previsit_context') or {}
    old_source = previous.get('source') or {}
    expected_npi = provider.get('rendering_npi') or provider.get('billing_npi')
    previous_npi = (old_source.get('rendering_provider') or {}).get('npi') or (old_source.get('billing_provider') or {}).get('npi')
    try:
        fresh = _api.datetime.fromisoformat(old['previsit']['checked_at']) + _api.timedelta(hours=24) > _api.datetime.now(_api.timezone.utc)
    except (KeyError, TypeError, ValueError):
        fresh = False
    reusable = (fresh and expected_npi == previous_npi and previous.get('inputs', {}).get('service_date') == context['inputs']['service_date']
        and old['previsit'].get('requested_service_types') == _api._previsit_service_types(context['inputs'])
        and not _api.visit_estimate.reconcile_eligibility(context['source'].get('saved_eligibility') or {},
            old['previsit'].get('benefits') or {}, old['previsit'].get('checked_at'))['requires_review']
        and previous.get('payer_id') and _api.stedi_discovery.automatic_match(item, context['inputs'], context['source']))
    recovery.update(evidence_source='saved_payer_response', route_source='returned_policy')
    if reusable:
        recovery['eligibility_object_key'] = old['previsit_raw_object_key']
        await _api._accept_recovered_coverage(case, session, item,
            {'stediId': previous['payer_id'], 'displayName': (item.get('payer') or {}).get('name')},
            _api._discovered_coverage(context['inputs'], context['source'], item))
        recovery['steps']['saved_policy'] = 'reused'
    elif allow_query:
        recovery['candidates'] = _api.stedi_discovery.candidates({'items': [item]}, context['inputs'])
        if recovery['candidates']:
            recovery['selected_candidate_id'] = recovery['candidates'][0]['id']
            recovery['steps']['saved_policy'] = 'rechecked'
            await _api._recheck_discovery_candidate(case, session, provider, discovery_payload={'items': [item]})
    else:
        recovery['steps']['saved_policy'] = 'needs_current_eligibility'
    _api._recovery_event(case, session, 'saved_policy', recovery['steps']['saved_policy'], count=1)
