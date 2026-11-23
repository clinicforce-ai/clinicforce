"""Bind a dated payer Part B report to the selected previsit patient."""

from app.insurance import identity
from app.insurance import payer_fees
from app.insurance.identity import patient_plan_applies
from app.insurance.stedi_benefits.payer_order import reported_medicare_part_b_secondary, reported_secondary_payer


def bind(payload: dict, coverage: dict, service_date: str, benefits: dict) -> dict:
    reported = reported_medicare_part_b_secondary(payload, service_date) if payload else None
    # This exception binds only the matched dependent; positive collection
    # remains blocked by the separate multi-policy collection gate.
    comparison_coverage = ({**coverage, 'active_coverages': 1}
        if reported and coverage.get('active_coverages', 0) > 1 and coverage.get('coverage_id')
        else coverage)
    comparison = identity.compare(payload, comparison_coverage)
    benefits['identity'] = comparison
    if identity.patient_benefits_bound(comparison):
        if named := reported_secondary_payer(payload, service_date):
            benefits['reported_secondary_payer'] = named
        if reported:
            benefits['medicare_part_b_secondary'] = reported
    return comparison


def clear_commercial_network(result: dict, benefits: dict) -> None:
    """A missing commercial contract does not create a Medicare network tier."""
    if benefits.get('network_source') != 'clinic_no_insurer_contract':
        return
    benefits.update(network_status='unknown', network_source=None,
                    network_evidence_ids=[], network_provider_npi=None)
    if (result.get('network_evidence') or {}).get('source') == 'clinic_no_insurer_contract':
        result.pop('network_evidence', None)


def price_original_medicare(result: dict, practice_id: str, inputs: dict, source: dict,
                            payer_id: str, benefits: dict, payload: dict | None,
                            review: dict | None, payer_name: str) -> None:
    from app.api.routers import commercial_estimator as api
    clear_commercial_network(result, benefits)
    result.pop('temporary_rate_reference', None)
    result['price_range'], issues = api._previsit_range(
        practice_id, inputs, source, payer_id, result['codes'], benefits,
        payload=payload, review=review, payer_route_id=payer_id, payer_name=payer_name)
    result['issues'].extend(issues)


def refresh_saved_original_medicare(result: dict, case: dict, practice_id: str,
                                    route_payer: dict | None, reprice) -> bool:
    """Rebuild a saved Medicare projection from intact payer evidence and current fees."""
    if (result.get('benefits') or {}).get('original_medicare') is not True:
        return False
    from app.api.routers import commercial_estimator as api
    context = case.get('previsit_context') or {}
    if (case.get('previsit', {}).get('request_state') != 'complete'
            or not case.get('previsit_raw_object_key') or not case.get('previsit_raw_sha256')):
        result['benefits']['historical_only'] = True
        return True
    try:
        raw = api.object_store.get_object_bytes(case['previsit_raw_object_key'])
        if api.hashlib.sha256(raw).hexdigest() != case['previsit_raw_sha256']:
            raise ValueError('Saved payer checksum mismatch')
        fresh = api._case_benefits(api.json.loads(raw), context.get('inputs') or {},
                                   context.get('source') or {}, case)
        if fresh.get('original_medicare') is not True:
            raise ValueError('Saved payer route changed')
    except Exception:
        result['benefits']['historical_only'] = True
        return True
    result['benefits'] = fresh
    result['identity'] = fresh.get('identity') or result.get('identity')
    reprice(result, case, practice_id, route_payer)
    return True


def restore_saved_humana_hmo_product(result: dict, case: dict) -> None:
    """Recover an older saved Medicare HMO product from its intact payer response."""
    from app.api.routers import commercial_estimator as api
    plan = ((result.get('plan_details') or {}).get('stedi') or {})
    benefits = result.get('benefits') or {}
    service_date = str(((case.get('previsit_context') or {}).get('inputs') or {}).get('service_date') or '')
    if (payer_fees.payer_key(str(result.get('payer') or '')) == 'humana'
            and plan.get('names') and not plan.get('medicare_hmo_evidence')
            and benefits.get('active') is True
            and patient_plan_applies(benefits, result.get('identity') or benefits.get('identity') or {}, service_date)
            and case.get('previsit_raw_object_key') and case.get('previsit_raw_sha256')):
        try:
            raw = api.object_store.get_object_bytes(case['previsit_raw_object_key'])
            if api.hashlib.sha256(raw).hexdigest() == case['previsit_raw_sha256']:
                current = api.commercial.plan_details(api.json.loads(raw))
                if current.get('names') == plan['names'] and current.get('medicare_hmo_evidence'):
                    plan['medicare_hmo_evidence'] = current['medicare_hmo_evidence']
                    if (benefits.get('plan_details') or {}).get('names') == plan['names']:
                        benefits['plan_details']['medicare_hmo_evidence'] = current['medicare_hmo_evidence']
        except Exception:
            pass  # Retain the saved estimate when protected payer evidence is unavailable.
