"""Protected, source-scoped financial facts used by a saved visit decision."""
from __future__ import annotations

from app.insurance.identity import patient_benefits_bound
from app.insurance.reference_price import select_reference

RULE_VERSION = 'evidence-facts-1'
FINANCIAL_FIELDS = ('copay_cents', 'copay_period', 'deductible_applies',
                    'deductible_remaining_cents', 'coinsurance_percent',
                    'oop_remaining_cents', 'interaction_rule')


def build(result: dict, review: dict | None = None) -> dict:
    """Project selected evidence; keep conflicts without copying raw payer rows or identities."""
    benefits = result.get('benefits') or {}
    identity = benefits.get('identity') or result.get('identity') or {}
    service_date = result.get('service_date')
    source = result.get('benefit_source') or ('Stedi' if result.get('checked_at') else 'unavailable')
    pricing_scope = result.get('pricing_scope') or {}
    payer_scope = {'patient_binding': identity.get('binding'),
                   'payer_route': result.get('payer_id'),
                   'product': result.get('payer_product_binding'),
                   'network': benefits.get('network_status'), 'service_date': service_date,
                   'provider_npi': pricing_scope.get('rendering_npi'),
                   'location_id': pricing_scope.get('location_id')}
    facts: list[dict] = []
    resolutions: list[dict] = []

    def add(field: str, value: object, system: str, reference: str | None,
            scope: dict, state: str, rule: str, *, effective: str | None = None,
            configuration_version: str | int | None = None) -> None:
        facts.append({'field': field, 'value': value, 'source_system': system,
                      'evidence_reference': reference, 'scope': scope,
                      'effective_date': effective, 'retrieved_at': result.get('checked_at'),
                      'verification_state': state, 'normalization_rule': rule,
                      'rule_version': RULE_VERSION,
                      'configuration_version': configuration_version})

    bound = patient_benefits_bound(identity)
    add('patient_policy_binding', bound, 'identity_compare', identity.get('binding'),
        payer_scope, 'verified' if bound else 'conflicting' if identity.get('status') == 'review' else 'unavailable',
        'identity.patient_benefits_bound')
    add('coverage_active', benefits.get('active'), source, result.get('response_reference'),
        payer_scope, 'source_reported' if benefits.get('active') is not None else 'unavailable', 'active_on_service_date')
    network_source = benefits.get('network_source')
    add('provider_network', benefits.get('network_status'), network_source or 'unavailable',
        (result.get('network_evidence') or {}).get('document_id'), payer_scope,
        'conflicting' if benefits.get('network_conflict') else 'source_reported' if network_source else 'unavailable',
        'network_participation')
    for line in (result.get('price_range') or {}).get('lines') or []:
        terms = line.get('benefits') or {}
        service_scope = {**payer_scope, 'line_id': line.get('id'), 'cpt': line.get('code'),
                         'modifiers': line.get('modifiers') or [], 'units': line.get('units')}
        refs = terms.get('selected_benefit_ids') or []
        conflicts = terms.get('source_errors') or terms.get('conflicts') or {}
        for field in FINANCIAL_FIELDS:
            value = terms.get(field)
            add(field, value, source, refs[0] if len(refs) == 1 else None,
                service_scope, 'conflicting' if conflicts else 'source_reported' if refs and value is not None else 'unavailable',
                'selected_service_benefit')
        if refs:
            add('selected_benefit_ids', refs, source, None, service_scope,
                'source_reported', 'service_specificity_and_tier')
        if conflicts:
            conflict_codes = ([str(item.get('kind') or 'source_error') for item in conflicts if isinstance(item, dict)]
                              if isinstance(conflicts, list) else list(conflicts) if isinstance(conflicts, dict) else ['source_error'])
            add('benefit_conflicts', conflict_codes, source, None, service_scope,
                'conflicting', 'preserve_payer_conflict')
        rate = line.get('rate_resolution') or {}
        if rate.get('status') == 'RESOLVED' and type(line.get('allowed_cents')) is int:
            add('exact_price_cents', line['allowed_cents'], line.get('allowed_basis') or 'clinic_rate',
                line.get('rate_source'), service_scope, 'verified', 'exact_rate_match',
                effective=line.get('rate_effective_from'), configuration_version=line.get('rate_version'))
        else:
            row = select_reference(line, (result.get('temporary_rate_reference') or {}).get('lines') or [],
                                   service_date, pricing_scope)
            if row:
                add('reference_unit_price_cents', row.get('unit_reference_cents'),
                    'BCBSTX_reference', row.get('source_reference'), service_scope,
                    'verified',
                    'approved_reference_rate_match', configuration_version=row.get('rate_version'))
        resolutions.append({'field': 'benefits', 'line_id': line.get('id'),
                            'status': 'conflicting' if conflicts else 'resolved' if refs else 'unavailable',
                            'attempted_sources': [source],
                            'next_action': 'Resolve selected payer rows.' if conflicts else
                                           'Find applicable service benefit.' if not refs else None})
    policy = result.get('visit_collection_policies') or []
    for row in policy:
        if isinstance(row, dict):
            add('collection_policy', row.get('id'), 'NMCT_policy', row.get('reference'),
                {**payer_scope, 'service_groups': row.get('service_groups')},
                'verified' if row.get('approved_by') and row.get('approved_at') else 'unavailable',
                'approved_exact_scope', effective=row.get('effective_from'),
                configuration_version=row.get('version'))
    payment = (review or {}).get('payments') or {}
    if type(payment.get('amount_cents')) is int and payment.get('reference'):
        add('visit_payments_cents', payment['amount_cents'], 'staff_ledger_verification',
            payment['reference'], payer_scope, 'verified', 'visit_payment_reference')
    resolutions.extend([
        {'field': 'patient_policy_binding', 'status': 'resolved' if bound else 'conflicting' if identity.get('status') == 'review' else 'verification_required',
         'attempted_sources': ['eCW Coverage', source], 'next_action': None if bound else 'Reconcile patient and subscriber identity.'},
        {'field': 'service_date', 'status': 'conflicting' if benefits.get('service_date_matches') is False else
         'resolved' if service_date and benefits.get('service_date_matches') is True else 'verification_required',
         'attempted_sources': ['eCW appointment', source], 'next_action': None if service_date and benefits.get('service_date_matches') is True else 'Verify visit-date coverage.'},
        {'field': 'primary_coverage', 'status': 'verification_required' if benefits.get('has_secondary') else 'resolved',
         'attempted_sources': ['eCW Coverage', source], 'next_action': 'Resolve coordination of benefits.' if benefits.get('has_secondary') else None},
        {'field': 'payer_route', 'status': 'resolved' if result.get('payer_id') or result.get('payer_source') else 'verification_required',
         'attempted_sources': ['eCW Coverage', 'Stedi discovery'], 'next_action': None if result.get('payer_id') or result.get('payer_source') else 'Confirm payer routing.'},
        {'field': 'exact_product', 'status': 'resolved' if result.get('payer_product_binding') else 'verification_required',
         'attempted_sources': ['eCW Coverage', source, 'NMCT payer configuration'],
         'next_action': None if result.get('payer_product_binding') else 'Bind the exact insurance product.'},
        {'field': 'provider_network', 'status': 'conflicting' if benefits.get('network_conflict') else
         'resolved' if network_source and benefits.get('network_status') in {'in_network', 'out_of_network'} else 'verification_required',
         'attempted_sources': [source, 'NMCT network configuration'],
         'next_action': None if network_source and not benefits.get('network_conflict') else 'Verify provider, product and location participation.'},
        {'field': 'selected_service', 'status': 'resolved' if result.get('service_selection_status') == 'approved' else 'verification_required',
         'attempted_sources': ['eCW appointment', 'Bedrock proposal', 'clinician selection'],
         'next_action': None if result.get('service_selection_status') == 'approved' else 'Confirm billable CPTs and units.'},
        {'field': 'collection_policy', 'status': 'resolved' if any(
            isinstance(row, dict) and row.get('approved_by') and row.get('approved_at') for row in policy) else 'verification_required',
         'attempted_sources': ['NMCT policy configuration'],
         'next_action': None if any(isinstance(row, dict) and row.get('approved_by') and row.get('approved_at') for row in policy)
         else 'Approve an exact-scope visit collection policy.'},
    ])
    return {'rule_version': RULE_VERSION, 'facts': facts, 'resolutions': resolutions}
