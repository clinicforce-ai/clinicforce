import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'
import { firstSelectedCptIssue } from './selectedCptIssue'

export function payerReportedPartialCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
) {
  const benefits = result.benefits
  const line = model.officeCopayLine
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    model.amount ||
    model.estimatedShare ||
    model.provisionalAmount != null ||
    !['local_medicare_advantage_ppo', 'regional_medicare_advantage_ppo'].includes(
      result.agreement_exclusion?.product_kind || '',
    ) ||
    result.price_range?.mode !== 'services' ||
    result.price_range.service_issues?.length ||
    benefits?.active !== true ||
    benefits.service_date_matches === false ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.historical_only ||
    benefits.source_errors?.length ||
    benefits.has_secondary ||
    benefits.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    !patientBenefitsBound(result.identity) ||
    firstSelectedCptIssue(result) ||
    line?.benefits.active !== true ||
    line.benefits.source_errors?.length ||
    !line.benefits.network_independent
  )
    return null
  const copay = line.benefits.copay_cents
  return typeof copay === 'number' && Number.isInteger(copay) && copay > 0 ? copay : null
}
