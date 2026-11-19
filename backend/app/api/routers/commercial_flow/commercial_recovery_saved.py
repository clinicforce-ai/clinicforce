"""Read and revalidate an already saved Cigna member-specific eligibility response."""
from __future__ import annotations


def cigna_response_route(request_payer_id: str, response: dict) -> bool:
    """Stedi may return canonical 62308 for an HGJLR Cigna inquiry."""
    aliases = {'HGJLR', '62308'}
    payer = response.get('payer') or {}
    name = payer.get('name') or {}
    name = name.get('organization') if isinstance(name, dict) else name
    returned = response.get('tradingPartnerServiceId') or response.get('payerId')
    return request_payer_id in aliases and returned in aliases and str(name).upper() in {'CHLIC', 'CIGNA'}


async def saved_cigna_followup(api, case: dict, provider: dict) -> tuple[dict, dict] | None:
    """Return a patient-bound 271 and coverage, without submitting a payer request."""
    context, recovery = case['previsit_context'], case.get('previsit_discovery') or {}
    inputs, source = context['inputs'], context['source']
    attempts = recovery.get('eligibility_attempts') or []
    if (recovery.get('request_state') != 'complete' or recovery.get('outcome') != 'review_needed'
            or recovery.get('reason_code') != 'returned_identity_or_coverage_mismatch'
            or recovery.get('identity_pathway') != 'cigna_member_followup'
            or recovery.get('identity_binding') != api._recovery_binding(inputs, source)
            or context.get('payer_id') not in {'HGJLR', '62308'}
            or source.get('active_coverages') != 1 or str(source.get('payer') or '').lower() != 'cigna'
            or not recovery.get('selected_candidate_id') or len(attempts) != 1
            or not recovery.get('raw_object_key') or not recovery.get('eligibility_object_key')
            or not recovery.get('eligibility_sha256') or not attempts[0].get('request_object_key')):
        return None
    try:
        raw = await api.run_in_threadpool(api.object_store.get_object_bytes, recovery['eligibility_object_key'])
        if api.hashlib.sha256(raw).hexdigest() != recovery['eligibility_sha256']:
            return None
        verified = api.json.loads(raw)
        request = api.json.loads(await api.run_in_threadpool(
            api.object_store.get_object_bytes, attempts[0]['request_object_key']))
        discovery = api.json.loads(await api.run_in_threadpool(
            api.object_store.get_object_bytes, recovery['raw_object_key']))
        if (not all(isinstance(row, dict) for row in (verified, request, discovery))
                or discovery.get('status') != 'COMPLETE' or discovery.get('errors')
                or api.hashlib.sha256(api.json.dumps(request, sort_keys=True).encode()).hexdigest()
                    != attempts[0].get('fingerprint')
                or not cigna_response_route(request.get('payerId'), verified)
                or request.get('payerId') != context['payer_id']
                or (request.get('provider') or {}).get('npi') not in {
                    provider.get('rendering_npi'), provider.get('billing_npi')}
                or ((request.get('encounter') or {}).get('dates') or {}).get('service', {}).get('start') != inputs['service_date']
                or not set(api._previsit_service_types(inputs)) <= {
                    row.get('value') for row in (request.get('encounter') or {}).get('services') or [] if isinstance(row, dict)}
                or api.commercial.eligibility_errors(verified)):
            return None
        candidates = api.stedi_discovery.candidates(discovery, inputs, source)
        items = discovery.get('items') or []
        if api.stedi_discovery.active_policy_count(candidates, items, api._previsit_service_types(inputs)) != 1:
            return None
        selected = next((row for row in candidates if row['id'] == recovery['selected_candidate_id']
                         and row.get('active_medical')), None)
        if selected is None:
            return None
        item = items[selected['index']]
        returned = api.stedi_discovery.candidate_identity(verified, inputs)
        request_subscriber = request.get('subscriber') or {}
        request_name = (request_subscriber.get('name') or {}).get('person') or {}
        if (not returned or not api.stedi_discovery.cigna_member_change_inquiry(item, inputs, source)
                or not api.stedi_discovery.payer_followup_confirms(item, verified, inputs, source)
                or api.stedi_discovery.normalized(request_subscriber.get('memberId')) != api.stedi_discovery.normalized(returned['member_id'])
                or api.stedi_discovery.normalized(request_name.get('firstName')) != api.stedi_discovery.normalized(returned['subscriber_first'])
                or api.stedi_discovery.normalized(request_name.get('lastName')) != api.stedi_discovery.normalized(returned['subscriber_last'])
                or api.insurance_identity.birth_date(request_subscriber.get('dateOfBirth')) != api.insurance_identity.birth_date(returned['subscriber_birth_date'])
                or not api.stedi_discovery.active_medical(verified, inputs, api._previsit_service_types(inputs)[0])):
            return None
        return verified, api._discovered_coverage(inputs, source, verified)
    except Exception:
        return None
