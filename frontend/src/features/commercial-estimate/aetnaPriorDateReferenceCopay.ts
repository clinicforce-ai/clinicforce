import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'
import { singleOfficeCopayLine, specialistOfficeCopay } from './payerReportedPartialCopay'

export function aetnaPriorDateReferenceCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const benefits = result.reference_benefits ?? result.benefits
  const line = singleOfficeCopayLine(
    result.price_range?.mode === 'services' ? result.price_range.lines || [] : [],
  )
  const otherCoverage =
    benefits?.has_secondary || result.recovery?.reason_code === 'multiple_active_policies'
  if (
    model.cash ||
    model.previousEstimate ||
    model.calculating ||
    model.amount ||
    result.collection_guidance?.amount_cents != null ||
    !/^aetna$/i.test(result.payer?.trim() || '') ||
    benefits?.active !== true ||
    benefits.service_date_matches !== false ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.network_conflict ||
    benefits.historical_only ||
    benefits.source_errors?.length ||
    (otherCoverage &&
      (!patientBenefitsBound(result.identity) ||
        benefits.network_status !== 'in_network' ||
        benefits.network_source !== 'payer_participation_message')) ||
    benefits.qmb_status === 'protected' ||
    result.benefits?.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'same_policy_evidence_conflict' ||
    result.price_range?.service_issues?.length ||
    !line
  )
    return null
  const copay = specialistOfficeCopay({ ...result, benefits })
  return copay === line.benefits.copay_cents ? copay : null
}
