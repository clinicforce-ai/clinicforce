"""Build payer benefit evidence for one saved commercial visit."""
from app.api.routers import commercial_estimator as _api
from app.api.routers.commercial_flow.commercial_previsit_service_scope import retain_incomplete_service_evidence


def case_benefits(payload: dict, inputs: dict, source: dict, case: dict) -> dict:
    coverage = _api._coverage_snapshot(inputs, source)
    recovery = case.get('previsit_discovery') or {}
    recovered = (recovery.get('outcome') == 'recovered' and recovery.get('identity_binding') == _api._recovery_binding(inputs, source)
                 and recovery.get('verified_coverage'))
    if recovered:
        coverage.update(recovery['verified_coverage'])
    requested = ((case.get('previsit') or {}).get('requested_service_types')
                 if payload.get('_service_payloads') else None) or _api._previsit_service_types(inputs)
    benefits = _api._previsit_benefits(payload, coverage, inputs['service_date'], requested, inputs=inputs, source=source)
    if recovered:
        benefits['normalization_notes'].append('Coverage was selected from Stedi discovery for this estimate. eCW was not changed.')
    return retain_incomplete_service_evidence(benefits, case)
