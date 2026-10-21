"""Offline, read-only replay of an explicitly linked saved eligibility response."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.insurance.historical_replay import replay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path, help='Protected local JSON bundle; never commit it')
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text())
    report = replay(bundle)
    result = report.get('result') or {}
    price = result.get('price_range') or {}
    guidance = result.get('collection_guidance') or {}
    summary = {'case_reference': bundle.get('case_reference'),
               'status': report['status'], 'mode': report['mode'],
               'retrospective_headline': report.get('retrospective_headline'),
               'source_binding': report.get('source_binding'),
               'service_date': report.get('service_date'),
               'service_origin': report.get('service_origin'),
               'medical_products': (report.get('product') or {}).get('names'),
               'network_decision': (report.get('network_evidence') or
                                    report.get('exclusion') or {}).get('decision'),
               'contract_source': {'document_id': (report.get('exclusion') or {}).get('document_id'),
                                   'page': (report.get('exclusion') or {}).get('page')},
               'lines': [{'code': line.get('code'), 'units': line.get('units'),
                          'allowed_cents': line.get('allowed_cents'),
                          'allowed_basis': line.get('allowed_basis'),
                          'rate_source': line.get('rate_source'),
                          'benefit_rows': next((service.get('selected_benefit_ids')
                              for service in guidance.get('services') or []
                              if service.get('id') == line.get('id')), []),
                          'calculation': line.get('calculation')}
                         for line in price.get('lines') or []],
               'patient_share_cents': price.get('patient_min_cents') if price.get('complete') else None,
               'collect_at_checkin_cents': guidance.get('amount_cents'),
               'collection_basis': guidance.get('basis'),
               'explanation': (result.get('payment_explanation') or {}).get('sentence'),
               'integrity_error': (result.get('encounter_summary') or {}).get('integrity_error'),
               'original_estimate_unchanged': report.get('original_estimate_unchanged')}
    print(json.dumps(summary, indent=2, default=str))
    return 0 if report['status'] in {'calculated', 'action_required'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
