"""Explicit clinic approvals used only by synthetic estimator tests."""

ADVANCE_POLICY = {'mode': 'collect_supported_advance', 'approved_by': 'synthetic-admin',
                  'approved_at': '2026-10-01T12:00:00Z', 'reference': 'Synthetic advance collection policy'}
COPAY_POLICY = {**ADVANCE_POLICY, 'mode': 'collect_verified_copay'}
