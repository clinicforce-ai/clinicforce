"""Print deidentified saved UHC regression results for rendered frontend checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))

from testing.test_uhc_saved_response_projections import (  # noqa: E402
    FIXTURES, _synthetic_choice_plus_terms, replay, supported_result,
    supported_four_requirement_result,
)
from app.insurance import (amount_explanation, collection_guidance,  # noqa: E402
                           commercial, contract_requirements, encounter_summary, visit_estimate)


def view_result(name: str, *, approved: bool) -> dict:
    result, guidance, explanation = replay(name)
    if not approved:
        result.pop('collection_policy', None)
        guidance = collection_guidance.build(result)
        explanation = amount_explanation.build(result, guidance)
    codes = [{'id': code, 'code': code, 'units': 1, 'source': 'conditional office E/M alternative'}
             for code in FIXTURES[name]['service_plan']['alternatives']]
    for line, code in zip(result['price_range']['lines'], codes):
        line['id'] = code['id']
        line['units'] = 1
    result.update(appointment_id='synthetic-visit', service_date='2026-10-14',
                  checked_at='2026-10-14T12:00:00Z', codes=codes,
                  selected_ids=[], available_codes=codes,
                  plan_details={**result['plan_details'], 'ecw': {'plan': []}},
                  collection_guidance=guidance, payment_explanation=explanation,
                  encounter_summary=encounter_summary.build({**result, 'collection_guidance': guidance}))
    result['requirement_evidence'] = contract_requirements.evaluate(result, {
        'service_date': result['service_date'], 'payer_names': ['UnitedHealthcare'],
        'plans': [FIXTURES[name]['product']], 'member_id': 'synthetic-member',
        'group_number': 'synthetic-group', 'rendering_npi': '1234567890',
        'billing_npi': '0987654321', 'pos': '11',
    }, [])
    return result


def choice_plus_unresolved() -> dict:
    day = '2026-10-14'
    raw = _synthetic_choice_plus_terms(waive_deductible=False)
    identity = {'status': 'matched', 'binding': 'synthetic-binding', 'match_version': 2}
    benefits = commercial.normalize_stedi_271(raw, '98', True, service_date=day)
    benefits.update(identity=identity, network_status='in_network',
                    network_source='clinic_library_contract')
    codes = [{'id': 'office', 'code': '99203', 'units': 1}]
    price = visit_estimate.price_lines(codes,
        [{'code': '99203', 'allowed_cents': 15000,
          'source_reference': 'synthetic scoped UHC contract'}], benefits,
        {'cost_share': 'payer_returned', 'place_of_service': '11'},
        payload=raw, service_date=day)
    result = {'payer': 'United Healthcare', 'payment_mode': 'insurance',
              'service_date': day, 'identity': identity, 'benefits': benefits,
              'price_range': price, 'codes': codes, 'selected_ids': ['office'],
              'available_codes': codes, 'service_plan': {'selected_codes': ['99203']},
              'plan_details': {'stedi': {'names': ['POS CHOICE PLUS']}}}
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    result['encounter_summary'] = encounter_summary.build(result)
    result['payment_explanation'] = amount_explanation.build(result, guidance)
    return result


if '--required-authorization' in sys.argv:
    result = supported_result()
    result['price_range']['lines'][1]['benefits']['requirements']['authorization'] = {
        'required': 'required', 'status': 'pending', 'cleared': False,
    }
    result['requirement_evidence'] = {
        'authorization': {'status': 'Required', 'approval_status': 'Not verified'},
        'procedures': [
            {'code': '99203', 'authorization': {'status': 'Not required'}},
            {'code': '93000', 'authorization': {'status': 'Required'}}]}
    guidance = collection_guidance.build(result)
    result['collection_guidance'] = guidance
    result['encounter_summary'] = encounter_summary.build(result)
    result['payment_explanation'] = amount_explanation.build(result, guidance)
    print(json.dumps(result))
elif '--four-requirements' in sys.argv:
    print(json.dumps(supported_four_requirement_result()))
elif '--supported' in sys.argv:
    print(json.dumps(supported_result()))
elif '--choice-plus-unresolved' in sys.argv:
    print(json.dumps(choice_plus_unresolved()))
else:
    print(json.dumps({name: {'approved': view_result(name, approved=True),
                             'unapproved': view_result(name, approved=False)}
                      for name in FIXTURES}))
