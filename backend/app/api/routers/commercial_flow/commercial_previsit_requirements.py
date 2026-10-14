"""Evaluate documented coverage requirements for selected and candidate services."""
from __future__ import annotations

from types import ModuleType


def attach_requirements(api: ModuleType, result: dict, case: dict, inputs: dict,
                        source: dict, session: dict, connections: list[dict], payer_id: str) -> None:
    library_ids = ([row['id'] for row in session['_library_documents']
                    if row.get('kind') == 'plan_matrix' or row.get('kind') == 'contract' and row.get('content_type') == 'application/pdf']
                   if '_library_documents' in session else
                   [row['document_id'] for row in case.get('library_pricing', []) if row.get('document_id')])
    library_sources = [api.staff_store.get_library(document_id, session['practice_id']) for document_id in library_ids]
    payer_connections = [row for row in connections if row.get('stedi_payer_id') == payer_id]
    connection = payer_connections[0] if len(payer_connections) == 1 else {}
    requirement_context = {
        'service_date': inputs['service_date'], 'payer_verified': bool(connection),
        'payer_names': [result.get('payer'), source.get('payer'), connection.get('display_name'),
                        connection.get('stedi_payer_id'), *(connection.get('aliases') or [])],
        'plans': [source.get('plan'), result.get('plan'),
                  *(name for names in (source.get('plan_details') or {}).values() for name in names),
                  *(result['plan_details']['stedi'].get('names') or []),
                  *([(result.get('network_evidence') or {}).get('network_name')]
                    if result.get('benefits', {}).get('network_status') == 'in_network' else [])],
        'network': (result.get('network_evidence') or {}).get('network_name'),
        'rendering_npi': (inputs.get('rendering_provider') or {}).get('npi'),
        'billing_npi': (source.get('billing_provider') or {}).get('npi'),
        'tin': (source.get('billing_provider') or {}).get('tin'),
        'group_number': result['insurance_details'].get('group_number') or result['plan_details']['stedi'].get('group_number') or inputs.get('group_number'),
        'plan_number': inputs.get('plan_number') or result['plan_details']['stedi'].get('plan_number'),
        'member_id': result['insurance_details'].get('member_id') or inputs.get('member_id'),
        'coverage_period': source.get('coverage_period') or result['insurance_details'].get('payer_coverage_period'),
        'location_id': source.get('location_id'),
        'pos': inputs.get('place_of_service'),
    }
    requirement_documents = [document for document in library_sources if document]
    result['requirement_evidence'] = api.contract_requirements.evaluate(result, requirement_context, requirement_documents)
    candidates = api._requirement_candidates(result)
    if candidates:
        # Research the other suggested choices without changing the requirement
        # summary for the services actually selected for this visit.
        candidate_result = {**result, 'codes': candidates, 'price_range': {'lines': []}}
        result['candidate_requirements'] = api.contract_requirements.evaluate(
            candidate_result, requirement_context, requirement_documents)['procedures']
    else:
        result['candidate_requirements'] = []
