"""Plain-language eligibility enrollment causes and next steps."""

ENROLLMENT_REASON_ACTION = {
    'enrollment_lookup_failed': (
        'Stedi could not read the provider eligibility enrollment status',
        'retry the Stedi enrollment lookup for this payer and provider NPI before checking eligibility'),
    'enrollment_missing': (
        'Stedi returned no eligibility enrollment for this payer and provider NPI',
        'clinic administrator: enroll this provider NPI for eligibility with this payer in Stedi, then rerun the check'),
    'enrollment_rejected': (
        'Stedi reports this provider eligibility enrollment was rejected',
        'clinic administrator: review the rejection and correct the enrollment before rerunning eligibility'),
    'enrollment_canceled': (
        'Stedi reports this provider eligibility enrollment was canceled',
        'clinic administrator: restore or submit the enrollment before rerunning eligibility'),
    'enrollment_draft': (
        'Stedi reports this provider eligibility enrollment is still a draft',
        'clinic administrator: submit the draft enrollment before rerunning eligibility'),
    'enrollment_provider_action_required': (
        'Stedi reports this provider must complete enrollment steps',
        'clinic administrator: complete the provider steps in Stedi before rerunning eligibility'),
    'enrollment_pending': (
        'Stedi reports this provider eligibility enrollment is pending',
        'clinic administrator: check the enrollment status when processing completes, then rerun eligibility'),
}
ENROLLMENT_DEFAULT = (
    'Stedi has not returned live eligibility enrollment for this payer and provider NPI',
    'clinic administrator: review the recorded Stedi enrollment status for this payer and provider NPI, then rerun eligibility when live')
