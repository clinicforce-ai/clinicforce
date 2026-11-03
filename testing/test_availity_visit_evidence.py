"""Dated portal evidence must bind to one saved specialist visit."""
import fitz
import pytest

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


def test_dated_availity_reference_exposes_only_office_fee_reference(monkeypatch):
    from app.api.routers.commercial_flow import commercial_previsit_rate_reference as rates

    monkeypatch.setattr(rates._api.network_participation, 'is_nmct_practice', lambda _: True)
    result = {
        'payment_mode': 'insurance',
        'benefits': {'active': False},
        'availity_visit_reference': _parse(_pdf()),
        'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}],
    }
    rates._attach_temporary_aetna_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-03', 'place_of_service': '11'}, {})
    reference = result['temporary_rate_reference']
    assert reference['lines'][0]['unit_reference_cents'] == 11960
    assert 'planning reference' in reference['message']
    assert 'allowed_cents' not in result
