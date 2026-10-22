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
  const payer = [result.stedi_payer?.name, result.payer].filter(Boolean).join(' ')
  const plans = result.plan_details?.stedi.names || []
  const officeLines = result.price_range?.lines?.filter((item) =>
    /^992(?:0[2-5]|1[2-5])$/.test(item.code),
  )
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    model.amount ||
    model.estimatedShare ||
    model.provisionalAmount != null ||
    !/\b(?:uhc|united\s*health(?:care)?)\b/i.test(payer) ||
    plans.length !== 1 ||
    !/(?:\bmedicare(?:\s+advantage)?\s+ppo\b|\b(?:rppo|lppo)\b)/i.test(plans[0]) ||
    result.price_range?.mode !== 'services' ||
    result.price_range.service_issues?.length ||
    officeLines?.length !== 1 ||
    benefits?.active !== true ||
    benefits.service_date_matches === false ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.network_conflict ||
    benefits.historical_only ||
    benefits.source_errors?.length ||
    benefits.has_secondary ||
    benefits.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    !patientBenefitsBound(result.identity) ||
    firstSelectedCptIssue(result) ||
    line?.benefits.active !== true ||
    Number(line.units) !== 1 ||
    line.benefits.source_errors?.length ||
    !['', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'].includes(
      line.benefits.copay_period || '',
    ) ||
    !line.benefits.network_independent
  )
    return null
  const copay = line.benefits.copay_cents
  return typeof copay === 'number' && Number.isInteger(copay) && copay > 0 ? copay : null
}
