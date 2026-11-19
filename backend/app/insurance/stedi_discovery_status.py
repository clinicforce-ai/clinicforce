"""Public status projection for bounded Stedi discovery."""
from datetime import datetime, timedelta, timezone

def public_status(discovery: dict) -> dict:
    state = discovery.get('request_state')
    if (discovery.get('exhausted') is False and state in {'complete', 'rejected'}
            and discovery.get('outcome') != 'recovered'):
        state = 'pending'  # The next automatic recovery stage has not finished.
    # ponytail: background work is bounded below ten minutes. Interrupted tasks
    # remain indeterminate; resume only GET by a known ID, never another POST.
    if state == 'pending' and discovery.get('started_at'):
        try:
            if datetime.fromisoformat(discovery['started_at']) + timedelta(minutes=10) < datetime.now(timezone.utc):
                state = 'indeterminate'
        except (ValueError, TypeError):
            state = 'indeterminate'
    outcome = discovery.get('outcome')
    # Older caches used no_match when ClinicForce filtered every returned item.
    # Only a completed, empty upstream search can establish NOT_FOUND.
    filtered_legacy_result = outcome == 'no_match' and bool(discovery.get('coverage_count') or discovery.get('candidates'))
    if filtered_legacy_result:
        outcome = 'review_needed'
    message = discovery.get('selection_error') or {
        'recovered': 'Coverage recovered from Stedi and rechecked with the scheduled provider and visit date.',
        'no_match': 'The additional Stedi search did not find a matching policy. Saved eCW insurance details are retained below. This search does not establish that coverage is inactive.',
        'review_needed': 'Returned insurance information requires identity, coverage-date or policy-order review before it can verify coverage.',
        'rejected': 'Stedi could not complete insurance discovery. The original payer response and clinic rates are retained.',
        'not_available': 'Insurance discovery did not start. Open the saved recovery steps to correct the missing patient, provider or payer details.',
    }.get(outcome, 'Searching Stedi using the patient details from eCW. This usually takes a few seconds and can take several minutes.'
          if state == 'pending' else 'No payer coverage decision is recorded. Open the saved recovery steps before collecting.')
    if filtered_legacy_result:
        message = 'The saved search returned policies, but no usable match was established. Coverage requires review, not an uninsured classification.'
    if state == 'indeterminate':
        message = 'The recovery request outcome is unknown. Check Stedi activity before submitting another search.'
    elif discovery.get('exhausted') is False and state == 'pending':
        message = 'Continuing automatic insurance recovery using the available records and payer interfaces.'
    elif (discovery.get('exhausted') and outcome != 'recovered'
          and (not discovery.get('selection_error') or discovery.get('reason_code') not in {
              'returned_identity_or_coverage_mismatch', 'returned_coverage_not_established',
              'eligibility_rejected', 'selected_policy_check_failed'})):
        if discovery.get('reason_code') == 'multiple_active_policies':
            message = 'Multiple active medical policies were returned. Reconcile the subscriber details and establish payer order before collecting.'
        elif discovery.get('reason_code') == 'same_policy_evidence_conflict':
            message = 'One active medical policy has conflicting subscriber, date or visit-benefit evidence. Reconcile the payer responses before collecting.'
        elif discovery.get('reason_code') in {'identity_or_coverage_review', 'subscriber_relationship_conflict',
                                              'returned_identity_or_coverage_mismatch'}:
            message = 'The returned policy and eCW identity or coverage details differ. Reconcile the flagged fields before using the payer benefits.'
        elif not discovery.get('selection_error'):
            message = 'The automatic checks finished without a patient-matched policy. Review the payer response and exact missing input before collecting.'
    candidates = [{key: value for key, value in row.items() if key != 'index'} for row in discovery.get('candidates', [])]
    return {'state': state, 'outcome': outcome, 'message': message,
            'coverage_status': 'VERIFIED' if state == 'complete' and outcome == 'recovered' else
                'NOT_FOUND' if state == 'complete' and outcome == 'no_match' else 'REVIEW_REQUIRED',
            'card_status': discovery.get('card_status'),
            'exhausted': discovery.get('exhausted'),
            'steps': discovery.get('steps') or {},
            'payer_routes': [{'id': row['stediId'], 'name': row.get('displayName'),
                'payer_id': row.get('primaryPayerId'), 'aliases': row.get('aliases') or [],
                'match_basis': row.get('match_basis', 'name_or_alias'),
                'names': row.get('names') or [], 'eligibility': row.get('transactionSupport', {}).get('eligibilityCheck')}
                for row in discovery.get('payer_routes', [])] if outcome != 'recovered' else [],
            'attempt_count': len(discovery.get('attempts') or []),
            'started_at': discovery.get('started_at'), 'checked_at': discovery.get('checked_at'),
            'reference': discovery.get('discovery_id'), 'coverage_count': discovery.get('coverage_count'),
            'raw_candidate_count': discovery.get('raw_candidate_count', discovery.get('coverage_count')),
            'active_policy_count': discovery.get('active_policy_count'),
            'retained_candidate_count': len(candidates),
            'verified_candidate_count': sum(row.get('verification_status') == 'VERIFIED' for row in candidates),
            'stage': discovery.get('stage'), 'reason_code': 'identity_or_coverage_review' if filtered_legacy_result else discovery.get('reason_code'),
            'candidates': candidates}
