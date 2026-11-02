"""The generic visit model supersedes payer-shaped proxy suggestions under V2."""
from app.api.routers import commercial_estimator  # noqa: F401 - initialize established router import order
from app.api.routers.commercial_flow import commercial_previsit_finish as finish


def test_v2_never_builds_or_retains_a_legacy_fee_proxy(monkeypatch):
    monkeypatch.setenv('COLLECTION_ENGINE_V2_ENABLED', 'true')
    def forbidden(*_args):
        raise AssertionError('A legacy payer proxy must not run under V2')
    monkeypatch.setattr(finish.blue_cross_proxy, 'estimate', forbidden)
    monkeypatch.setattr(finish.blue_cross_proxy, 'cigna_estimate', forbidden)
    value = {'fee_proxy_estimate': {'patient_share_cents': 12345}}
    finish._attach_legacy_fee_proxy(value, '2026-11-10')
    assert 'fee_proxy_estimate' not in value


def test_legacy_path_keeps_proxy_until_its_full_replay_gate(monkeypatch):
    monkeypatch.delenv('COLLECTION_ENGINE_V2_ENABLED', raising=False)
    monkeypatch.setattr(finish.blue_cross_proxy, 'estimate',
                        lambda *_args: {'patient_share_cents': 1200})
    value = {}
    finish._attach_legacy_fee_proxy(value, '2026-11-10')
    assert value['fee_proxy_estimate']['patient_share_cents'] == 1200
