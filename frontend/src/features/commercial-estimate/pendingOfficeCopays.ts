import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'

export function pendingBluecardOfficeCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const line =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  const terms = line?.benefits
  const copay = terms?.copay_cents
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    model.amount ||
    result.collection_guidance?.amount_cents != null ||
    !/\b(?:blue cross|bcbs(?:tx| texas)?)\b/i.test(result.payer || '') ||
    result.plan_details?.stedi?.names?.length !== 1 ||
    !/\b(?:PPO|Preferred Provider Organization)\b/i.test(result.plan_details.stedi.names[0]) ||
    result.benefits?.active !== true ||
    result.benefits.service_date_matches === false ||
    result.benefits.has_secondary ||
    result.benefits.network_conflict ||
    result.benefits.coverage_status_conflict ||
    result.benefits.coverage_dates_invalid ||
    result.benefits.historical_only ||
    result.benefits.source_errors?.length ||
    result.benefits.network_status === 'out_of_network' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    !patientBenefitsBound(result.identity) ||
    !line ||
    !/^992(?:0[2-5]|1[2-5])$/.test(line.code) ||
    line.units !== 1 ||
    line.modifiers?.length ||
    terms?.active !== true ||
    terms.network_status === 'out_of_network' ||
    terms.service_date_matches === false ||
    terms.has_secondary ||
    terms.coverage_status_conflict ||
    terms.coverage_dates_invalid ||
    terms.source_errors?.length ||
    (terms.copay_period && !['VISIT', '27', 'DAY', '7'].includes(terms.copay_period)) ||
    (terms.deductible_applies !== false && terms.deductible_remaining_cents !== 0) ||
    (terms.coinsurance_percent != null && Number(terms.coinsurance_percent) !== 0) ||
    !Number.isInteger(copay) ||
    copay! <= 0 ||
    (terms.separate_copay_limit
      ? terms.copay_remaining_cents == null || terms.copay_remaining_cents < copay!
      : terms.oop_remaining_cents == null || terms.oop_remaining_cents < copay!)
  )
    return null
  return Math.max(0, copay! - (result.encounter_summary?.payments_verified_cents || 0))
}

export function pendingCignaOfficeCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const officeLine =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  return !model.cash &&
    !model.previousEstimate &&
    !model.requirementIssue &&
    result.collection_guidance?.amount_cents == null &&
    /\bcigna\b/i.test(result.payer || '') &&
    result.benefits?.active !== false &&
    result.benefits?.network_status === 'in_network' &&
    !result.benefits?.network_conflict &&
    result.benefits?.service_date_matches !== false &&
    !result.benefits?.coverage_status_conflict &&
    !result.benefits?.coverage_dates_invalid &&
    !result.benefits?.has_secondary &&
    !result.benefits?.source_errors?.length &&
    !result.benefits?.historical_only &&
    !result.price_range?.service_issues?.length &&
    result.recovery?.reason_code !== 'multiple_active_policies' &&
    result.identity?.status !== 'review' &&
    result.billing_protection?.status !== 'qmb' &&
    officeLine?.units === 1 &&
    /^992(?:0[2-5]|1[2-5])$/.test(officeLine.code) &&
    officeLine.benefits?.active !== false &&
    officeLine.benefits?.network_status !== 'out_of_network' &&
    !officeLine.benefits?.coverage_status_conflict &&
    !officeLine.benefits?.coverage_dates_invalid &&
    officeLine.benefits?.service_date_matches !== false &&
    !officeLine.benefits?.source_errors?.length &&
    (officeLine.benefits?.deductible_applies !== true ||
      officeLine.benefits?.deductible_remaining_cents === 0) &&
    [undefined, null, '0', '0.0', '0.00'].includes(officeLine.benefits?.coinsurance_percent) &&
    (officeLine.benefits?.oop_remaining_cents == null ||
      officeLine.benefits.oop_remaining_cents >= officeLine.benefits.copay_cents!) &&
    Number.isInteger(officeLine.benefits?.copay_cents) &&
    officeLine.benefits!.copay_cents! > 0
    ? Math.max(
        0,
        officeLine.benefits.copay_cents! -
          Math.max(0, result.encounter_summary?.payments_verified_cents || 0),
      )
    : null
}
