"""Read-only reconstruction of a saved visit through the current pricing engine."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import base64
import hashlib
import json

from app.insurance import (amount_explanation, cms_locality, cms_pfs, collection_guidance,
                           commercial, encounter_summary, insurance_product, inventory_certification,
                           library_network_exclusions, payer_allowance, stedi_discovery,
                           visit_estimate)
from app.insurance.identity import compare as compare_identity, patient_benefits_bound


def _timestamp(value: str) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return instant.astimezone(timezone.utc) if instant.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _as_of(value: dict | None, cutoff: str) -> dict | None:
    if not isinstance(value, dict) or not value:
        return None
    stamp = str(value.get('approved_at') or value.get('verified_at') or '')
    approved, check = _timestamp(stamp), _timestamp(cutoff)
    return value if approved is not None and check is not None and approved <= check else None


def replay(bundle: dict) -> dict:
    """Recalculate without writes, a payer call, or reuse of the old dollar result."""
    case = deepcopy(bundle.get('case') or {})
    context = case.get('previsit_context') or {}
    inputs, source = context.get('inputs') or {}, context.get('source') or {}
    service_date = str(bundle.get('service_date') or '')
    if not service_date or date.fromisoformat(service_date).isoformat() != str(inputs.get('service_date')):
        raise ValueError('The replay date must equal the saved encounter service date.')
    original = deepcopy(context.get('result') or {})
    raw_reference = str(bundle.get('raw_reference') or '')
    if not raw_reference or raw_reference != str(case.get('previsit_raw_object_key') or ''):
        return {'status': 'unlinked_saved_response', 'mode': 'evidence_review',
                'reason': 'The supplied raw 271 reference is not the saved case object key.'}
    checked = str((case.get('previsit') or {}).get('checked_at') or original.get('checked_at') or '')[:10]
    if not checked or checked > service_date:
        return {'status': 'inapplicable_saved_response', 'mode': 'evidence_review',
                'reason': 'The saved eligibility check is not dated on or before this encounter.'}
    raw_bytes = (base64.b64decode(bundle['raw_response_b64'], validate=True)
                 if bundle.get('raw_response_b64') else None)
    payload = json.loads(raw_bytes) if raw_bytes is not None else bundle.get('raw_response')
    if not isinstance(payload, dict):
        raise ValueError('Provide the protected saved JSON for the matching object key.')
    raw_digest = (hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else
                  hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    expected_digest = str(case.get('previsit_raw_sha256') or '')
    if expected_digest and (raw_bytes is None or expected_digest != raw_digest):
        return {'status': 'unlinked_saved_response', 'mode': 'evidence_review',
                'reason': 'The supplied 271 content does not match the saved response digest.'}
    intake_id = str(case.get('intake_id') or '')
    if intake_id and str(bundle.get('intake_id') or '') != intake_id:
        return {'status': 'unlinked_saved_response', 'mode': 'evidence_review',
                'reason': 'The saved response is not linked to the selected intake.'}
    selection = bundle.get('selection') or {}
    origin = selection.get('origin')
    if origin not in {'documented', 'historical_anticipated', 'synthetic'}:
        raise ValueError('Label selected codes as documented, historical_anticipated or synthetic.')
    codes = selection.get('lines') or []
    if not codes or any(not row.get('id') or not row.get('code') or type(row.get('units')) is not int
                        for row in codes):
        raise ValueError('Select exact CPT lines and units; E/M alternatives are not separate services.')
    office = [row for row in codes if str(row['code']).isdigit() and
              99202 <= int(row['code']) <= 99215]
    if len(office) > 1:
        raise ValueError('Select one documented or explicitly simulated office E/M level.')
    if origin == 'documented':
        saved_ids = set(original.get('selected_ids') or [])
        saved_codes = {row.get('id'): row for row in original.get('codes') or []}
        if not saved_ids or any(row['id'] not in saved_ids or
                any(saved_codes[row['id']].get(key) != row.get(key) for key in ('code', 'units'))
                for row in codes):
            raise ValueError('Documented lines must match the saved selected CPT IDs, codes and units.')
    simulated = origin != 'documented' or bool(bundle.get('simulated_inventory') or bundle.get('simulated_policy'))
    recorded_cutoff = str((case.get('previsit') or {}).get('checked_at') or '')
    cutoff = str(bundle.get('historical_cutoff') or recorded_cutoff or service_date)
    if cutoff[:10] > service_date or (recorded_cutoff and
            (_timestamp(cutoff) is None or _timestamp(recorded_cutoff) is None
             or _timestamp(cutoff) > _timestamp(recorded_cutoff))):
        raise ValueError('The historical cutoff cannot exceed the saved check time or service date.')
    documents = [row for row in bundle.get('documents') or []
                 if bundle.get('simulated_inventory') or _as_of({'verified_at': row.get('created_at')}, cutoff)]
    payer = bundle.get('payer_route') or {}
    practice_id = str(bundle.get('practice_id') or '')
    product = commercial.plan_details(payload)
    identity = deepcopy((original.get('identity') or (original.get('benefits') or {}).get('identity') or {}))
    saved_coverage = original.get('insurance_details') or {}
    if saved_coverage.get('member_id') and saved_coverage.get('patient_first'):
        policy_match = ((original.get('benefits') or {}).get('policy_match') or {})
        if policy_match.get('source') == 'payer_response' and policy_match.get('status') == 'verified':
            if not (stedi_discovery.corroborates_patient_policy(payload, inputs, source)
                    and stedi_discovery.automatic_match(payload, inputs, source)):
                return {'status': 'unlinked_saved_response', 'mode': 'evidence_review',
                        'reason': 'The saved dependent-policy match cannot be verified from the linked evidence.'}
            saved_coverage = stedi_discovery.discovered_coverage(saved_coverage, inputs, payload)
        reconstructed = compare_identity(payload, saved_coverage, identity.get('evidence'))
        if identity.get('binding') and reconstructed.get('binding') != identity['binding']:
            return {'status': 'unlinked_saved_response', 'mode': 'evidence_review',
                    'reason': 'The saved 271 member identity does not match the saved estimate identity binding.'}
        identity = reconstructed
    selected_services = [visit_estimate.service_type(row['code'], {}) for row in codes]
    response_service = next((kind for kind in selected_services if kind), '30')
    benefits = commercial.normalize_stedi_271(payload, response_service, False, service_date=service_date)
    benefits['identity'] = identity
    benefits['benefit_source'] = 'saved Stedi 271'
    named = product.get('names') or []
    bound = len(named) == 1 and patient_benefits_bound(identity) and benefits.get('active') is True
    exact_source = {**source, 'payer_plan_names': named, 'payer_plan_bound': bound}
    exclusion = library_network_exclusions.applicable_exclusion(practice_id,
        str(payer.get('display_name') or ''), named, service_date, documents=documents)
    saved_approval = ((payer.get('rules') or {}).get('agreement_inventory') or {})
    approval = bundle.get('simulated_inventory') if simulated and bundle.get('simulated_inventory') else saved_approval
    if not bundle.get('simulated_inventory'):
        approval = _as_of(approval, cutoff) or {}
    inventory = inventory_certification.applicable(practice_id,
        {**payer, 'rules': {**(payer.get('rules') or {}), 'agreement_inventory': approval}},
        inputs, exact_source, exclusion, documents=documents)
    if inventory:
        benefits.update(network_status='out_of_network', network_source='clinic_contract_inventory',
                        network_evidence_ids=[exclusion['document_id']])
    else:
        benefits.update(network_status='unknown', network_source=None)
    review = case.get('visit_review') or {}
    policy = (bundle.get('simulated_policy') if bundle.get('simulated_policy') else
              _as_of((payer.get('rules') or {}).get('collection_policy'), cutoff))
    billing = _as_of(review.get('billing_protection'), cutoff) or {}
    if (billing.get('identity_binding') != identity.get('binding')
            or billing.get('service_date') not in {None, service_date}
            or billing.get('payer_id') not in {None, str(context.get('payer_id') or '')}):
        billing = {}
    dated_review = {**review, 'payer_allowances': {key: quote for key, quote in
                    (review.get('payer_allowances') or {}).items() if _as_of(quote, cutoff)}}
    rates = payer_allowance.quoted_rates(dated_review, codes, payer_id=str(context.get('payer_id') or ''),
        service_date=service_date, provider_npi=str((inputs.get('rendering_provider') or {}).get('npi') or ''),
        place_of_service=str(inputs.get('place_of_service') or ''), patient_binding=str(identity.get('binding') or ''),
        product_binding=payer_allowance.product_binding(product))
    scope = (inventory or {}).get('scope') or {}
    locality, locality_evidence = cms_locality.pricing_locality(source.get('cms_service_zip'),
        str(scope.get('cms_locality') or source.get('cms_locality') or '') or None)
    if inventory:
        for line in codes:
            if any(rate.get('line_id') == line['id'] for rate in rates):
                continue
            rate = cms_pfs.estimate_rate(line['code'], line.get('modifiers') or [], line['units'],
                service_date=service_date, locality=str(locality or ''),
                qp_status=str(scope.get('cms_qp_status') or source.get('cms_qp_status') or ''),
                place_of_service=str(inputs.get('place_of_service') or ''),
                provider_npi=str((inputs.get('rendering_provider') or {}).get('npi') or ''),
                patient_binding=str(identity.get('binding') or ''))
            if rate:
                rates.append({**rate, 'line_id': line['id'], 'units': line['units'],
                              'product_binding': payer_allowance.product_binding(product)})
    rules = {'cost_share': 'payer_returned', 'office_service_types': ['98'],
             'medicare_advantage': insurance_product.is_medicare_advantage(named),
             'patient_binding': identity.get('binding'),
             'product_binding': payer_allowance.product_binding(product),
             'cms_locality': locality, 'cms_locality_evidence': locality_evidence,
             'cms_qp_status': (inventory or {}).get('scope', {}).get('cms_qp_status'),
             'provider_npi': (inputs.get('rendering_provider') or {}).get('npi'),
             'place_of_service': inputs.get('place_of_service'),
             'visit_context': {key: inputs.get(key) for key in ('reason', 'diagnoses')}}
    price = visit_estimate.price_lines(codes, rates, benefits, rules, payload=payload,
        service_date=service_date, reviews={key: value for key, value in
            (review.get('lines') or {}).items() if _as_of(value, cutoff)})
    result = {'payer': payer.get('display_name'), 'payment_mode': 'insurance',
              'service_date': service_date, 'checked_at': (case.get('previsit') or {}).get('checked_at'),
              'identity': identity, 'benefits': benefits, 'plan_details': {'stedi': product},
              'codes': codes, 'available_codes': codes, 'selected_ids': [row['id'] for row in codes],
              'service_selection_status': 'approved', 'service_plan': {'selected_codes': [row['code'] for row in codes]},
              'price_range': price, 'billing_protection': billing, 'collection_policy': policy,
              'network_evidence': inventory or exclusion or {}, 'agreement_exclusion': exclusion,
              'benefit_source': 'saved Stedi 271'}
    guidance = collection_guidance.build(result, dated_review)
    result['collection_guidance'] = guidance
    result['encounter_summary'] = encounter_summary.build(result, dated_review)
    result['payment_explanation'] = amount_explanation.build(result, guidance, dated_review)
    if simulated and guidance.get('basis') == 'claim_after_service':
        explanation = result['payment_explanation']
        explanation['sentence'] = (explanation['sentence'].rstrip('.') +
            '; this deferral policy is simulated, and final patient liability follows the processed claim.')
    amount = guidance.get('amount_cents')
    headline = ('Amount NMCT would have collected at check-in: ' +
                (f'${amount // 100:,}.{amount % 100:02d}' if amount is not None else 'unresolved'))
    if simulated and guidance.get('basis') == 'claim_after_service':
        headline = '$0 collected at check-in — simulated deferral policy'
    return {'status': 'calculated' if guidance.get('amount_cents') is not None else 'action_required',
            'mode': 'counterfactual_simulation' if simulated else 'historical_as_recorded',
            'retrospective_headline': headline,
            'source_binding': 'saved_case_raw_digest' if expected_digest else 'saved_case_raw_object_key_only',
            'raw_digest': raw_digest, 'historical_cutoff': cutoff, 'service_date': service_date,
            'service_origin': origin, 'product': product, 'exclusion': exclusion,
            'network_evidence': inventory, 'selected_lines': codes,
            'result': result, 'original_estimate_unchanged': context.get('result') == original}
