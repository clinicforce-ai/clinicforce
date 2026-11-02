"""A saved payer check can supply figures only as a bounded reference."""

from copy import deepcopy

from app.api.routers import commercial_estimator  # noqa: F401 - initialize route imports
from app.api.routers.commercial_flow.commercial_previsit_reference import _same_person_for_reference
from app.api.routers.commercial_flow import commercial_previsit_status as status
from app.insurance import network_participation


def test_reference_identity_allows_one_payer_spelling_correction():
    coverage = {'patient_first': 'Boonie', 'patient_last': 'Mccann',
                'patient_birth_date': '1947-10-11'}
    request = {'subscriber': {'memberId': 'SYNTHETIC1234', 'dateOfBirth': '1947-10-11',
        'name': {'person': {'firstName': 'Boonie', 'lastName': 'Mccann'}}}}
    response = {'subscriber': {'memberId': 'SYNTHETIC1234', 'dateOfBirth': '1947-10-11',
        'name': {'person': {'firstName': 'Bonnie', 'lastName': 'Mccann'}}}}
    assert _same_person_for_reference(request, response, coverage)

    wrong_member = deepcopy(response)
    wrong_member['subscriber']['memberId'] = 'OTHER'
    assert not _same_person_for_reference(request, wrong_member, coverage)

    wrong_patient = deepcopy(response)
    wrong_patient['subscriber']['name']['person']['firstName'] = 'Bobby'
    assert not _same_person_for_reference(request, wrong_patient, coverage)

    wrong_birth = deepcopy(response)
    wrong_birth['subscriber']['dateOfBirth'] = '1948-10-11'
    assert not _same_person_for_reference(request, wrong_birth, coverage)


def test_registered_clinic_saved_reference_does_not_infer_network(monkeypatch):
    monkeypatch.setattr(status._api.staff_store, 'list_library', lambda *_: [])
    case = {
        'previsit_context': {
            'context_token': 'synthetic-token',
            'inputs': {'service_date': '2026-10-27'},
            'source': {},
            'payer_id': '',
            'result': {
                'service_date': '2026-10-27', 'payer': 'Aetna',
                'benefits': {'active': None, 'network_status': 'unknown'},
                'payment_mode': 'cash', 'cash_fallback': {},
                'price_range': {'lines': []}, 'codes': [],
            },
        },
        'previsit_reference': {
            'context_token': 'synthetic-token', 'service_date': '2026-10-27',
            'benefits': {'active': True, 'service_date_matches': False,
                         'network_status': 'unknown', 'network_source': None},
        },
    }
    monkeypatch.setattr(status._api.commercial_store, 'list_connections', lambda *_: [])
    monkeypatch.setattr(status, '_recheck_saved_clinic_pricing', lambda *_: None)
    monkeypatch.setattr(status, '_recheck_saved_payer_allowance', lambda *_: None)
    monkeypatch.setattr(status, 'refresh_saved_requirements', lambda *_: None)
    monkeypatch.setattr(status._api.collection_guidance, 'build', lambda *_: {'amount_cents': None})
    monkeypatch.setattr(status._api, '_insurance_diagnosis', lambda *_: {})
    monkeypatch.setattr(status._api.encounter_summary, 'build', lambda *_: {})
    monkeypatch.setattr(status._api.amount_explanation, 'build', lambda *_: {})
    monkeypatch.setattr(status.evidence_resolution, 'build', lambda *_: {})
    result = status._previsit_result(case, network_participation.NMCT_REGISTERED_PRACTICE_ID)
    assert result['reference_benefits']['network_status'] == 'unknown'
    assert result['reference_benefits'].get('network_source') is None
    assert result['reference_benefits']['service_date_matches'] is False
    assert case['previsit_reference']['benefits']['network_status'] == 'unknown'
