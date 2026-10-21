"""Source-attributed decisions made while pricing a saved commercial visit.

This is a projection of the shared identity, network, benefit, rate and
requirement decisions. It never turns a suggestion into an approved fact.
"""
from __future__ import annotations

from app.insurance.identity import patient_benefits_bound


def build(result: dict, source: dict) -> list[dict]:
    benefits = result.get('benefits') or {}
    network = result.get('network_evidence') or {}
    requirements = result.get('requirement_evidence') or {}
    lines = (result.get('price_range') or {}).get('lines') or []
    identity = result.get('identity') or {}
    checked = bool(result.get('checked_at'))
    rows: list[dict] = []

    def record(field: str, resolved: bool, sources: list[tuple[str, str]], missing: str = '', action: str = '') -> None:
        rows.append({'field': field, 'status': 'resolved' if resolved else 'unresolved',
                     'sources': [{'name': name, 'finding': finding} for name, finding in sources],
                     'missing_fact': '' if resolved else missing,
                     'next_action': '' if resolved else action})

    record('patient_identity', patient_benefits_bound(identity), [
        ('eCW Patient and Coverage', 'patient and policy identifiers compared' if source.get('coverage_id') else 'policy identifier not returned'),
        ('saved payer response', 'patient-bound response compared' if checked else 'no saved response for this visit'),
    ], 'Patient and subscriber binding for this coverage',
        'Reconcile only the fields named in the identity comparison against the patient-linked policy.')

    payer_names = ((result.get('plan_details') or {}).get('stedi') or {}).get('names') or []
    identified = ((result.get('plan_details') or {}).get('stedi') or {}).get('identified_product')
    record('exact_product', bool(identified and patient_benefits_bound(identity)), [
        ('saved payer response', 'correlated plan, enrollment and FEP source markers' if identified else
         'no corroborated exact product'),
        ('published plan reference', '2026 OPM PSHB brochure 71-020' if identified else 'no exact rule selected'),
    ], 'Member-bound exact insurance product',
        'Find a payer-confirmed enrollment or network identifier that distinguishes this product from similarly named plans.')
    matched_network = benefits.get('network_status') in {'in_network', 'out_of_network'}
    product_linked = bool(network.get('document_id') and network.get('decision') == 'confirmed_participating')
    record('product_mapping', product_linked, [
        ('eCW Coverage', 'plan label returned' if source.get('plan') else 'plan label not returned'),
        ('saved payer response', 'product label returned' if payer_names else 'exact product not returned' if checked else 'not checked'),
        ('clinic network configuration', network.get('decision') or 'no applicable decision'),
        ('Clinical Library contract', 'exact linked document' if network.get('document_id') else
         'related document only' if network.get('related_document_id') else 'no exact linked document'),
    ], 'Exact member product linked to the clinic agreement',
        'Match the member product or network identifier to the signed agreement and exact provider, location and date scope.')
    record('network_participation', matched_network, [
        ('saved payer response', benefits.get('network_source') or 'no provider-specific participation statement'),
        ('clinic network configuration', network.get('decision') or 'no applicable decision'),
    ], 'Provider participation tier for this member and visit',
        'Resolve the exact payer product against the scoped clinic agreement or obtain a provider-specific payer decision.')

    approved = result.get('service_selection_status') == 'approved'
    candidates = result.get('codes') or []
    suggestions = result.get('suggestions')
    model_count = sum(len(group.get('codes') or []) for group in suggestions.get('groups') or []) if isinstance(suggestions, dict) else 0
    record('service_code', approved, [
        ('eCW visit', 'scheduled service returned' if any(row.get('source') == 'eCW scheduled service' for row in candidates)
         else 'visit context returned; no selected billable code; appointment notes ' +
         str(source.get('appointment_notes_status') or 'not recorded')),
        ('service planner', f'{len(candidates)} conditional service candidate(s)' if candidates else 'no supported candidate'),
        ('Bedrock preliminary suggestions', f'{model_count} candidate(s)' if isinstance(suggestions, dict)
         else 'no suggestion retained for this result'),
    ], 'Clinician-approved service code and units',
        'Review the documented service and approve its CPT and units; conditional E/M levels are alternatives.')

    selected = benefits.get('selected_benefit_ids') or []
    excluded = benefits.get('excluded_service_evidence_ids') or []
    cost_share = any((row.get('benefits') or {}).get('copay_cents') is not None or
                     (row.get('benefits') or {}).get('coinsurance_percent') is not None for row in lines)
    record('office_cost_share', cost_share, [
        ('saved payer benefit rows', f'{len(selected)} applicable row(s); {len(excluded)} restricted row(s) excluded'
         if checked else 'no saved payer benefit response'),
        ('service-specific pricing', 'applicable copay or coinsurance selected' if cost_share else 'no applicable office term selected'),
        ('published plan benefit', 'Preferred specialist copay applies only after FEP Preferred participation is established'
         if benefits.get('published_preferred_office_copay_cents') is not None else 'no exact-product published office rule'),
    ], 'In-person office copay or coinsurance for the selected service',
        'Obtain an applicable member and service-specific office cost-share decision; restricted benefit rows cannot be substituted.')

    deductible = benefits.get('deductible_applicability')
    deductible_resolved = deductible in {'DOES_NOT_APPLY', 'ALREADY_MET'} or (
        deductible == 'APPLIES' and benefits.get('deductible_remaining_cents') is not None)
    record('deductible', deductible_resolved, [
        ('saved payer benefit rows', f'applicability {deductible or "not returned"}'),
        ('individual accumulator', 'remaining balance returned' if benefits.get('deductible_remaining_cents') is not None
         else 'applicable remaining balance not returned'),
        ('published plan rule', '2026 FEP Basic has no annual medical deductible'
         if benefits.get('published_plan_rule') else 'no exact-product rule applied'),
    ], 'Deductible applicability and remaining individual balance for this service',
        'Resolve the service-specific deductible rule and current individual balance with the payer.')

    rate_count = sum(row.get('allowed_cents') is not None for row in lines)
    record('contracted_rate', bool(lines) and rate_count == len(lines), [
        ('Clinical Library fee schedule and active rate versions',
         f'{rate_count} of {len(lines)} candidate line(s) priced' if matched_network else
         'rate selection held until product and network scope is established'),
        ('network and product scope', 'established' if matched_network else 'not established'),
    ], 'Applicable contracted allowance for each proposed CPT',
        'Link the exact product and provider scope, then match the active CPT rate version; do not use the cash schedule.')

    for field in ('referral', 'authorization'):
        decision = requirements.get(field) or {}
        status = decision.get('status') or 'not evaluated'
        sources = decision.get('sources') or []
        record(field + '_requirement', status in {'Required', 'Not required'}, [
            ('NMCT payer matrix and published payer rules', f'{len(sources)} applicable source(s); {status}'),
            ('Clinical Library research',
             f'{len(result.get("requirement_sources") or [])} recorded document(s)' if 'requirement_sources' in result
             else 'document inventory not retained in this result'),
            ('saved payer response and eCW', 'member-specific signal evaluated' if checked else 'no saved payer response'),
        ], f'{field.capitalize()} rule for the exact product and selected CPT',
            'Check the date-effective payer rule for this member and CPT only if no applicable saved rule resolves it.')
    referral = requirements.get('referral') or {}
    if referral.get('status') == 'Required':
        record('referral_obtained', referral.get('referral_obtained') == 'YES', [
            ('eCW and saved review', referral.get('referral_obtained') or 'no matching referral recorded'),
        ], 'Member and CPT-specific referral clearance',
            'Record the referral reference and validity for the selected service.')
    auth = requirements.get('authorization') or {}
    if auth.get('status') == 'Required':
        record('authorization_approval', auth.get('approval_status') == 'Verified', [
            ('eCW and saved review', auth.get('approval_status') or 'no matching approval recorded'),
        ], 'Member and CPT-specific authorization approval',
            'Record the payer approval reference and validity for the selected service.')

    guidance = result.get('collection_guidance') or {}
    record('collection_amount', guidance.get('amount_cents') is not None, [
        ('shared estimator', guidance.get('basis') or 'no supported basis'),
        ('clinic collection policy', 'approved decision applied' if result.get('collection_policy') else 'no approved policy applied'),
    ], 'Supported check-in instruction after service, benefits and clinic policy',
        'Resolve the specific unresolved fields above, then recalculate this saved visit without another payer transaction.')
    return rows
