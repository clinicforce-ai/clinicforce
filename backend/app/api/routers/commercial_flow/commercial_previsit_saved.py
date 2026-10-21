"""Reuse sufficient saved eCW eligibility before making a paid payer request."""
from __future__ import annotations

from types import ModuleType


async def reuse_saved_eligibility(api: ModuleType, result: dict, session: dict,
                                  inputs: dict, source: dict, coverage: dict) -> dict | None:
    """Return a completed previsit result only when saved evidence supports it."""
    # Evaluate saved eCW evidence before credentials, payer directory or any paid request.
    ecw_payload = api.ecw_benefits.payload(source['saved_eligibility'], source, inputs)
    if ecw_payload and api.object_store.enabled() and coverage['active_coverages'] == 1:
        connections = [row for row in api.commercial_store.list_connections(session['practice_id'])
                       if row.get('status') == 'staff_guidance_enabled'
                       and (str(source.get('payer') or '').casefold() in
                            {str(name).casefold() for name in [row.get('display_name'), *(row.get('aliases') or [])]}
                            or row.get('stedi_payer_id') in source.get('payer_identifiers', []))]
        ecw_payer_id = connections[0]['stedi_payer_id'] if len(connections) == 1 else ''
        binding = api._visit_context_token(inputs, source)
        ecw_case = await api.run_in_threadpool(api.store.create_case, session['patient_id'], '', binding,
            {'source_mode': 'commercial_previsit', 'previsit': {'request_state': 'not_requested'}},
            expected_intake_updated_at=session['intake']['updated_at'],
            workflow_slot='commercial-ecw:' + binding, **api._scope(session))
        candidate = api.deepcopy(result)
        candidate.update(check_id=ecw_case['id'], checked_at=source['saved_eligibility']['checked_at'],
            benefits=api._previsit_benefits(ecw_payload, coverage, inputs['service_date'], api._previsit_service_types(inputs)[0], inputs=inputs, source=source))
        await api.run_in_threadpool(api._finish_previsit, candidate, ecw_case, inputs, source, ecw_payload, session, ecw_payer_id)
        guidance = candidate['collection_guidance']
        # Never skip a check just because an unsupported result has a numeric fallback.
        preview_lines = (candidate.get('price_range') or {}).get('lines') or []
        common_office_copay = (candidate.get('price_range', {}).get('mode') == 'alternatives'
            and bool(preview_lines) and all(line.get('code') in
                {str(n) for n in (*range(99202, 99206), *range(99212, 99216))} for line in preview_lines)
            and all((line.get('benefits') or {}).get('copay_cents') is not None for line in preview_lines)
            and len({line['benefits']['copay_cents'] for line in preview_lines}) == 1)
        reusable_preview = (candidate.get('service_selection_status') == 'preview'
            and (candidate.get('price_range', {}).get('complete')
                 or common_office_copay)
            and candidate['benefits'].get('active') is True
            and candidate['benefits'].get('service_date_matches') is not False
            and not candidate['benefits'].get('has_secondary')
            and api.insurance_identity.patient_benefits_bound(candidate.get('identity'))
            and candidate['benefits'].get('network_status') == 'in_network')
        if ((guidance['basis'] in {'office_copay', 'anticipated_services', 'patient_obligations'} or reusable_preview)
                and (guidance['basis'] != 'office_copay' or len(candidate['codes']) == 1
                     or candidate.get('price_range', {}).get('mode') == 'alternatives')
                and not candidate.get('price_range', {}).get('service_issues')
                and all((line.get('benefits') or {}).get('copay_cents') is not None
                        or line.get('calculation') is not None for line in candidate['price_range']['lines'])
                and not any((line.get('benefits') or {}).get('source_errors') for line in candidate['price_range']['lines'])):
            raw_key = await api.run_in_threadpool(api.object_store.put_staff_document, session['practice_id'], ecw_case['id'],
                'previsit-ecw-eligibility.json', api.json.dumps(ecw_payload).encode(), 'application/json')
            ecw_case['previsit'] = {'request_state': 'complete', 'benefits': candidate['benefits'],
                'checked_at': candidate['checked_at'], 'benefit_source': 'eCW saved eligibility',
                'requested_service_types': api._previsit_service_types(inputs)}
            ecw_case['previsit_raw_object_key'] = raw_key
            candidate['ecw_saved_eligibility']['message'] = ('Current eCW benefits support a planning estimate. Clinician approval is still needed before collection.'
                if reusable_preview else 'Current eCW benefits support this advance estimate. No Stedi eligibility request was needed.')
            api.collection_event('eligibility_reused', practice_id=session['practice_id'], intake_id=(session.get('intake') or {}).get('id'),
                             case_id=ecw_case['id'], stage='eligibility', source='ecw_saved')
            return api._previsit_result(await api.run_in_threadpool(api._save, ecw_case, session, 'previsit-ecw-benefits-reused'), session['practice_id'])

    return None
