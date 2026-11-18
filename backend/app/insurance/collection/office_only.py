"""Keep an evidenced office copay when separately planned procedures remain unresolved."""
from __future__ import annotations

from app.insurance.benefit_review import selected_service_blocker
from app.insurance.identity import patient_benefits_bound


def apply(result: dict, review: dict | None, guidance: dict, build) -> dict:
    if (guidance.get('amount_cents') is not None or guidance.get('integrity_error')
            or result.get('payment_mode') == 'cash'
            or (result.get('billing_note') or {}).get('kind') == 'no_collection'):
        return guidance
    price = result.get('price_range') or {}
    lines = price.get('lines') or []
    office = [line for line in lines if str(line.get('code') or '').isdigit()
              and (99202 <= int(line['code']) <= 99205 or 99212 <= int(line['code']) <= 99215)]
    other = [line for line in lines if line not in office]
    if (price.get('mode') != 'services' or len(office) != 1
            or not (other or price.get('service_issues'))):
        return guidance
    line = office[0]
    selected = [code for code in result.get('codes') or []
                if code.get('id') == line.get('id') and code.get('code') == line.get('code')
                and code.get('units') == line.get('units') == 1]
    if (len(selected) != 1 or not (result.get('service_selection_status') == 'approved'
                                  or selected[0].get('source') == 'eCW scheduled service')
            or (other or result.get('botox_review') is not None)
            and result.get('separate_em_planned') is not True):
        return guidance
    benefits = result.get('benefits') or {}
    identity = result.get('identity') or benefits.get('identity') or {}
    if (benefits.get('active') is not True or benefits.get('service_date_matches') is False
            or benefits.get('coverage_status_conflict') or benefits.get('coverage_dates_invalid')
            or benefits.get('historical_only') or benefits.get('source_errors')
            or benefits.get('network_conflict') or benefits.get('has_secondary')
            or (result.get('recovery') or {}).get('reason_code') in
               {'multiple_active_policies', 'same_policy_evidence_conflict'}
            or (result.get('card_check') or {}).get('status') in {'conflict', 'mismatch'}
            or (result.get('card_check') or {}).get('independent_ecw_check') is True
            or not patient_benefits_bound(identity)
            or selected_service_blocker(benefits.get('network_status') or 'unknown', identity, [line])
            or (line.get('benefits') or {}).get('source_errors')):
        return guidance
    evidence = result.get('requirement_evidence') or {}
    decisions = [row for row in evidence.get('procedures') or []
                 if row.get('id') == line.get('id') or row.get('code') == line.get('code')]
    office_requirements = (line.get('benefits') or {}).get('requirements') or {}
    for kind in ('referral', 'authorization'):
        if str((office_requirements.get(kind) or {}).get('status') or '').lower() in {'denied', 'expired'}:
            return guidance
    for item in decisions:
        for kind in ('referral', 'authorization'):
            requirement = item.get(kind) or {}
            if (str(requirement.get('status') or '').lower() in {'denied', 'expired'}
                    or str(requirement.get('pa_approval') or '').upper() in {'DENIED', 'EXPIRED'}):
                return guidance
    if any(str((evidence.get(kind) or {}).get('pa_approval') or '').upper() in {'DENIED', 'EXPIRED'}
           or str((evidence.get(kind) or {}).get('status') or '').lower() in {'denied', 'expired'}
           for kind in ('referral', 'authorization')):
        return guidance
    # Reuse the complete copay and clinic-policy gates with only the documented office line.
    scoped_price = {**price, 'lines': [line], 'complete': False, 'conditional': False,
                    'patient_min_cents': None, 'patient_max_cents': None, 'service_issues': []}
    scoped_result = {**result, '_office_only_eval': True, 'price_range': scoped_price,
                     'codes': selected, 'selected_ids': [line['id']],
                     'service_plan': {**(result.get('service_plan') or {}), 'recommended_ids': [line['id']]},
                     'requirement_evidence': {**evidence, 'procedures': decisions},
                     'arrival_collection': {}, 'planning_estimate': {}, 'fee_proxy_estimate': {}}
    scoped = build(scoped_result, review)
    if (scoped.get('basis') != 'office_copay' or type(scoped.get('amount_cents')) is not int
            or not scoped.get('collection_rule')):
        return guidance
    unresolved = list(dict.fromkeys([
        *(str(issue) for issue in price.get('service_issues') or [] if issue),
        *(f'CPT {item.get("code")}: {issue}' for item in other
          for issue in item.get('issues') or [] if issue),
        *(f'CPT {row.get("code")}: {kind} {approval.lower()}'
          for row in evidence.get('procedures') or [] if row not in decisions
          for kind in ('referral', 'authorization')
          for approval in [str((row.get(kind) or {}).get('pa_approval') or '').upper()]
          if approval in {'DENIED', 'EXPIRED'}),
        *(f'CPT {item.get("code")} remains outside this office-only advance' for item in other),
    ]))
    guidance.update(amount_cents=scoped['amount_cents'], status=scoped['status'],
                    basis='office_copay', scope='office_only', office_only_line_id=line['id'],
                    collection_rule=scoped['collection_rule'],
                    office_visit_copay_advance=scoped.get('office_visit_copay_advance'),
                    unresolved_service_issues=unresolved,
                    reason='Collect only the supported office-visit copay. Other planned services remain under review.',
                    patient_message='This advance covers only the office visit. Other planned services and final responsibility follow the processed claim.',
                    assumptions=list(dict.fromkeys([*(guidance.get('assumptions') or []),
                                                     *(scoped.get('assumptions') or [])])))
    guidance.pop('policy_setup_required', None)
    guidance.pop('positive_advance_policy_missing', None)
    return guidance
