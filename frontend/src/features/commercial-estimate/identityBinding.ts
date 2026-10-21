export function patientBenefitsBound(
  identity?: {
    status?: string
    binding?: string | null
    match_version?: number
    reconciliation_rule?: string | null
  } | null,
): boolean {
  return (
    identity?.match_version === 2 &&
    ((['matched', 'reconciled', 'warning'].includes(identity.status || '') &&
      Boolean(identity.binding)) ||
      (identity.status === 'matched' &&
        identity.binding == null &&
        identity.reconciliation_rule === 'ecw_patient_coverage_reference'))
  )
}
