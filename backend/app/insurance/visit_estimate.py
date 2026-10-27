"""Explainable visit-line calculations over protected payer and clinic evidence."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import re
from datetime import date, datetime

from app.insurance import commercial, cpt_rules, identity
from app.insurance.network_participation import verified_in_network
from app.insurance.pricing.visit import _workflow_condition, service_type, review_valid, resolve_requirements, price_lines


def clinical_group(code: str) -> str:
    return cpt_rules.service_definition(code)['group']


def is_emg_visit(inputs: dict) -> bool:
    if type(inputs.get('emg_required')) is bool:
        return inputs['emg_required']
    if any(row.get('service_status') == 'scheduled' and row.get('code') in cpt_rules.NCS_CODES | cpt_rules.EMG_CODES
           for row in inputs.get('lines', [])):
        return True
    appointment_type = str(inputs.get('appointment_type') or '')
    if (re.search(r'\b(?:emg|ncs|electromyograph\w*|nerve conduction)\b', appointment_type, re.I)
            and not re.search(r'\b(?:possible|consider\w*|discuss\w*|if needed|no|not)\b', appointment_type, re.I)):
        return True
    reason = str(inputs.get('reason') or '')
    procedure = r'\b(?:emg|ncs|electromyograph\w*|nerve conduction)\b'
    # A possible future test is not today's booked procedure. Explicit orders win above.
    optional = r'\b(?:possible|potential|consider\w*|discuss\w*|may need|may require|if needed|if indicated|no|not)\b'
    for clause in re.split(r'[.;\n]', reason):
        match = re.search(procedure, clause, re.I)
        if (match and not re.search(optional, clause[:match.start()], re.I)
                and not re.search(r'\b(?:if needed|if indicated|only if|as needed|may be needed)\b', clause[match.end():], re.I)):
            return True
    return False


def is_spinal_tap(inputs: dict, *, clinic_abbreviation: bool = False) -> bool:
    reason = str(inputs.get('reason') or '')
    for clause in re.split(r'[.;\n]', reason):
        if re.search(r'\b(?:spinal tap|lumbar puncture)\b', clause, re.I) and not re.search(
                r'\b(?:no|not|possible|consider\w*|discuss\w*|history|results?|review|if needed)\b', clause, re.I):
            return True
    # SP is a clinic-confirmed booking abbreviation, not a universal diagnosis.
    return clinic_abbreviation and str(inputs.get('appointment_reason') or '').strip().upper() == 'SP'


def service_plan(available: list[dict], inputs: dict, source: dict) -> dict:
    """Describe planning choices; past care is a suggestion, never a new order."""
    emg = is_emg_visit(inputs)
    order = ['nerve', 'muscle', 'visit', 'other'] if emg else ['visit', 'nerve', 'muscle', 'other']
    labels = {'visit': 'Office consultation', 'nerve': 'Nerve conduction study', 'muscle': 'Needle EMG', 'other': 'Other planned services'}
    descriptions = {'visit': 'Choose one anticipated visit level. Final coding follows the documented care.',
                    'nerve': 'Choose the range for the total planned nerve studies. These levels are alternatives.',
                    'muscle': 'Add muscle testing to the nerve study. Specify the limbs being examined.',
                    'other': 'Include only services expected at this appointment.'}
    groups = [{'id': key, 'label': labels[key], 'description': descriptions[key],
               'selection_mode': 'single' if key in {'visit', 'nerve'} else 'multiple',
               'role': 'Main service' if key == ('nerve' if emg else 'visit') else 'Additional services',
               'ids': [row['id'] for row in available if clinical_group(row['code']) == key]}
              for key in order if any(clinical_group(row['code']) == key for row in available)]
    scheduled = [row['id'] for row in available if row['source'] == 'eCW scheduled service']
    recommended = list(scheduled)
    basis = 'Planned services on this eCW appointment' if scheduled else ''
    for group in groups:
        if group['selection_mode'] == 'single' and len(set(scheduled) & set(group['ids'])) > 1:
            recommended = [key for key in recommended if key not in group['ids']]
            basis = 'eCW contains multiple records for alternative services. Choose the intended procedure for this visit.'
    if not scheduled:
        # Only use this patient's comparable completed visits. An empty or tied
        # history cannot establish a "most common" code or a clinical quantity.
        history = [row for row in source.get('recent_completed_appointments', [])
                   if (emg and any(code in cpt_rules.NCS_CODES for code in row.get('coded_services', [])))
                   or (not emg and inputs.get('visit_type') and row.get('visit_type') == inputs['visit_type'])]
        for group in groups:
            if group['id'] not in ({'nerve', 'muscle'} if emg else {'visit'}):
                continue
            counts = Counter(code for visit in history for code in set(visit.get('coded_services', [])))
            rows = [row for row in available if row['id'] in group['ids'] and counts[row['code']] > 0]
            if rows:
                highest = max(counts[row['code']] for row in rows)
                winners = [row for row in rows if counts[row['code']] == highest]
                if len(winners) == 1:
                    recommended.append(winners[0]['id'])
        if recommended:
            basis = f'Most frequent matching services in {len(history)} recent eCW visit(s) for this patient. Review for today’s plan.'
    return {'kind': 'emg_ncs' if emg else 'office' if inputs.get('visit_type') else 'other',
            'groups': groups, 'recommended_ids': recommended, 'recommendation_basis': basis,
            'history_count': len(source.get('recent_completed_appointments', []))}




def reconcile_eligibility(saved: dict, benefits: dict, checked_at: str | None) -> dict:
    """Reconcile dated coverage evidence; an in-force flag supplies no benefit amounts."""
    active = benefits.get('active')
    if benefits.get('benefit_source') == 'eCW saved eligibility':
        return {'requires_review': False, 'message': 'Current matched eCW eligibility supplies these service benefits; no Stedi check was needed.'}
    if saved.get('status') not in {'verified_active', 'verified_inactive'}:
        return {'requires_review': False, 'message': (
            'Current benefit information comes from Stedi; no usable current eCW eligibility was returned.' if type(active) is bool else
            'No usable current eCW or Stedi eligibility was returned. Payer verification is still needed.')}
    if type(active) is not bool:
        return {'requires_review': False, 'message': 'Current eCW eligibility is on file. Additional payer benefits are still needed for this visit.'}
    if active == saved.get('active'):
        return {'requires_review': False, 'message': 'eCW and Stedi agree on coverage. Stedi supplies the additional service benefits.'}
    try:
        newer = datetime.fromisoformat(checked_at.replace('Z', '+00:00')) > datetime.fromisoformat(saved['checked_at'].replace('Z', '+00:00'))
    except (AttributeError, KeyError, TypeError, ValueError):
        newer = False
    return {'requires_review': not newer, 'message': (
        'The newer Stedi check updates the earlier eCW coverage status. Both results are retained.' if newer else
        'Eligibility evidence conflicts: the eCW result is newer or the order of the checks is unknown. Refresh payer benefits before collection.')}


def arrival_collection(price: dict, benefits: dict, review: dict) -> dict:
    """At-service patient obligations, separate from the insurer's later payment."""
    result = {'amount_cents': None, 'estimated_cents': None, 'verified_copay_cents': None, 'status': 'verify', 'basis': 'patient_obligations',
              'prior_balance_cents': None, 'noncovered_cents': None, 'payments_received_cents': None,
              'reason': 'Verify coverage, planned services, benefits and approval requirements before collecting.'}
    if price.get('service_issues'):
        return result
    if benefits.get('active') is not True or not verified_in_network(benefits) or benefits.get('has_secondary') or benefits.get('service_date_matches') is False or not identity.patient_benefits_bound(benefits.get('identity')) or any('identity' in reason.lower() or 'evidence' in reason.lower() for reason in benefits.get('review_reasons', [])):
        return result
    same_alternative_total = (price.get('mode') == 'alternatives' and price.get('complete')
                              and price.get('patient_min_cents') is not None
                              and price.get('patient_min_cents') == price.get('patient_max_cents'))
    if price.get('mode') == 'alternatives' and not price.get('conditional') and not same_alternative_total:
        # One office visit: collect a common verified copay only when every
        # possible level resolves to the same applicable benefit and approvals.
        lines = price.get('lines') or []
        office_codes = {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}
        if lines and all(row.get('code') in office_codes and
                         all(row.get('benefits', {}).get('requirements', {}).get(kind, {}).get('cleared')
                             for kind in ('referral', 'authorization')) for row in lines):
            amounts = [arrival_collection({'complete': False, 'lines': [row]}, benefits, review) for row in lines]
            if all(row['status'] == 'copay_only' for row in amounts) and len({row['verified_copay_cents'] for row in amounts}) == 1:
                return {**amounts[0], 'reason': 'The same verified office-visit copay applies to every anticipated E/M level. ' + amounts[0]['reason']}
    if price.get('conditional') or price.get('mode') == 'options' or (price.get('mode') == 'alternatives' and not same_alternative_total):
        result['estimated_cents'] = price.get('patient_max_cents')
        result['reason'] = 'This is a planning amount. Confirm the services and resolve benefit or approval conditions before collection.'
        return result
    office = [line for line in price.get('lines', []) if str(line.get('code')) in {str(n) for n in (*range(99202, 99206), *range(99212, 99216))}]
    if len(office) == 1:
        terms = office[0]['benefits']
        if (office[0].get('units') == 1 and not terms.get('requires_review') and not office[0].get('conditions')
                and terms.get('cost_share') == 'copay' and terms.get('deductible_applies') is False
                and terms.get('copay_period') in {'VISIT', '27', 'DAY', '7', 'EPISODE'}
                and terms.get('copay_cents') is not None and terms.get('oop_remaining_cents') is not None):
            result['verified_copay_cents'] = min(terms['copay_cents'], terms['oop_remaining_cents'])
    if not price.get('complete') and result['verified_copay_cents'] is None:
        return result
    payments = review.get('payments') or {}
    result['estimated_cents'] = price.get('patient_max_cents') if price.get('complete') else None
    amount = (result['verified_copay_cents'] if result['verified_copay_cents'] is not None
              else result['estimated_cents'])
    if payments.get('reference'):
        result['payments_received_cents'] = payments['amount_cents']
        amount = max(0, amount - payments['amount_cents'])
    result.update(amount_cents=amount, status='estimate', reason=(
        'Copay, applicable deductible and coinsurance for this visit, less verified payments. Prior balances and non-covered charges are not included unless separately verified.' if payments.get('reference') else
        'Copay, applicable deductible and coinsurance for this visit, before payments or credits. Check the clinic ledger for prior balances and amounts already collected; those values were not returned.'))
    if result['verified_copay_cents'] is not None:
        result.update(status='copay_only', basis='office_copay', reason='Only the verified office copay is included. Additional deductible and coinsurance remain for estimation or post-adjudication billing. ' +
                      ('Verified payments have been subtracted.' if payments.get('reference') else 'Before payments or credits; check the clinic ledger.'))
    return result


def planning_estimate(price: dict, benefits: dict, comparison: dict) -> dict | None:
    """A conditional planning result never authorizes collection or clears evidence."""
    lines = price.get('lines') or []
    if (not lines or benefits.get('active') is not True or benefits.get('has_secondary')
            or benefits.get('network_status') == 'out_of_network'
            or benefits.get('service_date_matches') is False or price.get('service_issues')
            or (not identity.patient_benefits_bound(comparison) and not
                (comparison.get('status') == 'review' and identity.name_difference_only(comparison)))
            or any('evidence' in r.lower() or 'date' in r.lower() for r in benefits.get('review_reasons', []))
            or any('needs mapping' in line.get('benefit_basis', '') for line in lines)
            or any(requirement.get('status') in {'denied', 'expired', 'pending'}
                   for line in lines for requirement in line.get('benefits', {}).get('requirements', {}).values())):
        return None
    assumptions = []
    if comparison.get('status') == 'review':
        assumptions.append('Calculated using the returned patient coverage. eCW and payer patient names differ; both are retained for billing.')
    if benefits.get('network_status') != 'in_network':
        assumptions.append('The provider and visit location participate in this plan’s network.')
    assumptions.append('The selected services are covered and any required referral or authorization is in place.')
    if benefits.get('network_status') == 'in_network' and all(line.get('units') and line['benefits'].get('active') is True
           and line['benefits'].get('network_status') != 'out_of_network'
           and (line['benefits'].get('oop_remaining_cents') == 0 or
                line['benefits'].get('family_oop_zero_applies'))
           and not line['benefits'].get('separate_copay_limit')
           and line['benefits'].get('service_date_matches') is not False
           and not line['benefits'].get('has_secondary')
           and not any('limit' in r.lower() or 'date' in r.lower() for r in line['benefits'].get('review_reasons', []))
           for line in lines):
        family = any(line['benefits'].get('family_oop_zero_applies') for line in lines)
        if family:
            assumptions.append('The payer reports $0 remaining on the family in-network out-of-pocket limit for these services; individual balances remain separate.')
        return {'amount_cents': 0, 'collectable': False, 'basis': 'out_of_pocket_limit',
                'explanation': 'The payer reports $0 remaining on the applicable in-network out-of-pocket limit. Under the assumptions below, the selected covered services have an estimated patient cost of $0.',
                'assumptions': assumptions}
    if (price.get('complete') and price.get('patient_min_cents') is not None
            and price.get('patient_max_cents') is not None):
        minimum, maximum = price['patient_min_cents'], price['patient_max_cents']
        return {'amount_cents': minimum if minimum == maximum else None,
                'min_cents': minimum, 'max_cents': maximum,
                'collectable': False, 'basis': 'conditional_services',
                'explanation': ('Estimated patient responsibility across mutually exclusive service alternatives.'
                                if price.get('mode') == 'alternatives' else
                                'Estimated patient responsibility for the selected services, before payments and credits.'),
                'assumptions': assumptions}
    return None
