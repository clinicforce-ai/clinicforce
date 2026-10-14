"""Reconcile plan-matrix research with payer and contract requirement evidence."""
from __future__ import annotations


def merge_matrix_research(output: dict, research: dict | None, rules: dict, linked_id: str | None, kinds: tuple[str, ...]) -> None:
    if research:
        for kind in kinds:
            current, matrix = output[kind], research[kind]
            cited = [source for source in current['sources']
                     if not source.get('document_id') or source.get('page') or source.get('document_id') == linked_id]
            sources = list({(source.get('document_id'), source.get('page'), source.get('label')): source
                            for source in [*matrix['sources'], *cited]}.values())
            verify_url = matrix.get('verify_url')
            if current['status'] != 'Verification required' and matrix['status'] != 'Verification required':
                if current['status'] != matrix['status']:
                    output[kind] = {'status': 'Verification required',
                                    'reason': 'The contract or payer response conflicts with plan-specific Excel research; confirm with the payer.',
                                    'sources': sources, 'verify_url': verify_url}
                else:
                    output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            elif current['status'] == 'Verification required' and matrix['status'] != 'Verification required' and not rules[kind]:
                output[kind] = {**matrix, 'sources': sources}
            elif matrix['status'] == 'Verification required' and current['status'] != 'Verification required' and \
                    matrix['reason'].startswith('Matrix does not settle authorization'):
                output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            elif matrix['status'] == 'Verification required':
                output[kind] = {**matrix, 'sources': sources,
                                'reason': matrix['reason'] + (' ' + current['reason'] if current['reason'].startswith('Payer response reports ') else '')}
            else:
                output[kind] = {**current, 'sources': sources, 'verify_url': verify_url}
            for field in ('verification', 'contact'):
                if matrix.get(field):
                    output[kind][field] = matrix[field]
