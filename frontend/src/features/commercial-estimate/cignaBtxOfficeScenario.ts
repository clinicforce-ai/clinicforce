import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'
import { patientBenefitsBound } from './identityBinding'
import { specialistOfficeCopay, specialistOfficeCoinsurance } from './payerReportedPartialCopay'

export function cignaBtxOfficeScenario(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): (CostBreakdownRow & { modeledAmountCents: number }) | null {
  const benefits = result.benefits
  const cignaBtx =
    /\bcigna\b/i.test(result.payer || '') &&
    (result.service_plan?.kind === 'botox' || /\b(?:BTX|Botox)\b/i.test(result.reason || ''))
  const officeCopay = cignaBtx ? specialistOfficeCopay(result) : null
  const officeCoinsurance = cignaBtx ? specialistOfficeCoinsurance(result) : null
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    result.reference_benefits ||
    result.collection_guidance?.amount_cents != null ||
    benefits?.active !== true ||
    benefits.service_date_matches === false ||
    benefits.network_status === 'out_of_network' ||
    benefits.network_conflict ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.source_errors?.length ||
    !patientBenefitsBound(result.identity) ||
    result.billing_protection?.status === 'qmb' ||
    result.price_range?.lines?.some((line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code)) ||
    officeCopay == null ||
    officeCopay <= 0 ||
    officeCoinsurance == null ||
    Number(officeCoinsurance) !== 0 ||
    benefits.deductible_remaining_cents !== 0 ||
    benefits.oop_remaining_cents == null ||
    benefits.oop_remaining_cents < officeCopay
  )
    return null
  const balance = Math.max(
    0,
    officeCopay - (result.encounter_summary?.payments_verified_cents || 0),
  )
  return {
    modeledAmountCents: officeCopay,
    label: 'Amount to collect at visit · office scenario pending',
    value: `${money(balance)} conditional specialist office visit · do not collect yet`,
    detail: `min(${money(officeCopay)} office copay + $0.00 remaining deductible + 0% office coinsurance, ${money(benefits.oop_remaining_cents)} remaining out-of-pocket) = ${money(officeCopay)} before verified prior payments. BTX injection and drug charges, separate E/M work, payer order and the final allowed amount are not established. This is an office-only scenario, not an approved charge.`,
    emphasis: true,
  }
}
