"""Official-source scope and independently published Dallas fee checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.insurance import cms_locality, cms_pfs


FIXTURE = Path(__file__).with_name('fixtures') / 'cms_dallas_nonqp_carrier_2026.json'


def test_rvu26d_rates_match_independent_cms_dallas_carrier_fees():
    carrier = json.loads(FIXTURE.read_text())
    assert carrier['source_member'] == 'PFTX26A.TXT'
    for code, cents in carrier['fees_cents'].items():
        rate = cms_pfs.estimate_rate(code, [], 1, service_date='2026-10-14',
            locality='11', qp_status='non_qp', place_of_service='11',
            provider_npi='1234567890', patient_binding='synthetic-member')
        assert rate is not None
        assert rate['allowed_cents'] == cents
        assert rate['source_sha256'] == cms_pfs.SOURCE_SHA256


def test_service_zip_resolves_dallas_and_requires_zip_plus_four_when_cms_says_so():
    resolved = cms_locality.resolve('75247')
    assert resolved['locality'] == '11'
    assert resolved['source_sha256'] == cms_locality.SOURCE_SHA256
    assert cms_locality.resolve('75007')['status'] == 'zip_plus_four_required'
    assert cms_locality.pricing_locality('75247', '99')[0] is None
    assert cms_locality.pricing_locality('75247', '99')[1]['status'] == 'clinic_location_conflict'
    assert cms_locality.pricing_locality('75247', '11')[0] == '11'


def test_import_rejects_unverified_release(tmp_path):
    source = tmp_path / 'rvu.zip'
    source.write_bytes(b'not the verified CMS source')
    with pytest.raises(ValueError, match='SHA-256'):
        cms_pfs.import_official_zip(source, tmp_path / 'out.json.gz')
    with pytest.raises(ValueError, match='verified October'):
        cms_locality.import_official_zip(source, tmp_path / 'locality.json.gz')
