"""Actionable enrollment blocker for the selected eligibility payer route."""
from __future__ import annotations


def _enrollment_failure(enrollment: dict) -> dict:
    descriptions = {
        'LOOKUP_FAILED': 'The provider enrollment status could not be read. This does not establish missing enrollment. Retry the enrollment lookup before eligibility.',
        'MISSING': 'No eligibility enrollment was returned for this exact payer and provider NPI.',
        'REJECTED': 'The payer rejected this provider eligibility enrollment. Review the enrollment decision.',
        'CANCELED': 'This provider eligibility enrollment was canceled.',
        'DRAFT': 'The provider eligibility enrollment is still a draft and has not been submitted.',
        'PROVIDER_ACTION_REQUIRED': 'The provider must complete the outstanding enrollment requirements.',
    }
    detail = descriptions.get(enrollment['status'], 'Provider eligibility enrollment is pending or not verified as live.')
    if enrollment.get('route') == 'original_medicare_hets' and enrollment['status'] != 'LOOKUP_FAILED':
        detail = ('CMS HETS eligibility for Original Medicare Part A/B requires completed EDI attestation '
                  'for this billing NPI. The recorded enrollment is ' + enrollment['status'].replace('_', ' ').lower() + '.')
        action = ('Clinic administrator: complete the CMS HETS EDI attestation for this billing NPI '
                  'through the authorized Stedi enrollment task; then recheck enrollment. '
                  'No paid eligibility request was submitted.')
    else:
        action = detail + ' No paid eligibility request was submitted.'
    return {'kind': 'provider_enrollment', 'title': 'Provider enrollment status', 'detail': detail,
            'action': action, 'codes': [], 'reason_code': enrollment['reason_code']}
