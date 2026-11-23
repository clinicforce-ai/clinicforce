import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

export function coveragePolicyStatus({
  result,
  tooEarly,
  readOnly,
  busy,
  failureKind,
  candidateStatus,
  patientBenefitsBound,
}: {
  result: PrevisitEstimate | null
  tooEarly: boolean
  readOnly: boolean
  busy: string | null
  failureKind?: string
  candidateStatus?: string
  patientBenefitsBound: boolean
}) {
  const benefits = result?.benefits
  const policyStatus = tooEarly
    ? 'Check closer to visit'
    : result?.reference_only && result.reference_benefits?.active === true
      ? 'Aetna coverage active'
      : result?.recovery?.state === 'pending'
        ? readOnly
          ? benefits?.active === true
            ? 'Payer benefits returned · policy search was pending'
            : 'Coverage check was pending'
          : benefits?.active === true
            ? 'Payer benefits returned · policy search in progress'
            : 'Coverage check in progress'
        : result?.recovery?.state === 'indeterminate'
          ? benefits?.active === true
            ? 'Payer benefits returned · policy search outcome unknown'
            : 'Coverage check outcome unknown'
          : result?.recovery?.reason_code === 'multiple_active_policies'
            ? result?.coordination?.status === 'primacy_reported'
              ? 'Stedi COB reports payer roles · reconcile policies'
              : benefits?.active === true &&
                  benefits.service_date_matches !== false &&
                  patientBenefitsBound
                ? `${result.payer || 'Selected payer'} coverage active · payer order unverified`
                : 'Multiple active policies · payer order unknown'
            : result?.recovery?.reason_code === 'same_policy_evidence_conflict'
              ? benefits?.active === true
                ? 'Payer benefits returned · policy evidence conflicts'
                : 'Policy evidence conflicts'
              : result?.recovery?.state === 'complete' &&
                  result?.recovery?.outcome === 'no_match' &&
                  benefits?.active === true &&
                  result.coverage_status !== 'VERIFIED'
                ? result?.identity?.status === 'review'
                  ? 'Payer benefits returned · patient identity differs'
                  : 'Payer benefits returned · policy match unresolved'
                : result?.recovery?.state === 'complete' &&
                    result?.recovery?.outcome === 'no_match' &&
                    result.coverage_status !== 'VERIFIED'
                  ? result?.coverage_status === 'NOT_FOUND'
                    ? 'Coverage not found electronically'
                    : 'No matching policy found in discovery'
                  : result?.recovery?.state === 'rejected' ||
                      result?.recovery?.outcome === 'not_available'
                    ? benefits?.active === true
                      ? 'Payer benefits returned · policy search could not complete'
                      : 'Coverage check could not complete'
                    : result?.payment_mode === 'cash' && result.cash_fallback
                      ? result.insurance_diagnosis?.eligibilityStatus === 'BENEFITS_VERIFIED'
                        ? 'Coverage active'
                        : 'Payer benefits unresolved · cash option'
                      : benefits?.active === true && !patientBenefitsBound
                        ? result?.identity?.status === 'review'
                          ? 'Payer benefits returned · patient identity differs'
                          : 'Payer benefits returned · patient match missing'
                        : result?.collection_guidance?.collection_rule?.mode ===
                            'aetna_office_individual_oop_deferral'
                          ? 'Aetna office benefits · $0 check-in'
                          : benefits?.service_date_matches === false
                            ? 'Coverage dates differ from visit'
                            : benefits?.active === true
                              ? 'Coverage active'
                              : benefits?.coverage_evidence?.status === 'EXPIRED'
                                ? 'Returned policy expired'
                                : benefits?.coverage_evidence?.status === 'FUTURE'
                                  ? 'Coverage starts after this visit'
                                  : benefits?.active === false
                                    ? 'Coverage inactive'
                                    : candidateStatus === 'EXPIRED'
                                      ? 'Returned policy expired'
                                      : candidateStatus === 'INACTIVE'
                                        ? 'Returned policy inactive'
                                        : candidateStatus === 'FUTURE'
                                          ? 'Returned coverage starts after this visit'
                                          : result?.coverage_status === 'NOT_FOUND'
                                            ? 'Coverage not found electronically'
                                            : result?.coverage_status === 'REVIEW_REQUIRED'
                                              ? 'Policy review pending'
                                              : result?.ecw_coverage_active
                                                ? 'eCW policy · payer check needed'
                                                : result?.recovery?.reason_code ===
                                                    'multiple_active_policies'
                                                  ? 'Multiple active policies found'
                                                  : failureKind === 'provider_enrollment'
                                                    ? 'Clinic payer access missing'
                                                    : result?.identity?.status === 'review'
                                                      ? 'Policy details differ'
                                                      : result?.cash_fallback
                                                        ? 'Payer check needed'
                                                        : result
                                                          ? 'Payer check incomplete'
                                                          : busy === 'estimate'
                                                            ? 'Coverage check in progress'
                                                            : 'Coverage not checked'
  return policyStatus
}

export function coveragePolicyTone({
  policyStatus,
  result,
  tooEarly,
  failureKind,
  referenceActive,
}: {
  policyStatus: string
  result: PrevisitEstimate | null
  tooEarly: boolean
  failureKind?: string
  referenceActive: boolean
}) {
  if (
    referenceActive ||
    policyStatus === 'Coverage active' ||
    policyStatus === 'Aetna office benefits · $0 check-in'
  )
    return 'is-confirmed'
  if (policyStatus === 'Coverage dates differ from visit') return 'is-review'
  if (
    !result ||
    tooEarly ||
    result.recovery?.state === 'pending' ||
    failureKind === 'provider_enrollment' ||
    (result.payment_mode === 'cash' && !result.cash_fallback)
  )
    return ''
  return 'is-missing'
}
