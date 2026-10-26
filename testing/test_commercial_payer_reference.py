"""A saved payer check can supply figures only as a bounded reference."""

from copy import deepcopy

from app.api.routers import commercial_estimator  # noqa: F401 - initialize route imports
from app.api.routers.commercial_flow.commercial_previsit_reference import _same_person_for_reference


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
