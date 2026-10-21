import type { CommercialEstimateActions } from './useCommercialEstimateActions'
import { range } from './format'

export function benefitPresentation(
  result: CommercialEstimateActions['result'],
  guards: {
    networkConfirmed: boolean
    payerEvidenceSafe: boolean
    financialSourceValid: boolean
    patientBenefitsBound: boolean
  },
) {
  const benefits = result?.benefits
  const price = result?.price_range
  const patientPrice =
    benefits?.active === true &&
    benefits.service_date_matches !== false &&
    guards.payerEvidenceSafe &&
    guards.financialSourceValid &&
    guards.networkConfirmed &&
    result?.encounter_summary?.complete === true &&
    guards.patientBenefitsBound &&
    !benefits.has_secondary &&
    result?.recovery?.reason_code !== 'multiple_active_policies' &&
    price?.complete &&
    price?.patient_min_cents != null &&
    price.patient_max_cents != null
      ? range(price.patient_min_cents, price.patient_max_cents)
      : null
  const calculationReasons = [
    ...new Set((price?.lines || []).map((line) => line.benefits.benefit_summary).filter(Boolean)),
  ]
  const policySources = [
    ...new Map(
      (price?.lines || [])
        .filter((line) => line.benefits.policy_evidence)
        .map((line) => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!]),
    ).values(),
  ]
  return { patientPrice, calculationReasons, policySources }
}
