"""Emit deidentified counterfactual replay results for the rendered clinician check."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.insurance.historical_replay import replay  # noqa: E402
from testing.test_historical_replay import _bundle, _positive_policy, _row  # noqa: E402


def main() -> None:
    scenarios = {
        'fixed_copay': _bundle([_row('B', 40, note='Deductible does not apply')],
                              policy=_positive_policy()),
        'deductible_coinsurance': _bundle([_row('C', 50, '30'), _row('G', 500, '30'),
                                         _row('A', .20)], policy=_positive_policy()),
        'multi_service': _bundle([_row('C', 50, '30'), _row('G', 500, '30'),
                                  _row('A', .20, '98'), _row('A', .20, '73')],
                                 [{'id': key, 'code': code, 'units': 1, 'modifiers': []}
                                  for key, code in [('office', '99204'), ('ncs', '95907'),
                                                    ('emg', '95886')]], policy=_positive_policy()),
        'incomplete': _bundle([]),
    }
    for field in ('cms_locality', 'cms_qp_status', 'cms_payment_reference'):
        scenarios['fixed_copay']['payer_route']['rules']['agreement_inventory']['scopes'][0].pop(field)
    print(json.dumps({name: replay(bundle)['result'] for name, bundle in scenarios.items()}))


if __name__ == '__main__':
    main()
