"""Dated portal evidence must bind to one saved specialist visit."""
import asyncio
import fitz
import pytest
from io import BytesIO
from starlette.datastructures import Headers, UploadFile

from app.api.routers import commercial_estimator  # noqa: F401 - initialize route imports
from app.insurance.availity_visit_evidence import parse_visit_pdf


def _pdf(*, copay: int = 70, network: str = "participating", conflicting: bool = False) -> bytes:
    pages = [
        "Availity Essentials\nDate of Service Nov 3, 2026\nFeedback SAMPLE, JANE\n"
        "Member Status Date of Birth\nActive Coverage Aug 20, 1947\n"
        "Group Number: 000003-TX\nPlan Number: 000003-TX000047\n"
        "Requesting Provider\nName: DOCTOR, TEST\nNPI: 1234567890\n"
        "Plan / Product: Aetna Medicare Signature (PPO)",
        "Out Of Pocket\n$6,750 / Calendar Year(s) $3,920.50 Remaining In Network\n"
        f"Our records indicate the provider ID you entered is {network} in this patient's network.",
        "In Network Place of Service: Office Coverage Level: Individual\n"
        f"— ${copay} — —\nGYN Visit\nSpecialist Visit or Evaluation\nCopay Included In CATAS OOP\n"
        "In Network Place of Service: Office Coverage Level: Individual\n"
        "GYN Visit\n0% — — —\nSpecialist Visit or Evaluation\n"
        "COINS APPLIES TO CATAS OUT OF POCKET",
    ]
    if conflicting:
        pages[2] += ("\nIn Network Place of Service: Office Coverage Level: Individual\n"
                     "— $80 — —\nSpecialist Visit or Evaluation\nCopay Included In CATAS OOP")
    doc = fitz.open()
    for content in pages:
        page = doc.new_page()
        page.insert_text((50, 50), content)
    raw = doc.tobytes()
    doc.close()
    return raw


def _parse(raw: bytes, **changes):
    inputs = dict(first='Jane', last='Sample', birth='1947-08-20',
                  service_date='2026-11-03', rendering_npi='1234567890')
    inputs.update(changes)
    return parse_visit_pdf(raw, **inputs)


def test_visit_date_provider_and_specialist_terms():
    result = _parse(_pdf())
    assert result['copay_cents'] == 7000
    assert result['coinsurance_percent'] == 0
    assert result['oop_remaining_cents'] == 392050
    assert result['network_status'] == 'in_network'
    assert 'Jane' not in str(result) and '1234567890' not in str(result)
    for changed in ({'first': 'Janet'}, {'birth': '1947-08-21'},
                    {'service_date': '2026-11-04'}, {'rendering_npi': '9999999999'}):
        with pytest.raises(ValueError, match='differs'):
            _parse(_pdf(), **changed)


def test_no_participation_or_conflicting_specialist_copay_is_rejected():
    with pytest.raises(ValueError, match='participates'):
        _parse(_pdf(network='not participating'))
    with pytest.raises(ValueError, match='missing or conflicting'):
        _parse(_pdf(conflicting=True))


def test_dated_availity_reference_does_not_supply_an_aetna_allowed_fee():
    from app.api.routers.commercial_flow import commercial_previsit_rate_reference as rates

    result = {
        'payer': 'Aetna',
        'payment_mode': 'insurance',
        'benefits': {'active': False},
        'availity_visit_reference': _parse(_pdf()),
        'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}],
    }
    rates.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-03', 'place_of_service': '11'}, {}, None)
    assert 'temporary_rate_reference' not in result
    assert result['availity_visit_reference']['copay_cents'] == 7000


def test_admin_upload_binds_saved_visit_and_keeps_source_protected(monkeypatch):
    from app.api.routers.commercial_flow import commercial_previsit_availity as route

    case = {
        'id': 'synthetic-case', 'source_mode': 'commercial_previsit', 'revision': 2,
        'previsit_context': {'context_token': 'synthetic-token',
            'inputs': {'service_date': '2026-11-03', 'place_of_service': '11',
                       'rendering_provider': {'npi': '1234567890'}},
            'source': {'rendering_provider': {'npi': '1234567890', 'roles': ['Neurology']}},
            'result': {'service_date': '2026-11-03'}},
    }
    async def session(_):
        return {'staff': {'role': 'admin'}, 'patient_id': 'synthetic-patient',
                'share_manifest_id': None, 'practice_id': 'synthetic-practice'}
    writes = []
    monkeypatch.setattr(route._api, '_session', session)
    monkeypatch.setattr(route._api, '_scope', lambda _: {})
    monkeypatch.setattr(route._api.store, 'get_case', lambda *_args, **_kwargs: case)
    monkeypatch.setattr(route._api, '_coverage_snapshot', lambda *_: {
        'patient_first': 'Jane', 'patient_last': 'Sample', 'patient_birth_date': '1947-08-20'})
    monkeypatch.setattr(route._api.object_store, 'put_staff_document',
        lambda *_args: writes.append('source') or 'protected-object-key')
    monkeypatch.setattr(route._api, '_save', lambda value, *_: value)
    monkeypatch.setattr(route._api, '_previsit_result',
        lambda value, *_: {'availity_visit_reference': value['availity_visit_reference']['evidence']})
    upload = UploadFile(file=BytesIO(_pdf()), filename='synthetic.pdf',
                        headers=Headers({'content-type': 'application/pdf'}))
    result = asyncio.run(route.attach_availity_eligibility('synthetic-case', None,
        revision=2, context_token='synthetic-token', file=upload))
    assert result['availity_visit_reference']['copay_cents'] == 7000
    assert 'raw_object_key' not in result['availity_visit_reference']
    assert case['availity_visit_reference']['raw_object_key'] == 'protected-object-key'
    assert writes == ['source']
