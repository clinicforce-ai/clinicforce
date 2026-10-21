"""Match an administrator-certified agreement inventory to one visit."""
from __future__ import annotations

from app.core import staff_store
from app.insurance import commercial
from app.insurance.library_network_exclusions import product_kind


def agreement_hashes(practice_id: str, payer: str, *, documents: list[dict] | None = None) -> dict[str, str]:
    """A new, removed or replaced payer agreement invalidates an older certification."""
    if not commercial.is_united_healthcare_payer({'payer': payer}):
        return {}
    documents = [row for row in (documents if documents is not None else staff_store.list_library(practice_id))
                 if row.get('kind') == 'contract'
                 and commercial.is_united_healthcare_payer({'payer': row.get('payer') or ''})]
    if any(not row.get('sha256') for row in documents):
        return {}
    return {row['id']: row['sha256'] for row in documents}


def _separate_participation(practice_id: str, document_hashes: dict[str, str],
                            excluded_document_id: str, product: str,
                            visit: dict[str, str], documents: list[dict] | None = None) -> bool:
    """A second executed agreement blocks an inventory-based nonparticipation inference."""
    for document_id, digest in document_hashes.items():
        if document_id == excluded_document_id:
            continue
        document = (next((row for row in documents if row.get('id') == document_id), None)
                    if documents is not None else staff_store.get_library(document_id, practice_id)) or {}
        if document.get('sha256') != digest:
            continue
        compiled = ((document.get('extraction') or {}).get('compiled') or {})
        for record in compiled.get('networks') or []:
            values = record.get('values') or {}
            if (values.get('participation', '').lower() != 'participating'
                    or product_kind(values.get('plan', '')) != product_kind(product)
                    or any(str(values.get(field) or '') != visit[key] for field, key in
                           (('renderingnpi', 'rendering_npi'), ('billingnpi', 'billing_npi'),
                            ('tin', 'tin'), ('locationid', 'location_id'),
                            ('pos', 'place_of_service')))):
                continue
            if (str(values.get('effectivefrom') or '') <= visit['service_date']
                    <= str(values.get('effectiveto') or '9999-12-31')):
                return True
    return False


def applicable(practice_id: str, payer: dict, inputs: dict, source: dict,
               exclusion: dict | None, *, documents: list[dict] | None = None) -> dict | None:
    """Return the current approval only for its exact provider, site and date scope."""
    names = source.get('payer_plan_names') or []
    if (not exclusion or not source.get('payer_plan_bound') or len(names) != 1
            or product_kind(str(names[0])) != exclusion.get('product_kind')):
        return None
    approval = (payer.get('rules') or {}).get('agreement_inventory') or {}
    if not isinstance(approval, dict) or not all(approval.get(key) for key in
            ('approved_by', 'approved_at', 'reference', 'document_hashes', 'scopes')):
        return None
    current = agreement_hashes(practice_id, str(payer.get('display_name') or ''), documents=documents)
    if current != approval['document_hashes'] or exclusion.get('document_id') not in current:
        return None
    billing = source.get('billing_provider') or {}
    if billing.get('tin_conflict'):
        return None
    visit = {'rendering_npi': str((inputs.get('rendering_provider') or {}).get('npi') or ''),
             'billing_npi': str(billing.get('npi') or ''),
             'tin': str(billing.get('tin') or '').replace('-', ''),
             'location_id': str(source.get('location_id') or ''),
             'place_of_service': str(inputs.get('place_of_service') or ''),
             'service_date': str(inputs.get('service_date') or '')}
    if not all(visit.values()):
        return None
    if _separate_participation(practice_id, current, exclusion['document_id'],
                               str(names[0]), visit, documents):
        return None
    for scope in approval['scopes']:
        if (all(str(scope.get(key) or '') == visit[key] for key in
                ('rendering_npi', 'billing_npi', 'tin', 'location_id', 'place_of_service'))
                and str(scope.get('effective_from') or '') <= visit['service_date']
                <= str(scope.get('effective_to') or '9999-12-31')):
            return {'decision': 'out_of_network_by_clinic_contract_inventory',
                    'source': 'clinic_contract_inventory', 'approved_by': approval['approved_by'],
                    'approved_at': approval['approved_at'], 'reference': approval['reference'],
                    'document_hashes': current, 'scope': scope,
                    'excluded_document_id': exclusion['document_id']}
    return None
